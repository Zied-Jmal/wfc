# Test_Protocols.md — WFC System Test Catalogue

This file is the single source of truth for every test in the WFC system,
from unit tests inside one function to full end-to-end scenarios across
all repos. It exists so that anyone — a new engineer, a reviewer, an AI
assistant — can read this document and know exactly what is tested, why,
and what passing or failing actually means, **without reading any test
code**.

Each protocol follows the same template:

```
ID            — unique identifier, referenced from test code as a comment
Layer         — Unit | Component-Integration | Repo-Integration |
                System-Integration | E2E
Repo / Module — which repo and file the test targets
Description   — what behavior is being verified, in plain language
Goal          — why this test exists (what bug class it catches)
Input         — exact inputs / fixture state
Output        — exact expected output
Pass condition  — precise, checkable condition for PASS
Fail condition  — precise, checkable condition for FAIL
Type          — Deterministic | Probabilistic | Timing-sensitive
Notes         — edge cases, known limitations, related bugs
```

Tests are numbered by layer: `U-` (unit), `CI-` (component integration),
`RI-` (repo integration), `SI-` (system integration), `E2E-` (end to end,
maps to the existing 5 scenarios in `Integration_Tests/`).

---

## How to use this document

- **Writing a new test?** Find the closest existing protocol, copy its
  template, fill in the real values, and implement it using the stated
  Input/Output as your test fixture and assertion.
- **Reviewing test coverage?** Every row with no corresponding test file
  yet is a gap — search the codebase for the ID as a comment marker
  (e.g. `# TEST: U-RULE-001`) to find which protocols are implemented.
- **Debugging a production issue?** Find the protocol whose Description
  matches the broken behavior — its Pass/Fail conditions tell you exactly
  what assertion should have caught it, and its Notes may already
  document the failure mode.

---

# LAYER 1 — UNIT TESTS

Unit tests run with no broker, no Docker, no network, no database (use an
in-memory fake or pass `db=None`). Each targets one function or one class
method in isolation. Target runtime: under 50ms each, full unit suite
under 15 seconds.

## 1.1 — Commander_Repo / RuleEngine — Rule Engine Rules

Each of the 15 rules in `core/rule_engine/rules/` is a pure function of
`(fire: FireRecord, registry: NodeRegistry, context: RuleContext) →
RuleResult` (5 older rules take only `(fire, registry)`). All can be unit
tested by constructing a fake `FireRecord`, a fake registry (a dict-backed
stub implementing `.get()`, `.get_all()`, `.get_available()`,
`.get_closest()`, `.get_in_zone()`, `.get_by_capability()`), and a fake
`RuleContext`, then calling `.evaluate()` directly.

### U-RULE-001 — FireDispatchRule: proximity selection
- **Layer:** Unit
- **Module:** `core/rule_engine/rules/fire_dispatch.py :: FireDispatchRule.evaluate`
- **Description:** When a fire has GPS coordinates and at least one idle
  SWARM_LEAD node has a registered location, the rule must select the
  geometrically closest such node, not the first one found or a random one.
- **Goal:** This is the single most safety-critical dispatch decision in
  the system — picking a far-away leader when a closer one is idle adds
  real-world response delay to a fire.
- **Input:** `fire.state="ACTIVE"`, `fire.assigned_nodes=[]`,
  `fire.location_coords=(36.80, 10.18)`. Registry contains 3 SWARM_LEAD
  nodes, all `status="ACTIVE"`, `current_job=None`: `sl-1` at
  `(36.81, 10.19)` (~1.5km away), `sl-2` at `(37.50, 11.00)` (~85km away),
  `sl-3` at `(36.805, 10.185)` (~0.7km away, closest).
- **Output:** `RuleResult.triggered == True`, exactly one command in
  `.commands`, `command.command_type == "RESPOND_TO_FIRE"`,
  `command.target_node == "sl-3"`, `result.reason` contains `"proximity"`.
- **Pass condition:** target_node equals the closest node by Euclidean
  distance, and `reason` string indicates the `"proximity"` strategy was used.
- **Fail condition:** any other node selected, or `triggered == False`.
- **Type:** Deterministic.
- **Notes:** Tests tier 1 of the 3-tier fallback (`_select_target`).
  Pair with U-RULE-002 and U-RULE-003 to cover tiers 2 and 3.

### U-RULE-002 — FireDispatchRule: zone fallback when no coordinates match
- **Description:** When `fire.location_coords` is `None` (or no idle node
  has a registered `.location`), the rule must fall back to selecting any
  idle SWARM_LEAD node in the same `.zone` as the fire.
- **Goal:** Catches regressions in the fallback chain — a missing GPS fix
  from a degraded sensor must not mean the fire goes undispatched.
- **Input:** `fire.location_coords=None`, `fire.zone="zone_alpha"`.
  Registry: `sl-1` zone="zone_bravo" idle, `sl-2` zone="zone_alpha" idle.
- **Output:** `target_node == "sl-2"`, `reason` contains `"zone_match"`.
- **Pass condition:** as above.
- **Fail condition:** `sl-1` selected, or no dispatch occurs.
- **Type:** Deterministic.

### U-RULE-003 — FireDispatchRule: any-available last resort
- **Description:** When neither proximity nor zone match finds a
  candidate, the rule must dispatch to any idle SWARM_LEAD node regardless
  of location/zone, rather than leaving the fire unhandled.
- **Goal:** Ensures the system never silently drops a fire dispatch when
  geography data is simply unavailable but a responder exists.
- **Input:** `fire.location_coords=None`, `fire.zone="zone_unknown"`.
  Registry: one idle SWARM_LEAD `sl-9`, zone="zone_alpha", no `.location`.
- **Output:** `target_node == "sl-9"`, `reason` contains `"any_available"`.
- **Pass condition:** as above.
- **Fail condition:** `triggered == False` despite an idle leader existing.
- **Type:** Deterministic.

### U-RULE-004 — FireDispatchRule: no double-dispatch on already-assigned fire
- **Description:** If `fire.assigned_nodes` is non-empty (fire already has
  a leader), the rule must return `triggered=False`, even if called again
  with the same fire state (idempotency under re-evaluation/duplicate events).
- **Goal:** This is the core fix from V1.9 documented in the file's own
  changelog — prevents double-dispatch from duplicate/out-of-order events,
  since the rule checks current STATE, not the triggering event.
- **Input:** `fire.state="ACTIVE"`, `fire.assigned_nodes=["sl-1"]`. Any registry.
- **Output:** `RuleResult.triggered == False`, `reason == "fire_already_assigned"`.
- **Pass condition:** exact reason string match (this is intentionally a
  hard string assertion, since the lack of dispatch is the only
  observable signal of this safety property).
- **Fail condition:** `triggered == True` (regression of V1.9 fix).
- **Type:** Deterministic.

### U-RULE-005 — FireDispatchRule: no candidates at all
- **Description:** If no SWARM_LEAD node is idle anywhere, rule must
  return `triggered=False` with `reason="no_available_swarm_leaders"` and
  must NOT raise an exception.
- **Goal:** Verifies graceful degradation feeding into U-RULE-014
  (NoRespondersRule should then trigger the escalation).
- **Input:** `fire.state="ACTIVE"`, registry has zero idle SWARM_LEAD nodes.
- **Output:** `triggered=False`, no exception raised.
- **Pass condition:** as above.
- **Fail condition:** any exception, or `triggered=True`.
- **Type:** Deterministic.

### U-RULE-006 — HighSeverityRule: triggers and requires approval
- **Module:** `core/rule_engine/rules/high_severity.py`
- **Description:** For a fire in state `IGNITED` or `ACTIVE` with
  `severity` in `("HIGH", "CRITICAL")`, the rule must trigger and emit
  `ESCALATE_FIRE` to every node with `DISPATCH_COMMANDS` capability, and
  `rule.requires_approval` must report `True`.
- **Goal:** HIGH/CRITICAL fires must never be auto-escalated without
  human sign-off — this is a policy/safety requirement, not just logic.
- **Input:** `fire.state="ACTIVE"`, `fire.severity="HIGH"`. Registry has 2
  nodes with `DISPATCH_COMMANDS` capability (e.g. central + backup commander).
- **Output:** `triggered=True`, 2 commands (one per DISPATCH_COMMANDS
  node), each `command_type == "ESCALATE_FIRE"`.
  `HighSeverityRule().requires_approval == True`.
- **Pass condition:** command count equals DISPATCH_COMMANDS node count,
  `requires_approval` is `True` regardless of fire state.
- **Fail condition:** fewer/more commands than nodes, or
  `requires_approval == False`.
- **Type:** Deterministic.
- **Notes:** Also test the negative: `severity="LOW"` → `triggered=False`,
  and `fire.state="CONTAINED"` with `severity="HIGH"` → `triggered=False`
  (not in `_ESCALATABLE_STATES`).

### U-RULE-007 — FireContainedRule: targets ONLY the assigned leader (regression test for V2.0.8 bug)
- **Module:** `core/rule_engine/rules/fire_contained.py`
- **Description:** When a fire transitions to `CONTAINED`, `CONTAIN_FIRE`
  must be sent to exactly the one leader in `fire.assigned_node` — NOT
  broadcast to every SWARM_LEAD node in the registry.
- **Goal:** This is a direct regression test for a documented, already-fixed
  bug (V2.0.8): under concurrent fires, broadcasting to all leaders would
  make a leader working a DIFFERENT fire abandon it. This test exists
  specifically so nobody reintroduces `get_by_capability()` here.
- **Input:** `fire.state="CONTAINED"`, `fire.assigned_nodes=["sl-1"]`.
  Registry has 3 SWARM_LEAD nodes total: `sl-1`, `sl-2`, `sl-3`.
- **Output:** exactly 1 command, `target_node == "sl-1"`.
- **Pass condition:** command count is exactly 1 and targets only the
  assigned leader.
- **Fail condition:** more than 1 command, or any command targets `sl-2`
  or `sl-3`.
- **Type:** Deterministic.
- **Notes:** This exact pattern (single-target, not broadcast) must also
  be verified for `FireSuppressedRule` (U-RULE-008) — same bug class,
  same fix, same regression risk.

### U-RULE-008 — FireSuppressedRule: targets ONLY the assigned leader (regression test for V2.0.8 bug)
- **Description:** Identical to U-RULE-007 but for `STAND_DOWN` on
  `fire.state="SUPPRESSED"`.
- **Goal:** Same as U-RULE-007 — concurrent-fire safety regression.
- **Input:** `fire.state="SUPPRESSED"`, `fire.assigned_nodes=["sl-2"]`,
  3 total SWARM_LEAD nodes in registry.
- **Output:** exactly 1 command, `target_node == "sl-2"`,
  `command_type == "STAND_DOWN"`.
- **Pass / Fail:** mirrors U-RULE-007.
- **Type:** Deterministic.

### U-RULE-009 — NoRespondersRule: escalates only when zero leaders available
- **Module:** `core/rule_engine/rules/no_responders.py`
- **Description:** Triggers `ESCALATE_FIRE` to all `DISPATCH_COMMANDS`
  nodes only when `fire.state="ACTIVE"`, fire has no assigned node, AND
  `registry.get_available(SWARM_LEAD)` returns an empty list.
- **Goal:** Ensures a fire is never silently left unhandled; this is the
  safety net behind FireDispatchRule.
- **Input (trigger case):** `fire.state="ACTIVE"`, `assigned_nodes=[]`, 0
  idle SWARM_LEAD nodes, 1 DISPATCH_COMMANDS node.
- **Output (trigger case):** `triggered=True`, 1 `ESCALATE_FIRE` command,
  `payload.reason == "NO_SWARM_LEADERS_AVAILABLE"`.
- **Input (non-trigger case):** same fire, but 1 idle SWARM_LEAD node exists.
- **Output (non-trigger case):** `triggered=False`,
  `reason="swarm_leaders_available"`.
- **Pass condition:** both cases match exactly.
- **Fail condition:** triggers when leaders ARE available (false escalation
  noise), or fails to trigger when none are (silent fire).
- **Type:** Deterministic.

### U-RULE-010 — ElectedLeaderRule: stale term rejection
- **Module:** `core/rule_engine/rules/elected_leader.py`
- **Description:** A bully-election result with `term <= fire.leader_term`
  must be rejected (not trigger), since it represents a stale/delayed
  message arriving after a newer term has already been accepted.
- **Goal:** Prevents a network-delayed old election message from
  overriding a more recent, already-confirmed leadership change — a
  classic distributed-systems correctness bug.
- **Input:** `fire.leader_term=3`, `context.election_metadata={"term": 2,
  "fire_id": fire.fire_id, "new_leader_id": "sl-2"}`.
- **Output:** `triggered=False`, `reason` contains `"stale_term_2"`.
- **Pass condition:** exact reason prefix match.
- **Fail condition:** `triggered=True` for a term not strictly greater
  than the current one.
- **Type:** Deterministic.
- **Notes:** Also test the boundary: `term == fire.leader_term` must
  ALSO be rejected (the check is `<=`, not `<`) — write this as a
  separate assertion since off-by-one errors here are easy to introduce.

### U-RULE-011 — ElectedLeaderRule: accepts valid newer term for capable node
- **Description:** A valid election result (newer term, matching fire_id,
  target node has `SWARM_LEAD` capability) must trigger
  `CONFIRM_LEADERSHIP` to the new leader.
- **Input:** `fire.leader_term=1`, `fire.fire_id="abc123"`,
  `context.election_metadata={"term": 2, "fire_id": "abc123",
  "new_leader_id": "sl-9"}`. Registry: `sl-9` has `"SWARM_LEAD"` in
  capabilities.
- **Output:** `triggered=True`, 1 command, `command_type ==
  "CONFIRM_LEADERSHIP"`, `target_node == "sl-9"`,
  `payload["term_accepted"] == 2`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.
- **Notes:** Also test rejection when `new_leader` lacks `SWARM_LEAD`
  capability (`reason == "node_not_capable"`) and when `fire_id` doesn't
  match (`reason == "fire_id_mismatch"`).

### U-RULE-012 — PriorityRule: preemption only when strictly higher priority
- **Module:** `core/rule_engine/rules/priority.py`
- **Description:** When no SWARM_LEAD nodes are idle, the rule must steal
  a leader from the lowest-priority OTHER active fire — but ONLY if the
  new fire's priority is strictly greater than that fire's priority.
  Priority order: `CRITICAL(100) > HIGH(90) > SPREADING(85) > MEDIUM(70)
  > ACTIVE(60) > LOW(30) > CONTAINED(10)`.
- **Goal:** This is a load-shedding safety valve — must never steal a
  leader for an equal-or-lower-priority fire (would cause priority
  inversion / thrashing between fires of similar urgency).
- **Input (trigger case):** `context.trigger=NEW_FIRE`, fire.severity="CRITICAL",
  no idle leaders, one other active fire with severity="MEDIUM" and
  `assigned_nodes=["sl-1"]`.
- **Output (trigger case):** `triggered=True`, 2 commands: `STAND_DOWN` to
  `sl-1` for the old fire, `RESPOND_TO_FIRE` to `sl-1` for the new fire.
  `state_updates` contains `preempt_from_fire_id` and `preempt_node_id`.
- **Input (non-trigger case):** same setup but new fire severity="LOW"
  (lower than the candidate's MEDIUM).
- **Output (non-trigger case):** `triggered=False`, `reason ==
  "new_fire_lower_priority"`.
- **Pass condition:** both cases match.
- **Fail condition:** preemption happens for equal or lower priority, or
  fails to happen for genuinely higher priority with no other option.
- **Type:** Deterministic.
- **Notes:** Also test: `context.trigger != NEW_FIRE` → always
  `triggered=False` regardless of priority (this rule only runs on new
  fire arrival, not on every re-evaluation).

### U-RULE-013 — ContainmentFailureRule: re-activates on dead leader
- **Module:** `core/rule_engine/rules/containment_failure.py`
- **Description:** A `CONTAINED` fire whose assigned leader is dead
  (`registry.get(leader_id)` returns `None` or `status != "ACTIVE"`) must
  transition back to `ACTIVE` and clear `assigned_nodes`.
- **Goal:** Prevents a fire from being permanently "stuck" as falsely
  contained when the only node holding the perimeter has died.
- **Input:** `fire.state="CONTAINED"`, `fire.assigned_nodes=["sl-1"]`,
  registry returns `None` for `sl-1` (node no longer exists / unregistered).
- **Output:** `triggered=True`,
  `state_updates == {"state": "ACTIVE", "clear_assigned_nodes": True}`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.
- **Notes:** Also test the "holding" case: leader is `status="ACTIVE"` →
  `triggered=False`, `reason="containment_holding"`.

### U-RULE-014 — RekindleDetectionRule: only fires on REKINDLED trigger
- **Module:** `core/rule_engine/rules/rekindled.py`
- **Description:** Must only trigger when `context.trigger ==
  EvalTrigger.REKINDLED` AND `fire.state` is `SUPPRESSED` or
  `EXTINGUISHED`. Transitions state back to `ACTIVE`.
- **Goal:** A suppressed/extinguished fire reigniting is a distinct event
  type from a brand-new fire and must follow this specific path, not be
  silently merged with FireDispatchRule logic.
- **Input:** `context.trigger=EvalTrigger.REKINDLED`, `fire.state="SUPPRESSED"`,
  `context._rekindled_payload={"timestamp": 1234.5}`.
- **Output:** `triggered=True`, `state_updates["state"] == "ACTIVE"`,
  `state_updates["rekindled_at"] == 1234.5`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.
- **Notes:** Also test: `fire.state="ACTIVE"` (already active, can't
  "rekindle" something not out) → `triggered=False`, `reason="not_suppressed"`.

### U-RULE-015 — SeverityIncreaseRule: ignores unchanged severity
- **Module:** `core/rule_engine/rules/severity_increase.py`
- **Description:** Must NOT trigger if the incoming intensity update's
  `new_intensity` equals the fire's current `severity` (no-op update).
- **Goal:** Prevents redundant state churn / log noise / repeated
  transition attempts from a sensor that just re-reports the same value.
- **Input:** `fire.severity="HIGH"`, `context.trigger=INTENSITY_UPDATE`,
  `context._intensity_payload={"new_intensity": "HIGH"}`.
- **Output:** `triggered=False`, `reason="severity_unchanged"`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

## 1.2 — Commander_Repo / NodeRegistry

### U-REG-001 — register(): new node defaults to REGISTERED, not ACTIVE
- **Module:** `core/node_registry/registry.py :: NodeRegistry.register`
- **Description:** A freshly registered node must have `status ==
  "REGISTERED"`, never `"ACTIVE"`, until `grant_active()` or
  `heartbeat()` is called.
- **Goal:** Encodes rule R2 from the file's own changelog — ACTIVE is
  earned by a valid heartbeat, not granted on mere announcement. Without
  this, a node could receive commands before proving it's actually alive.
- **Input:** `registry.register("sl-1", "SWARM_LEADER", ["SWARM_LEAD"])`
  on an empty registry.
- **Output:** `registry.get("sl-1").status == "REGISTERED"`.
- **Pass / Fail:** exact status string match.
- **Type:** Deterministic.

### U-REG-002 — heartbeat() from unregistered node is silently ignored (rule R1)
- **Description:** Calling `heartbeat("ghost-node")` when `"ghost-node"`
  was never registered must do nothing — no exception, no entry created.
- **Goal:** Prevents heartbeat spoofing/garbage from creating phantom
  registry entries.
- **Input:** empty registry, `registry.heartbeat("ghost-node")`.
- **Output:** `registry.exists("ghost-node") == False` after the call.
- **Pass / Fail:** as stated. No exception must be raised.
- **Type:** Deterministic.

### U-REG-003 — heartbeat() grants ACTIVE on first valid heartbeat (rule R3)
- **Description:** A node in `REGISTERED` status that receives its first
  `heartbeat()` call transitions to `ACTIVE`.
- **Input:** registered node `sl-1` (status=REGISTERED),
  `registry.heartbeat("sl-1")`.
- **Output:** `registry.get("sl-1").status == "ACTIVE"`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

### U-REG-004 — mark_offline() is the ONLY path to OFFLINE (rule R4)
- **Description:** A node's status must never become `"OFFLINE"` except
  via an explicit `mark_offline()` call. Verify by exhaustively calling
  every other public mutator (`register`, `heartbeat`, `grant_active`,
  `assign_job`, `release_job`) on an ACTIVE node and confirming none of
  them ever sets status to OFFLINE.
- **Goal:** This is an architectural invariant the system depends on for
  correctness — only the system (heartbeat timeout / LWT) decides a node
  is dead, never the node "self-reporting" dead.
- **Input:** ACTIVE node `sl-1`; call each of the 5 other mutators in turn.
- **Output:** `status` remains `"ACTIVE"` after each call (except where the
  method's own documented behavior changes it, e.g. none do).
- **Pass condition:** status never transitions to OFFLINE except via
  `mark_offline()`.
- **Fail condition:** any other method flips status to OFFLINE.
- **Type:** Deterministic.

### U-REG-005 — register() clears stale current_job ONLY when recovering from OFFLINE
- **Description:** Re-registering a node that is currently `OFFLINE` and
  has a non-null `current_job` must clear `current_job` to `None`. But
  re-registering a node that is currently `ACTIVE` (still alive, just
  re-announcing) with a non-null `current_job` must NOT clear it.
- **Goal:** Direct test of the V2.0.4.1/V2.0.9 fix described at length in
  the file header — this is exactly the kind of subtle, easy-to-regress
  logic that needs an explicit test, since the "wrong" behavior (always
  clearing, or never clearing) would each silently break the system in
  different ways (permanently-busy ghost node vs. double-dispatch race).
- **Input A:** node OFFLINE, `current_job="fire-1"`. Call `register()` again
  with same node_id.
- **Output A:** `current_job is None`.
- **Input B:** node ACTIVE, `current_job="fire-2"`. Call `register()` again
  with same node_id (simulating periodic re-announce).
- **Output B:** `current_job == "fire-2"` (unchanged).
- **Pass condition:** both A and B match exactly.
- **Fail condition:** either case behaves like the other.
- **Type:** Deterministic.
- **Notes:** This is one of the highest-value tests in the whole catalogue
  — it directly encodes a previously-shipped, then-fixed bug with a
  documented two-sided failure mode.

### U-REG-006 — get_available() excludes busy nodes
- **Description:** `get_available(capability)` must return only nodes
  that have the capability, are ACTIVE, AND have `current_job is None`.
- **Input:** 3 SWARM_LEAD nodes: `sl-1` ACTIVE+idle, `sl-2` ACTIVE+busy
  (`current_job="fire-x"`), `sl-3` REGISTERED (not yet ACTIVE)+idle.
- **Output:** `get_available("SWARM_LEAD") == ["sl-1"]`.
- **Pass / Fail:** exact list match (order doesn't matter, but contents must).
- **Type:** Deterministic.

### U-REG-007 — get_closest() respects idle_only flag
- **Description:** With `idle_only=True` (default), a closer but BUSY
  node must be excluded in favor of a farther IDLE node.
- **Goal:** Prevents double-dispatch via the proximity path specifically
  — this is the exact mechanism U-RULE-001 depends on.
- **Input:** target location `(0,0)`. `sl-1` at `(0.01, 0.01)` (close) but
  `current_job="fire-y"`. `sl-2` at `(1.0, 1.0)` (far) and idle.
- **Output:** `get_closest("SWARM_LEAD", (0,0), idle_only=True) == "sl-2"`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

## 1.3 — Commander_Repo / FireStateStore

### U-FIRE-001 — transition() rejects invalid state transitions
- **Module:** `core/state/fire_state_store.py :: FireStateStore.transition`
- **Description:** Calling `transition(fire_id, new_state)` where
  `new_state` is not in `FIRE_TRANSITIONS[current_state]` must return
  `None` and leave the fire's actual stored state unchanged.
- **Goal:** Prevents the rule engine (or a bug in it) from forcing the
  fire lifecycle into an invalid state, e.g. jumping straight from
  `IGNITED` to `SUPPRESSED` without passing through `ACTIVE`.
- **Input:** fire in state `IGNITED`. Call
  `transition(fire_id, "SUPPRESSED")` assuming this isn't a legal direct
  transition per `FIRE_TRANSITIONS`.
- **Output:** return value is `None`; `store.get(fire_id).state` is still
  `"IGNITED"`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.
- **Notes:** Requires reading `shared/enums/fire_status.py`'s
  `FIRE_TRANSITIONS` table to know which transitions are actually legal
  for the specific `current_state` used in the test — write one test per
  illegal edge in that table for full coverage, not just one example.

### U-FIRE-002 — transition() on a terminal-state fire is always rejected
- **Description:** Once `fire.state` is in `TERMINAL_FIRE_STATES`, ANY
  call to `transition()` (even to a state that would otherwise be valid)
  must return the fire unchanged (current behavior: returns `rec`
  unmodified, not `None` — verify this exact return value too).
- **Goal:** Terminal states must be truly terminal — a late-arriving event
  must never resurrect a fire through this path (rekindling uses a
  dedicated rule/path, not generic transition()).
- **Input:** fire in a terminal state (e.g. `EXTINGUISHED`). Call
  `transition(fire_id, "ACTIVE")`.
- **Output:** returned record's `.state` is unchanged from before the call.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

### U-FIRE-003 — apply_snapshot_record() last-write-wins by updated_at
- **Description:** `apply_snapshot_record()` must reject (return `False`)
  an incoming snapshot whose `updated_at` is less than or equal to the
  existing record's `updated_at` — protecting against out-of-order
  delivery overwriting newer state with stale state.
- **Goal:** Core correctness property for any distributed state sync path
  (e.g. backup commander syncing state from central).
- **Input:** existing fire record with `updated_at=100.0`. Incoming
  `data={"updated_at": 50.0, ...}` (older).
- **Output:** `apply_snapshot_record()` returns `False`; stored record
  unchanged.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

### U-FIRE-004 — apply_snapshot_record() merges assigned_nodes as a union, deduped
- **Description:** When merging a newer snapshot, `assigned_nodes` must
  become the union of existing and incoming lists, with duplicates
  removed, preserving first-seen order.
- **Goal:** Prevents losing a leader assignment that one source knows
  about but the other doesn't, during multi-leader/multi-source updates
  (e.g. PriorityRule preemption tracking, or election results).
- **Input:** existing `assigned_nodes=["sl-1", "sl-2"]`. Incoming
  `data={"assigned_nodes": ["sl-2", "sl-3"], "updated_at": <newer>}`.
- **Output:** merged record's `assigned_nodes == ["sl-1", "sl-2", "sl-3"]`.
- **Pass / Fail:** exact list match including order.
- **Type:** Deterministic.

## 1.4 — Commander_Repo / CommandTracker

### U-TRACK-001 — update() rejects unknown trace_id
- **Module:** `core/commands_monitor/command_tracker.py :: CommandTracker.update`
- **Description:** Calling `update()` with a `trace_id` that was never
  `create()`-d must return `False` and must not create a new record.
- **Goal:** Directly tests the V2.0.5 fix documented in the file header —
  this return value is what callers (e.g. `CommanderCore._handle_ack`)
  must check before treating an ACK as legitimate.
- **Input:** empty tracker. `tracker.update("unknown-trace", "COMMAND_RECEIVED", {"event_id": "e1"})`.
- **Output:** returns `False`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

### U-TRACK-002 — update() rejects duplicate event_id (global dedup)
- **Description:** A second `update()` call using an `event_id` already
  seen for ANY trace_id (not just the same one) must be rejected.
- **Goal:** Verifies the dedup is genuinely global/cross-trace, as the
  class docstring claims — a narrower per-trace dedup would be a silent
  regression of this guarantee.
- **Input:** two tracked commands, `trace-A` and `trace-B`. Call
  `update("trace-A", "COMMAND_RECEIVED", {"event_id": "shared-id"})`
  (succeeds), then `update("trace-B", "COMMAND_RECEIVED", {"event_id":
  "shared-id"})`.
- **Output:** first call returns `True`, second call returns `False`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

### U-TRACK-003 — update() enforces the ISSUED→RECEIVED→{EXECUTED,FAILED} state machine
- **Description:** From state `ISSUED`, only a transition to `RECEIVED`
  is valid. From `RECEIVED`, only `EXECUTED` or `FAILED` are valid.
  `EXECUTED` and `FAILED` are terminal — any further update returns `False`.
- **Goal:** Prevents a malformed or out-of-order ACK sequence (e.g.
  EXECUTED arriving before RECEIVED) from corrupting the tracked lifecycle.
- **Input/Output table:**
  | from state | event_type | event_id | expected return | expected new state |
  |---|---|---|---|---|
  | ISSUED | COMMAND_RECEIVED | unique | True | RECEIVED |
  | ISSUED | COMMAND_EXECUTED | unique | False | ISSUED (unchanged) |
  | RECEIVED | COMMAND_EXECUTED | unique | True | EXECUTED |
  | RECEIVED | COMMAND_RECEIVED | unique | False (same-state) | RECEIVED |
  | EXECUTED | COMMAND_FAILED | unique | False (terminal) | EXECUTED |
- **Pass condition:** every row in the table matches exactly.
- **Fail condition:** any row mismatches.
- **Type:** Deterministic.

## 1.5 — Commander_Repo / PendingCommandStore (Approval)

### U-APPR-001 — add() always sets a future expires_at when TTL configured
- **Module:** `core/approval/pending_store.py :: PendingCommandStore.add`
- **Description:** A pending command added with no explicit `expires_at`
  must be assigned `created_at + ttl`.
- **Input:** store with `ttl=30.0`. `pending.created_at=1000.0`,
  `pending.expires_at=None`.
- **Output:** stored record's `expires_at == 1030.0`.
- **Pass / Fail:** exact float match.
- **Type:** Deterministic.

### U-APPR-002 — approve() dispatches BEFORE persisting APPROVED status (BUG-N regression)
- **Description:** If the dispatcher's `.send()` raises an exception
  during `approve()`, the pending command's status must NOT have already
  been written as `APPROVED` to the in-memory store before the exception
  propagates.
- **Goal:** Direct regression test for "BUG-N" documented in the file:
  previously, status was persisted as APPROVED before dispatch, so a
  failed dispatch silently looked successful to the operator. This test
  exists specifically to catch anyone reverting the order.
- **Input:** a pending command in `PENDING` status. Mock dispatcher whose
  `.send()` raises `ConnectionError`.
- **Output:** calling `store.approve(pending_id)` either propagates the
  exception, or (depending on final error-handling design) leaves
  `store.get(pending_id).status == "PENDING"`, NOT `"APPROVED"`.
- **Pass condition:** status is never `APPROVED` when dispatch failed.
- **Fail condition:** status reads `APPROVED` despite a failed send.
- **Type:** Deterministic.
- **Notes:** This test's exact pass condition depends on whether
  `approve()` is expected to catch the dispatcher exception or let it
  propagate — confirm the intended contract with whoever owns this file
  before finalizing the assertion, since the current code calls
  `self._dispatcher.send()` unguarded (no try/except visible), meaning an
  exception WILL propagate up and stop execution before the
  `self._store[pending_id] = pending` line — verify this is actually the
  desired behavior (command never marked approved) rather than assuming it.

### U-APPR-003 — expire_stale() only expires PENDING commands past TTL
- **Description:** `expire_stale()` must only affect commands in
  `PENDING` status whose `expires_at` has passed; commands already
  `APPROVED`/`REJECTED`, or still within TTL, must be untouched.
- **Input:** 3 pending commands: A (PENDING, expired), B (PENDING, not
  yet expired), C (already APPROVED, expired timestamp irrelevant).
- **Output:** returned list contains only A's `pending_id`. B and C
  statuses unchanged.
- **Pass / Fail:** as stated.
- **Type:** Timing-sensitive (uses `time.time()` — inject a fake clock or
  control `expires_at` values directly rather than sleeping in the test).

### U-APPR-004 — ApprovalHandler routes APPROVED/REJECTED correctly, drops unknown decisions
- **Module:** `core/approval/approval_handler.py :: ApprovalHandler.handle`
- **Description:** A payload with `decision="APPROVED"` calls
  `store.approve()`; `decision="REJECTED"` calls `store.reject()`; any
  other value (including missing/malformed) calls neither and logs a drop.
- **Input A:** `{"pending_id": "p1", "decision": "APPROVED"}`.
- **Output A:** `store.approve` called once with `"p1"`.
- **Input B:** `{"pending_id": "p1", "decision": "MAYBE"}`.
- **Output B:** neither `approve` nor `reject` called.
- **Pass / Fail:** mock call assertions match exactly.
- **Type:** Deterministic.
- **Notes:** This directly guards the wire-contract bug fixed earlier in
  this project (Dashboard previously sent `approved: bool` instead of
  `decision: str` — this unit test on the Commander side complements the
  Dashboard-side fix and catches a regression from either direction.

## 1.6 — Swarm_Repo / TelemetryAggregator

### U-TELE-001 — _calc_fire_intensity(): threshold boundaries
- **Module:** `core/aggregator/telemetry_aggregator.py :: TelemetryAggregator._calc_fire_intensity`
- **Description:** Verify the exact V2 °C thresholds:
  `>= 400 → CRITICAL`, `>= 280 → HIGH`, `>= 180 → MEDIUM`, `< 180 → LOW`,
  computed over the top-10th-percentile of scout thermal readings.
- **Goal:** This value directly drives `FireTactics` dispatch decisions
  and dashboard severity display — an off-by-one or wrong-direction
  comparison here cascades into wrong tactical decisions.
- **Input/Output table (single scout, so top_n=1):**
  | thermal_peak_temp_c | expected intensity |
  |---|---|
  | 399.9 | HIGH |
  | 400.0 | CRITICAL |
  | 279.9 | MEDIUM |
  | 280.0 | HIGH |
  | 179.9 | LOW |
  | 180.0 | MEDIUM |
  | 0.0 | LOW |
- **Pass condition:** every row matches exactly (boundary values are
  inclusive on the upper side per the `>=` comparisons in source).
- **Fail condition:** any row mismatches — especially the exact boundary
  values, since those are the most likely site of an off-by-one regression.
- **Type:** Deterministic.

### U-TELE-002 — snapshot(): empty telemetry returns IDLE status, not a crash
- **Description:** Calling `.snapshot()` on an aggregator that has never
  received any telemetry must return a valid `SwarmStatusSnapshot` with
  `status="IDLE"` and zeroed/None numeric fields — not raise.
- **Goal:** A freshly-started leader with no drones yet must not crash
  its own status-publish loop.
- **Input:** new `TelemetryAggregator`, `.snapshot()` called immediately.
- **Output:** valid `SwarmStatusSnapshot`, `status == "IDLE"`,
  `active_drones` is absent/0 (per dataclass defaults).
- **Pass / Fail:** as stated, and specifically: no exception raised.
- **Type:** Deterministic.

### U-TELE-003 — _calc_suppression(): division-by-zero guard on tiny perimeter
- **Description:** When `perimeter_m < 1.0` (including `0.0` or
  negative/garbage values), `_calc_suppression()` must return `None`
  rather than raising or dividing by a near-zero area.
- **Goal:** Defensive test against a sensor glitch or early-flight state
  producing a degenerate perimeter estimate.
- **Input:** `total_litres=100.0`, `perimeter_m=0.5`, `fire_intensity="HIGH"`.
- **Output:** return value is `None`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

### U-TELE-004 — ingest(): sliding window correctly evicts stale telemetry
- **Description:** `.ingest()` must drop history entries for a drone
  older than `_WINDOW_SECONDS` (60s) from that drone's deque, on every
  new ingest call.
- **Goal:** Prevents the 60-second `_calc_spread_rate()` window from
  silently growing unbounded in memory or including stale data from
  hours ago.
- **Input:** ingest 3 telemetry points for `drone-1` at `t=0`, `t=30`,
  `t=70` (using a fake/injectable clock or by setting
  `telem.timestamp` directly rather than relying on `time.time()`).
- **Output:** after ingesting the `t=70` point, the deque for `drone-1`
  must no longer contain the `t=0` point (since `70 - 60 = 10 > 0`), but
  must still contain `t=30` and `t=70`.
- **Pass / Fail:** exact deque contents match.
- **Type:** Deterministic (inject timestamps, don't sleep in real time).

## 1.7 — Swarm_Repo / DroneRegistry

### U-DRONE-001 — update_telemetry(): role inference from sensor fields (regression test)
- **Module:** `core/state/drone_registry.py :: DroneRegistry.update_telemetry`
- **Description:** When telemetry arrives for an unregistered drone_id,
  the auto-created `DroneRecord.role` must be `"SCOUT"` if
  `thermal_peak_temp_c is not None`, else `"FIREFIGHTING"` if
  `payload_litres is not None and >= 0.0`, else default to `"SCOUT"`.
- **Goal:** Direct regression test for a previously-shipped bug (role was
  inferred from `telem.task`, e.g. `"SCOUTING"`/`"SUPPRESSING"`, which
  never matched the `"SCOUT"`/`"FIREFIGHTING"` strings `get_by_role()`
  actually queries for — silently breaking all role-based queries for
  any auto-registered drone).
- **Input A:** telemetry with `thermal_peak_temp_c=300.0`,
  `payload_litres=None`, for unregistered `"sd-99"`.
- **Output A:** `registry.get_by_role("SCOUT")` includes `"sd-99"`.
- **Input B:** telemetry with `thermal_peak_temp_c=None`,
  `payload_litres=5.0`, for unregistered `"fd-99"`.
- **Output B:** `registry.get_by_role("FIREFIGHTING")` includes `"fd-99"`.
- **Pass condition:** both A and B match.
- **Fail condition:** either drone ends up filed under the wrong role, or
  under the literal telemetry `.task` string.
- **Type:** Deterministic.

### U-DRONE-002 — get_lost(): based on last_seen, not connectivity field
- **Description:** `get_lost()` must return drones whose `last_seen` is
  older than `stale_threshold` seconds — independent of whatever the
  drone's last reported `connectivity` string said (a drone could report
  `"STRONG"` connectivity right before going silent).
- **Input:** `stale_threshold=5.0`. Drone with `last_seen = now - 6.0`,
  last telemetry `connectivity="STRONG"`.
- **Output:** drone IS included in `get_lost()` despite reporting strong
  connectivity, because the registry hasn't heard from it in 6 seconds.
- **Pass / Fail:** as stated.
- **Type:** Deterministic (inject `last_seen` directly rather than sleeping).

## 1.8 — Swarm_Repo / FireTactics

### U-TACT-001 — assign_respond_to_fire(): skips fighters with low payload
- **Module:** `core/tactics/fire_tactics.py :: FireTactics.assign_respond_to_fire`
- **Description:** A firefighting drone whose last telemetry shows
  `payload_litres <= LOW_PAYLOAD_L` (1.5L) must be excluded from
  suppression assignments — should not receive a `SUPPRESSING` task.
- **Goal:** Prevents dispatching a drone that's nearly empty into an
  active suppression role where it would have to immediately RTB anyway.
- **Input:** one fighter with `last_telemetry.payload_litres = 1.0`
  (below threshold), one fighter with `payload_litres = 8.0`.
- **Output:** only the second fighter appears in the returned assignment
  list with `task="SUPPRESSING"`.
- **Pass / Fail:** assignment list length and contents match exactly.
- **Type:** Deterministic.

### U-TACT-002 — assign_respond_to_fire(): scout orbit positions are evenly distributed
- **Description:** For N scouts, each scout's assigned orbit bearing must
  be `(360/N) * i` degrees from the fire position, at the radius given by
  `ORBIT_RADIUS_BY_SEVERITY[severity]`.
- **Goal:** Verifies the geometric distribution logic — uneven spacing
  would mean some sectors of the fire perimeter go unobserved.
- **Input:** 4 scouts, `severity="HIGH"` (orbit radius 110m), fire at
  `(36.80, 10.18)`.
- **Output:** 4 assignments with `task="SCOUTING"`, target positions
  computed via the haversine destination formula at bearings `0°, 90°,
  180°, 270°` and radius 110m from the fire position (assert each
  position is within a small floating-point tolerance, e.g. 1 meter, of
  the independently-computed expected coordinate).
- **Pass / Fail:** all 4 positions within tolerance.
- **Type:** Deterministic (floating point — use `pytest.approx` or
  equivalent with a tight tolerance, not exact equality).

### U-TACT-003 — reassess(): Rule 3 low battery triggers RETURNING + fresh swap
- **Description:** A drone whose `battery_pct < 0.25` OR
  `battery_wh < 87.9` must be assigned `task="RETURNING"`, and if a fresh
  idle drone of the same role exists, it must be assigned to take over
  the position.
- **Goal:** Battery-safety logic is one of the most operationally
  critical autopilot rules — a drone that doesn't RTB in time could crash.
- **Input:** one fighter, `battery_pct=0.20` (below 0.25 threshold),
  `task="SUPPRESSING"`. One fresh idle fighter available,
  `battery_pct=0.90`.
- **Output:** assignments include `{drone_id: <low-battery-drone>,
  task: "RETURNING"}` and `{drone_id: <fresh-drone>, task: "SUPPRESSING"}`.
- **Pass / Fail:** both assignments present with correct tasks.
- **Type:** Deterministic.
- **Notes:** Also test the dual-condition: a drone with healthy
  `battery_pct` (e.g. 0.30) but very low `battery_wh` (e.g. 50.0, below
  the 87.9 Wh absolute floor) must STILL trigger RETURNING — this is the
  "V2 dual check" mentioned in the file header; a test that only checks
  `battery_pct` would miss a regression in the `battery_wh` half of the
  condition.

## 1.9 — Swarm_Repo / LeaderElection & ElectionState

### U-ELEC-001 — ElectionState.accept_term(): rejects strictly older terms
- **Module:** `core/election/election_state.py :: ElectionState.accept_term`
- **Description:** `accept_term(n)` must return `False` and leave state
  unchanged when `n < current_term`; must return `True` (and reset
  `in_election`/`received_ok`/`won`) when `n > current_term`; must return
  `True` without resetting state when `n == current_term`.
- **Goal:** This term-comparison logic is the entire correctness
  foundation of the bully election's resistance to stale/delayed messages.
- **Input/Output table:**
  | current_term | incoming | expected return | state reset? |
  |---|---|---|---|
  | 5 | 3 | False | no |
  | 5 | 5 | True | no |
  | 5 | 7 | True | yes |
- **Pass condition:** all 3 rows match exactly, including the reset behavior.
- **Type:** Deterministic.

### U-ELEC-002 — LeaderElection.start_election(): no higher peers means immediate win
- **Module:** `core/election/leader_election.py :: LeaderElection.start_election`
- **Description:** If `node_id` is higher (lexicographically/by ID
  comparison) than every entry in `peer_ids`, `start_election()` must
  call `_declare_victory()` immediately without sending any
  `ELECTION_START` messages or starting a timeout timer.
- **Goal:** Avoids an unnecessary network round-trip and timeout delay
  when the outcome is already certain.
- **Input:** `node_id="sl-Z"`, `peer_ids=["sl-A", "sl-B"]` (both lower).
- **Output:** `mqtt.publish` is never called with an `ELECTION_START`
  payload; `on_win` callback IS called.
- **Pass / Fail:** mock assertions match.
- **Type:** Deterministic.

### U-ELEC-003 — LeaderElection._handle_election_start(): only responds to lower-ID senders
- **Description:** Receiving `ELECTION_START` from a node with
  `from_node_id >= self._node_id` must be ignored (no `ELECTION_OK`
  sent, no own election started) — per the bully algorithm, you only
  respond to nodes lower than yourself.
- **Input:** `self._node_id = "sl-B"`. Incoming
  `{"type": "ELECTION_START", "from_node_id": "sl-C", "term": 2}`
  (sender is higher).
- **Output:** no `ELECTION_OK` published; `start_election()` not called
  on self.
- **Pass / Fail:** mock assertions match.
- **Type:** Deterministic.

## 1.10 — Dashboard_Repo / SwarmState

### U-DASH-001 — apply_telemetry(): no-op for unregistered node (no crash, no auto-create)
- **Module:** `dashboard/state.py :: SwarmState.apply_telemetry`
- **Description:** Telemetry for a `drone_id` with no corresponding
  `NodeState` already in `_nodes` (i.e. no announcement was ever seen)
  must be silently dropped — not create a phantom node entry.
- **Goal:** Confirms the dashboard treats `NodeAnnouncement` as the
  authoritative source of node existence; telemetry alone should never
  fabricate a node record (this is a deliberate design choice worth
  protecting with a test, since the opposite behavior — auto-creating —
  would be an easy "fix" for someone to introduce without realizing it
  changes this contract).
- **Input:** empty `SwarmState`. Call `apply_telemetry(t)` for
  `t.drone_id="unknown-drone"`.
- **Output:** `get_node("unknown-drone")` still returns `None` afterward.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

### U-DASH-002 — apply_announcement(): OFFLINE status correctly overwrites prior ONLINE
- **Description:** Applying an announcement with `status="OFFLINE"` for
  a node that was previously `"ONLINE"`/`"ACTIVE"` must update its stored
  status to `"OFFLINE"`.
- **Input:** node previously announced ONLINE. New announcement for same
  `node_id` with `status="OFFLINE"`.
- **Output:** `get_node(node_id)["status"] == "OFFLINE"`.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

---

# LAYER 2 — COMPONENT-INTEGRATION TESTS

These tests combine 2-3 real classes from the SAME repo, with no network,
no Docker, no broker — just real Python objects talking to each other
in-process. Use real `MQTTClient`-shaped mocks/fakes (recording
`.publish()` calls instead of sending over a socket).

### CI-CMD-001 — RuleEngine + ApprovalGate: HIGH severity fire is gated, not dispatched directly
- **Layer:** Component-Integration
- **Repo:** Commander_Repo
- **Description:** Running a real `RuleEngine.evaluate()` against a HIGH
  severity fire, wired to a real `ApprovalGate` (with a fake dispatcher
  recording calls), must result in the command landing in the
  `PendingCommandStore`, NOT in the fake dispatcher's sent-commands list.
- **Goal:** Verifies the `COMMAND_RISK` lookup in `RuleEngine.evaluate()`
  correctly routes risky commands through the gate — this is the actual
  wiring point between rule output and approval enforcement, which a
  rule-only unit test (U-RULE-006) cannot verify by itself.
- **Input:** real `RuleEngine`, real `ApprovalGate`, fake dispatcher, fake
  `PendingCommandStore` (in-memory). Fire with `severity="HIGH"`.
- **Output:** `dispatcher.send` is NOT called for the `ESCALATE_FIRE`
  command; `pending_store.add` IS called once with that command.
- **Pass / Fail:** mock call assertions on both objects.
- **Type:** Deterministic.

### CI-CMD-002 — RuleEngine + FireStateStore: FireDispatchRule's RESPOND_TO_FIRE causes assign_job AND add_assigned_node
- **Description:** When `RuleEngine.evaluate()` processes a triggered
  `RESPOND_TO_FIRE` command, it must call BOTH
  `registry.assign_job(target, fire_id)` (marking the node busy) AND
  `fires.add_assigned_node(fire_id, target, ...)` (recording the
  assignment on the fire) — these are two separate state stores that
  must stay in sync.
- **Goal:** This dual-write is exactly the kind of cross-store
  consistency that's invisible to a unit test of either store alone, and
  if these two ever drift out of sync, `get_available()` and
  `fire.assigned_nodes` would disagree about who's working what.
- **Input:** real `RuleEngine`, real `NodeRegistry`, real
  `FireStateStore`, fake `ApprovalGate`. A fire that will trigger
  `FireDispatchRule`.
- **Output:** after `evaluate()`, `registry.get(target).current_job ==
  fire_id` AND `fires.get(fire_id).assigned_nodes` contains `target`.
- **Pass / Fail:** both assertions must hold simultaneously.
- **Type:** Deterministic.

### CI-CMD-003 — ApprovalGate + PendingCommandStore + ApprovalHandler: full approve round-trip
- **Description:** Submit a command requiring approval through
  `ApprovalGate.submit()`, then simulate an operator decision through
  `ApprovalHandler.handle({"decision": "APPROVED", ...})`, and confirm
  the command ultimately reaches the fake dispatcher exactly once.
- **Goal:** Validates the full in-process approval round trip without
  needing MQTT — isolates "does the approval logic work" from "does the
  wire format work" (the latter is covered by E2E-04).
- **Input:** real `ApprovalGate`, real `PendingCommandStore`, real
  `ApprovalHandler`, fake dispatcher, fake MQTT (records publishes).
- **Output:** `dispatcher.send` called exactly once, with the originally
  submitted command. Fake MQTT recorded a `COMMAND_PENDING` publish
  followed by a `COMMAND_APPROVED` publish.
- **Pass / Fail:** call count and payload content assertions.
- **Type:** Deterministic.

### CI-SWARM-001 — TelemetryAggregator + FireTactics.reassess(): low battery flows into a RETURNING assignment
- **Layer:** Component-Integration
- **Repo:** Swarm_Repo
- **Description:** Ingest telemetry showing a drone with low battery into
  a real `TelemetryAggregator`, take its `.snapshot()`, and feed that
  snapshot plus a real `DroneRegistry` into `FireTactics.reassess()` —
  confirm the resulting assignment list includes the expected RETURNING action.
- **Goal:** U-TELE and U-TACT unit tests verify each class alone; this
  confirms the SHAPE of `SwarmStatusSnapshot` that the aggregator
  produces is actually what `FireTactics.reassess()` expects to consume
  — a field rename in one without the other would pass both unit suites
  but fail here.
- **Input:** real aggregator + real registry, one drone with
  `battery_pct=0.15`.
- **Output:** `reassess()` returns an assignment with `task="RETURNING"`
  for that drone.
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

### CI-DASH-001 — MQTTBridge message handler + SwarmState: malformed payload doesn't crash the bridge
- **Layer:** Component-Integration
- **Repo:** Dashboard_Repo
- **Description:** Feed a syntactically valid JSON payload that fails
  Pydantic validation (e.g. missing a required `DroneTelemetry` field)
  directly into the bridge's `_on_message` handler (bypassing real MQTT)
  and confirm it's caught and logged, not propagated as an unhandled
  exception that would kill the bridge's processing thread.
- **Goal:** A single malformed message (from a buggy drone, a version
  mismatch, or a corrupted payload) must never take down the entire
  dashboard's live data feed.
- **Input:** a mock `msg` object with `topic="wfc/telemetry/sd-1"` and
  `payload` JSON missing the required `drone_id` field.
- **Output:** `_on_message` returns normally (no exception propagates);
  `swarm_state.get_node("sd-1")` is unaffected (no garbage written).
- **Pass / Fail:** as stated.
- **Type:** Deterministic.

---

# LAYER 3 — REPO-INTEGRATION TESTS

These exercise one entire repo (e.g. the whole Commander, or the whole
Swarm leader) as a single process, against a REAL local MQTT broker
(no other repos running), to verify the repo's own MQTT wiring — topics,
QoS, payload shapes — independent of whether any other repo is present
to receive them.

### RI-CMD-001 — Commander central node: publishes retained ONLINE announcement on startup
- **Layer:** Repo-Integration
- **Repo:** Commander_Repo
- **Description:** Starting `command_nodes/central/main.py` against a
  local broker must result in a retained message on
  `wfc/registry/announce/central-commander` with `status="ONLINE"`
  within a few seconds of startup.
- **Goal:** Verifies `BaseNode.start()`'s announce sequence actually
  fires correctly when run as the real entrypoint, not just in a unit
  test of the method.
- **Input:** real broker, run the central commander process with
  `NODE_ID=central-commander`.
- **Output:** subscribing to `wfc/registry/announce/central-commander`
  (even AFTER the announcement was published, thanks to `retain=True`)
  yields a message with `status="ONLINE"`.
- **Pass / Fail:** message received within timeout (e.g. 10s), correct
  `status` field.
- **Type:** Timing-sensitive.

### RI-CMD-002 — Commander central node: ESCALATE_FIRE for a HIGH fire is held for approval (not auto-dispatched), observable on the wire
- **Description:** Inject a HIGH-severity `FireEvent` on
  `wfc/events/fire` against a running (real-process) central commander.
  Confirm a `COMMAND_PENDING` event appears on `wfc/approval/pending`,
  and NO `ESCALATE_FIRE` command is ever published to any
  `wfc/command/+` topic before an approval is sent.
- **Goal:** This is RI-CMD-001's equivalent of CI-CMD-001 but observed
  purely from the wire — confirms the behavior holds when the commander
  runs as its real entrypoint with its real MQTT client, not just with
  an in-process fake.
- **Input:** real broker, real commander process, published `FireEvent`
  with `severity="HIGH"`.
- **Output:** `wfc/approval/pending` receives a message within 10s; no
  `wfc/command/+` message with `command_type="ESCALATE_FIRE"` appears in
  that same window.
- **Pass / Fail:** as stated.
- **Type:** Timing-sensitive.

### RI-SWARM-001 — Swarm leader: dispatched drone command actually reaches wfc/command/{drone_id}
- **Layer:** Repo-Integration
- **Repo:** Swarm_Repo
- **Description:** Run a real `SwarmLeaderNode` process against a local
  broker. Publish a synthetic `RESPOND_TO_FIRE` command directly to its
  command topic. Confirm it subsequently publishes a `DISPATCH_DRONE`/
  `UPDATE_TASK` command on `wfc/command/{some_drone_id}` — without
  needing a real drone process running to receive it.
- **Goal:** Isolates "does the leader's own dispatch logic + MQTT
  publish work correctly" from "does a downstream drone exist to react"
  — narrower and faster to debug than the full E2E-01 scenario.
- **Input:** real broker, one real `SwarmLeaderNode` process (no drones
  running), a registered fake drone announcement injected manually.
- **Output:** a message appears on `wfc/command/{drone_id}` within a few
  seconds of the `RESPOND_TO_FIRE` command being received.
- **Pass / Fail:** as stated.
- **Type:** Timing-sensitive.

### RI-SWARM-002 — Swarm leader: heartbeat timeout from a drone triggers DroneRegistry unregister (regression test, BUG 9)
- **Description:** Run a real leader; register a fake drone via a real
  announcement; then stop sending that drone's announcements/heartbeats
  (simulate it disappearing) and publish an explicit OFFLINE announcement
  for it. Confirm the leader's internal `DroneRegistry` no longer
  includes that drone in `get_by_role()` results afterward.
- **Goal:** Direct regression test for the previously-fixed bug where
  `_on_registry_announce` ignored `status=="OFFLINE"` and never
  unregistered dead drones, causing `FireTactics` to keep assigning tasks
  to drones that no longer existed.
- **Input:** real broker, real leader process, fake drone announced
  ONLINE then OFFLINE.
- **Output:** after the OFFLINE announcement, the drone is absent from
  internal registry queries (verified indirectly: e.g. trigger a new fire
  dispatch and confirm the dead drone receives no command).
- **Pass / Fail:** as stated.
- **Type:** Timing-sensitive.
- **Notes:** Since `DroneRegistry` is internal to the leader process
  (not exposed over MQTT), this test must verify the effect indirectly
  through observable MQTT behavior, OR (preferred, if feasible) via a
  debug/introspection hook exposed specifically for testing.

### RI-DASH-001 — Dashboard: approval_respond endpoint publishes the correct wire-format decision (regression test, BUG 4)
- **Layer:** Repo-Integration
- **Repo:** Dashboard_Repo
- **Description:** Run the real Dashboard FastAPI app against a local
  broker (no commander needed). Call `POST /api/approval/respond` with
  `{"request_id": "p1", "approved": true}`. Confirm the message published
  to `wfc/approval/response` has `decision: "APPROVED"` (a string), NOT
  `approved: true` (a bool).
- **Goal:** Direct regression test for the previously-fixed wire-contract
  bug — this is the kind of test that should have existed BEFORE that
  bug shipped, and its absence is exactly why it reached integration
  testing instead of being caught here.
- **Input:** real broker, real Dashboard process, real HTTP POST.
- **Output:** captured MQTT message on `wfc/approval/response` has a
  `"decision"` key with value `"APPROVED"`; no `"approved"` boolean key
  is present in the published payload.
- **Pass / Fail:** exact key/value assertion on the captured payload.
- **Type:** Deterministic (network timing aside).

---

# LAYER 4 — SYSTEM-INTEGRATION TESTS

Two or more repos running together against a real broker, but NOT the
full stack — a deliberately narrower slice than the 5 E2E scenarios, used
to localize a fault to a specific repo PAIR boundary.

### SI-01 — Commander ↔ Swarm: RESPOND_TO_FIRE round trip without Dashboard
- **Layer:** System-Integration
- **Repos:** Commander_Repo + Swarm_Repo
- **Description:** Run a real commander and a real swarm leader (no
  dashboard, no drones). Inject a `FireEvent`. Confirm the leader
  receives `RESPOND_TO_FIRE` and ACKs RECEIVED then EXECUTED, and the
  commander's `CommandTracker` reflects the EXECUTED state.
- **Goal:** This is the core of E2E-01 (Fire Dispatch) minus the Dashboard
  and drone layers — if E2E-01 fails, running SI-01 alone tells you
  whether the fault is in the Commander↔Swarm boundary specifically, or
  further downstream (drone, dashboard).
- **Input:** real broker, real commander, real swarm leader (zero drones
  registered). Injected fire.
- **Output:** commander's `CommandTracker.get(trace_id).status ==
  "EXECUTED"` within a reasonable timeout (account for the leader's
  internal tactics/dispatch logic completing even with no drones to
  actually assign).
- **Pass / Fail:** as stated.
- **Type:** Timing-sensitive.

### SI-02 — Swarm ↔ Dashboard: telemetry visibility without Commander
- **Repos:** Swarm_Repo + Dashboard_Repo
- **Description:** Run a real swarm leader + a real drone (or a synthetic
  telemetry publisher standing in for one) + a real dashboard. No
  commander running. Confirm the dashboard's REST API reflects the
  drone's telemetry and the leader's swarm status snapshot.
- **Goal:** Isolates the Swarm→Dashboard telemetry pipeline from the
  Commander entirely — exactly mirrors E2E-02 but without needing the
  full 8-container stack, useful when E2E-02 fails and you need to know
  if the issue is upstream (leader aggregation) or downstream (dashboard
  ingestion).
- **Input:** real broker, real leader, real or synthetic drone, real dashboard.
- **Output:** `GET /api/nodes/{drone_id}` returns telemetry; node's
  parent leader shows a non-null `avg_battery_pct` reflecting the
  drone's reported battery.
- **Pass / Fail:** as stated.
- **Type:** Timing-sensitive.

### SI-03 — Commander ↔ Dashboard: approval pending visibility without Swarm
- **Repos:** Commander_Repo + Dashboard_Repo
- **Description:** Run a real commander + real dashboard, no swarm leader
  running at all. Inject a HIGH severity fire (commander has no SWARM_LEAD
  nodes registered, so `NoRespondersRule` should also fire). Confirm the
  dashboard surfaces the pending approval via its REST API / event log,
  entirely independent of whether any swarm leader exists to eventually
  receive the approved command.
- **Goal:** Tests the approval-visibility half of E2E-04 in isolation —
  if the operator can't even SEE a pending approval, it doesn't matter
  whether the downstream dispatch logic works.
- **Input:** real broker, real commander (zero swarm leaders registered),
  real dashboard. Injected HIGH fire.
- **Output:** dashboard's pending-approvals endpoint (or event log)
  shows the `ESCALATE_FIRE` (or equivalent) pending command within a
  reasonable timeout.
- **Pass / Fail:** as stated.
- **Type:** Timing-sensitive.

---

# LAYER 5 — END-TO-END (E2E) TESTS

These are the 5 scenarios already implemented in
`Integration_Tests/orchestrator/scenarios/`, run via the Test Orchestrator
UI against the full Docker Compose stack. Full design rationale for each
lives in that scenario's own source file; this section gives the
canonical summary so this document remains the single complete index.

### E2E-01 — Fire Dispatch End-to-End
- **Layer:** E2E
- **Repos:** All (Commander, Swarm ×2 leaders + drones, Dashboard)
- **Description:** A simulated sensor fire event flows through Commander
  RuleEngine dispatch, Swarm Leader tactical assignment, drone command
  execution, and back out as drone telemetry — the full operational loop.
- **Goal:** Validates PROJECT_MAP.md's G-05 — the single most important
  end-to-end guarantee the system makes.
- **Input:** harness-published `FireEvent` on `wfc/events/fire`.
- **Output:** 6 independently-checkable stages, see scenario source for
  exact match conditions on each.
- **Pass condition:** all 6 stages PASSED.
- **Fail condition:** any stage TIMEOUT/FAILED — the orchestrator UI shows
  exactly which one, with the option to skip and continue testing
  downstream stages independently.
- **Type:** Timing-sensitive, runs against live Docker containers.
- **File:** `Integration_Tests/orchestrator/scenarios/scenario_1_fire_dispatch.py`

### E2E-02 — Telemetry Aggregation & Dashboard Visibility
- **Description:** Synthetic drone telemetry → leader's
  `SwarmStatusSnapshot` → dashboard REST/event log, isolating the
  leader+dashboard pipeline from real drone hardware/physics simulation.
- **Goal:** Validates G-04, G-06, G-09.
- **Pass condition:** all 4 stages PASSED.
- **File:** `Integration_Tests/orchestrator/scenarios/scenario_2_telemetry_aggregation.py`

### E2E-03 — Leader Election Failover
- **Description:** Simulates the active swarm leader going OFFLINE,
  verifies a backup correctly runs the bully election protocol, wins,
  announces itself, and resumes publishing status snapshots.
- **Goal:** Validates G-07, G-08.
- **Pass condition:** all 5 stages PASSED.
- **Notes:** Requires ≥2 leaders configured as mutual backup peers
  (`sl-A-01`/`sl-A-02` in the default compose file). With only 1 leader
  deployed, stages B-E correctly TIMEOUT — that result means "no backup
  configured," not "election logic broken."
- **File:** `Integration_Tests/orchestrator/scenarios/scenario_3_leader_election.py`

### E2E-04 — Human Approval Gate
- **Description:** A high-risk command (ABORT_MISSION) is intercepted by
  the ApprovalGate, surfaced to the dashboard, and only forwarded to the
  field after explicit operator approval via the real REST API.
- **Goal:** Also regression-tests the `decision: str` wire-format fix
  (see RI-DASH-001 for the narrower repo-level version of this same check).
- **Pass condition:** all 5 stages PASSED.
- **Notes:** Stage A cannot reach into the Commander's RuleEngine
  internals to force a real approval-required decision from inside the
  engine — it raises intent on an observability-only topic. If the
  ApprovalGate's real trigger condition changes, this stage may need
  adjustment to match.
- **File:** `Integration_Tests/orchestrator/scenarios/scenario_4_approval_gate.py`

### E2E-05 — Node Lifecycle, Heartbeat & LWT Crash Detection
- **Description:** A throwaway fake node joins exactly as `BaseNode` does
  (LWT registered before connect), heartbeats, then is killed
  ungracefully (raw socket close, not a clean MQTT disconnect) to verify
  the broker's Last Will fires and the dashboard reflects OFFLINE within
  the documented timeout.
- **Goal:** Validates G-02. Most likely scenario to catch a silent LWT
  wiring regression (e.g. someone moves `connect()` before `will_set()`).
- **Pass condition:** all 5 stages PASSED.
- **File:** `Integration_Tests/orchestrator/scenarios/scenario_5_node_lifecycle.py`

---

# Coverage gaps (known, as of this writing)

This section is itself part of the protocol — an honest list of what is
NOT yet tested at any layer, so it isn't mistaken for "tested and passing."

- **Concurrent multi-fire scenarios.** `PriorityRule`'s preemption logic
  (U-RULE-012) is unit-testable in isolation, but no integration or E2E
  test yet exercises two simultaneous real fires competing for the same
  leader pool.
- **Backup Commander failover** (as opposed to Swarm Leader failover,
  which E2E-03 covers). No test yet verifies the backup commander
  actually takes over central command duties if the central commander dies.
- **Physical engine** (`Swarm_Repo/action/`: gps.py, wind.py, movement.py,
  sensors.py, resources.py, scouting.py, suppression.py). None of this
  Stefan-Boltzmann/Dryden-turbulence/GPS-noise simulation code has any
  test coverage at any layer in this document yet — it's a large,
  numerically dense module that deserves its own dedicated unit test pass.
- **Database persistence layer** (`core/persistence/`). No test yet
  verifies that hydrating from a real SQLite database on restart actually
  reconstructs identical in-memory state to what was persisted.
- **Concurrent approval expiry vs. operator decision race.** What happens
  if `expire_stale()` and an incoming operator APPROVED decision for the
  same `pending_id` are processed in close succession? No test covers
  this race today.
- **Reliability under packet loss / broker restart mid-flow.** All
  current tests assume a stable broker connection throughout. No chaos
  testing (broker restart, network partition) exists at any layer.
