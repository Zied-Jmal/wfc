# proximity-first dispatch; fixes dispatch-to-all-nodes bug.
# Selection: get_closest() get_in_zone() get_by_capability()
# targets SWARM_LEAD (not RECEIVE_COMMANDS directly).
# Passes location_coords through to payload.
# Uses get_available() / idle_only=True to prevent double-dispatch.
# reads FireRecord (state) instead of FireEvent. Triggers on
# fire.state == ACTIVE (not on event_type == FIRE_DETECTED).
"""fire_dispatch.py
FireDispatchRule - dispatch closest node to a fire
- Trigger on FIRE_DETECTED events
- Select ONE target node via 3-tier priority:
1. Closest by coordinates (get_closest)
2. Same zone (get_in_zone)
3. Any available (get_by_capability)
- Emit RESPOND_TO_FIRE command
"""

from __future__ import annotations

# Standard Library

# Third-Party Libraries

# Project Imports

from wfc_shared.enums.fire_status import ACTIVE
from wfc_shared.enums.capabilities import SWARM_LEAD
from wfc_shared.enums.command_types import RESPOND_TO_FIRE
from wfc_shared.schemas.commands import Command
from core.state.fire_state_store import FireRecord
from core.node_registry.registry import NodeRegistry
from core.rule_engine.rule import Rule, RuleResult
from core.rule_engine.context import RuleContext
from core.utils.logger import log

# region  CLASS - FireDispatchRule

class FireDispatchRule(Rule):

    """Triggers on FIRE_DETECTED. Selects the single best node
    using proximity zone any fallback, then emits RESPOND_TO_FIRE.
    """

    # region  RULE METADATA

    @property
    def name(self) -> str:
        return "fire_dispatch"

    @property
    def requires_approval(self) -> bool:
        return False

    # endregion

    # region  EVALUATION

    def evaluate(self, fire: FireRecord, registry: NodeRegistry, context: RuleContext | None = None) -> RuleResult:
        if fire.state != ACTIVE:
            return RuleResult(triggered=False, reason=f"fire_state_is_{fire.state}_not_active")

# already has an assigned node - don't dispatch a second time.
# This is the structural fix for E2/E3: a duplicate or re-evaluated
# event cannot cause double-dispatch because we check STATE, not
# whether this particular event was "new".
        if fire.assigned_node is not None:
            return RuleResult(triggered=False, reason="fire_already_assigned")

        target, strategy = self._select_target(fire, registry)

        if not target:
            return RuleResult(triggered=False, reason="no_available_swarm_leaders")

        log("FireDispatchRule",
            f"dispatching via {strategy} → {target} "
            f"fire={fire.fire_id[:8]} zone={fire.zone}",
            channel="RULES")

        return RuleResult(
            triggered=True,
            reason=f"dispatching_to_{target}_via_{strategy}",
            commands=[Command(
                target_node=target,
                command_type=RESPOND_TO_FIRE,  # pyright: ignore[reportArgumentType]
                payload={
                    "fire_id":         fire.fire_id,
                    "zone":            fire.zone,
                    "location":        fire.zone,        # back-compat alias
                    "location_coords": fire.location_coords,
                    "severity":        fire.severity,
                    "sensor_id":       fire.sensor_id,
                },
            )],
        )

    # endregion

    # region  PRIVATE - target selection
    def _select_target(self, fire: FireRecord, registry: NodeRegistry) -> tuple[str | None, str]:
        """
        3-tier selection - idle swarm leaders only (current_job is None):
          1. Closest by coordinates
          2. Same zone
          3. Any available swarm leader (fallback)
        """

# 1 - proximity
        if fire.location_coords:
            target = registry.get_closest(SWARM_LEAD, fire.location_coords, idle_only=True)
            if target:
                log("FireDispatchRule", f"proximity target: {target}", channel="RULES")
                return target, "proximity"

# 2 - zone match
        candidates = registry.get_in_zone(SWARM_LEAD, fire.zone, idle_only=True)
        if candidates:
            log("FireDispatchRule", f"zone_match target: {candidates[0]}", channel="RULES")
            return candidates[0], "zone_match"

# 3 - any idle
        candidates = registry.get_available(SWARM_LEAD)
        if candidates:
            log("FireDispatchRule", f"any_available target: {candidates[0]}", channel="RULES")
            return candidates[0], "any_available"

        log("FireDispatchRule", "no target found", channel="RULES")
        return None, "none"

    # endregion

# endregion (end of class FireDispatchRule)
