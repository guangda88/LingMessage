"""Tests for SDT Registry module."""

from __future__ import annotations

from pathlib import Path

import pytest

from lingmessage.lingbus import LingBus
from lingmessage.sdt_registry import (
    SDTEntry,
    create_sdt_registry_table,
    get_sdt_stats,
    list_sdts,
    register_sdt,
    update_sdt_run,
)


@pytest.fixture
def bus(tmp_path: Path) -> LingBus:
    b = LingBus(bus_dir=tmp_path / "bus", throttle=False)
    yield b
    b.close()


class TestCreateTable:
    def test_creates_table(self, bus: LingBus) -> None:
        create_sdt_registry_table(bus)
        rows = bus.execute_readonly(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sdt_registry'"
        )
        assert len(rows) == 1

    def test_idempotent(self, bus: LingBus) -> None:
        create_sdt_registry_table(bus)
        create_sdt_registry_table(bus)  # should not raise
        rows = bus.execute_readonly(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sdt_registry'"
        )
        assert len(rows) == 1


class TestRegisterSDT:
    def test_register_new(self, bus: LingBus) -> None:
        entry = SDTEntry(
            member="lingmessage",
            sdt_id="SDT-lm-001",
            name="LingBus 健康巡检",
            description="检查消息总线完整性",
            direction="方向2",
            priority="P1",
            interval_minutes=360,
            risk_level="medium",
            type="monitor",
            exit_condition="总线健康",
            external_verification="L1: cli health输出",
        )
        register_sdt(bus, entry)
        entries = list_sdts(bus)
        assert len(entries) == 1
        assert entries[0]["member"] == "lingmessage"
        assert entries[0]["sdt_id"] == "SDT-lm-001"
        assert entries[0]["name"] == "LingBus 健康巡检"
        assert entries[0]["priority"] == "P1"
        assert entries[0]["external_verification"] == "L1: cli health输出"

    def test_register_update(self, bus: LingBus) -> None:
        entry = SDTEntry(member="lingflow", sdt_id="SDT-lf-001", name="旧名称")
        register_sdt(bus, entry)
        entry.name = "新名称"
        register_sdt(bus, entry)
        entries = list_sdts(bus, member="lingflow")
        assert len(entries) == 1
        assert entries[0]["name"] == "新名称"

    def test_register_multiple_members(self, bus: LingBus) -> None:
        for m, sid in [("lingmessage", "SDT-lm-001"), ("lingflow", "SDT-lf-001")]:
            register_sdt(bus, SDTEntry(member=m, sdt_id=sid, name=f"{m} task"))
        assert len(list_sdts(bus)) == 2
        assert len(list_sdts(bus, member="lingmessage")) == 1


class TestListSDTs:
    def test_list_empty(self, bus: LingBus) -> None:
        assert list_sdts(bus) == []

    def test_list_filter_by_member(self, bus: LingBus) -> None:
        register_sdt(bus, SDTEntry(member="lingmessage", sdt_id="SDT-lm-001", name="A"))
        register_sdt(bus, SDTEntry(member="lingflow", sdt_id="SDT-lf-001", name="B"))
        results = list_sdts(bus, member="lingmessage")
        assert len(results) == 1
        assert results[0]["member"] == "lingmessage"

    def test_list_filter_by_status(self, bus: LingBus) -> None:
        register_sdt(bus, SDTEntry(member="lingmessage", sdt_id="SDT-lm-001", name="A", status="active"))
        register_sdt(bus, SDTEntry(member="lingmessage", sdt_id="SDT-lm-002", name="B", status="retired"))
        results = list_sdts(bus, status="active")
        assert len(results) == 1
        assert results[0]["sdt_id"] == "SDT-lm-001"


class TestUpdateRun:
    def test_update_success(self, bus: LingBus) -> None:
        register_sdt(bus, SDTEntry(member="lingmessage", sdt_id="SDT-lm-001", name="test"))
        update_sdt_run(bus, "lingmessage", "SDT-lm-001", result="success")
        entries = list_sdts(bus, member="lingmessage")
        assert entries[0]["last_result"] == "success"
        assert entries[0]["consecutive_runs"] == 1
        assert entries[0]["last_run"] != ""

    def test_update_failure_resets_counter(self, bus: LingBus) -> None:
        register_sdt(bus, SDTEntry(member="lingmessage", sdt_id="SDT-lm-001", name="test"))
        update_sdt_run(bus, "lingmessage", "SDT-lm-001", result="success")
        update_sdt_run(bus, "lingmessage", "SDT-lm-001", result="success")
        entries = list_sdts(bus, member="lingmessage")
        assert entries[0]["consecutive_runs"] == 2

        update_sdt_run(bus, "lingmessage", "SDT-lm-001", result="failed", increment_runs=False)
        entries = list_sdts(bus, member="lingmessage")
        assert entries[0]["consecutive_runs"] == 0
        assert entries[0]["last_result"] == "failed"


class TestStats:
    def test_stats_empty(self, bus: LingBus) -> None:
        stats = get_sdt_stats(bus)
        assert stats["total"] == 0

    def test_stats_with_entries(self, bus: LingBus) -> None:
        register_sdt(bus, SDTEntry(
            member="lingmessage", sdt_id="SDT-lm-001", name="A",
            status="active", external_verification="L1: test",
        ))
        register_sdt(bus, SDTEntry(
            member="lingflow", sdt_id="SDT-lf-001", name="B",
            status="active", external_verification="",
        ))
        update_sdt_run(bus, "lingmessage", "SDT-lm-001", result="success")

        stats = get_sdt_stats(bus)
        assert stats["total"] == 2
        assert stats["active"] == 2
        assert stats["execution_rate"] == 0.5  # 1 out of 2 executed
        assert stats["success_rate"] == 0.5    # 1 out of 2 active succeeded
        assert stats["external_verification_rate"] == 0.5  # 1 out of 2

    def test_stats_per_member(self, bus: LingBus) -> None:
        register_sdt(bus, SDTEntry(member="lingmessage", sdt_id="SDT-lm-001", name="A"))
        register_sdt(bus, SDTEntry(member="lingflow", sdt_id="SDT-lf-001", name="B"))
        stats = get_sdt_stats(bus)
        assert "by_member" in stats
        assert stats["by_member"]["lingmessage"]["total"] == 1
        assert stats["by_member"]["lingflow"]["total"] == 1

    def test_stats_filter_by_member(self, bus: LingBus) -> None:
        register_sdt(bus, SDTEntry(member="lingmessage", sdt_id="SDT-lm-001", name="A"))
        register_sdt(bus, SDTEntry(member="lingflow", sdt_id="SDT-lf-001", name="B"))
        stats = get_sdt_stats(bus, member="lingmessage")
        assert stats["total"] == 1
        assert "by_member" not in stats
