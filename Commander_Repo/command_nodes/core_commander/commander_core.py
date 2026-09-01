"""commander_core.py
CommanderCore - reusable commander subsystem
Does NOT inherit from BaseNode. Owns all commander-specific
logic that was previously embedded in CentralNode:
- RuleEngine / CommandDispatcher / CommandTracker
- ApprovalGate / PendingCommandStore / ApprovalHandler
- FireStateStore / MissionStore / AlertRepository
- RegistryBridge
- _expire_loop / _snapshot_loop background threads
Activation model:
_active = False handle_message() is a no-op
_active = True full routing + rule evaluation
Usage:
core = CommanderCore(node_id, mqtt, registry, db)
core.start(active=True) # CentralNode: always active
core.start(active=False) # BackupCommander: standby
core.activate() # promote backup on failover
core.deactivate() # stand down when primary back
core.handle_message(topic, payload)
core.stop()
State sync:
The backup pre-subscribes to wfc/state/snapshot so it
passively mirrors FireStateStore + MissionStore while
standby. On activation it is already current.
ApprovalHandler, CommandDispatcher,
CommandTracker, RegistryBridge,
AlertRepository, FireStateStore, MissionStore,
MQTTClient, NodeRegistry, Database
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import Any, Final

from core.approval.approval_gate import ApprovalGate
from core.approval.approval_handler import ApprovalHandler
from core.approval.pending_store import PendingCommandStore
from core.commands_monitor.command_dispatcher import CommandDispatcher
from core.commands_monitor.command_tracker import CommandTracker
from core.commands_monitor.lifecycle_rules import STATUS_TO_EVENT_TYPE
from core.messaging.mqtt_client import MQTTClient
from core.messaging.registry_bridge import RegistryBridge
from core.node_registry.registry import NodeRegistry
from core.persistence.database import Database
from core.persistence.repositories.alert_repo import AlertRepository
from core.persistence.repositories.fire_event_repo import FireEventRepository
from core.rule_engine.context import RuleContext
from core.rule_engine.engine import RuleEngine
from core.rule_engine.trigger import EvalTrigger
from core.state.domain_event_log import DomainEventLog
from core.state.fire_state_store import FireStateStore
from core.state.mission_store import MissionStore
from core.state.swarm_status_store import SwarmStatusStore
from core.utils.logger import log
from wfc_shared.enums.capabilities import SWARM_LEAD
from wfc_shared.enums.domain_event_types import (
    COMMAND_ACK_EXECUTED,
    COMMAND_ACK_FAILED,
    COMMAND_ACK_RECEIVED,
    FIRE_REDISPATCHED,
    LEADER_DIED,
    LEADER_REPLACED,
    NODE_BECAME_AVAILABLE,
)
from wfc_shared.enums.domain_event_types import (
    FIRE_CONTAINED as DE_FIRE_CONTAINED,
)
from wfc_shared.enums.domain_event_types import (
    FIRE_DETECTED as DE_FIRE_DETECTED,
)
from wfc_shared.enums.domain_event_types import (
    FIRE_SUPPRESSED as DE_FIRE_SUPPRESSED,
)
from wfc_shared.enums.events import (  # pyright: ignore[reportUnusedImport]
    FIRE_CONTAINED,
    FIRE_DETECTED,
    FIRE_SUPPRESSED,
)
from wfc_shared.enums.fire_status import ACTIVE, CONTAINED, SUPPRESSED
from wfc_shared.enums.mission_status import ASSIGNED, COMPLETED, RUNNING
from wfc_shared.enums.topics import (
    ACK,
    APPROVAL_RESPONSE,
    EVENTS_FIRE,
    FIRE_INTENSITY,
    FIRE_REKINDLED_TOPIC,
    FIRE_VERIFIED_TOPIC,
    REGISTRY_ANNOUNCE_TARGET,
    STATE_SNAPSHOT,
    SWARM_ELECTION_PREFIX,
    SWARM_ELECTION_SUB,
    SWARM_STATUS_PREFIX,
    SWARM_STATUS_SUB,
    SYSTEM_LEASE,
)
from wfc_shared.schemas.domain_event import DomainEvent
from wfc_shared.schemas.events import FireEvent
from wfc_shared.schemas.telemetry import FireIntensityUpdate, SwarmStatusSnapshot

# prefix used to recognize ANY per-node registry-announce
# topic (wfc/registry/announce/{node_id}) - announcements moved off
# a single shared topic to avoid topic collision.
_REGISTRY_ANNOUNCE_PREFIX = REGISTRY_ANNOUNCE_TARGET.split("{node_id}")[0]

EXPIRE_TICK_SECONDS: Final = 5.0
COMMAND_ACK_TIMEOUT_SECONDS = 30.0  # max wait for a field node ACK
STATE_SNAPSHOT_SECONDS: Final = 10.0
LEASE_RENEW_SECONDS: Final = 5.0
# How long since the last lease renewal before Backup considers the
# lease itself stale (not just "I missed one heartbeat"). Combined
# with the heartbeat monitor's own 10s timeout as a second, independent
# signal - see _check_lease_and_maybe_promote().
LEASE_TTL_SECONDS: Final = 15.0

STATE_SNAPSHOT_TOPIC = STATE_SNAPSHOT

# 30s gives the loop guaranteed time to publish at least two more
# snapshots after deactivate() before the grace expires, ensuring
# the recovering primary absorbs all state changes made during the
# outage window. (Was 10s - equal to STATE_SNAPSHOT_SECONDS - which
# made the grace publish unreachable.)
GRACE_PERIOD_SECONDS: Final = 30.0


# region  CLASS - CommanderCore


class CommanderCore:
    """All commander-specific logic, decoupled from BaseNode.

    CentralNode and BackupCommander each own one instance.
    CentralNode starts it active=True.
    BackupCommander starts it active=False and calls activate()
    when the primary fails.
    """

    # region  INITIALISATION

    def __init__(self, node_id: str, mqtt: MQTTClient, registry: NodeRegistry, db: Database) -> None:
        """
        Parameters
        ----------
        node_id  : identity of the owning node (for logging / MQTT payloads)
        mqtt     : MQTTClient - already connected by the owning BaseNode
        registry : NodeRegistry - shared with the owning BaseNode
        db       : Database - shared with the owning BaseNode
        """
        self._node_id = node_id
        self._mqtt = mqtt
        self._registry = registry
        self._db = db
        self._active = False

        # state stores
        self.fires = FireStateStore(db=self._db)
        self.missions = MissionStore(db=db)
        self.alerts = AlertRepository(db=self._db)

        # sensor fire event log - records raw FIRE_DETECTED/
        #    CONTAINED/SUPPRESSED from field sensors, separate from the
        # commander domain event log which records decisions
        self._fire_events = FireEventRepository(db=self._db)

        # domain event log (hybrid event sourcing)
        self.event_log = DomainEventLog(db=self._db)

        # command pipeline
        self.tracker = CommandTracker(db=self._db)
        self.dispatcher = CommandDispatcher(mqtt=self._mqtt, tracker=self.tracker)

        # approval pipeline
        self._store = PendingCommandStore(
            self.dispatcher,
            mqtt=self._mqtt,
            node_id=self._node_id,
            db=self._db,
            event_log=self.event_log,
        )
        self._gate = ApprovalGate(self.dispatcher, self._store)
        self._handler = ApprovalHandler(self._store)

        # rule engine
        self.rule_engine = RuleEngine(
            registry=self._registry,
            gate=self._gate,
            fires=self.fires,
            alerts=self.alerts,
            event_log=self.event_log,
        )

        # registry bridge (wired on start)
        self._registry_bridge: RegistryBridge | None = None

        # leadership lease
        # term we currently hold/believe is current; None until we've
        # either issued one ourselves or observed one on the wire.
        self._lease_term: int = 0
        self._lease_owner: str | None = None
        self._lease_since: float = 0.0
        self._last_lease_seen_at: float = 0.0  # local clock, for TTL check

        # background loop control
        self._loops_running = False

        # grace period for snapshot publish
        self._deactivation_grace_start: float | None = None
        self._last_snapshot_at: float = 0.0  # tracks when we last published a snapshot

        # SWARM :
        self.swarm_status = SwarmStatusStore()
        self._last_election_metadata: dict[str, Any] | None = None

    # endregion

    # region  LIFECYCLE

    def start(self, active: bool = True) -> None:
        """
        Wire subsystems and launch background threads.

        Parameters
        ----------
        active : if True the core processes messages immediately (CentralNode).
                 if False it is in standby - it subscribes to the state
                 snapshot topic to stay current but does not run rules
                 (BackupCommander).

        Subscribes to SYSTEM_LEASE and, if starting as active, waits
        briefly for any retained lease to arrive before deciding whether
        to issue a fresh term-1 lease or RECLAIM a higher term left
        behind by a backup that took over while this node was down.

        APPROVAL_RESPONSE, SYSTEM_LEASE, ACK, and EVENTS_FIRE are
        subscribed at qos=1. STATE_SNAPSHOT stays at qos=0 deliberately
        - it's periodic and self-healing.
        """
        self._active = active

        # register NODE_BECAME_AVAILABLE callback on the registry
        # so domain events are written when SWARM_LEAD nodes become active.
        self._registry._on_node_available = self._on_node_became_available  # pyright: ignore[reportPrivateUsage]

        self._registry_bridge = RegistryBridge(self._mqtt, self._registry)
        self._registry_bridge.start()

        self._mqtt.subscribe(APPROVAL_RESPONSE, qos=1)
        self._mqtt.subscribe(SYSTEM_LEASE, qos=1)
        self._mqtt.subscribe(ACK, qos=1)
        self._mqtt.subscribe(EVENTS_FIRE, qos=1)

        # Telemetry loop subscriptions (using SUB constants with #)
        self._mqtt.subscribe(SWARM_STATUS_SUB, qos=0)
        self._mqtt.subscribe(FIRE_INTENSITY, qos=1)
        self._mqtt.subscribe(FIRE_REKINDLED_TOPIC, qos=1)
        self._mqtt.subscribe(FIRE_VERIFIED_TOPIC, qos=1)
        self._mqtt.subscribe(SWARM_ELECTION_SUB, qos=1)

        # Always subscribe to snapshots - backup uses them to mirror state
        self._mqtt.subscribe(STATE_SNAPSHOT_TOPIC)
        log("CommanderCore", f"subscribed to {STATE_SNAPSHOT_TOPIC} ({self._node_id})", channel="STATE")

        # replay recent domain events on startup to fill any
        # state gaps that the fire_states/missions DB snapshot may have missed.
        # This is particularly important for the backup after a failover: even if
        # the snapshot LWW rejected a FIRE_REDISPATCHED change (because updated_at
        # looked stale from a clock skew), the event log replay will still apply it.
        try:
            from core.state.projector import FireProjector

            projector = FireProjector(self.fires, self.missions)
            recent_events = self.event_log.get_recent(limit=500)
            projector.replay(recent_events)
        except Exception as exc:
            log("CommanderCore", f"startup event replay failed (non-fatal): {exc}", channel="SYSTEM")

        self._loops_running = True
        threading.Thread(target=self._expire_loop, daemon=True).start()

        if active:
            # Give the retained SYSTEM_LEASE message a moment to arrive
            # via handle_message() before we decide our term. Paho
            # delivers retained messages immediately after subscribe,
            # so this window only needs to cover normal network latency.
            time.sleep(1.0)
            self._claim_or_reclaim_lease()
            threading.Thread(target=self._snapshot_loop, daemon=True).start()
            threading.Thread(target=self._lease_renew_loop, daemon=True).start()
            log(
                "CommanderCore",
                f"snapshot publish loop launched, first publish in {STATE_SNAPSHOT_SECONDS:.0f}s ({self._node_id})",
                channel="STATE",
            )

        mode = "ACTIVE" if active else "STANDBY"
        log("CommanderCore", f"started in {mode} mode ({self._node_id})", channel="SYSTEM")

    def stop(self) -> None:
        """Stop background loops (MQTT is stopped by the owning BaseNode)."""
        self._loops_running = False
        log("CommanderCore", f"stopped ({self._node_id})", channel="SYSTEM")

    def activate(self) -> None:
        """
        Promote standby core to active commander.

        Called by BackupCommander._become_active() - which has already
        confirmed via _check_lease_and_maybe_promote() that this is a
        real takeover, not a momentary blip. Bumps the lease term and
        starts publishing it as the new owner, so Central sees an
        unambiguous "someone else took over while you were gone"
        signal when it comes back (see _claim_or_reclaim_lease()).
        """
        if self._active:
            return
        self._active = True
        self._lease_term = max(self._lease_term, 1) + 1
        self._lease_owner = self._node_id
        self._lease_since = time.time()
        self._publish_lease()
        threading.Thread(target=self._snapshot_loop, daemon=True).start()
        threading.Thread(target=self._lease_renew_loop, daemon=True).start()
        log(
            "CommanderCore",
            f"snapshot publish loop launched, first publish in {STATE_SNAPSHOT_SECONDS:.0f}s ({self._node_id})",
            channel="STATE",
        )
        log(
            "CommanderCore",
            f"activated - now commanding, lease term={self._lease_term} ({self._node_id})",
            channel="SYSTEM",
        )

    def deactivate(self) -> None:
        """
        Return to standby (primary came back, or we lost a reclaim race).

        The snapshot loop and lease renew loop both check _active on
        every iteration and exit on their own; we don't need to track
        or interrupt the threads directly. We restart them on the next
        activate() call if needed.
        Instead of stopping the snapshot loop instantly,
        mark the start of a grace period so that we continue publishing
        snapshots for a short time to allow any recovering primary to catch up.
        """
        # Publish final snapshot
        self._publish_snapshot_now()
        self._active = False
        self._deactivation_grace_start = time.time()
        log("CommanderCore", f"deactivated - returning to standby ({self._node_id})", channel="SYSTEM")

    # endregion

    # region  LEADERSHIP LEASE
    # KNOWN LIMITATION (stated honestly, not hidden):
    # This lease lives entirely in MQTT retained messages on the one
    # shared broker both nodes connect to. It debounces a momentary
    # heartbeat miss and gives Central an explicit "you were replaced"
    # signal to react to on recovery. It is NOT a partition-tolerant
    # fencing token in the distributed-systems sense (no quorum, no
    # consensus) - if the broker itself partitions Central from Backup
    # while both can still process traffic from field nodes on their
    # own side, both could still believe they hold the lease. With a
    # single broker (your current topology) that specific failure mode
    # doesn't apply; if a second broker or any broker clustering is
    # introduced later, this needs revisiting.

    def _publish_lease(self) -> None:
        # qos=1 - this retained message IS the entire fencing
        # mechanism. Losing it in transit doesn't just delay
        # delivery, it means a node checking leadership could see no
        # lease at all and make the wrong call about whether to
        # reclaim or promote.
        self._mqtt.publish_retained(
            SYSTEM_LEASE,
            {
                "owner": self._lease_owner,
                "term": self._lease_term,
                "since": self._lease_since,
            },
            qos=1,
        )

    def _on_lease_message(self, payload: dict[str, Any]) -> None:
        """Record the latest lease state observed on the wire."""
        term = payload.get("term")
        owner = payload.get("owner")
        since = payload.get("since")
        if not isinstance(term, int):
            return
        self._last_lease_seen_at = time.time()
        if term >= self._lease_term:
            self._lease_term = term
            self._lease_owner = owner
            self._lease_since = since or time.time()

    def _claim_or_reclaim_lease(self) -> None:
        """
        Called once by an ACTIVATING node (CentralNode.start(), after
        the brief wait for any retained lease to arrive).

        If no lease was ever seen: this is a fresh system, issue term 1.
        If a lease WAS seen and we already own the highest term: nothing
        to do, just keep renewing (handles a simple process restart of
        Central where Backup never took over).
        If a lease was seen with a DIFFERENT owner: someone else (the
        backup) took over while we were gone. We reclaim - per the
        stated rule "central always wins if both are up" - by bumping
        the term again and republishing ourselves as owner. This is a
        visible, logged event, not a silent resume.
        """
        if self._lease_owner is None:
            self._lease_term = 1
            self._lease_owner = self._node_id
            self._lease_since = time.time()
            self._publish_lease()
            log("CommanderCore", f"no prior lease found - issuing term=1 ({self._node_id})", channel="SYSTEM")
            return

        if self._lease_owner == self._node_id:
            # We already hold it (e.g. quick restart, lease still ours).
            self._publish_lease()
            return

        # Someone else held it - reclaim.
        old_owner, old_term = self._lease_owner, self._lease_term
        self._lease_term = old_term + 1
        self._lease_owner = self._node_id
        self._lease_since = time.time()
        self._publish_lease()
        log(
            "CommanderCore",
            f"RECLAIMING leadership from {old_owner} (was term={old_term}) "
            f"- new term={self._lease_term} ({self._node_id})",
            channel="SYSTEM",
        )

    def _check_lease_and_maybe_promote(self) -> bool:
        """
        Called by BackupCommander after the heartbeat monitor declares
        the primary dead. Returns True if the lease ALSO looks stale
        (no renewal seen within LEASE_TTL_SECONDS), which is the
        second, independent signal required before promoting.

        This exists specifically to avoid promoting on a single missed
        heartbeat cycle that might just be a brief hiccup - see the
        KNOWN LIMITATION note above for what this does and doesn't
        protect against.
        """
        if self._last_lease_seen_at == 0.0:
            # We've never seen a lease at all (e.g. started after
            # Central was already down) - don't block promotion on it.
            return True
        stale_for = time.time() - self._last_lease_seen_at
        return stale_for >= LEASE_TTL_SECONDS

    def _lease_renew_loop(self) -> None:
        """Active commander re-publishes the lease periodically so a
        late-joining or recovering node always finds a fresh `since`.
        """
        while self._loops_running and self._active:
            time.sleep(LEASE_RENEW_SECONDS)
            if not self._active:
                break
            self._lease_since = time.time()
            try:
                self._publish_lease()
            except Exception as exc:
                log("CommanderCore", f"lease renew error: {exc}", channel="SYSTEM")

    # endregion

    # region  MESSAGE HANDLING

    def handle_message(self, topic: str, payload: Any) -> None:
        """
        Route an MQTT message.

        Order of precedence (FIRST MATCH WINS):
        1. Registry announces (always, even in standby)
        2. System lease & State snapshots (always, even in standby)
        3. Exact-match operational topics (Approval, ACK, Intensity, Rekindle)
        4. Specific prefix topics (Election, Swarm Status)
        5. Generic fallback (Fire Events, General events)
        """
        try:
            if not isinstance(payload, dict):
                return

            # 1. ALWAYS PROCESS (even in standby)

            # Registry sync (per-node announce topics)
            if topic.startswith(_REGISTRY_ANNOUNCE_PREFIX):
                if self._registry_bridge is not None:
                    try:
                        self._registry_bridge.on_message(topic, payload)  # pyright: ignore[reportUnknownArgumentType]
                    except Exception as e:
                        log("CommanderCore", f"RegistryBridge error: {e}", channel="ERROR")
                else:
                    log("CommanderCore", "RegistryBridge not ready - ignoring announce", channel="WARN")
                return

            # Leadership lease
            if topic == SYSTEM_LEASE:
                self._on_lease_message(payload)  # pyright: ignore[reportUnknownArgumentType]
                return

            # State snapshot sync
            if topic == STATE_SNAPSHOT_TOPIC:
                log("CommanderCore", f"snapshot received ({self._node_id}, active={self._active})", channel="STATE")
                self._apply_snapshot(payload)  # pyright: ignore[reportUnknownArgumentType]
                return

            # 2. STANDBY GUARD (operational traffic only)
            if not self._active:
                return

            # 3. EXACT MATCH HANDLERS (Highest priority for specific topics)
            exact_handlers = {
                APPROVAL_RESPONSE: self._handler.handle,
                ACK: self._handle_ack,
                FIRE_INTENSITY: self._handle_fire_intensity,
                FIRE_REKINDLED_TOPIC: self._handle_fire_rekindled,
            }

            handler = exact_handlers.get(topic)
            if handler:
                # Special case: approval handler needs try/except
                if topic == APPROVAL_RESPONSE:
                    try:
                        handler(payload)  # pyright: ignore[reportUnknownArgumentType]
                    except Exception as exc:
                        log("CommanderCore", f"approval handler error: {exc}", channel="APPROVAL")
                else:
                    handler(payload)  # pyright: ignore[reportUnknownArgumentType]
                return

            # 4. PREFIX MATCH HANDLERS (Specific prefixes before generic)

            # Bully election results (wfc/swarm/election/#)
            if topic.startswith(SWARM_ELECTION_PREFIX):
                self._handle_swarm_election(payload)  # pyright: ignore[reportUnknownArgumentType]
                return

            # Swarm status snapshots (wfc/swarm/status/#)
            if topic.startswith(SWARM_STATUS_PREFIX):
                self._handle_swarm_status(payload)  # pyright: ignore[reportUnknownArgumentType]
                return

            # 5. GENERIC FALLBACK HANDLERS

            # Fire events (wfc/events/fire) - catches DETECTED/CONTAINED/SUPPRESSED
            if topic.startswith(EVENTS_FIRE):
                try:
                    event = FireEvent(**payload)  # pyright: ignore[reportUnknownArgumentType]
                    self._handle_fire_event(event)
                except Exception as exc:
                    log("CommanderCore", f"fire event error: {exc}", channel="RULES")
                return

            # Any other event topics (verbose logging)
            if "events" in topic:
                log("CommanderCore", f"event: {payload}", channel="BUS", level="VERBOSE")
                return

            # Unknown topic (ignore silently)
            log("CommanderCore", f"unhandled topic: {topic} (active={self._active})", channel="BUS", level="VERBOSE")

        except Exception as e:
            log("CommanderCore", f"Unhandled error in handle_message: {e} (topic={topic})", channel="ERROR")
            import traceback

            log("CommanderCore", traceback.format_exc(), channel="ERROR")

    # endregion

    # region  FIRE EVENT HANDLER
    def _on_node_became_available(self, node_id: str, capabilities: list[str]) -> None:
        """Uses self.fires.get_active() instead of internal dict."""
        log(
            "CommanderCore", f"NODE_BECAME_AVAILABLE callback fired for {node_id} caps={capabilities}", channel="SYSTEM"
        )
        from wfc_shared.enums.capabilities import SWARM_LEAD

        if SWARM_LEAD not in (capabilities or []):
            return

        self.event_log.append(
            DomainEvent(
                event_type=NODE_BECAME_AVAILABLE,  # pyright: ignore[reportArgumentType]
                node_id=node_id,
                reason="node_became_active",
                payload={"capabilities": capabilities},
            )
        )

        log("CommanderCore", f"Re-evaluating active unassigned fires after {node_id} became available", channel="RULES")

        # use get_active() instead of _fires.items()
        active_unassigned = [
            fire
            for fire in self.fires.get_active()
            if not fire.assigned_nodes  # empty list = unassigned
        ]

        if not active_unassigned:
            log("CommanderCore", "No active unassigned fires to re-evaluate", channel="RULES")
            return

        for fire in active_unassigned:
            try:
                context = self._build_context(  # pyright: ignore[reportUnknownMemberType]
                    trigger=EvalTrigger.NODE_AVAILABLE, telemetry_rules=["fire_dispatch", "no_responders"]
                )
                self.rule_engine.evaluate(fire, context)
            except Exception as e:
                log("CommanderCore", f"Error evaluating fire {fire.fire_id}: {e}", channel="ERROR")

    # endregion

    def _handle_fire_event(self, event: FireEvent) -> None:
        """
        State-driven fire handling.
        State is updated FIRST; rules evaluate against FireRecord.

        Passes RuleContext with appropriate trigger so rules like
        PriorityRule can distinguish NEW_FIRE from other evaluations.
        """
        p = event.payload

        if event.event_type == FIRE_DETECTED:
            # persist raw sensor event to fire_events table
            self._fire_events.add(event)

            self.fires.ignite(
                fire_id=p.fire_id,
                zone=p.zone,
                severity=p.severity,
                sensor_id=p.sensor_id,
                location_coords=p.location_coords,
            )
            self.fires.transition(p.fire_id, ACTIVE, reason="response_dispatching")
            mission = self.missions.create(p.fire_id)

            # write FIRE_DETECTED domain event for audit trail
            self.event_log.append(
                DomainEvent(
                    event_type=DE_FIRE_DETECTED,  # pyright: ignore[reportArgumentType]
                    fire_id=p.fire_id,
                    payload={"zone": p.zone, "severity": p.severity, "sensor_id": p.sensor_id},
                )
            )

            fire = self.fires.get(p.fire_id)

            # Pass context with NEW_FIRE trigger
            context = self._build_context(trigger=EvalTrigger.NEW_FIRE)  # pyright: ignore[reportUnknownMemberType]
            self.rule_engine.evaluate(fire, context)  # pyright: ignore[reportArgumentType]

            # use get_available() not get_by_capability().
            if self._registry.get_available(SWARM_LEAD):
                self.missions.transition(mission.mission_id, ASSIGNED, reason="swarm_leader_dispatched")

        elif event.event_type == FIRE_CONTAINED:
            fire = self.fires.get(p.fire_id)
            if fire is None:
                log("CommanderCore", f"FIRE_CONTAINED for unknown fire {p.fire_id[:8]} - ignoring", channel="STATE")
                return
            # persist raw sensor event
            self._fire_events.add(event)
            self.fires.transition(p.fire_id, CONTAINED, reason="contained_event")
            # write FIRE_CONTAINED domain event
            self.event_log.append(
                DomainEvent(
                    event_type=DE_FIRE_CONTAINED,  # pyright: ignore[reportArgumentType]
                    fire_id=p.fire_id,
                )
            )
            fire = self.fires.get(p.fire_id)
            # Use MANUAL trigger for state-change evaluations
            context = self._build_context(trigger=EvalTrigger.MANUAL)  # pyright: ignore[reportUnknownMemberType]
            self.rule_engine.evaluate(fire, context)  # pyright: ignore[reportArgumentType]

        elif event.event_type == FIRE_SUPPRESSED:
            fire = self.fires.get(p.fire_id)
            if fire is None:
                log("CommanderCore", f"FIRE_SUPPRESSED for unknown fire {p.fire_id[:8]} - ignoring", channel="STATE")
                return
            self._fire_events.add(event)
            # release the assigned node's job IMMEDIATELY on FIRE_SUPPRESSED
            if fire.assigned_node:
                self._registry.release_job(fire.assigned_node)
            self.fires.transition(p.fire_id, SUPPRESSED, reason="suppressed_event")
            # write FIRE_SUPPRESSED domain event
            self.event_log.append(
                DomainEvent(
                    event_type=DE_FIRE_SUPPRESSED,  # pyright: ignore[reportArgumentType]
                    fire_id=p.fire_id,
                )
            )
            fire = self.fires.get(p.fire_id)
            context = self._build_context(trigger=EvalTrigger.MANUAL)  # pyright: ignore[reportUnknownMemberType]
            self.rule_engine.evaluate(fire, context)  # pyright: ignore[reportArgumentType]

            mission = self.missions.get_for_fire(p.fire_id)
            if mission:
                self.missions.transition(mission.mission_id, COMPLETED, reason="fire_suppressed")

    # endregion
    # region  TELEMETRY LOOP HANDLERS

    def _build_context(
        self,
        trigger: EvalTrigger,
        election_meta: dict[str, Any] | None = None,
        telemetry_rules: list[str] | None = None,
    ) -> RuleContext:
        return RuleContext(
            trigger=trigger,
            event_log=self.event_log,
            swarm_snapshots=self.swarm_status.get_all_active_dict(),
            election_metadata=election_meta,  # pyright: ignore[reportUnknownArgumentType]
            telemetry_rules=telemetry_rules,
            fires_store=self.fires,  # needed by PriorityRule (SCN-25)
        )

    def _handle_swarm_status(self, payload: dict[str, Any]) -> None:
        """Receives SwarmStatusSnapshot from a leader every 10s."""
        log("CommanderCore", f"✅✅✅ SWARM STATUS RECEIVED: {payload.get('leader_id')}", channel="STATE")
        try:
            snap = SwarmStatusSnapshot(**payload)
        except Exception as e:
            log("CommanderCore", f"invalid swarm status payload: {e}", channel="STATE")
            return

        self.swarm_status.update(snap)

        if not snap.fire_id:
            return  # leader is idle

        fire = self.fires.get(snap.fire_id)
        if fire is None:
            return

        # Re-evaluate only telemetry-sensitive rules
        context = self._build_context(  # pyright: ignore[reportUnknownMemberType]
            trigger=EvalTrigger.TELEMETRY_UPDATE,
            telemetry_rules=[
                "swarm_attrition",
                "resource_exhaustion",
                "fire_expansion",
                "containment_failure",
            ],
        )
        self.rule_engine.evaluate(fire, context)

    def _handle_fire_intensity(self, payload: dict[str, Any]) -> None:
        """Handles FIRE_INTENSITY_UPDATE from scout/leader."""
        try:
            update = FireIntensityUpdate(**payload)
        except Exception as e:
            log("CommanderCore", f"invalid intensity payload: {e}", channel="STATE")
            return

        fire = self.fires.get(update.fire_id)
        if fire is None:
            log("CommanderCore", f"intensity update for unknown fire {update.fire_id[:8]}", channel="STATE")
            return

        if update.new_intensity == fire.severity:
            return

        # Update severity via store
        self.fires.transition(
            update.fire_id,
            fire.state,  # keep state unchanged
            reason=f"intensity_update_to_{update.new_intensity}_from_{update.leader_id}",
        )
        # Force severity update via direct field (transition doesn't change severity)
        # Since FireRecord is frozen, we need to use the repo or a direct update.
        # For simplicity, we use assign_node trick? Better: add update_severity method.
        # Quick fix: apply_snapshot_record style update.
        rec = self.fires.get(update.fire_id)
        if rec:
            self.fires.update_severity(update.fire_id, update.new_intensity, "intensity_update")

        fire = self.fires.get(update.fire_id)
        if fire:
            context = self._build_context(  # pyright: ignore[reportUnknownMemberType]
                trigger=EvalTrigger.INTENSITY_UPDATE,
                telemetry_rules=["severity_increase", "high_severity", "fire_expansion"],
            )
            # Attach intensity payload to context (the rule reads it)
            context._intensity_payload = update.model_dump()  # pyright: ignore[reportAttributeAccessIssue]
            self.rule_engine.evaluate(fire, context)

    def _handle_fire_rekindled(self, payload: dict[str, Any]) -> None:
        """Handles FIRE_REKINDLED event from scout."""
        fire_id = payload.get("fire_id")
        if not fire_id:
            return
        fire = self.fires.get(fire_id)
        if fire is None:
            log("CommanderCore", f"rekindle for unknown fire {fire_id[:8]}", channel="STATE")
            return

        if fire.state not in ("SUPPRESSED", "EXTINGUISHED"):
            log("CommanderCore", f"rekindle ignored: fire {fire_id[:8]} is {fire.state}", channel="STATE")
            return

        # Transition back to ACTIVE
        self.fires.transition(fire_id, "ACTIVE", reason="rekindled_detected")
        self.event_log.append(
            DomainEvent(
                event_type="FIRE_REKINDLED",  # pyright: ignore[reportArgumentType]
                fire_id=fire_id,
                reason="scout_detected_hotspot",
                payload=payload,
            )
        )

        # Re-evaluate to trigger dispatch
        fire = self.fires.get(fire_id)
        if fire:
            context = self._build_context(  # pyright: ignore[reportUnknownMemberType]
                trigger=EvalTrigger.REKINDLED, telemetry_rules=["fire_dispatch"]
            )
            context._rekindled_payload = payload  # pyright: ignore[reportAttributeAccessIssue]
            self.rule_engine.evaluate(fire, context)

    def _handle_swarm_election(self, payload: dict[str, Any]) -> None:
        """Handles SWARM_ELECTION event from bully algorithm winner."""
        fire_id = payload.get("fire_id")
        new_leader = payload.get("new_leader_id")
        old_leader = payload.get("old_leader_id")
        term = payload.get("term", 0)
        election_type = payload.get("election_type", "BULLY")

        if not (fire_id and new_leader):
            log("CommanderCore", "election payload missing fire_id/new_leader", channel="SYSTEM")
            return

        fire = self.fires.get(fire_id)
        if fire is None:
            log("CommanderCore", f"election for unknown fire {fire_id[:8]}", channel="SYSTEM")
            return

        # Term guard: reject stale elections
        if term <= fire.leader_term:
            log(
                "CommanderCore",
                f"election term {term} <= current {fire.leader_term} - rejecting stale",
                channel="SYSTEM",
            )
            return

        # Verify the new leader is actually capable
        rec = self._registry.get(new_leader)
        if rec is None or "SWARM_LEAD" not in (rec.capabilities or []):
            log("CommanderCore", f"elected {new_leader} is not SWARM_LEAD capable", channel="SYSTEM")
            return

        # Accept the election - update assigned nodes now, but defer leader_term update
        # until AFTER ElectedLeaderRule fires. ElectedLeaderRule checks:
        # meta["term"] <= fire.leader_term stale
        # If we set leader_term = term here first, the rule rejects its own election.
        self.fires.add_assigned_node(fire_id, new_leader, reason=f"bully_election_term_{term}")
        # Persist
        if self.fires._repo:  # pyright: ignore[reportPrivateUsage]
            self.fires._repo.upsert(self.fires.get(fire_id))  # pyright: ignore[reportArgumentType, reportPrivateUsage]

        # Write LEADER_REPLACED domain event
        self.event_log.append(
            DomainEvent(
                event_type=LEADER_REPLACED,  # pyright: ignore[reportArgumentType]
                fire_id=fire_id,
                node_id=new_leader,
                reason=f"bully_election_term_{term}",
                payload={"old_leader": old_leader, "term": term, "election_type": election_type},
            )
        )

        # Re-evaluate the fire to send CONFIRM_LEADERSHIP via ElectedLeaderRule
        # NOTE: leader_term is still the OLD value here - ElectedLeaderRule's guard
        # checks meta["term"] > fire.leader_term, which is correct at this point.
        fire = self.fires.get(fire_id)
        if fire:
            context = self._build_context(  # pyright: ignore[reportUnknownMemberType]
                trigger=EvalTrigger.ELECTION_RESULT,
                election_meta={
                    "fire_id": fire_id,
                    "new_leader_id": new_leader,
                    "old_leader_id": old_leader,
                    "term": term,
                },
                telemetry_rules=["elected_leader"],
            )
            self.rule_engine.evaluate(fire, context)

        # Now advance leader_term so future stale elections (lower term) are rejected
        current = self.fires.get(fire_id)
        if current is not None:
            self.fires.update_leader_term(fire_id, term, "election_accepted")

        log(
            "CommanderCore",
            f"election accepted: fire {fire_id[:8]} now led by {new_leader} term={term}",
            channel="SYSTEM",
        )

    # endregion

    # region  ACK HANDLER

    def _handle_ack(self, payload: dict[str, Any]) -> None:
        """Reads event_type/event_id from the payload sent by
        FieldNode._send_ack. Falls back to deriving them from `status`
        for any field node not yet upgraded, so a rolling deploy doesn't
        silently break ACKs in the meantime.

        tracker.update()'s return value now gates every downstream side
        effect (release_job, mission RUNNING transition, COMMAND_FAILED
        alert). Any rejection (unknown trace, dup event_id, bad
        transition, etc.) short-circuits before any of that runs.
        """
        trace_id = payload.get("trace_id")
        status = payload.get("status")
        node_id = payload.get("node_id")

        if not (trace_id and status):
            return

        event_type = payload.get("event_type") or STATUS_TO_EVENT_TYPE.get(status, status)
        event_id = payload.get("event_id") or f"{trace_id}:{status}"

        accepted = self.tracker.update(trace_id, event_type, {**payload, "event_id": event_id})  # pyright: ignore[reportArgumentType, reportUnknownMemberType]
        log(
            "CommanderCore",
            f"ACK trace={trace_id[:8]} status={status} from={node_id} accepted={accepted}",
            channel="TRACKER",
        )

        if not accepted:
            # Tracker rejected this event (unknown trace, dup, bad
            # transition, etc.) - do NOT act on it as if it were a
            # genuine state change. The rejection is already logged
            # by CommandTracker itself with the specific reason.
            return

        # Step 2 completion: write ACK domain events for audit trail
        # and future Step 5 sync. fire_id recovered from tracker record if
        # not in the ACK payload directly.
        _tracker_rec = self.tracker.get_all().get(trace_id, {})
        _fire_id = payload.get("fire_id") or _tracker_rec.get("command", {}).get("payload", {}).get("fire_id")
        _de_type_map = {
            "RECEIVED": COMMAND_ACK_RECEIVED,
            "EXECUTED": COMMAND_ACK_EXECUTED,
            "FAILED": COMMAND_ACK_FAILED,
            "COMMAND_FAILED": COMMAND_ACK_FAILED,
        }
        _de_type = _de_type_map.get(status)
        if _de_type:
            self.event_log.append(
                DomainEvent(
                    event_type=_de_type,  # pyright: ignore[reportArgumentType]
                    fire_id=_fire_id,
                    node_id=node_id,
                    reason=f"ack_{status.lower()}_trace_{trace_id[:8]}",
                    payload={"trace_id": trace_id, "status": status},
                )
            )

        # Only release job on command failure or explicit node offline.
        # EXECUTED does NOT free the node - it remains assigned to the fire
        # until the fire is resolved (SUPPRESSED/CONTAINED) or node dies.
        if status in ("FAILED", "COMMAND_FAILED") and node_id:
            self._registry.release_job(node_id)

        if status == "RECEIVED" and node_id:
            rec = self._registry.get(node_id)
            if rec and rec.current_job:
                mission = self.missions.get_for_fire(rec.current_job)
                if mission:
                    self.missions.transition(
                        mission.mission_id,
                        RUNNING,
                        reason=f"ack_received_from_{node_id}",
                        assigned_node=node_id,
                    )

        if status in ("FAILED", "COMMAND_FAILED"):
            self.alerts.add(
                alert_id=f"cmd_failed:{trace_id}",
                kind="COMMAND_FAILED",
                severity="WARNING",
                title=f"Command failed on {node_id}",
                detail=f"trace_id={trace_id}",
                source_ref=trace_id,
            )

    # endregion

    # region  REDISPATCH

    def redispatch_fire(self, fire_id: str, dead_leader: str) -> None:
        """Re-run rules against an existing fire after its leader dies.

        Clearing assigned_node goes through FireStateStore.assign_node()
        to bump updated_at, so the backup's LWW merge accepts the change.

        Write LEADER_DIED and FIRE_REDISPATCHED events BEFORE evaluating
        so the event log has a record even if evaluate() raises.
        After evaluate(), READ the event log to recover context: was
        a new leader dispatched? If so, transition mission PAUSEDASSIGNED
        instead of leaving it permanently stuck.
        """
        fire = self.fires.get(fire_id)
        if fire is None:
            return
            # Terminal fires should never be redispatched
        if fire.state in ("COMPLETED", "SUPPRESSED"):
            log("CommanderCore", f"fire {fire_id[:8]} is {fire.state} - skipping redispatch", channel="SYSTEM")
            return
        try:
            # Step 1: write LEADER_DIED event (audit trail)
            self.event_log.append(
                DomainEvent(
                    event_type=LEADER_DIED,  # pyright: ignore[reportArgumentType]
                    fire_id=fire_id,
                    node_id=dead_leader,
                    reason="heartbeat_timeout",
                )
            )

            # Step 2: write FIRE_REDISPATCHED event BEFORE evaluate() so
            # we can read it back as context after evaluate() returns.
            self.event_log.append(
                DomainEvent(
                    event_type=FIRE_REDISPATCHED,  # pyright: ignore[reportArgumentType]
                    fire_id=fire_id,
                    node_id=dead_leader,
                    reason=f"leader_{dead_leader}_offline",
                )
            )

            # Step 3: clear assignment through FireStateStore - bumps
            # updated_at so the backup's LWW merge accepts the change.
            self.fires.assign_node(fire_id, None, reason=f"leader_{dead_leader}_offline")

            # Step 4: evaluate rules against updated state
            self.rule_engine.evaluate(self.fires.get(fire_id))  # pyright: ignore[reportArgumentType]

            # Step 4b: ContainmentFailureRule may have just re-activated the fire
            # (CONTAINED ACTIVE, assigned_nodes cleared). FireDispatchRule ran
            # BEFORE ContainmentFailureRule in the same pass and saw CONTAINED state,
            # so it skipped dispatch. Re-evaluate now that the fire is ACTIVE.
            fire_mid = self.fires.get(fire_id)
            if fire_mid and fire_mid.state == ACTIVE and not fire_mid.assigned_nodes:
                log(
                    "CommanderCore",
                    f"fire {fire_id[:8]} re-activated by ContainmentFailureRule - running second dispatch pass",
                    channel="SYSTEM",
                )
                context = self._build_context(trigger=EvalTrigger.REDISPATCH)  # pyright: ignore[reportUnknownMemberType]
                self.rule_engine.evaluate(fire_mid, context)

            # Step 5: read back the fire state after evaluate() to see
            # if a new leader was dispatched. Context is recovered from
            # the event log: the last FIRE_DISPATCHED event (if any) tells
            # us who was picked. Use that to transition the mission from
            # PAUSED back to ASSIGNED.
            fire_after = self.fires.get(fire_id)
            if fire_after and fire_after.assigned_node:
                mission = self.missions.get_for_fire(fire_id)
                if mission:
                    self.missions.transition(
                        mission.mission_id,
                        ASSIGNED,
                        reason=f"redispatched_to_{fire_after.assigned_node}",
                        assigned_node=fire_after.assigned_node,
                    )
                    log(
                        "CommanderCore",
                        f"mission {mission.mission_id[:8]} → ASSIGNED after redispatch to {fire_after.assigned_node}",
                        channel="SYSTEM",
                    )

            log(
                "CommanderCore",
                f"re-dispatched fire={fire_id[:8]} after leader {dead_leader} offline",
                channel="SYSTEM",
            )
        except Exception as exc:
            log("CommanderCore", f"re-dispatch failed for fire={fire_id[:8]}: {exc}", channel="SYSTEM")

    # endregion

    # region  STATE SYNC (Immediately publish)

    def _publish_snapshot_now(self) -> None:
        """Immediately publish a full snapshot, regardless of _active flag.

        Snapshot payload now includes a domain events delta - all events
        written since the previous snapshot publish. The backup replays
        these in _apply_snapshot() to stay in sync even if a fire
        decision happened between two snapshot intervals.
        """
        fires_payload = self.fires.snapshot_all()
        missions_payload = self.missions.snapshot_all()
        events_delta = [e.model_dump() for e in self.event_log.get_since(self._last_snapshot_at)]
        now = time.time()
        self._mqtt.publish(
            STATE_SNAPSHOT_TOPIC,
            {
                "timestamp": now,
                "fires": fires_payload,
                "missions": missions_payload,
                "nodes": {
                    nid: {"status": rec.status, "current_job": rec.current_job}
                    for nid, rec in self._registry.get_all().items()
                },
                "events_delta": events_delta,
            },
        )
        self._last_snapshot_at = now
        log(
            "CommanderCore",
            f"final snapshot published: {len(fires_payload)} fire(s), "
            f"{len(missions_payload)} mission(s), "
            f"{len(events_delta)} event(s) ({self._node_id})",
            channel="STATE",
        )

    # endregion

    # region  STATE SYNC (standby mirror)

    def _apply_snapshot(self, payload: dict[str, Any]) -> None:
        """Merge a wfc/state/snapshot payload into local state stores.

        Runs in BOTH standby and active mode. Only fires and missions
        are mirrored here; NodeRegistry is already kept in sync by
        RegistryBridge (which both nodes run).

        fires_snap/missions_snap are LISTS (as produced by
        FireStateStore.snapshot_all() / MissionStore.snapshot_all()),
        not dicts.

        Now safe to run unconditionally because
        FireStateStore/MissionStore.apply_snapshot_record() do their
        own last-write-wins merge by `updated_at` - a node's own
        broadcast can never clobber data it already has that's equal
        or newer.

        Also processes the events_delta carried in the snapshot payload.
        Each event is appended to the local event log
        (INSERT OR IGNORE deduplicates by event_id) and replayed through
        FireProjector to fill any state gaps the LWW merge missed.
        """
        try:
            fires_snap = payload.get("fires", [])
            missions_snap = payload.get("missions", [])
            events_delta = payload.get("events_delta", [])

            fires_applied = 0
            for fire_data in fires_snap:
                fire_id = fire_data.get("fire_id")
                if fire_id and self.fires.apply_snapshot_record(fire_id, fire_data):
                    fires_applied += 1

            missions_applied = 0
            for mission_data in missions_snap:
                mission_id = mission_data.get("mission_id")
                if mission_id and self.missions.apply_snapshot_record(mission_id, mission_data):
                    missions_applied += 1

            # Step 5: replay incoming events
            events_applied = 0
            if events_delta:
                from core.state.projector import FireProjector

                projector = FireProjector(self.fires, self.missions)
                for raw in events_delta:
                    try:
                        event = DomainEvent(**raw)
                        stored = self.event_log.append(event)
                        # Only replay if this was a NEW event (not a dup we
                        # already had - INSERT OR IGNORE returns sequence=None
                        # for existing rows via DomainEventRepository.insert())
                        if stored.sequence is not None:
                            projector.apply(event)
                            events_applied += 1
                    except Exception as e_exc:
                        log(
                            "CommanderCore",
                            f"events_delta replay error for event {raw.get('event_id', '?')[:8]}: {e_exc}",
                            channel="STATE",
                        )

            log(
                "CommanderCore",
                f"snapshot merged: {fires_applied}/{len(fires_snap)} fire(s), "
                f"{missions_applied}/{len(missions_snap)} mission(s), "
                f"{events_applied}/{len(events_delta)} event(s) applied "
                f"({self._node_id})",
                channel="STATE",
            )

        except Exception as exc:
            log("CommanderCore", f"snapshot apply error: {exc} ({self._node_id})", channel="STATE")

    # endregion

    # region  BACKGROUND LOOPS

    def _expire_loop(self) -> None:
        while self._loops_running:
            time.sleep(EXPIRE_TICK_SECONDS)
            if not self._active:
                continue
            with contextlib.suppress(Exception):
                self._store.expire_stale()
            # evict old terminal missions so _missions
            # dict doesn't grow unboundedly over long runs.
            try:
                evicted = self.missions.evict_terminal(max_age_seconds=3600.0)
                if evicted:
                    log("CommanderCore", f"evicted {evicted} terminal mission(s) from memory", channel="STATE")
            except Exception:
                pass
            # check for commands that never received an ACK.
            with contextlib.suppress(Exception):
                self._check_command_timeouts()

    def _check_command_timeouts(self) -> None:
        """Detect commands stuck in ISSUED with no ACK.

        A lost ACK permanently locks the target node's current_job -
        no further fires can be dispatched to it. This loop runs every
        EXPIRE_TICK_SECONDS and treats any ISSUED command older than
        COMMAND_ACK_TIMEOUT_SECONDS as failed: releases the node's job
        and redispatches the fire so another leader can take over.

        Using CommandTracker.get_issued() avoids scanning all commands -
        only ISSUED ones are checked.
        """
        now = time.time()
        for trace_id, record in self.tracker.get_issued():
            cmd = record.get("command", {})
            issued_hist = record.get("history", [])
            # issued timestamp is on the first history entry
            issued_at = issued_hist[0].get("timestamp", now) if issued_hist else now
            if now - issued_at < COMMAND_ACK_TIMEOUT_SECONDS:
                continue

            node_id = cmd.get("target_node")
            fire_id = cmd.get("payload", {}).get("fire_id")
            if fire_id:
                fire = self.fires.get(fire_id)
                if fire and fire.state in ("SUPPRESSED", "COMPLETED"):
                    # No need to timeout; just mark the command as obsolete and clean up
                    self.tracker.update(trace_id, "COMMAND_ACK_FAILED", {"reason": "fire_terminal"})  # pyright: ignore[reportUnknownMemberType]
                    if node_id:
                        self._registry.release_job(node_id)
                    continue
            log(
                "CommanderCore",
                f"ACK timeout trace={trace_id[:8]} node={node_id} fire={str(fire_id)[:8] if fire_id else 'none'}"
                f" - treating as failed, releasing node",
                channel="TRACKER",
            )

            # Mark the command failed in the tracker so it stops appearing
            self.tracker.update(
                trace_id,
                "COMMAND_ACK_FAILED",  # pyright: ignore[reportUnknownMemberType]
                {"reason": "ack_timeout"},
            )

            if node_id:
                self._registry.release_job(node_id)

            if fire_id:
                self.redispatch_fire(fire_id, dead_leader=node_id or "timeout")

    def _snapshot_loop(self) -> None:
        """Publish full system state snapshot every STATE_SNAPSHOT_SECONDS.

        Only runs when _active is True. Exits when _loops_running is False
        or _active becomes False (standby after primary recovery).

        Each snapshot now carries an events_delta - the domain events
        written since the previous publish. The backup replays them on
        receipt via _apply_snapshot().
        """
        while self._loops_running:
            in_grace = (
                self._deactivation_grace_start is not None
                and time.time() - self._deactivation_grace_start < GRACE_PERIOD_SECONDS
            )

            if not self._active and not in_grace:
                break

            time.sleep(STATE_SNAPSHOT_SECONDS)

            if not self._active and not in_grace:
                break
            try:
                fires_payload = self.fires.snapshot_all()
                missions_payload = self.missions.snapshot_all()
                events_delta = [e.model_dump() for e in self.event_log.get_since(self._last_snapshot_at)]
                now = time.time()
                self._mqtt.publish(
                    STATE_SNAPSHOT_TOPIC,
                    {
                        "timestamp": now,
                        "fires": fires_payload,
                        "missions": missions_payload,
                        "nodes": {
                            nid: {"status": rec.status, "current_job": rec.current_job}
                            for nid, rec in self._registry.get_all().items()
                        },
                        "events_delta": events_delta,
                    },
                )
                self._last_snapshot_at = now
                log(
                    "CommanderCore",
                    f"snapshot published: {len(fires_payload)} fire(s), "
                    f"{len(missions_payload)} mission(s), "
                    f"{len(events_delta)} event(s) ({self._node_id})",
                    channel="STATE",
                )
            except Exception as exc:
                log("CommanderCore", f"snapshot loop error: {exc}", channel="STATE")

    # endregion


# endregion (end of class CommanderCore)
