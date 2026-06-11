"""Unified message store abstraction over Mailbox and LingBus backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from lingmessage.types import (
    Channel,
    DeliveryStatus,
    LingIdentity,
    Message,
    MessageType,
    SourceType,
    ThreadHeader,
    ThreadStatus,
)


def _resolve_identity(value: str) -> LingIdentity:
    from lingmessage.types import IDENTITY_MAP
    if value not in IDENTITY_MAP:
        raise ValueError(f"unknown identity: {value!r}")
    return IDENTITY_MAP[value]


def _bus_message_to_message(bm: Any) -> Message:
    from lingmessage.lingbus import BusMessage
    if not isinstance(bm, BusMessage):
        raise TypeError(f"Expected BusMessage, got {type(bm)}")
    metadata_tuple = tuple(sorted(bm.metadata.items()))
    return Message(
        message_id=bm.message_id,
        thread_id=bm.thread_id,
        sender=_resolve_identity(bm.sender),
        recipient=_resolve_identity(bm.recipient),
        message_type=MessageType(bm.message_type) if bm.message_type else MessageType.REPLY,
        channel=Channel(bm.channel) if bm.channel else Channel.ECOSYSTEM,
        subject=bm.subject,
        body=bm.body,
        timestamp=bm.timestamp,
        reply_to=bm.reply_to,
        metadata=metadata_tuple,
        source_type=SourceType(bm.source_type) if bm.source_type else SourceType.INFERRED,
        source_trace=bm.source_trace,
        delivery_status=DeliveryStatus.SENT,
        delivered_at="",
    )


def _thread_dict_to_header(d: dict[str, Any]) -> ThreadHeader:
    channel_val = d.get("channel", "ecosystem")
    status_val = d.get("status", "active")
    try:
        channel_enum = Channel(channel_val)
    except ValueError:
        channel_enum = Channel.ECOSYSTEM
    try:
        status_enum = ThreadStatus(status_val)
    except ValueError:
        status_enum = ThreadStatus.ACTIVE
    return ThreadHeader(
        thread_id=d.get("thread_id", ""),
        topic=d.get("topic", ""),
        channel=channel_enum,
        status=status_enum,
        participants=tuple(d.get("participants", [])),
        created_at=d.get("created_at", ""),
        updated_at=d.get("updated_at", ""),
        message_count=d.get("message_count", 0),
        summary=d.get("summary", ""),
    )


class MessageStore(ABC):
    """Abstract base class for message storage backends.

    Defines the unified interface that both Mailbox and LingBus implement.
    All identity parameters use LingIdentity enums; all return types use
    Message/ThreadHeader dataclasses.
    """

    @abstractmethod
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
        source_type: SourceType = SourceType.INFERRED,
        source_trace: str = "",
        signature: str = "",
    ) -> tuple[ThreadHeader, Message]:
        ...

    @abstractmethod
    def reply(
        self,
        thread_id: str,
        sender: LingIdentity,
        recipient: LingIdentity,
        subject: str,
        body: str,
        message_type: MessageType = MessageType.REPLY,
        metadata: dict[str, str] | None = None,
        source_type: SourceType = SourceType.INFERRED,
        source_trace: str = "",
        signature: str = "",
    ) -> Message:
        ...

    @abstractmethod
    def post(self, message: Message, signature: str = "") -> Message:
        ...

    @abstractmethod
    def load_thread_header(self, thread_id: str) -> ThreadHeader | None:
        ...

    @abstractmethod
    def load_thread_messages(self, thread_id: str) -> tuple[Message, ...]:
        ...

    @abstractmethod
    def list_threads(
        self,
        channel: Channel | None = None,
        status: ThreadStatus | None = None,
        participant: LingIdentity | None = None,
    ) -> tuple[ThreadHeader, ...]:
        ...

    @abstractmethod
    def ack_message(self, thread_id: str, message_id: str) -> Message | None:
        ...

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        ...


class MailboxStore(MessageStore):
    """MessageStore backed by the file-system Mailbox.

    Thin delegation layer — no type conversion needed since Mailbox
    already uses LingIdentity/Message/ThreadHeader natively.
    """

    def __init__(self, mailbox: Any) -> None:
        from lingmessage.mailbox import Mailbox
        if not isinstance(mailbox, Mailbox):
            raise TypeError(f"Expected Mailbox, got {type(mailbox)}")
        self._mb = mailbox

    @property
    def mailbox(self) -> Any:
        return self._mb

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
        source_type: SourceType = SourceType.INFERRED,
        source_trace: str = "",
        signature: str = "",
    ) -> tuple[ThreadHeader, Message]:
        return self._mb.open_thread(
            sender=sender,
            recipients=recipients,
            channel=channel,
            topic=topic,
            subject=subject,
            body=body,
            message_type=message_type,
            metadata=metadata,
            source_type=source_type,
            source_trace=source_trace,
            signature=signature,
        )

    def reply(
        self,
        thread_id: str,
        sender: LingIdentity,
        recipient: LingIdentity,
        subject: str,
        body: str,
        message_type: MessageType = MessageType.REPLY,
        metadata: dict[str, str] | None = None,
        source_type: SourceType = SourceType.INFERRED,
        source_trace: str = "",
        signature: str = "",
    ) -> Message:
        return self._mb.reply(
            thread_id=thread_id,
            sender=sender,
            recipient=recipient,
            subject=subject,
            body=body,
            message_type=message_type,
            metadata=metadata,
            source_type=source_type,
            source_trace=source_trace,
            signature=signature,
        )

    def post(self, message: Message, signature: str = "") -> Message:
        return self._mb.post(message, signature)

    def load_thread_header(self, thread_id: str) -> ThreadHeader | None:
        return self._mb.load_thread_header(thread_id)

    def load_thread_messages(self, thread_id: str) -> tuple[Message, ...]:
        return self._mb.load_thread_messages(thread_id)

    def list_threads(
        self,
        channel: Channel | None = None,
        status: ThreadStatus | None = None,
        participant: LingIdentity | None = None,
    ) -> tuple[ThreadHeader, ...]:
        return self._mb.list_threads(channel=channel, status=status, participant=participant)

    def ack_message(self, thread_id: str, message_id: str) -> Message | None:
        return self._mb.ack_message(thread_id, message_id)

    def get_stats(self) -> dict[str, Any]:
        return self._mb.get_summary()


class LingBusStore(MessageStore):
    """MessageStore backed by the SQLite-based LingBus.

    Translates between LingBus's string-based API and the enum-typed
    MessageStore interface. Write operations fetch back created records
    to return full Message/ThreadHeader objects.
    """

    def __init__(self, bus: Any) -> None:
        from lingmessage.lingbus import LingBus
        if not isinstance(bus, LingBus):
            raise TypeError(f"Expected LingBus, got {type(bus)}")
        self._bus = bus

    @property
    def bus(self) -> Any:
        return self._bus

    def close(self) -> None:
        self._bus.close()

    def __enter__(self) -> LingBusStore:
        self._bus.__enter__()
        return self

    def __exit__(self, *args: object) -> None:
        self._bus.__exit__(*args)

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
        source_type: SourceType = SourceType.INFERRED,
        source_trace: str = "",
        signature: str = "",
    ) -> tuple[ThreadHeader, Message]:
        sender_str = sender.value
        recipients_str = [r.value for r in recipients]
        channel_str = channel.value

        thread_id, message_id = self._bus.open_thread(
            topic=topic,
            sender=sender_str,
            recipients=recipients_str,
            channel=channel_str,
            subject=subject,
            body=body,
        )

        bus_msgs = self._bus.get_thread(thread_id)
        if not bus_msgs:
            raise RuntimeError(f"Thread {thread_id} created but fetch-back returned no messages")

        msg = _bus_message_to_message(bus_msgs[0])

        thread_dicts = self._bus.list_threads()
        header_dict = None
        for td in thread_dicts:
            if td.get("thread_id") == thread_id:
                header_dict = td
                break

        if header_dict is None:
            header = ThreadHeader(
                thread_id=thread_id,
                topic=topic,
                channel=channel,
                status=ThreadStatus.ACTIVE,
                participants=tuple(recipients_str + [sender_str]),
                created_at=msg.timestamp,
                updated_at=msg.timestamp,
                message_count=1,
            )
        else:
            header = _thread_dict_to_header(header_dict)

        return header, msg

    def reply(
        self,
        thread_id: str,
        sender: LingIdentity,
        recipient: LingIdentity,
        subject: str,
        body: str,
        message_type: MessageType = MessageType.REPLY,
        metadata: dict[str, str] | None = None,
        source_type: SourceType = SourceType.INFERRED,
        source_trace: str = "",
        signature: str = "",
    ) -> Message:
        message_id = self._bus.post_reply(
            thread_id=thread_id,
            sender=sender.value,
            recipient=recipient.value,
            body=body,
            subject=subject,
            message_type=message_type.value,
            metadata=metadata,
        )

        bus_msgs = self._bus.get_thread(thread_id)
        for bm in bus_msgs:
            if bm.message_id == message_id:
                return _bus_message_to_message(bm)

        raise RuntimeError(f"Reply {message_id} created but fetch-back failed")

    def post(self, message: Message, signature: str = "") -> Message:
        if message.message_type == MessageType.OPEN and message.reply_to == "":
            self._bus.open_thread(
                topic=message.subject,
                sender=message.sender.value,
                recipients=[message.recipient.value],
                channel=message.channel.value,
                subject=message.subject,
                body=message.body,
            )
        else:
            self._bus.post_reply(
                thread_id=message.thread_id,
                sender=message.sender.value,
                recipient=message.recipient.value,
                body=message.body,
                subject=message.subject,
                message_type=message.message_type.value,
                metadata=dict(message.metadata) if message.metadata else None,
            )
        return message

    def load_thread_header(self, thread_id: str) -> ThreadHeader | None:
        thread_dicts = self._bus.list_threads()
        for td in thread_dicts:
            if td.get("thread_id") == thread_id:
                return _thread_dict_to_header(td)
        return None

    def load_thread_messages(self, thread_id: str) -> tuple[Message, ...]:
        bus_msgs = self._bus.get_thread(thread_id)
        return tuple(_bus_message_to_message(bm) for bm in bus_msgs)

    def list_threads(
        self,
        channel: Channel | None = None,
        status: ThreadStatus | None = None,
        participant: LingIdentity | None = None,
    ) -> tuple[ThreadHeader, ...]:
        caller = participant.value if participant else None
        status_str = status.value if status else None
        thread_dicts = self._bus.list_threads(status=status_str, caller=caller)

        results: list[ThreadHeader] = []
        for td in thread_dicts:
            header = _thread_dict_to_header(td)
            if channel is not None and header.channel != channel:
                continue
            results.append(header)
        return tuple(results)

    def ack_message(self, thread_id: str, message_id: str) -> Message | None:
        bus_msgs = self._bus.get_thread(thread_id)
        target = None
        for bm in bus_msgs:
            if bm.message_id == message_id:
                target = bm
                break
        if target is None:
            return None

        self._bus.ack(message_id, target.recipient)
        updated_bus_msgs = self._bus.get_thread(thread_id)
        for bm in updated_bus_msgs:
            if bm.message_id == message_id:
                return _bus_message_to_message(bm)
        return None

    def get_pending(self, member: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return self._bus.get_pending(member, limit=limit)

    def batch_ack(self, member: str) -> int:
        return self._bus.batch_ack(member)

    def pending_count(self, member: str) -> int:
        return self._bus.pending_count(member)

    def get_stats(self) -> dict[str, Any]:
        return self._bus.stats()
