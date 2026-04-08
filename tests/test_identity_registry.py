"""Tests for IdentityRegistry — Phase 0: unified identity system."""

import pytest
from lingmessage.types import (
    IdentityEntry,
    IdentityRegistry,
    LingIdentity,
    SourceType,
)


class TestIdentityEntry:
    def test_to_dict_roundtrip(self):
        entry = IdentityEntry(
            identity=LingIdentity.LINGFLOW,
            display_name="灵通",
            mcp_server_key="lingtong",
            mcp_command="lingflow-mcp",
            tools=("list_skills", "run_skill"),
        )
        d = entry.to_dict()
        assert d["identity"] == "lingflow"
        assert d["display_name"] == "灵通"
        assert d["mcp_server_key"] == "lingtong"
        assert d["mcp_command"] == "lingflow-mcp"
        assert d["tools"] == ["list_skills", "run_skill"]
        assert d["source_type"] == "inferred"

    def test_from_dict(self):
        d = {
            "identity": "lingclaude",
            "display_name": "灵克",
            "mcp_server_key": "lingke",
            "mcp_command": "lingclaude-mcp",
            "mcp_args": [],
            "tools": ["read_file", "write_file"],
            "source_type": "verified",
            "process_status": "running",
        }
        entry = IdentityEntry.from_dict(d)
        assert entry.identity == LingIdentity.LINGCLAUDE
        assert entry.display_name == "灵克"
        assert entry.tools == ("read_file", "write_file")
        assert entry.source_type == SourceType.VERIFIED
        assert entry.process_status == "running"

    def test_from_dict_minimal(self):
        d = {"identity": "lingyi", "display_name": "灵依"}
        entry = IdentityEntry.from_dict(d)
        assert entry.identity == LingIdentity.LINGYI
        assert entry.mcp_server_key == ""
        assert entry.tools == ()
        assert entry.process_status == "unknown"

    def test_to_dict_omits_empty_fields(self):
        entry = IdentityEntry(identity=LingIdentity.LINGMINOPT, display_name="灵极优")
        d = entry.to_dict()
        assert "mcp_server_key" not in d
        assert "mcp_command" not in d
        assert "tools" not in d

    def test_frozen(self):
        entry = IdentityEntry(identity=LingIdentity.LINGFLOW, display_name="灵通")
        with pytest.raises(AttributeError):
            entry.display_name = "改名"


class TestIdentityRegistry:
    def test_default_has_all_identities(self):
        reg = IdentityRegistry.default()
        all_ids = [i for i in LingIdentity if i != LingIdentity.ALL]
        assert len(reg.list_all()) == len(all_ids)
        for identity in all_ids:
            assert reg.get(identity) is not None

    def test_get_by_server_key(self):
        reg = IdentityRegistry.default()
        entry = reg.get_by_server_key("lingtong")
        assert entry is not None
        assert entry.identity == LingIdentity.LINGFLOW
        assert entry.display_name == "灵通"

    def test_get_by_server_key_not_found(self):
        reg = IdentityRegistry.default()
        assert reg.get_by_server_key("nonexistent") is None

    def test_get_by_value(self):
        reg = IdentityRegistry.default()
        entry = reg.get_by_value("lingflow")
        assert entry is not None
        assert entry.identity == LingIdentity.LINGFLOW

    def test_get_by_value_invalid(self):
        reg = IdentityRegistry.default()
        assert reg.get_by_value("nonsense") is None

    def test_find_tool_provider(self):
        reg = IdentityRegistry.default()
        providers = reg.find_tool_provider("knowledge_search")
        assert len(providers) >= 1
        assert any(p.identity == LingIdentity.LINGZHI for p in providers)

    def test_find_tool_provider_no_match(self):
        reg = IdentityRegistry.default()
        assert reg.find_tool_provider("nonexistent_tool") == []

    def test_list_active_empty(self):
        reg = IdentityRegistry.default()
        assert reg.list_active() == []

    def test_list_active(self):
        reg = IdentityRegistry.default()
        reg.update_status(LingIdentity.LINGFLOW, "running")
        reg.update_status(LingIdentity.LINGYI, "running")
        active = reg.list_active()
        assert len(active) == 2
        assert all(e.process_status == "running" for e in active)

    def test_register_new_entry(self):
        reg = IdentityRegistry()
        entry = IdentityEntry(
            identity=LingIdentity.LINGRESEARCH,
            display_name="灵研",
            mcp_server_key="lingresearch",
            tools=("add_intel",),
        )
        reg.register(entry)
        assert reg.get(LingIdentity.LINGRESEARCH) == entry
        assert reg.get_by_server_key("lingresearch") == entry

    def test_update_status(self):
        reg = IdentityRegistry.default()
        assert reg.get(LingIdentity.LINGFLOW).process_status == "unknown"
        reg.update_status(LingIdentity.LINGFLOW, "running")
        updated = reg.get(LingIdentity.LINGFLOW)
        assert updated.process_status == "running"
        assert updated.last_heartbeat != ""

    def test_update_status_preserves_other_fields(self):
        reg = IdentityRegistry.default()
        original = reg.get(LingIdentity.LINGFLOW)
        reg.update_status(LingIdentity.LINGFLOW, "stopped")
        updated = reg.get(LingIdentity.LINGFLOW)
        assert updated.display_name == original.display_name
        assert updated.tools == original.tools
        assert updated.mcp_server_key == original.mcp_server_key

    def test_serialization_roundtrip(self):
        reg = IdentityRegistry.default()
        reg.update_status(LingIdentity.LINGFLOW, "running")
        d = reg.to_dict()
        restored = IdentityRegistry.from_dict(d)
        assert len(restored.list_all()) == len(reg.list_all())
        assert restored.get(LingIdentity.LINGFLOW).process_status == "running"
        assert restored.get(LingIdentity.LINGYI).process_status == "unknown"

    def test_from_dict_ignores_invalid_identity(self):
        d = {
            "entries": {
                "lingflow": {"identity": "lingflow", "display_name": "灵通"},
                "fake_agent": {"identity": "fake_agent", "display_name": "假人"},
            }
        }
        reg = IdentityRegistry.from_dict(d)
        assert reg.get(LingIdentity.LINGFLOW) is not None
        assert reg.get_by_value("fake_agent") is None

    def test_default_entry_display_names_match_identity_names(self):
        reg = IdentityRegistry.default()
        for entry in reg.list_all():
            assert entry.display_name != ""
            assert entry.identity.value != ""


class TestIdentityRegistryAlignment:
    def test_all_entries_have_consistent_identity_value(self):
        reg = IdentityRegistry.default()
        for entry in reg.list_all():
            assert entry.identity.value in {
                "lingflow", "lingclaude", "lingyi", "lingzhi",
                "lingtongask", "lingxi", "lingminopt", "lingresearch",
                "lingyang", "zhibridge",
            }

    def test_server_keys_are_unique(self):
        reg = IdentityRegistry.default()
        keys = [e.mcp_server_key for e in reg.list_all() if e.mcp_server_key]
        assert len(keys) == len(set(keys))

    def test_tool_provider_finds_all_registered_tools(self):
        reg = IdentityRegistry.default()
        for entry in reg.list_all():
            for tool in entry.tools:
                providers = reg.find_tool_provider(tool)
                assert any(p.identity == entry.identity for p in providers), \
                    f"Tool {tool} not found for {entry.identity.value}"

    def test_empty_registry_serialization(self):
        reg = IdentityRegistry(entries={})
        d = reg.to_dict()
        loaded = IdentityRegistry.from_dict(d)
        assert len(loaded.list_all()) == len(reg.list_all())

    def test_register_overwrites_existing(self):
        reg = IdentityRegistry.default()
        entry2 = IdentityEntry(
            identity=LingIdentity.LINGFLOW,
            display_name="灵通v2",
            process_status="running",
        )
        original = reg.get(LingIdentity.LINGFLOW)
        assert original.display_name == "灵通"
        reg.register(entry2)
        assert reg.get(LingIdentity.LINGFLOW).display_name == "灵通v2"

    def test_update_status_not_in_registry(self):
        reg = IdentityRegistry.default()
        reg._entries = dict(reg._entries)
        reg._entries.pop(LingIdentity.LINGYI, None)
        reg.update_status(LingIdentity.LINGYI, "running")
        assert reg.get(LingIdentity.LINGYI) is None

    def test_list_active_various_statuses(self):
        reg = IdentityRegistry.default()
        reg.update_status(LingIdentity.LINGFLOW, "running")
        reg.update_status(LingIdentity.LINGYI, "stopped")
        active = reg.list_active()
        assert all(e.process_status == "running" for e in active)
        identities = [e.identity for e in active]
        assert LingIdentity.LINGFLOW in identities
        assert LingIdentity.LINGYI not in identities
