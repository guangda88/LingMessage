"""灵信议事轮询守护进程

定期扫描 active 讨论，检测未回复的参与者，发送催办通知。
支持多级升级：首次提醒 → 二次催办 → 升级通知。

用法：
    python -m lingmessage.poller              # 前台运行
    python -m lingmessage.poller --once       # 单次扫描
    python -m lingmessage.poller --interval 300  # 自定义间隔（秒）
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

from lingmessage.mailbox import Mailbox
from lingmessage.types import (
    LingIdentity,
    MessageType,
    ThreadStatus,
    sender_display,
)

logger = logging.getLogger("lingmessage.poller")

_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})


def _is_localhost_url(url: str) -> bool:
    """Check if a URL points to localhost only."""
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        return parsed.hostname in _LOCALHOST_HOSTS
    except Exception:
        return False

STATE_FILE = Path.home() / ".lingmessage" / "poller_state.json"

FIRST_REMIND_HOURS = 4
SECOND_REMIND_HOURS = 12
ESCALATE_HOURS = 24

NOTIFY_TIMEOUT_SECONDS = 10
NOTIFY_MAX_RETRIES = 3
NOTIFY_BACKOFF_BASE = 1.0

DELIVERY_LOG = Path.home() / ".lingmessage" / "delivery_failures.log"


class PollerState:
    """轮询状态持久化"""

    def __init__(self, path: Path | None = None):
        self._path = path or STATE_FILE
        self._reminders: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._reminders = data.get("reminders", {})
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load poller state: {e}")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path.write_text(json.dumps({"reminders": self._reminders}, indent=2, ensure_ascii=False))
        except OSError as e:
            logger.warning(f"Failed to save poller state: {e}")

    def get_reminder_level(self, thread_id: str, participant: str) -> int:
        key = f"{thread_id}:{participant}"
        info = self._reminders.get(key, {})
        return info.get("level", 0)

    def record_reminder(self, thread_id: str, participant: str, level: int) -> None:
        key = f"{thread_id}:{participant}"
        self._reminders[key] = {
            "thread_id": thread_id,
            "participant": participant,
            "level": level,
            "last_reminded": datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def cleanup_thread(self, thread_id: str) -> None:
        keys_to_remove = [k for k in self._reminders if k.startswith(f"{thread_id}:")]
        for k in keys_to_remove:
            del self._reminders[k]
        if keys_to_remove:
            self._save()


class DiscussionPoller:
    """议事轮询器"""

    def __init__(
        self,
        mailbox: Mailbox | None = None,
        state: PollerState | None = None,
        first_hours: float = FIRST_REMIND_HOURS,
        second_hours: float = SECOND_REMIND_HOURS,
        escalate_hours: float = ESCALATE_HOURS,
    ):
        self.mailbox = mailbox or Mailbox()
        self.state = state or PollerState()
        self.first_hours = first_hours
        self.second_hours = second_hours
        self.escalate_hours = escalate_hours
        self._running = False
        self._stats = {"scanned": 0, "reminders_sent": 0, "escalations": 0}

    def scan_once(self) -> dict[str, Any]:
        """单次扫描所有 active 讨论"""
        threads = self.mailbox.list_threads(status=ThreadStatus.ACTIVE)
        actions: list[str] = []
        self._stats["scanned"] += len(threads)

        for header in threads:
            thread_actions = self._check_thread(header.thread_id, header)
            actions.extend(thread_actions)

        return {
            "scanned": len(threads),
            "actions": actions,
            "stats": dict(self._stats),
        }

    def _check_thread(self, thread_id: str, header: Any) -> list[str]:
        """检查单个讨论的回复状态"""
        actions: list[str] = []
        try:
            messages = self.mailbox.load_thread_messages(thread_id)
        except Exception as e:
            logger.debug(f"Skipping thread {thread_id}: {e}")
            return actions
        if not messages:
            return actions

        participants = set(header.participants)
        replied: set[str] = set()
        for msg in messages:
            sender_val = msg.sender.value if hasattr(msg.sender, "value") else str(msg.sender)
            replied.add(sender_val)

        waiting = participants - replied - {"all"}
        if not waiting:
            self.state.cleanup_thread(thread_id)
            return actions

        first_msg = messages[0]
        first_time = self._parse_time(first_msg.timestamp)
        if not first_time:
            return actions
        elapsed_hours = (datetime.now(timezone.utc) - first_time).total_seconds() / 3600

        for participant in waiting:
            try:
                name = sender_display(LingIdentity(participant))
            except ValueError:
                name = participant
            level = self.state.get_reminder_level(thread_id, participant)

            if elapsed_hours >= self.escalate_hours and level < 3:
                self._send_escalation(thread_id, header.topic, participant, name, elapsed_hours)
                self.state.record_reminder(thread_id, participant, 3)
                self._stats["escalations"] += 1
                actions.append(f"升级通知 {name} @ {header.topic}")
            elif elapsed_hours >= self.second_hours and level < 2:
                self._send_reminder(thread_id, header.topic, participant, name, 2, elapsed_hours)
                self.state.record_reminder(thread_id, participant, 2)
                self._stats["reminders_sent"] += 1
                actions.append(f"二次催办 {name} @ {header.topic}")
            elif elapsed_hours >= self.first_hours and level < 1:
                self._send_reminder(thread_id, header.topic, participant, name, 1, elapsed_hours)
                self.state.record_reminder(thread_id, participant, 1)
                self._stats["reminders_sent"] += 1
                actions.append(f"首次提醒 {name} @ {header.topic}")

        return actions

    def _notify_endpoint(self, participant: str, payload: dict[str, Any]) -> bool:
        """Send notification to participant endpoint with retry and exponential backoff.

        Args:
            participant: The participant identity string
            payload: The notification payload to send

        Returns:
            True if notification succeeded, False otherwise
        """
        entry = self._get_identity_endpoint(participant)
        if not entry:
            logger.debug(f"No endpoint configured for {participant}, skipping notification")
            return False

        endpoint_url = entry.get("endpoint", "")
        if not endpoint_url:
            logger.debug(f"Empty endpoint for {participant}")
            return False

        if not _is_localhost_url(endpoint_url):
            logger.warning(f"Blocked non-localhost notification URL for {participant}: {endpoint_url}")
            return False

        for attempt in range(NOTIFY_MAX_RETRIES):
            try:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                request = Request(endpoint_url, data=data, headers={"Content-Type": "application/json"})
                resp = urlopen(request, timeout=NOTIFY_TIMEOUT_SECONDS)
                if 200 <= resp.status < 300:
                    logger.info(f"Notification delivered to {participant} (attempt {attempt + 1})")
                    return True
                logger.warning(f"Notification to {participant} returned status {resp.status}")
            except URLError as e:
                wait = NOTIFY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(f"Notification to {participant} failed (attempt {attempt + 1}/{NOTIFY_MAX_RETRIES}): {e}, retrying in {wait:.1f}s")
                if attempt < NOTIFY_MAX_RETRIES - 1:
                    time.sleep(wait)
            except Exception as e:
                logger.error(f"Unexpected error notifying {participant}: {e}")
                break

        self._log_delivery_failure(participant, endpoint_url, payload)
        return False

    @staticmethod
    def _get_identity_endpoint(participant: str) -> dict[str, str] | None:
        """Get endpoint configuration for a participant from IdentityRegistry."""
        try:
            from lingmessage.types import IdentityRegistry, LingIdentity
            reg = IdentityRegistry.default()
            identity = LingIdentity(participant)
            entry = reg.get(identity)
            if entry and entry.mcp_server_key:
                return {"endpoint": f"http://localhost:3000/mcp/{entry.mcp_server_key}"}
        except (ValueError, ImportError):
            pass
        return None

    @staticmethod
    def _log_delivery_failure(participant: str, endpoint: str, payload: dict[str, Any]) -> None:
        """Log delivery failure for later analysis."""
        try:
            DELIVERY_LOG.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "participant": participant,
                "endpoint": endpoint,
                "payload_keys": list(payload.keys()),
            }
            with DELIVERY_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.error(f"Failed to log delivery failure: {e}")

    def _send_reminder(
        self, thread_id: str, topic: str, participant: str, name: str, level: int, elapsed: float
    ) -> None:
        """发送催办消息"""
        prefix = "首次提醒" if level == 1 else "二次催办"
        subject = f"{prefix}：请回复「{topic}」"
        body = (
            f"{name}，你在讨论「{topic}」中尚未回复。\n"
            f"已等待 {elapsed:.0f} 小时。\n"
            f"讨论链接：thread={thread_id}\n\n"
            f"请尽快回复，或说明无法参与的原因。\n\n"
            f"—— 灵信议事轮询"
        )
        try:
            recipient = LingIdentity(participant)
        except ValueError:
            logger.warning(f"Unknown participant: {participant}")
            return

        self.mailbox.reply(
            thread_id=thread_id,
            sender=LingIdentity.LINGFLOW,
            recipient=recipient,
            subject=subject,
            body=body,
            message_type=MessageType.QUESTION,
        )
        self._notify_endpoint(participant, {
            "type": "reminder",
            "level": level,
            "thread_id": thread_id,
            "topic": topic,
            "elapsed_hours": elapsed,
        })
        logger.info(f"[L{level}] Reminded {name} for {topic}")

    def _send_escalation(
        self, thread_id: str, topic: str, participant: str, name: str, elapsed: float
    ) -> None:
        """发送升级通知（抄送 all）"""
        subject = f"升级通知：{name} 24h+ 未回复「{topic}」"
        body = (
            f"升级通知：\n\n"
            f"讨论「{topic}」已发起 {elapsed:.0f} 小时。\n"
            f"{name}({participant}) 始终未回复。\n\n"
            f"讨论链接：thread={thread_id}\n\n"
            f"请广大老师关注。\n\n"
            f"—— 灵信议事轮询"
        )
        self.mailbox.reply(
            thread_id=thread_id,
            sender=LingIdentity.LINGFLOW,
            recipient=LingIdentity.ALL,
            subject=subject,
            body=body,
            message_type=MessageType.QUESTION,
        )
        self._notify_endpoint(participant, {
            "type": "escalation",
            "thread_id": thread_id,
            "topic": topic,
            "elapsed_hours": elapsed,
        })
        logger.warning(f"[ESCALATE] {name} 24h+ no reply for {topic}")

    def run(self, interval: int = 300) -> None:
        """持续轮询"""
        self._running = True
        logger.info(f"Poller started, interval={interval}s")

        def _stop(signum: int, frame: Any) -> None:
            logger.info(f"Received signal {signum}, stopping...")
            self._running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while self._running:
            try:
                result = self.scan_once()
                if result["actions"]:
                    for action in result["actions"]:
                        logger.info(f"  → {action}")
                else:
                    logger.debug(f"Scan complete: {result['scanned']} threads, no action needed")
            except Exception as e:
                logger.error(f"Scan error: {e}", exc_info=True)

            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

        logger.info("Poller stopped")

    def init_existing(self) -> None:
        """将所有现有 active 讨论标记为已扫描（level=3），避免历史催办洪水"""
        threads = self.mailbox.list_threads(status=ThreadStatus.ACTIVE)
        marked = 0
        for header in threads:
            for p in header.participants:
                if p != "all":
                    self.state.record_reminder(header.thread_id, p, 3)
                    marked += 1
        self._stats["init_marked"] = marked
        logger.info(f"Initialized: marked {marked} participants across {len(threads)} threads")

    @staticmethod
    def _parse_time(ts: str) -> datetime | None:
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None


def main() -> None:
    parser = argparse.ArgumentParser(description="灵信议事轮询守护进程")
    parser.add_argument("--once", action="store_true", help="单次扫描后退出")
    parser.add_argument("--interval", type=int, default=300, help="轮询间隔（秒），默认 300")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--init", action="store_true", help="初始化：将所有现有讨论标记为已扫描，不发送催办")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    poller = DiscussionPoller()

    if args.init:
        poller.init_existing()
        print(f"Initialized: marked {poller._stats.get('init_marked', 0)} participants as scanned")
        return

    if args.once:
        result = poller.scan_once()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        poller.run(interval=args.interval)


if __name__ == "__main__":
    main()
