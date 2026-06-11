from __future__ import annotations

from pathlib import Path

import pytest

from lingmessage.capability import (
    CapabilityRegistry,
    ServerCapability,
    _is_active,
)


def _make_cap(
    server_key: str = "test",
    agent_id: str = "lingtest",
    tools: tuple[str, ...] = ("tool_a", "tool_b"),
    heartbeat: str = "",
) -> ServerCapability:
    return ServerCapability(
        server_key=server_key,
        agent_id=agent_id,
        display_name="TestServer",
        tools=tools,
        transport="stdio",
        command="python3",
        registered_at="2026-04-08T00:00:00+00:00",
        last_heartbeat=heartbeat,
    )


class TestServerCapability:
    def test_to_dict_roundtrip(self) -> None:
        cap = _make_cap()
        d = cap.to_dict()
        assert d["server_key"] == "test"
        assert d["agent_id"] == "lingtest"
        assert d["tools"] == ["tool_a", "tool_b"]
        assert d["transport"] == "stdio"

        restored = ServerCapability.from_dict(d)
        assert restored.server_key == cap.server_key
        assert restored.tools == cap.tools
        assert restored.command == cap.command

    def test_minimal_fields(self) -> None:
        cap = ServerCapability(server_key="x", agent_id="y", display_name="Z")
        d = cap.to_dict()
        assert d["tools"] == []
        assert "command" not in d
        assert "url" not in d

    def test_from_dict_extra_fields(self) -> None:
        d = {"server_key": "a", "agent_id": "b", "display_name": "C", "extra": "ignored"}
        cap = ServerCapability.from_dict(d)
        assert cap.server_key == "a"

    def test_frozen(self) -> None:
        cap = _make_cap()
        with pytest.raises(AttributeError):
            cap.server_key = "changed"


class TestCapabilityRegistry:
    def test_register_and_get(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        cap = _make_cap()
        reg.register(cap)
        got = reg.get("test")
        assert got is not None
        assert got.agent_id == "lingtest"
        assert len(got.tools) == 2

    def test_unregister(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        reg.register(_make_cap())
        assert reg.unregister("test") is True
        assert reg.get("test") is None
        assert reg.unregister("test") is False

    def test_heartbeat(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        reg.register(_make_cap())
        assert reg.heartbeat("test") is True
        got = reg.get("test")
        assert got is not None
        assert got.last_heartbeat != ""
        assert reg.heartbeat("nonexistent") is False

    def test_find_tool(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        reg.register(_make_cap("s1", "a1", ("tool_x", "tool_y")))
        reg.register(_make_cap("s2", "a2", ("tool_y", "tool_z")))
        providers = reg.find_tool("tool_y")
        assert len(providers) == 2
        providers_x = reg.find_tool("tool_x")
        assert len(providers_x) == 1
        assert reg.find_tool("nonexistent") == []

    def test_find_tool_best(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        reg.register(_make_cap("s1", "a1", ("tool_a",)))
        best = reg.find_tool_best("tool_a")
        assert best is not None
        assert best.server_key == "s1"
        assert reg.find_tool_best("nonexistent") is None

    def test_list_servers(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        reg.register(_make_cap("s1"))
        reg.register(_make_cap("s2"))
        assert len(reg.list_servers()) == 2

    def test_get_all_tools(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        reg.register(_make_cap("s1", "agent1", ("tool_a", "tool_b")))
        reg.register(_make_cap("s2", "agent1", ("tool_b", "tool_c")))
        reg.register(_make_cap("s3", "agent2", ("tool_d",)))
        tools = reg.get_all_tools()
        assert "agent1" in tools
        assert "tool_a" in tools["agent1"]
        assert "tool_b" in tools["agent1"]
        assert "tool_c" in tools["agent1"]
        assert "tool_d" in tools["agent2"]

    def test_get_routing_table(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        reg.register(_make_cap("s1", "a1", ("tool_a", "tool_b")))
        reg.register(_make_cap("s2", "a2", ("tool_c",)))
        table = reg.get_routing_table()
        assert table["tool_a"] == "s1"
        assert table["tool_b"] == "s1"
        assert table["tool_c"] == "s2"

    def test_stats(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        reg.register(_make_cap("s1", "a1", ("t1", "t2")))
        reg.register(_make_cap("s2", "a2", ("t3",)))
        s = reg.stats()
        assert s["total_servers"] == 2
        assert s["total_tools"] == 3
        assert s["unique_tools"] == 3

    def test_persistence(self, tmp_path: Path) -> None:
        p = tmp_path / "cap.json"
        reg = CapabilityRegistry(_path=p)
        reg.register(_make_cap("s1", "a1", ("tool_x",)))
        assert p.exists()

        reg2 = CapabilityRegistry.load(p)
        got = reg2.get("s1")
        assert got is not None
        assert got.agent_id == "a1"
        assert got.tools == ("tool_x",)

    def test_load_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "nonexistent.json"
        reg = CapabilityRegistry.load(p)
        assert len(reg.list_servers()) == 0

    def test_load_corrupt(self, tmp_path: Path) -> None:
        p = tmp_path / "cap.json"
        p.write_text("not json{{{")
        reg = CapabilityRegistry.load(p)
        assert len(reg.list_servers()) == 0

    def test_merge_from_mcp_registry(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        mcp = {
            "lingtong": ServerCapability(
                server_key="lingtong",
                agent_id="lingflow",
                display_name="灵通",
                tools=("list_skills", "run_skill"),
                command="python3",
            ),
            "lingke": ServerCapability(
                server_key="lingke",
                agent_id="lingclaude",
                display_name="灵克",
                tools=("read_file", "write_file"),
                command="node",
            ),
        }
        merged = reg.merge_from_mcp_registry(mcp)
        assert merged == 2
        assert reg.get("lingtong") is not None
        assert reg.get("lingke") is not None

        merged2 = reg.merge_from_mcp_registry(mcp)
        assert merged2 == 0

    def test_register_sets_heartbeat(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        cap = ServerCapability(server_key="s1", agent_id="a1", display_name="S1")
        reg.register(cap)
        got = reg.get("s1")
        assert got is not None
        assert got.registered_at != ""
        assert got.last_heartbeat != ""

    def test_overwrite_on_register(self, tmp_path: Path) -> None:
        reg = CapabilityRegistry(_path=tmp_path / "cap.json")
        reg.register(_make_cap("s1", "v1", ("t1",)))
        reg.register(_make_cap("s1", "v2", ("t1", "t2", "t3")))
        got = reg.get("s1")
        assert got is not None
        assert got.agent_id == "v2"
        assert len(got.tools) == 3


class TestIsActive:
    def test_active_with_recent_heartbeat(self) -> None:
        from datetime import datetime, timezone
        hb = datetime.now(timezone.utc).isoformat()
        cap = _make_cap(heartbeat=hb)
        assert _is_active(cap) is True

    def test_inactive_with_stale_heartbeat(self) -> None:
        cap = _make_cap(heartbeat="2020-01-01T00:00:00+00:00")
        assert _is_active(cap) is False

    def test_inactive_without_heartbeat(self) -> None:
        cap = _make_cap()
        assert _is_active(cap) is False

    def test_custom_max_age(self) -> None:
        cap = _make_cap(heartbeat="2020-01-01T00:00:00+00:00")
        assert _is_active(cap, max_age_seconds=9999999999) is True

    def test_invalid_heartbeat(self) -> None:
        cap = _make_cap(heartbeat="not-a-date")
        assert _is_active(cap) is False
