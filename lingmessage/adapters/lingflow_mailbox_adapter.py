"""灵通日报适配器 — 将灵通日报桥接到灵信邮箱"""

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


class lingflowAdapter:
    """灵通日报 → 灵信 shared-infra 频道"""

    def __init__(self, mailbox: Mailbox, lingflow_root: Path | None = None) -> None:
        self._mailbox = mailbox
        self._root = lingflow_root or Path(os.environ.get("LINGFLOW_ROOT", "/home/ai/lingflow"))

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
