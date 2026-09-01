"""wfc_shared.enums.command_types
===============================
All valid command_type string values.
Single source of truth for command routing.
Direction key
--------------
Commander Leader : RESPOND_TO_FIRE, CONTAIN_FIRE, STAND_DOWN,
REINFORCE_FIRE, ABORT_MISSION, REASSIGN_LEADER,
CONFIRM_LEADERSHIP
Leader Drone : DISPATCH_DRONE, RECALL_DRONE, UPDATE_TASK
(internal only) : ESCALATE_FIRE - commander rule-engine escalation;
NEVER sent over MQTT to field nodes.
"""

from __future__ import annotations

# Commander Leader
RESPOND_TO_FIRE = "RESPOND_TO_FIRE"  # dispatch leader to fire
CONTAIN_FIRE = "CONTAIN_FIRE"  # hold perimeter
STAND_DOWN = "STAND_DOWN"  # fire out, return to base
REINFORCE_FIRE = "REINFORCE_FIRE"  # send more resources to active fire
ABORT_MISSION = "ABORT_MISSION"  # terminate mission (requires approval)
REASSIGN_LEADER = "REASSIGN_LEADER"  # move leader to different fire
CONFIRM_LEADERSHIP = "CONFIRM_LEADERSHIP"  # acknowledge election; sync fire state

# Leader Drone
DISPATCH_DRONE = "DISPATCH_DRONE"  # assign drone to a task
RECALL_DRONE = "RECALL_DRONE"  # pull drone back to staging
UPDATE_TASK = "UPDATE_TASK"  # update drone's current objective

# Commander-internal only (never sent to field nodes)
ESCALATE_FIRE = "ESCALATE_FIRE"  # internal rule-engine escalation only

# Validation sets
COMMANDER_TO_LEADER_COMMANDS = frozenset(
    {
        RESPOND_TO_FIRE,
        CONTAIN_FIRE,
        STAND_DOWN,
        REINFORCE_FIRE,
        ABORT_MISSION,
        REASSIGN_LEADER,
        CONFIRM_LEADERSHIP,
    }
)

LEADER_TO_DRONE_COMMANDS = frozenset(
    {
        DISPATCH_DRONE,
        RECALL_DRONE,
        UPDATE_TASK,
    }
)
