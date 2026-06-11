"""Bidirectional migration between Mailbox and LingBus backends."""

from __future__ import annotations

import sys
from typing import Any

from lingmessage.store import MessageStore, MailboxStore, LingBusStore


def migrate(source: MessageStore, dest: MessageStore, *, verbose: bool = False) -> dict[str, int]:
    """Migrate all threads from *source* to *dest* (idempotent).

    Returns a dict with counts: ``{"threads": N, "messages": N, "skipped": N}``.
    Threads whose ``thread_id`` already exists in *dest* are skipped.
    """
    threads_migrated = 0
    messages_migrated = 0
    threads_skipped = 0

    dest_ids = {h.thread_id for h in dest.list_threads()}

    for header in source.list_threads():
        if header.thread_id in dest_ids:
            threads_skipped += 1
            if verbose:
                print(f"  skip {header.thread_id} (exists)")
            continue

        messages = source.load_thread_messages(header.thread_id)
        if not messages:
            threads_skipped += 1
            continue

        first = messages[0]
        dest.open_thread(
            sender=first.sender,
            recipients=(first.recipient,),
            channel=first.channel,
            topic=header.topic,
            subject=first.subject,
            body=first.body,
            message_type=first.message_type,
            metadata=dict(first.metadata) if first.metadata else None,
            source_type=first.source_type,
            source_trace=first.source_trace,
            signature="",
        )
        messages_migrated += 1

        for msg in messages[1:]:
            dest.reply(
                thread_id=msg.thread_id,
                sender=msg.sender,
                recipient=msg.recipient,
                subject=msg.subject,
                body=msg.body,
                message_type=msg.message_type,
                metadata=dict(msg.metadata) if msg.metadata else None,
                source_type=msg.source_type,
                source_trace=msg.source_trace,
                signature="",
            )
            messages_migrated += 1

        threads_migrated += 1
        if verbose:
            print(f"  migrated {header.thread_id} ({len(messages)} msgs)")

    return {
        "threads": threads_migrated,
        "messages": messages_migrated,
        "skipped": threads_skipped,
    }


def cmd_migrate(args: Any) -> None:
    """CLI entry-point for the ``migrate`` command."""
    from lingmessage.mailbox import Mailbox
    from lingmessage.lingbus import LingBus

    direction = getattr(args, "direction", "mailbox-to-lingbus")

    if direction == "mailbox-to-lingbus":
        source: MessageStore = MailboxStore(Mailbox())
        dest: MessageStore = LingBusStore(LingBus())
    elif direction == "lingbus-to-mailbox":
        source = LingBusStore(LingBus())
        dest = MailboxStore(Mailbox())
    else:
        print(f"Unknown direction: {direction}", file=sys.stderr)
        sys.exit(1)

    verbose = getattr(args, "verbose", False)
    print(f"Migrating {direction} ...")

    result = migrate(source, dest, verbose=verbose)

    print(
        f"Done: {result['threads']} threads migrated, "
        f"{result['messages']} messages, "
        f"{result['skipped']} skipped."
    )

    if isinstance(dest, LingBusStore):
        dest.close()
    if isinstance(source, LingBusStore):
        source.close()
