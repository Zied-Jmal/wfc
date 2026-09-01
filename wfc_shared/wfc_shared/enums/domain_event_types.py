"""
wfc_shared.enums.domain_event_types
=====================================
Internal domain event types written by DomainEventLog.
These are commander DECISION records, not external sensor inputs.
Distinct from shared/enums/events.py (which covers wire-level event types).
"""

from __future__ import annotations

# Fire lifecycle (mirrored from events.py for single-import convenience)
FIRE_DETECTED = "FIRE_DETECTED"
FIRE_CONTAINED = "FIRE_CONTAINED"
FIRE_SUPPRESSED = "FIRE_SUPPRESSED"

# Commander decisions
FIRE_DISPATCHED = "FIRE_DISPATCHED"  # RuleEngine dispatched RESPOND_TO_FIRE
FIRE_REDISPATCHED = "FIRE_REDISPATCHED"  # redispatch after leader death
LEADER_DIED = "LEADER_DIED"  # leader declared offline mid-fire
ESCALATION_REQUESTED = "ESCALATION_REQUESTED"  # HighSeverityRule created PendingCommand
ESCALATION_APPROVED = "ESCALATION_APPROVED"  # operator approved
ESCALATION_REJECTED = "ESCALATION_REJECTED"  # operator rejected
COMMAND_ACK_RECEIVED = "COMMAND_ACK_RECEIVED"  # RECEIVED ACK from field node
COMMAND_ACK_EXECUTED = "COMMAND_ACK_EXECUTED"  # EXECUTED ACK from field node
COMMAND_ACK_FAILED = "COMMAND_ACK_FAILED"  # FAILED ACK from field node
NODE_BECAME_AVAILABLE = "NODE_BECAME_AVAILABLE"  # node gained SWARM_LEAD capability
LEADER_REPLACED = "LEADER_REPLACED"  # bully election accepted
# payload: { fire_id, old_leader_id,
#   new_leader_id, term, election_type }
