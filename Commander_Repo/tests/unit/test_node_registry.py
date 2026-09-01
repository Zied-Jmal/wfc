# TEST: U-REG-001 to U-REG-007

from __future__ import annotations

from wfc_shared.enums.capabilities import SWARM_LEAD
from wfc_shared.enums.node_status import ACTIVE, OFFLINE, REGISTERED


class TestNodeRegistry:
    def test_register_defaults_to_registered(self) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        rec = reg.get("sl-1")
        assert rec is not None
        assert rec.status == REGISTERED

    def test_heartbeat_from_unregistered_is_ignored(self) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        reg.heartbeat("ghost-node")
        assert not reg.exists("ghost-node")

    def test_heartbeat_grants_active(self) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        rec = reg.get("sl-1")
        assert rec.status == ACTIVE  # pyright: ignore[reportOptionalMemberAccess]

    def test_only_mark_offline_sets_offline(self) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        # Try each mutator and verify none sets OFFLINE
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])  # re-register
        assert reg.get("sl-1").status == ACTIVE  # pyright: ignore[reportOptionalMemberAccess]
        reg.heartbeat("sl-1")
        assert reg.get("sl-1").status == ACTIVE  # pyright: ignore[reportOptionalMemberAccess]
        reg.assign_job("sl-1", "fire-1")
        assert reg.get("sl-1").status == ACTIVE  # pyright: ignore[reportOptionalMemberAccess]
        reg.release_job("sl-1")
        assert reg.get("sl-1").status == ACTIVE  # pyright: ignore[reportOptionalMemberAccess]
        # Now mark_offline should work
        reg.mark_offline("sl-1")
        assert reg.get("sl-1").status == OFFLINE  # pyright: ignore[reportOptionalMemberAccess]

    def test_register_clears_stale_job_when_recovering_from_offline(self) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        reg.assign_job("sl-1", "fire-1")
        reg.mark_offline("sl-1")
        # Re-register from OFFLINE should clear current_job
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        assert reg.get("sl-1").current_job is None  # pyright: ignore[reportOptionalMemberAccess]

    def test_register_preserves_job_when_re_registering_active(self) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        reg.assign_job("sl-1", "fire-2")
        # Re-register while ACTIVE should not clear job
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        assert reg.get("sl-1").current_job == "fire-2"  # pyright: ignore[reportOptionalMemberAccess]

    def test_get_available_excludes_busy_nodes(self) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD])
        reg.register("sl-2", "SWARM_LEADER", [SWARM_LEAD])
        reg.register("sl-3", "SWARM_LEADER", [SWARM_LEAD])
        reg.heartbeat("sl-1")
        reg.heartbeat("sl-2")
        reg.heartbeat("sl-3")
        reg.assign_job("sl-2", "fire-x")
        available = reg.get_available(SWARM_LEAD)
        assert "sl-2" not in available
        assert "sl-1" in available
        assert "sl-3" in available

    def test_get_closest_respects_idle_only(self) -> None:
        from core.node_registry.registry import NodeRegistry

        reg = NodeRegistry()
        reg.register("sl-1", "SWARM_LEADER", [SWARM_LEAD], location=(0.01, 0.01))
        reg.register("sl-2", "SWARM_LEADER", [SWARM_LEAD], location=(1.0, 1.0))
        reg.heartbeat("sl-1")
        reg.heartbeat("sl-2")
        reg.assign_job("sl-1", "fire-y")  # close but busy
        result = reg.get_closest(SWARM_LEAD, (0.0, 0.0), idle_only=True)
        assert result == "sl-2"  # farther but idle
