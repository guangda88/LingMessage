"""LingBus Push Manager — 内存级发布/订阅，写入即推送。

架构：
  lingbus.py 写入 → push_manager.broadcast(msg) → SSE 推送到所有订阅者
  订阅者连接 http://127.0.0.1:9527/events?recipient=lingflow
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Subscriber:
    queue: asyncio.Queue
    recipient: str
    connected_at: float = field(default_factory=time.time)


class PushManager:
    """进程内发布/订阅管理器。

    线程安全：broadcast() 可从任意线程调用（通过 loop.call_soon_threadsafe）。
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, Subscriber] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stats = {"broadcasts": 0, "delivered": 0, "dropped": 0}

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, recipient: str, max_queue: int = 200) -> Subscriber:
        sub = Subscriber(queue=asyncio.Queue(maxsize=max_queue), recipient=recipient)
        self._subscribers[f"{recipient}:{id(sub)}"] = sub
        logger.info("push: subscribe recipient=%s subscribers=%d", recipient, len(self._subscribers))
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        key = f"{sub.recipient}:{id(sub)}"
        self._subscribers.pop(key, None)
        logger.info("push: unsubscribe recipient=%s subscribers=%d", sub.recipient, len(self._subscribers))

    def broadcast(self, msg: dict[str, Any]) -> int:
        """写入后调用。返回实际送达的订阅者数。"""
        delivered = 0
        data = json.dumps(msg, ensure_ascii=False, default=str)

        targets = list(self._subscribers.values())
        for sub in targets:
            recipient = msg.get("recipient", "")
            if recipient != "all" and sub.recipient != recipient:
                continue
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._enqueue, sub, data)
            else:
                self._enqueue(sub, data)
            delivered += 1

        self._stats["broadcasts"] += 1
        self._stats["delivered"] += delivered
        if delivered:
            logger.debug("push: broadcast rowid=%s to %d subscribers", msg.get("rowid"), delivered)
        return delivered

    def _enqueue(self, sub: Subscriber, data: str) -> None:
        try:
            sub.queue.put_nowait(data)
        except asyncio.QueueFull:
            try:
                sub.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                sub.queue.put_nowait(data)
            except asyncio.QueueFull:
                self._stats["dropped"] += 1

    def broadcast_all(self, msg: dict[str, Any]) -> int:
        """广播给所有订阅者（不按recipient过滤）。"""
        delivered = 0
        data = json.dumps(msg, ensure_ascii=False, default=str)
        for sub in list(self._subscribers.values()):
            if self._loop and self._loop.is_running():
                self._loop.call_soon_threadsafe(self._enqueue, sub, data)
            else:
                self._enqueue(sub, data)
            delivered += 1
        self._stats["broadcasts"] += 1
        self._stats["delivered"] += delivered
        return delivered

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def get_stats(self) -> dict[str, Any]:
        return {
            "subscribers": len(self._subscribers),
            "by_recipient": self._recipient_counts(),
            **self._stats,
        }

    def _recipient_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sub in self._subscribers.values():
            counts[sub.recipient] = counts.get(sub.recipient, 0) + 1
        return counts


_manager: PushManager | None = None

PUSH_SERVER_URL = "http://127.0.0.1:9527/internal/broadcast"


def get_push_manager() -> PushManager:
    global _manager
    if _manager is None:
        _manager = PushManager()
    return _manager


def notify_push_server(msg: dict[str, Any]) -> None:
    """Fire-and-forget HTTP POST to SSE push server. Never raises."""
    import threading
    from urllib.request import Request, urlopen

    def _post() -> None:
        try:
            data = json.dumps(msg, ensure_ascii=False, default=str).encode("utf-8")
            req = Request(
                PUSH_SERVER_URL,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            urlopen(req, timeout=3)
        except Exception:
            pass

    t = threading.Thread(target=_post, daemon=True)
    t.start()
