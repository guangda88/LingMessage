"""灵依简报适配器 — 将灵依简报桥接到灵信邮箱"""

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


class lingyiBriefingAdapter:
    """灵依简报 → 灵信 ecosystem 频道"""

    def __init__(self, mailbox: Mailbox, lingyi_root: Path | None = None) -> None:
        self._mailbox = mailbox
        self._root = lingyi_root or Path(os.environ.get("LINGYI_ROOT", "/home/ai/lingyi"))

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


def get_lingyi_briefing_adapter(mailbox: Mailbox, **kwargs) -> lingyiBriefingAdapter:
    return lingyiBriefingAdapter(mailbox, **kwargs)
