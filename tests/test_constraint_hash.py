from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from lingmessage.constraint_hash import (
    check_and_alert,
    create_hash_registry_table,
    detect_changes,
    file_sha256,
    get_current_hashes,
    record_hashes,
    snapshot_member,
)
from lingmessage.lingbus import LingBus


@pytest.fixture
def bus(tmp_path: Path) -> LingBus:
    b = LingBus(bus_dir=tmp_path / "bus", throttle=False)
    yield b
    b.close()


@pytest.fixture
def member_dir(tmp_path: Path) -> Path:
    d = tmp_path / "lingflow"
    d.mkdir()
    return d


class TestFileSha256:
    def test_computes_hash(self, tmp_path: Path) -> None:
        f = tmp_path / "test.md"
        f.write_text("hello world\n", encoding="utf-8")
        h = file_sha256(f)
        expected = hashlib.sha256(b"hello world\n").hexdigest()
        assert h == expected

    def test_returns_empty_for_missing(self, tmp_path: Path) -> None:
        h = file_sha256(tmp_path / "nonexistent.md")
        assert h == ""


class TestSnapshotMember:
    def test_snapshots_crush_md(self, member_dir: Path) -> None:
        (member_dir / "CRUSH.md").write_text("# Test CRUSH", encoding="utf-8")
        entries = snapshot_member("lingflow", member_dir=str(member_dir))
        assert len(entries) == 1
        assert entries[0].filename == "CRUSH.md"
        assert entries[0].member == "lingflow"
        assert len(entries[0].hash_sha256) == 64

    def test_snapshots_both_files(self, member_dir: Path) -> None:
        (member_dir / "CRUSH.md").write_text("# CRUSH", encoding="utf-8")
        (member_dir / "AGENTS.md").write_text("# AGENTS", encoding="utf-8")
        entries = snapshot_member("lingflow", member_dir=str(member_dir))
        assert len(entries) == 2
        names = {e.filename for e in entries}
        assert names == {"CRUSH.md", "AGENTS.md"}

    def test_skips_missing_files(self, member_dir: Path) -> None:
        entries = snapshot_member("lingflow", member_dir=str(member_dir))
        assert len(entries) == 0


class TestRecordAndDetect:
    def test_record_stores_entries(self, bus: LingBus, member_dir: Path) -> None:
        create_hash_registry_table(bus)
        (member_dir / "CRUSH.md").write_text("# v1", encoding="utf-8")
        entries = snapshot_member("lingflow", member_dir=str(member_dir))
        count = record_hashes(bus, entries)
        assert count == 1
        hashes = get_current_hashes(bus, member="lingflow")
        assert len(hashes) == 1

    def test_detect_new_file(self, bus: LingBus, member_dir: Path) -> None:
        create_hash_registry_table(bus)
        (member_dir / "CRUSH.md").write_text("# v1", encoding="utf-8")
        entries = snapshot_member("lingflow", member_dir=str(member_dir))
        changes = detect_changes(bus, entries)
        assert len(changes) == 1
        assert changes[0]["change_type"] == "new"

    def test_detect_no_change(self, bus: LingBus, member_dir: Path) -> None:
        create_hash_registry_table(bus)
        (member_dir / "CRUSH.md").write_text("# v1", encoding="utf-8")
        entries = snapshot_member("lingflow", member_dir=str(member_dir))
        record_hashes(bus, entries)
        entries2 = snapshot_member("lingflow", member_dir=str(member_dir))
        changes = detect_changes(bus, entries2)
        assert len(changes) == 0

    def test_detect_modification(self, bus: LingBus, member_dir: Path) -> None:
        create_hash_registry_table(bus)
        crush = member_dir / "CRUSH.md"
        crush.write_text("# v1", encoding="utf-8")
        entries = snapshot_member("lingflow", member_dir=str(member_dir))
        record_hashes(bus, entries)
        crush.write_text("# v2 modified", encoding="utf-8")
        entries2 = snapshot_member("lingflow", member_dir=str(member_dir))
        changes = detect_changes(bus, entries2)
        assert len(changes) == 1
        assert changes[0]["change_type"] == "modified"
        assert changes[0]["old_hash"] != changes[0]["new_hash"]


class TestCheckAndAlert:
    def test_first_run_records_without_alert(self, bus: LingBus, member_dir: Path) -> None:
        (member_dir / "CRUSH.md").write_text("# v1", encoding="utf-8")
        from unittest.mock import patch
        members_dirs = {"lingflow": str(member_dir)}
        with patch("lingmessage.constraint_hash._MEMBER_DIRS", members_dirs):
            changes = check_and_alert(bus)
        stats = bus.stats()
        assert stats["threads"] == 0  # no alert for first-time registration
        hashes = get_current_hashes(bus)
        assert len(hashes) == 1
        assert len(changes) == 1  # returns the "new" changes
        assert changes[0]["change_type"] == "new"

    def test_detects_and_alerts_change(self, bus: LingBus, member_dir: Path) -> None:
        crush = member_dir / "CRUSH.md"
        crush.write_text("# v1", encoding="utf-8")

        from unittest.mock import patch
        members_dirs = {"lingflow": str(member_dir)}
        with patch("lingmessage.constraint_hash._MEMBER_DIRS", members_dirs):
            check_and_alert(bus)
            crush.write_text("# v2 modified", encoding="utf-8")
            changes = check_and_alert(bus)
            assert len(changes) == 1
            assert changes[0]["change_type"] == "modified"

    def test_no_changes_no_alert(self, bus: LingBus, member_dir: Path) -> None:
        (member_dir / "CRUSH.md").write_text("# v1", encoding="utf-8")
        from unittest.mock import patch
        members_dirs = {"lingflow": str(member_dir)}
        with patch("lingmessage.constraint_hash._MEMBER_DIRS", members_dirs):
            check_and_alert(bus)
            changes = check_and_alert(bus)
            assert len(changes) == 0


class TestGetCurrentHashes:
    def test_empty_when_no_records(self, bus: LingBus) -> None:
        create_hash_registry_table(bus)
        hashes = get_current_hashes(bus)
        assert hashes == []

    def test_filter_by_member(self, bus: LingBus, member_dir: Path) -> None:
        create_hash_registry_table(bus)
        (member_dir / "CRUSH.md").write_text("# v1", encoding="utf-8")
        entries = snapshot_member("lingflow", member_dir=str(member_dir))
        record_hashes(bus, entries)
        hashes = get_current_hashes(bus, member="lingflow")
        assert len(hashes) == 1
        hashes2 = get_current_hashes(bus, member="lingclaude")
        assert len(hashes2) == 0
