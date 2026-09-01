"""backup_commander.py
BackupCommander - standby failover node
- Owns a CommanderCore, started in standby (active=False)
- CommanderCore mirrors fire/mission state via snapshots
while in standby - zero warm-up lag on promotion
- _become_active() calls core.activate() full C2 online
- _stand_down() calls core.deactivate() back to standby
- handle_message() always delegates to core (core decides
what to act on based on its _active flag)
Failover sequence:
1. HeartbeatMonitor calls _on_node_failed(primary)
2. _become_active() core.activate() + SYSTEM_FAILOVER
3. BackupCommander is now a fully functional commander
Stand-down sequence:
1. HeartbeatMonitor calls _on_node_recovered(primary)
2. _stand_down() core.deactivate()
3. BackupCommander returns to standby monitoring
"""

from __future__ import annotations

import threading
import time
from typing import Any, Final

from command_nodes.core_commander.commander_core import CommanderCore
from core.node.base_node import BaseNode
from core.persistence.repositories.alert_repo import AlertRepository
from core.utils.config import get_node_location, get_node_zone
from core.utils.logger import log
from wfc_shared.enums.capabilities import (  # pyright: ignore[reportUnusedImport]
    DISPATCH_COMMANDS,
    HEARTBEAT,
    SWARM_LEAD,
)
from wfc_shared.enums.mission_status import PAUSED
from wfc_shared.enums.topics import SYSTEM_FAILOVER

PRIMARY_NODE_TYPE: Final = "CENTRAL_COMMANDER"

# bounded wait for the lease to confirm staleness before
# promoting. Checked in short steps rather than one long sleep so a
# lease renewal that arrives mid-wait (primary wasn't really dead,
# just slow) is picked up immediately via CommanderCore's own
# _on_lease_message() updating _last_lease_seen_at in the background.
LEASE_CONFIRM_POLL_SECONDS: Final = 1.0
LEASE_CONFIRM_MAX_WAIT_SECONDS: Final = 10.0

# region  CLASS - BackupCommander


class BackupCommander(BaseNode):
    """
    Standby failover node.

    Owns a CommanderCore that is pre-warmed in standby mode.
    On primary failure: activates the core and becomes a full commander.
    On primary recovery: deactivates the core and returns to standby.
    """

    # region  INITIALISATION

    def __init__(self) -> None:
        super().__init__(
            node_id="backup-commander",
            node_type="BACKUP_COMMANDER",
            capabilities=[DISPATCH_COMMANDS, HEARTBEAT],
            zone=get_node_zone(),
            location=get_node_location(),
        )
        self._primary_alive = True
        # alerts for node-down events (mirrors CentralNode._node_alerts)
        self._node_alerts: AlertRepository | None = None

    # endregion

    # region  LIFECYCLE

    def start(self) -> None:
        """
        Start order:
            1. BaseNode.start() - MQTT, heartbeat, registry, _node_alerts
            2. CommanderCore.start(active=False) - standby, mirrors state
        """
        # Core starts in standby - no rules fire, but state is mirrored

        self._core = CommanderCore(
            node_id=self.node_id,
            mqtt=self.mqtt,
            registry=self.registry,
            db=self.db,
        )
        super().start()

        self._core.start(active=False)

        log(
            "BackupCommander",
            "ready - standby mode (CommanderCore pre-warmed, watching for primary failure)",
            channel="SYSTEM",
        )

    def stop(self) -> None:
        log("BackupCommander", "stopping", channel="SYSTEM")
        self._core.stop()
        super().stop()

    # endregion

    # region  MESSAGE HANDLING - delegated to core

    def handle_message(self, topic: str, payload: Any) -> None:
        """
        Delegate all message routing to CommanderCore.

        In standby the core only processes state snapshots (to stay current).
        After activation the core processes fire events, approvals, and ACKs.
        """
        self._core.handle_message(topic, payload)

    # endregion

    # region  NODE LIFECYCLE CALLBACKS

    def _on_node_failed(self, payload: dict[str, Any]) -> None:
        """
        HeartbeatMonitor calls this when any node goes OFFLINE.

        _become_active() can block for up to LEASE_CONFIRM_MAX_WAIT_SECONDS
        while confirming lease staleness. Dispatched onto its own thread
        so the monitor loop is never held up by our confirmation wait.
        """
        node_id = payload.get("node_id", "")
        rec = self.registry.get(node_id)
        is_primary = rec is not None and rec.node_type == PRIMARY_NODE_TYPE

        if is_primary:
            log("BackupCommander", f"primary failure detected ({node_id}) - taking over", channel="SYSTEM")
            self._primary_alive = False
            self._announce_node_status(node_id, "OFFLINE")

            if self._node_alerts:
                self._node_alerts.add(
                    alert_id=f"node_down:{node_id}",
                    kind="NODE_DOWN",
                    severity="CRITICAL",
                    title=f"Primary commander offline: {node_id}",
                    detail=f"reason={payload.get('reason', 'HEARTBEAT_TIMEOUT')}",
                    source_ref=node_id,
                )

            threading.Thread(target=self._become_active, daemon=True).start()
            return

        # Not the primary - but if active and this is a swarm leader, re-dispatch
        if self._core._active and rec and rec.current_job and SWARM_LEAD in (rec.capabilities or []):  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
            fire_id = rec.current_job  # pyright: ignore[reportOptionalMemberAccess]
            log(
                "BackupCommander",
                f"swarm leader {node_id} offline while backup is active - re-dispatching fire={fire_id[:8]}",  # pyright: ignore[reportOptionalSubscript]
                channel="SYSTEM",
            )
            self.registry.release_job(node_id)

            mission = self._core.missions.get_for_fire(fire_id)  # pyright: ignore[reportArgumentType]
            if mission:
                self._core.missions.transition(mission.mission_id, PAUSED, reason=f"leader_{node_id}_offline")
            self._core.redispatch_fire(fire_id, dead_leader=node_id)  # pyright: ignore[reportArgumentType]

    def _on_node_recovered(self, payload: dict[str, Any]) -> None:
        """HeartbeatMonitor calls this when a node comes back ONLINE."""
        node_id = payload.get("node_id", "")
        rec = self.registry.get(node_id)
        is_primary = rec is not None and rec.node_type == PRIMARY_NODE_TYPE

        if not is_primary:
            return

        self._primary_alive = True
        self._announce_node_status(node_id, "ONLINE")

        if self._core._active:  # pyright: ignore[reportPrivateUsage]
            self._stand_down(node_id)

    # endregion

    # region  FAILOVER / STAND-DOWN

    def _become_active(self) -> None:
        """
        Promote this node to active commander - but only after confirming
        via the leadership lease that this looks like a real failure,
        not a single missed heartbeat (see CommanderCore's lease docs
        for what this can and can't actually guarantee).

        Polls _check_lease_and_maybe_promote() in short steps, up to
        LEASE_CONFIRM_MAX_WAIT_SECONDS, instead of either trusting the
        heartbeat timeout alone or blocking indefinitely. If the lease
        renews during this wait, we log it and stand down the attempt -
        the primary wasn't actually dead.

        On confirmed promotion:
        1. Activate CommanderCore - it bumps the lease term, starts
           processing fire events/approvals/ACKs, and begins publishing
           state snapshots.
        2. Publish SYSTEM_FAILOVER so field nodes and the dashboard know
           who the new primary is.
        """
        waited = 0.0
        while not self._core._check_lease_and_maybe_promote():  # pyright: ignore[reportPrivateUsage]
            if waited >= LEASE_CONFIRM_MAX_WAIT_SECONDS:
                log(
                    "BackupCommander",
                    "lease still not confirmed stale after max wait - promoting anyway on heartbeat timeout alone",
                    channel="SYSTEM",
                )
                break
            time.sleep(LEASE_CONFIRM_POLL_SECONDS)
            waited += LEASE_CONFIRM_POLL_SECONDS
            # If the primary renewed the lease while we waited, the
            # heartbeat monitor will independently call
            # _on_node_recovered() and clear self._primary_alive - bail
            # out of this promotion attempt rather than racing it.
            if self._primary_alive:
                log(
                    "BackupCommander",
                    "primary recovered while confirming lease staleness - aborting promotion",
                    channel="SYSTEM",
                )
                return

        self._core.activate()

        log("BackupCommander", "PRIMARY LOST - FAILOVER MODE: CommanderCore is now active", channel="SYSTEM")

        # qos=1 - this tells the rest of the system who the
        # new primary is; losing it silently leaves field nodes and
        # the dashboard unaware a failover happened at all.
        self.mqtt.publish(
            SYSTEM_FAILOVER,
            {
                "new_primary": self.node_id,
                "timestamp": time.time(),
            },
            qos=1,
        )

    def _stand_down(self, primary_node_id: str) -> None:
        """
        Return to standby after primary recovery.

        Deactivates CommanderCore - it stops running rules and publishing
        snapshots but continues mirroring state from the primary's snapshots.
        """
        self._core.deactivate()
        log(
            "BackupCommander",
            f"primary recovered ({primary_node_id}) - standing down, returning to standby",
            channel="SYSTEM",
        )

    @property
    def is_active(self) -> bool:
        """True if this backup node has taken over as the active commander."""
        return self._core._active  # pyright: ignore[reportPrivateUsage]

    # endregion


# endregion (end of class BackupCommander)
