"""Alive detection tests — 灵族成员存活状态检测"""

import subprocess
from pathlib import Path
import pytest

from lingmessage.alive import AliveStatus, check_all, check_member, format_report


class TestCheckMember:
    def test_missing_dir(self, tmp_path: Path):
        result = check_member("lingmessage", home=tmp_path)
        assert result.status == "missing"
        assert not result.dir_exists

    def test_not_git(self, tmp_path: Path):
        (tmp_path / "lingmessage").mkdir()
        result = check_member("lingmessage", home=tmp_path)
        assert result.status == "not_git"
        assert result.dir_exists
        assert not result.is_git

    def test_git_repo_with_commit(self, tmp_path: Path):
        d = tmp_path / "lingmessage"
        d.mkdir()
        subprocess.run(["git", "init"], cwd=str(d), capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "test"],
            cwd=str(d), capture_output=True,
            env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                 "PATH": "/usr/bin:/bin"},
        )
        result = check_member("lingmessage", home=tmp_path)
        assert result.status == "active"
        assert result.is_git
        assert result.age_hours < 1
        assert result.commits_7d >= 1

    def test_unknown_member(self, tmp_path: Path):
        with pytest.raises(Exception):
            check_member("nonexistent_member", home=tmp_path)


class TestCheckAll:
    def test_returns_all_members(self, tmp_path: Path):
        results = check_all(home=tmp_path)
        assert len(results) == 11
        identities = {r.identity for r in results}
        assert "lingmessage" not in identities

    def test_all_missing_on_empty_home(self, tmp_path: Path):
        results = check_all(home=tmp_path)
        assert all(r.status == "missing" for r in results)


class TestFormatReport:
    def test_basic_report(self):
        statuses = [
            AliveStatus("lingflow", "灵通", "lingflow", True, True,
                        "2026-04-16 12:00:00 +0800", "abcd1234", 1.0, 5, "active"),
            AliveStatus("zhibridge", "智桥", "zhineng-bridge", True, True,
                        "2026-04-10 01:55:00 +0800", "efgh5678", 165.0, 0, "offline"),
        ]
        report = format_report(statuses)
        assert "🟢" in report
        assert "🔴" in report
        assert "总计: 2 成员" in report

    def test_verbose_report(self):
        statuses = [
            AliveStatus("lingflow", "灵通", "lingflow", True, True,
                        "2026-04-16 12:00:00 +0800", "abcd1234", 1.0, 5, "active"),
        ]
        report = format_report(statuses, verbose=True)
        assert "lingflow" in report
        assert "abcd1234" not in report  # hash not shown in report line

    def test_missing_member(self):
        statuses = [
            AliveStatus("lingweb", "灵网", "lingweb", False, False,
                        "", "", -1, 0, "missing"),
        ]
        report = format_report(statuses)
        assert "❌" in report

    def test_not_git_member(self):
        statuses = [
            AliveStatus("lingweb", "灵网", "lingweb", True, False,
                        "", "", -1, 0, "not_git"),
        ]
        report = format_report(statuses)
        assert "⚠️" in report


class TestAliveStatus:
    def test_to_dict(self):
        s = AliveStatus("lingflow", "灵通", "lingflow", True, True,
                        "2026-04-16", "abcd", 5.123, 10, "active")
        d = s.to_dict()
        assert d["identity"] == "lingflow"
        assert d["age_hours"] == 5.1
        assert d["commits_7d"] == 10
        assert d["status"] == "active"
