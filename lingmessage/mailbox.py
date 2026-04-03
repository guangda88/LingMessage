from __future__ import annotations

"""灵信共享邮箱 — 所有灵项目读写的公共讨论区"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingmessage.types import (
    Channel,
    LingIdentity,
    Message,
    MessageType,
    ThreadHeader,
    ThreadStatus,
    _new_id,
    _now_iso,
    create_message,
    create_thread_header,
)


@dataclass
class Mailbox:
    root: Path = field(default_factory=lambda: Path.home() / ".lingmessage")

    def _threads_dir(self) -> Path:
        return self.root / "threads"

    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _thread_dir(self, thread_id: str) -> Path:
        d = self._threads_dir() / thread_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def post(self, message: Message) -> Message:
        d = self._thread_dir(message.thread_id)
        msg_path = d / f"msg_{message.message_id}.json"
        msg_path.write_text(message.to_json(indent=2), encoding="utf-8")
        self._update_index(message)
        return message

    def open_thread(
        self,
        sender: LingIdentity,
        recipients: tuple[LingIdentity, ...],
        channel: Channel,
        topic: str,
        subject: str,
        body: str,
        message_type: MessageType = MessageType.OPEN,
        metadata: dict[str, str] | None = None,
    ) -> tuple[ThreadHeader, Message]:
        tid = _new_id()
        first = create_message(
            sender=sender,
            recipient=LingIdentity.ALL,
            message_type=message_type,
            channel=channel,
            subject=subject,
            body=body,
            thread_id=tid,
            metadata=metadata,
        )
        header = create_thread_header(
            topic=topic,
            channel=channel,
            participants=recipients + (sender,),
            first_message=first,
        )
        d = self._thread_dir(tid)
        header_path = d / "thread.json"
        header_path.write_text(header.to_json(indent=2), encoding="utf-8")
        self.post(first)
        self._update_index(first, header)
        return header, first

    def reply(
        self,
        thread_id: str,
        sender: LingIdentity,
        recipient: LingIdentity,
        subject: str,
        body: str,
        message_type: MessageType = MessageType.REPLY,
        metadata: dict[str, str] | None = None,
    ) -> Message:
        header = self.load_thread_header(thread_id)
        if header is None:
            raise ValueError(f"Thread {thread_id} not found")
        msg = create_message(
            sender=sender,
            recipient=recipient,
            message_type=message_type,
            channel=Channel(header.channel),
            subject=subject,
            body=body,
            thread_id=thread_id,
            reply_to="",
            metadata=metadata,
        )
        self.post(msg)
        self._increment_thread(header)
        return msg

    def load_thread_header(self, thread_id: str) -> ThreadHeader | None:
        path = self._threads_dir() / thread_id / "thread.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return ThreadHeader.from_dict(data)

    def load_thread_messages(self, thread_id: str) -> tuple[Message, ...]:
        d = self._threads_dir() / thread_id
        if not d.exists():
            return ()
        messages: list[Message] = []
        for p in sorted(d.glob("msg_*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            messages.append(Message.from_dict(data))
        return tuple(messages)

    def list_threads(
        self,
        channel: Channel | None = None,
        status: ThreadStatus | None = None,
        participant: LingIdentity | None = None,
    ) -> tuple[ThreadHeader, ...]:
        index = self._load_index()
        results: list[ThreadHeader] = []
        for entry in index.get("threads", []):
            h = ThreadHeader.from_dict(entry)
            if channel is not None and h.channel != channel.value:
                continue
            if status is not None and h.status != status.value:
                continue
            if participant is not None and participant.value not in h.participants:
                continue
            results.append(h)
        return tuple(results)

    def _load_index(self) -> dict[str, Any]:
        path = self._index_path()
        if not path.exists():
            return {"threads": [], "last_updated": _now_iso()}
        return json.loads(path.read_text(encoding="utf-8"))

    def _update_index(
        self,
        message: Message,
        header: ThreadHeader | None = None,
    ) -> None:
        index = self._load_index()
        tid = message.thread_id
        threads = index.get("threads", [])
        found = False
        for i, t in enumerate(threads):
            if t["thread_id"] == tid:
                threads[i]["updated_at"] = _now_iso()
                threads[i]["message_count"] = threads[i].get("message_count", 0) + 1
                if header is not None:
                    threads[i]["topic"] = header.topic
                    threads[i]["channel"] = header.channel
                    threads[i]["status"] = header.status
                    threads[i]["participants"] = list(header.participants)
                found = True
                break
        if not found and header is not None:
            threads.append(header.to_dict())
        index["threads"] = threads
        index["last_updated"] = _now_iso()
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path().write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _increment_thread(self, header: ThreadHeader) -> None:
        index = self._load_index()
        tid = header.thread_id
        for i, t in enumerate(index.get("threads", [])):
            if t["thread_id"] == tid:
                index["threads"][i]["message_count"] = t.get("message_count", 1) + 1
                index["threads"][i]["updated_at"] = _now_iso()
                break
        index["last_updated"] = _now_iso()
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path().write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_summary(self) -> dict[str, Any]:
        index = self._load_index()
        threads = index.get("threads", [])
        by_channel: dict[str, int] = {}
        by_status: dict[str, int] = {}
        total_messages = 0
        for t in threads:
            ch = t.get("channel", "unknown")
            st = t.get("status", "unknown")
            by_channel[ch] = by_channel.get(ch, 0) + 1
            by_status[st] = by_status.get(st, 0) + 1
            total_messages += t.get("message_count", 0)
        return {
            "total_threads": len(threads),
            "total_messages": total_messages,
            "by_channel": by_channel,
            "by_status": by_status,
            "last_updated": index.get("last_updated", ""),
        }
