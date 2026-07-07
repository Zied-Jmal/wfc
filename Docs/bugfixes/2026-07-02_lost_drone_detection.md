# Bugfix — 2026-07-02: Lost/Offline Drone Detection & Replacement

**Files touched:** `Swarm_Repo/core/node/swarm_leader_node.py`, `Swarm_Repo/core/tactics/fire_tactics.py`

## Problem

Two gaps left drones dead in the water without recovery:

1. **No per-drone task reassignment** — When a scout or fighter went OFFLINE (LWT or crash), the leader unregistered it but never dispatched a replacement. Only the aggregate `SwarmAttritionRule` (lost_drones > 3) could trigger anything — a single lost drone was invisible to the system.

2. **No hung-drone detection** — If a drone's telemetry loop stalled but its MQTT connection remained open, no mechanism detected it. The Commander's `HeartbeatMonitor` only watched `SWARM_LEAD` nodes. The Swarm_Repo had no heartbeat monitor (removed from `base_node.py:7-14`). The `DroneRegistry.get_lost()` method existed (stale telemetry > 5s) but nothing consumed its output.

## Root cause

- `_on_registry_announce()` in `swarm_leader_node.py` called `unregister()` on OFFLINE drones but didn't check what task the drone was doing — so no replacement was dispatched.
- The `_analysis_loop()` ran `reassess()` (battery/payload swaps) but never checked for completely dead drones.
- No `_current_fire_pos` was stored (only `_current_fire_id`), so even if a replacement was wanted, there was no fire position to send it to.

## Changes

### 1. `_replace_lost_drone()` — new method

Added to `SwarmLeaderNode`. Queries `DroneRegistry.get_idle()` for a same-role drone with battery > 25% and payload > 1.5 L. If found, dispatches `UPDATE_TASK` with the stored `_current_fire_pos`. Logs if no replacement is available.

### 2. Extended `_on_registry_announce()` (OFFLINE path)

Before unregistering, captures the drone's role and last known task. If task was `SCOUTING` or `SUPPRESSING`, calls `_replace_lost_drone()` after unregistering.

### 3. `_check_lost_drones()` — new method for hung detection

Called every analysis tick (2s). Uses `DroneRegistry.get_lost()` (stale telemetry > 5s threshold) with a 15s grace period (`_LOST_GRACE_PERIOD`). After 15s without telemetry, the drone is treated as dead: unregistered, and if it had an active task, a replacement is dispatched. A `_lost_tracker` dict tracks when each drone first went silent, and recovered drones are automatically cleared.

### 4. `_current_fire_pos` — new field

Added to `SwarmLeaderNode.__init__()`. Set in `_cmd_respond_to_fire()`, `_cmd_contain_fire()`, and `_cmd_reinforce_fire()` so the fire position is always available for dispatching replacements.

## Verification

- Killed sd-A-01 (scout) mid-SCOUTING: leader logs `drone sd-A-01 went OFFLINE — unregistered`, `lost drone sd-A-01 had active task SCOUTING — dispatching replacement`, `no idle SCOUT available to replace lost sd-A-01` (expected — only 1 scout in the test setup).
- Full suppression cycle continues unaffected: fd-A-01 delivered all 100L fire.
- All 66 Commander_Repo unit/component/RI tests pass.
- 19/20 Swarm_Repo tests pass (1 pre-existing failure in orbit distribution, unrelated).
