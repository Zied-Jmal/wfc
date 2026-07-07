# Bugfixes — 2026-07-02

## Bug 1: Double-offset in drone navigation

**Files touched:** `Swarm_Repo/core/tactics/fire_tactics.py`

**Symptom:** Scouts orbited ~178m from the fire instead of the fire center. Fighters dropped water ~120m upwind of the fire (double-offset — leader added 120m, then fighter added another 120m from the offset position).

**Root cause:** `FireTactics.assign_respond_to_fire()`, `assign_reinforce()`, and `reassess()` computed offset positions (orbit offset for scouts, approach offset for fighters) and sent them as `target_pos`. The drone engines (`ScoutActionEngine`, `SuppressionActionEngine`) already compute their own navigation offsets from the received fire position. The leader's pre-computation created a double offset.

**Fix:**
- `assign_respond_to_fire()`: Send raw `fire_pos` instead of `_dest(fire_pos, bearing, orbit_r)` for scouts and `_dest(fire_pos, approach_bearing, 120.0)` for fighters.
- `assign_reinforce()`: Same — send raw `fire_pos` instead of offset positions.
- `reassess()` Rules 3 & 4: Send raw `fire_pos` instead of `_dest(fire_pos, wind_dir, 120.0)` for replacement fighters.

Unused variables (`n_scouts`, `orbit_r`, `approach_bearing`) cleaned up in the affected methods. `_dest()` import preserved for `assign_contain_fire()` and `_hot_flank()` which still use positional offsets intentionally.

---

## Bug 2: `resource_exhaustion` rule references non-existent attribute

**Files touched:** `Commander_Repo/core/rule_engine/rules/resource_exhaustion.py`

**Symptom:** Every telemetry evaluation cycle logged `rule 'resource_exhaustion' raised: 'SwarmStatusSnapshot' object has no attribute 'avg_payload_pct'`.

**Root cause:** The rule referenced `snap.avg_payload_pct` but `SwarmStatusSnapshot` has `avg_payload_litres` (float, litres), not a percentage field.

**Fix:** Changed `snap.avg_payload_pct < 0.1` to `snap.avg_payload_litres < 1.5`. Threshold 1.5 L matches the `LOW_PAYLOAD_L` constant in `fire_tactics.py` that controls return-to-base decisions.

---

## Bug 3: `NoneType is not iterable` when `location_coords` is null

**Files touched:** `Swarm_Repo/core/node/swarm_leader_node.py`

**Symptom:** When `location_coords` is sent as `null` in the command payload (e.g., from malformed fire inject), the leader crashes on `tuple(None)` → `TypeError: 'NoneType' object is not iterable`.

**Root cause:** Three command handlers (`_cmd_respond_to_fire`, `_cmd_contain_fire`, `_cmd_reinforce_fire`) used `tuple(payload.get("location_coords", [0.0, 0.0]))`. `dict.get()` returns the stored value (`None`) when the key exists, not the default — so `tuple(None)` raised TypeError.

**Fix:** Changed all three handlers to:
```python
raw = payload.get("location_coords")
fire_pos = tuple(raw) if raw else (0.0, 0.0)
```
This safely falls back to `(0.0, 0.0)` when the field is missing or explicitly null.
