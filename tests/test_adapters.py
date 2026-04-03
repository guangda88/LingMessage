from __future__ import annotations

import json
from pathlib import Path

from lingmessage.adapters import LingFlowAdapter, LingClaudeIntelAdapter, LingYiBriefingAdapter
from lingmessage.mailbox import Mailbox


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestLingFlowAdapter:
    def test_post_daily_reports(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        reports_dir = tmp_path / ".lingflow" / "intelligence" / "reports" / "daily"
        _write_json(reports_dir / "daily_report_20260403.json", {
            "date": "2026-04-03",
            "summary": "Test summary",
            "highlights": ["Star growth +50"],
            "concerns": [],
            "metrics": {"total_mentions": 10, "star_count": 100},
        })
        adapter = LingFlowAdapter(mailbox, lingflow_root=tmp_path)
        posted = adapter.post_daily_reports()
        assert len(posted) == 1
        msg_files = list((tmp_path / "mailbox" / "threads").rglob("msg_*.json"))
        assert len(msg_files) >= 1

    def test_no_reports_dir(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        adapter = LingFlowAdapter(mailbox, lingflow_root=tmp_path)
        assert adapter.post_daily_reports() == []


class TestLingClaudeIntelAdapter:
    def test_post_digests(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        intel_dir = tmp_path / ".lingclaude" / "intel"
        _write_json(intel_dir / "digest_2026-04-03.json", {
            "report_date": "2026-04-03",
            "summary": "Code quality improving",
            "key_findings": ["Long methods reduced"],
            "recommendations": ["Continue refactoring"],
            "category_counts": {"quality": 3},
        })
        adapter = LingClaudeIntelAdapter(mailbox, lingclaude_root=tmp_path)
        posted = adapter.post_digests()
        assert len(posted) == 1

    def test_no_intel_dir(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        adapter = LingClaudeIntelAdapter(mailbox, lingclaude_root=tmp_path)
        assert adapter.post_digests() == []


class TestLingYiBriefingAdapter:
    def test_post_briefings(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        intel_dir = tmp_path / ".lingyi" / "intelligence"
        _write_json(intel_dir / "briefing_20260403.json", {
            "timestamp": "2026-04-03T12:00:00",
            "lingflow": {"available": True},
            "lingclaude": {"available": True},
        })
        adapter = LingYiBriefingAdapter(mailbox, lingyi_root=tmp_path)
        posted = adapter.post_briefings()
        assert len(posted) == 1

    def test_no_intel_dir(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        adapter = LingYiBriefingAdapter(mailbox, lingyi_root=tmp_path)
        assert adapter.post_briefings() == []
