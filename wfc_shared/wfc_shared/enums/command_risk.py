"""
wfc_shared.enums.command_risk
==============================
Risk level for each command type.
ApprovalGate blocks IRREVERSIBLE commands until an operator approves.
Risk levels
-----------
  SAFE         : automatically dispatched, no operator gate
  DISRUPTIVE   : pulls resources from another assignment; logged but auto-approved
  IRREVERSIBLE : always routed to approval gate before dispatch
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

# Risk levels


class CommandRisklevels(StrEnum):
    """Enumeration of command risk levels for approval gating."""

    SAFE = "SAFE"
    DISRUPTIVE = "DISRUPTIVE"
    IRREVERSIBLE = "IRREVERSIBLE"


# Command risk mapping
COMMAND_RISK: Final[dict[str, CommandRisklevels]] = {
    "RESPOND_TO_FIRE": CommandRisklevels.SAFE,
    "CONTAIN_FIRE": CommandRisklevels.SAFE,
    "STAND_DOWN": CommandRisklevels.SAFE,
    "REINFORCE_FIRE": CommandRisklevels.SAFE,
    "REASSIGN_LEADER": CommandRisklevels.SAFE,
    "CONFIRM_LEADERSHIP": CommandRisklevels.SAFE,
    "DISPATCH_DRONE": CommandRisklevels.SAFE,
    "RECALL_DRONE": CommandRisklevels.SAFE,
    "UPDATE_TASK": CommandRisklevels.SAFE,
    "PREEMPT_RESOURCE": CommandRisklevels.DISRUPTIVE,
    "ESCALATE_FIRE": CommandRisklevels.IRREVERSIBLE,
    "ABORT_MISSION": CommandRisklevels.IRREVERSIBLE,
    "OVERRIDE_SAFETY": CommandRisklevels.IRREVERSIBLE,
}
