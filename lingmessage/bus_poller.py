"""LingBus 催复守护脚本

直接读取 LingBus SQLite 数据库，扫描所有 active 线程，
检测未回复的参与者，自动发送催复消息到 LingBus。

解决的核心问题：灵研发提案后，成员不知道要回复，需要人工催促。

用法：
    python -m lingmessage.bus_poller              # 前台持续运行（默认5分钟一轮）
    python -m lingmessage.bus_poller --once       # 单次扫描
    python -m lingmessage.bus_poller --init       # 初始化：标记所有现有线程，不发催复
    python -m lingmessage.bus_poller --interval 60  # 自定义间隔

催复升级策略：
    - 1小时：首次提醒（私发给参与者）
    - 4小时：二次催办（私发）
    - 12小时：升级通知（私发给参与者，不再广播到 all）

过滤规则：
    - SKIP_TOPIC_PREFIXES: 系统生成的主题（offline-recovery/wakeup/heartbeat 等）不催复
    - SKIP_RECIPIENTS: 机器账号（lingxi/webui 等）不催复
    - 级别3为最高级，不再广播，仅通知参与者本人
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("lingmessage.bus_poller")

FIRST_REMIND_HOURS = 1.0
SECOND_REMIND_HOURS = 4.0
ESCALATE_HOURS = 12.0

STALE_THREAD_HOURS = 168.0
ESCALATION_DONE_HOURS = 720.0  # 30 days, prevent re-escalation cycle
MAX_STATE_ENTRIES = 2000
MAX_MEMBER_THREADS = 50

SKIP_RECIPIENTS = frozenset({
    "lingxi", "lingterm", "webui_user", "webui_test", "e2e_tester",
    "test_recv", "mcp_recv", "council", "guangda", "linglu", "lingsheng",
})

SKIP_TOPIC_PREFIXES = (
    "offline-recovery:",
    "wakeup:",
    "throttle_test",
    "session-recovery:",
    "heartbeat",
    "bus_poller",
)

PROXY_FALLBACK_WINDOW = 300  # 5 minutes
PROXY_FALLBACK_THRESHOLD = 3  # broadcast after this many

DEFAULT_DB_PATHS: list[Path] = [
    Path.home() / ".lingmessage" / "lingbus.db",
    Path("/home/ai/lingmessage/reply/lingbus.db"),
]

STATE_FILE = Path.home() / ".lingmessage" / "bus_poller_state.json"


def _find_db() -> Path | None:
    for p in DEFAULT_DB_PATHS:
        if p.exists():
            return p
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex


def _parse_time(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


class BusPollerState:
    def __init__(self, path: Path | None = None):
        self._path = path or STATE_FILE
        self._reminders: dict[str, dict[str, Any]] = {}
        self._load()

    def prune(self, active_thread_ids: set[str] | None = None) -> int:
        """清理过期和过多的 state 条目，返回删除数。"""
        to_remove: list[str] = []

        for key, val in self._reminders.items():
            thread_id = val.get("thread_id", key.split(":")[0])

            if active_thread_ids is not None and thread_id not in active_thread_ids:
                to_remove.append(key)
                continue

            updated = _parse_time(val.get("updated_at", ""))
            if not updated:
                to_remove.append(key)
                continue

            # Level 3 (escalated) entries are only removed when the thread
            # is no longer active (handled by the active_thread_ids check above).
            # Previously, pruning after 24h caused a re-escalation loop that
            # generated thousands of spam messages.

        if len(self._reminders) - len(to_remove) > MAX_STATE_ENTRIES:
            entries = sorted(
                self._reminders.items(),
                key=lambda kv: _parse_time(kv[1].get("updated_at", "")) or datetime.min.replace(tzinfo=timezone.utc),
            )
            excess = len(self._reminders) - len(to_remove) - MAX_STATE_ENTRIES
            for key, _ in entries[:excess]:
                if key not in to_remove:
                    to_remove.append(key)

        for key in to_remove:
            self._reminders.pop(key, None)

        if to_remove:
            self._save()
        return len(to_remove)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            self._reminders = data.get("reminders", {})
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load state: {e}")

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"reminders": self._reminders}, indent=2, ensure_ascii=False)
        )

    def get_level(self, thread_id: str, participant: str) -> int:
        key = f"{thread_id}:{participant}"
        return self._reminders.get(key, {}).get("level", 0)

    def set_level(self, thread_id: str, participant: str, level: int) -> None:
        key = f"{thread_id}:{participant}"
        self._reminders[key] = {
            "thread_id": thread_id,
            "participant": participant,
            "level": level,
            "updated_at": _now_iso(),
        }
        self._save()

    def cleanup_thread(self, thread_id: str) -> None:
        to_remove = [k for k in self._reminders if k.startswith(f"{thread_id}:")]
        for k in to_remove:
            del self._reminders[k]
        if to_remove:
            self._save()


class BusPoller:
    def __init__(
        self,
        db_path: Path | None = None,
        state: BusPollerState | None = None,
        first_hours: float = FIRST_REMIND_HOURS,
        second_hours: float = SECOND_REMIND_HOURS,
        escalate_hours: float = ESCALATE_HOURS,
    ):
        self.db_path = db_path or _find_db()
        if not self.db_path:
            raise FileNotFoundError("No LingBus database found")
        self.state = state or BusPollerState()
        self.first_hours = first_hours
        self.second_hours = second_hours
        self.escalate_hours = escalate_hours
        self._running = False
        self._stats: dict[str, int] = {"scanned": 0, "reminders": 0, "escalations": 0, "delivery_retries": 0}
        self._proxy_fallback_times: list[float] = []

    def _check_proxy_fallback_alerts(self, conn: sqlite3.Connection) -> list[str]:
        """Detect proxy fallback messages and broadcast if threshold exceeded."""
        now = time.time()
        cutoff = now - PROXY_FALLBACK_WINDOW
        self._proxy_fallback_times = [t for t in self._proxy_fallback_times if t > cutoff]

        msgs = conn.execute(
            "SELECT rowid, sender, subject, body, timestamp FROM messages "
            "WHERE subject LIKE '%Proxy Fallback%' AND timestamp > ?",
            (datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(),),
        ).fetchall()

        alerts: list[str] = []
        for msg in msgs:
            logger.warning(
                "proxy_fallback: sender=%s subject=%s rowid=%d",
                msg["sender"], msg["subject"], msg["rowid"],
            )
            self._proxy_fallback_times.append(now)
            alerts.append(f"proxy_fallback from {msg['sender']}: {msg['subject'][:60]}")

        if len(self._proxy_fallback_times) >= PROXY_FALLBACK_THRESHOLD:
            count = len(self._proxy_fallback_times)
            logger.error(
                "proxy_fallback_broadcast: %d triggers in %ds (threshold=%d)",
                count, PROXY_FALLBACK_WINDOW, PROXY_FALLBACK_THRESHOLD,
            )
            alerts.append(f"BROADCAST: {count} proxy fallbacks in {PROXY_FALLBACK_WINDOW}s")
        return alerts

    def _check_undelivered_messages(self, conn: sqlite3.Connection) -> list[str]:
        """Scan pending delivery attempts and update their status."""
        now = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            "SELECT da.message_id, da.recipient, da.attempt_count, da.status, "
            "da.next_retry_at, m.acked_by "
            "FROM delivery_attempts da JOIN messages m ON da.message_id = m.message_id "
            "WHERE da.status = 'pending' AND (da.next_retry_at = '' OR da.next_retry_at <= ?) "
            "LIMIT 50",
            (now,),
        ).fetchall()

        if not rows:
            return []

        from lingmessage.lingbus import _MAX_DELIVERY_ATTEMPTS, _retry_at_iso

        actions: list[str] = []
        for r in rows:
            acked_by: list[str] = json.loads(r["acked_by"])
            if r["recipient"] in acked_by:
                conn.execute(
                    "UPDATE delivery_attempts SET status='confirmed', last_attempt_at=? "
                    "WHERE message_id=? AND recipient=?",
                    (now, r["message_id"], r["recipient"]),
                )
                actions.append(f"delivery_confirmed {r['recipient']} msg={r['message_id'][:12]}")
                continue

            attempt = r["attempt_count"] + 1
            if attempt >= _MAX_DELIVERY_ATTEMPTS:
                conn.execute(
                    "UPDATE delivery_attempts SET attempt_count=?, last_attempt_at=?, "
                    "status='escalated', next_retry_at='' "
                    "WHERE message_id=? AND recipient=?",
                    (attempt, now, r["message_id"], r["recipient"]),
                )
                actions.append(f"delivery_escalated {r['recipient']} msg={r['message_id'][:12]}")
            else:
                next_retry = _retry_at_iso(now, attempt)
                conn.execute(
                    "UPDATE delivery_attempts SET attempt_count=?, last_attempt_at=?, "
                    "next_retry_at=? WHERE message_id=? AND recipient=?",
                    (attempt, now, next_retry, r["message_id"], r["recipient"]),
                )
                actions.append(f"delivery_retry {r['recipient']} attempt={attempt} msg={r['message_id'][:12]}")
            self._stats["delivery_retries"] += 1

        conn.commit()
        return actions

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def scan_once(self) -> dict[str, Any]:
        conn = self._connect()
        try:
            return self._scan(conn)
        finally:
            conn.close()

    def _scan(self, conn: sqlite3.Connection) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=STALE_THREAD_HOURS)
        cutoff_iso = cutoff.isoformat()

        threads = conn.execute(
            "SELECT * FROM threads WHERE status = 'active' AND updated_at > ? ORDER BY updated_at DESC",
            (cutoff_iso,),
        ).fetchall()

        active_ids = {t["thread_id"] for t in threads}
        pruned = self.state.prune(active_ids)

        member_thread_count: dict[str, int] = {}

        actions: list[str] = []
        self._stats["scanned"] = len(threads)
        if pruned:
            actions.append(f"清理 {pruned} 条过期状态")

        for thread in threads:
            participants = json.loads(thread["participants"])
            thread_id = thread["thread_id"]
            topic = thread["topic"]

            if any(topic.startswith(pfx) or topic == pfx for pfx in SKIP_TOPIC_PREFIXES):
                continue

            if "all" in participants:
                participants = [p for p in participants if p not in ("all",)]
            participants = [p for p in participants if p not in SKIP_RECIPIENTS]
            if len(participants) <= 1:
                continue

            replied_senders: set[str] = set()
            msgs = conn.execute(
                "SELECT sender, timestamp FROM messages WHERE thread_id = ? ORDER BY rowid",
                (thread_id,),
            ).fetchall()

            if not msgs:
                continue

            for msg in msgs:
                replied_senders.add(msg["sender"])

            first_time = _parse_time(msgs[0]["timestamp"])
            if not first_time:
                continue

            elapsed_hours = (now - first_time).total_seconds() / 3600

            waiting = set(participants) - replied_senders
            if not waiting:
                self.state.cleanup_thread(thread_id)
                continue

            for participant in waiting:
                count = member_thread_count.get(participant, 0)
                if count >= MAX_MEMBER_THREADS:
                    continue
                member_thread_count[participant] = count + 1

                level = self.state.get_level(thread_id, participant)
                if level >= 3:
                    continue

                if elapsed_hours >= self.escalate_hours and level < 3:
                    self._send_escalation(
                        conn, thread_id, topic, participant, elapsed_hours
                    )
                    self.state.set_level(thread_id, participant, 3)
                    self._stats["escalations"] += 1
                    actions.append(f"升级 {participant} @ {topic[:30]}")
                elif elapsed_hours >= self.second_hours and level < 2:
                    self._send_reminder(
                        conn, thread_id, topic, participant, 2, elapsed_hours
                    )
                    self.state.set_level(thread_id, participant, 2)
                    self._stats["reminders"] += 1
                    actions.append(f"催办 {participant} @ {topic[:30]}")
                elif elapsed_hours >= self.first_hours and level < 1:
                    self._send_reminder(
                        conn, thread_id, topic, participant, 1, elapsed_hours
                    )
                    self.state.set_level(thread_id, participant, 1)
                    self._stats["reminders"] += 1
                    actions.append(f"提醒 {participant} @ {topic[:30]}")

        alerts = self._check_proxy_fallback_alerts(conn)
        for action in alerts:
            actions.append(action)

        delivery_actions = self._check_undelivered_messages(conn)
        for action in delivery_actions:
            actions.append(action)

        return {
            "db": str(self.db_path),
            "scanned": len(threads),
            "actions": actions,
            "stats": dict(self._stats),
        }

    def _send_reminder(
        self,
        conn: sqlite3.Connection,
        thread_id: str,
        topic: str,
        participant: str,
        level: int,
        elapsed: float,
    ) -> None:
        prefix = "首次提醒" if level == 1 else "二次催办"
        subject = f"{prefix}：请回复「{topic[:40]}」"
        body = (
            f"{participant}，你在讨论「{topic}」中尚未回复。\n"
            f"已等待 {elapsed:.1f} 小时。\n"
            f"讨论链接：thread={thread_id}\n\n"
            f"请尽快回复，或说明无法参与的原因。\n\n"
            f"—— LingBus 催复守护"
        )
        self._insert_message(conn, thread_id, "bus_poller", participant, subject, body)

    def _send_escalation(
        self,
        conn: sqlite3.Connection,
        thread_id: str,
        topic: str,
        participant: str,
        elapsed: float,
    ) -> None:
        subject = f"升级通知：{participant} 12h+ 未回复「{topic[:40]}」"
        body = (
            f"升级通知：\n\n"
            f"讨论「{topic}」已发起 {elapsed:.1f} 小时。\n"
            f"{participant} 始终未回复。\n\n"
            f"讨论链接：thread={thread_id}\n\n"
            f"—— LingBus 催复守护"
        )
        self._insert_message(conn, thread_id, "bus_poller", participant, subject, body)

    def _insert_message(
        self,
        conn: sqlite3.Connection,
        thread_id: str,
        sender: str,
        recipient: str,
        subject: str,
        body: str,
    ) -> None:
        msg_id = _new_id()
        now = _now_iso()
        conn.execute(
            """INSERT INTO messages
               (message_id, thread_id, sender, recipient, message_type, channel,
                subject, body, timestamp, reply_to, metadata, acked_by)
               VALUES (?, ?, ?, ?, 'reply', 'ecosystem', ?, ?, ?, '', '{}', '[]')""",
            (msg_id, thread_id, sender, recipient, subject, body, now),
        )
        conn.execute(
            "UPDATE threads SET message_count = message_count + 1, updated_at = ? WHERE thread_id = ?",
            (now, thread_id),
        )
        conn.commit()

    def init_existing(self) -> int:
        conn = self._connect()
        try:
            threads = conn.execute(
                "SELECT thread_id, participants FROM threads WHERE status = 'active'"
            ).fetchall()
            marked = 0
            for t in threads:
                participants = json.loads(t["participants"])
                for p in participants:
                    if p not in ("all",):
                        self.state.set_level(t["thread_id"], p, 3)
                        marked += 1
            return marked
        finally:
            conn.close()

    def run(self, interval: int = 300) -> None:
        self._running = True
        logger.info(f"BusPoller started on {self.db_path}, interval={interval}s")

        def _stop(signum: int, frame: Any) -> None:
            logger.info(f"Stopping (signal {signum})...")
            self._running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while self._running:
            try:
                result = self.scan_once()
                if result["actions"]:
                    for action in result["actions"]:
                        logger.info(f"  -> {action}")
                else:
                    logger.debug(f"Scan: {result['scanned']} threads, no action")
            except Exception as e:
                logger.error(f"Scan error: {e}", exc_info=True)

            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

        logger.info("BusPoller stopped")

    def run_multi_db(self, interval: int = 300) -> None:
        """扫描多个数据库文件"""
        self._running = True
        dbs = [p for p in DEFAULT_DB_PATHS if p.exists()]
        logger.info(f"BusPoller started on {len(dbs)} DBs: {[str(p) for p in dbs]}, interval={interval}s")

        def _stop(signum: int, frame: Any) -> None:
            logger.info(f"Stopping (signal {signum})...")
            self._running = False

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)

        while self._running:
            for db_path in dbs:
                try:
                    self.db_path = db_path
                    result = self.scan_once()
                    if result["actions"]:
                        for action in result["actions"]:
                            logger.info(f"  [{db_path.name}] {action}")
                except Exception as e:
                    logger.error(f"Scan error on {db_path}: {e}")
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

        logger.info("BusPoller stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description="LingBus 催复守护脚本")
    parser.add_argument("--once", action="store_true", help="单次扫描后退出")
    parser.add_argument("--init", action="store_true", help="初始化：标记现有线程，不发催复")
    parser.add_argument("--multi", action="store_true", help="扫描多个数据库")
    parser.add_argument("--db", type=str, help="指定数据库路径")
    parser.add_argument("--interval", type=int, default=300, help="轮询间隔（秒），默认 300")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    db_path = Path(args.db) if args.db else None
    poller = BusPoller(db_path=db_path)

    if args.init:
        marked = poller.init_existing()
        print(f"Initialized: marked {marked} participant-thread pairs")
        return

    if args.once:
        result = poller.scan_once()
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.multi:
        poller.run_multi_db(interval=args.interval)
    else:
        poller.run(interval=args.interval)


if __name__ == "__main__":
    main()
