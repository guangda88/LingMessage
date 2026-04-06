"""灵信适配器 — 将灵字辈各项目的现有数据桥接到灵信邮箱

每个灵项目有自己的数据输出格式和存储路径。
适配器的职责：读取各项目的输出 → 转换为灵信 Message → 写入共享邮箱。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lingmessage.mailbox import Mailbox
from lingmessage.types import (
    Channel,
    LingIdentity,
    MessageType,
    SourceType,
    create_message,
)


class LingFlowAdapter:
    """灵通日报 → 灵信 shared-infra 频道"""

    def __init__(self, mailbox: Mailbox, lingflow_root: Path | None = None) -> None:
        self._mailbox = mailbox
        self._root = lingflow_root or Path(os.environ.get("LINGFLOW_ROOT", "/home/ai/LingFlow"))

    def _daily_reports_dir(self) -> Path:
        return self._root / ".lingflow" / "intelligence" / "reports" / "daily"

    def _feedback_path(self) -> Path:
        return self._root / ".lingflow" / "feedback" / "feedbacks.json"

    def post_daily_reports(self) -> list[str]:
        posted: list[str] = []
        reports_dir = self._daily_reports_dir()
        if not reports_dir.exists():
            return posted
        for rp in sorted(reports_dir.glob("daily_report_*.json")):
            try:
                data = json.loads(rp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            date_str = data.get("date", rp.stem.replace("daily_report_", ""))
            summary = data.get("summary", "")
            highlights = data.get("highlights", [])
            concerns = data.get("concerns", [])
            body_parts: list[str] = [f"## 灵通日报 {date_str}\n"]
            if summary:
                body_parts.append(f"**摘要**: {summary}\n")
            if highlights:
                body_parts.append("**亮点**:")
                for h in highlights[:5]:
                    body_parts.append(f"  - {h}")
                body_parts.append("")
            if concerns:
                body_parts.append("**关注**:")
                for c in concerns[:5]:
                    body_parts.append(f"  - {c}")
                body_parts.append("")
            metrics = data.get("metrics", {})
            if metrics:
                body_parts.append(f"**指标**: mentions={metrics.get('total_mentions', 0)}, "
                                  f"stars={metrics.get('star_count', 0)}")
            body = "\n".join(body_parts)
            msg = create_message(
                sender=LingIdentity.LINGFLOW,
                recipient=LingIdentity.ALL,
                message_type=MessageType.SUMMARY,
                channel=Channel.SHARED_INFRA,
                subject=f"灵通日报 {date_str}",
                body=body,
                metadata={"source": "daily_report", "date": date_str},
                source_type=SourceType.GENERATED,
                source_trace=f"lingflow:adapter:report:{date_str}",
            )
            self._mailbox.post(msg)
            posted.append(msg.message_id)
        return posted


class LingClaudeIntelAdapter:
    """灵克情报 → 灵信 shared-infra 频道"""

    def __init__(self, mailbox: Mailbox, lingclaude_root: Path | None = None) -> None:
        self._mailbox = mailbox
        self._root = lingclaude_root or Path(os.environ.get("LINGCLAUDE_ROOT", "/home/ai/LingClaude"))

    def _intel_dir(self) -> Path:
        return self._root / ".lingclaude" / "intel"

    def post_digests(self) -> list[str]:
        posted: list[str] = []
        intel_dir = self._intel_dir()
        if not intel_dir.exists():
            return posted
        for dp in sorted(intel_dir.glob("digest_*.json")):
            try:
                data = json.loads(dp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            report_date = data.get("report_date", "")
            summary_text = data.get("summary", "")
            findings = data.get("key_findings", [])
            recommendations = data.get("recommendations", [])
            body_parts: list[str] = [f"## 灵克情报摘要 {report_date}\n"]
            if summary_text:
                body_parts.append(f"**概要**: {summary_text}\n")
            if findings:
                body_parts.append("**关键发现**:")
                for f in findings[:5]:
                    body_parts.append(f"  - {f}")
                body_parts.append("")
            if recommendations:
                body_parts.append("**建议**:")
                for r in recommendations[:5]:
                    body_parts.append(f"  - {r}")
                body_parts.append("")
            cat_counts = data.get("category_counts", {})
            if cat_counts:
                body_parts.append(f"**类别分布**: {cat_counts}")
            body = "\n".join(body_parts)
            msg = create_message(
                sender=LingIdentity.LINGCLAUDE,
                recipient=LingIdentity.ALL,
                message_type=MessageType.SUMMARY,
                channel=Channel.SHARED_INFRA,
                subject=f"灵克情报摘要 {report_date}",
                body=body,
                metadata={"source": "daily_digest", "date": report_date},
                source_type=SourceType.GENERATED,
                source_trace=f"lingclaude:adapter:digest:{report_date}",
            )
            self._mailbox.post(msg)
            posted.append(msg.message_id)
        return posted


class LingYiBriefingAdapter:
    """灵依简报 → 灵信 ecosystem 频道"""

    def __init__(self, mailbox: Mailbox, lingyi_root: Path | None = None) -> None:
        self._mailbox = mailbox
        self._root = lingyi_root or Path(os.environ.get("LINGYI_ROOT", "/home/ai/LingYi"))

    def _intelligence_dir(self) -> Path:
        return self._root / ".lingyi" / "intelligence"

    def post_briefings(self) -> list[str]:
        posted: list[str] = []
        intel_dir = self._intelligence_dir()
        if not intel_dir.exists():
            return posted
        for bp in sorted(intel_dir.glob("briefing_*.json")):
            try:
                data = json.loads(bp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            ts = data.get("timestamp", "")
            body = json.dumps(data, ensure_ascii=False, indent=2)
            msg = create_message(
                sender=LingIdentity.LINGYI,
                recipient=LingIdentity.ALL,
                message_type=MessageType.SUMMARY,
                channel=Channel.ECOSYSTEM,
                subject=f"灵依简报 {ts}",
                body=body,
                metadata={"source": "briefing"},
                source_type=SourceType.GENERATED,
                source_trace=f"lingyi:adapter:briefing:{ts}",
            )
            self._mailbox.post(msg)
            posted.append(msg.message_id)
        return posted
