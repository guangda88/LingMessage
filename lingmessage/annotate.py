#!/usr/bin/env python3
"""
Historical message annotation tool for LingMessage

Detects and annotates identity hallucinations by:
1. Loading all threads and messages
2. Detecting timestamp anomalies (multiple members posting in the same second)
3. Annotating suspicious messages with source_type=GENERATED
"""

from __future__ import annotations

import json
from collections import defaultdict

from lingmessage.mailbox import Mailbox
from lingmessage.types import SourceType, Message


def detect_anomalies(messages: tuple[Message, ...]) -> dict[str, list[str]]:
    """
    Detect timestamp anomalies: multiple members posting in the same second.

    Returns dict mapping second (e.g., "2026-04-04T01:41:23") to list of message IDs
    """
    second_to_messages: dict[str, list[str]] = defaultdict(list)

    for msg in messages:
        # Extract second-level timestamp (remove microseconds)
        second = msg.timestamp.split(".")[0]
        second_to_messages[second].append(msg.message_id)

    # Find seconds with multiple messages
    anomalies = {
        second: msg_ids
        for second, msg_ids in second_to_messages.items()
        if len(msg_ids) > 1
    }

    return anomalies


def annotate_message(message: Message, reason: str) -> Message:
    """
    Create an annotated message with source_type=GENERATED.

    The original message data is preserved, only source_type and source_trace are updated.
    """
    # Load current message dict
    msg_dict = json.loads(message.to_json())

    # Update source_type and source_trace
    msg_dict["source_type"] = SourceType.GENERATED.value

    # Build source_trace with annotation reason
    current_trace = msg_dict.get("source_trace", "{}")
    try:
        trace_dict = json.loads(current_trace)
    except json.JSONDecodeError:
        trace_dict = {}

    trace_dict["annotation_reason"] = reason
    trace_dict["annotated_at"] = message.timestamp

    msg_dict["source_trace"] = json.dumps(trace_dict)

    # Reconstruct message from annotated dict
    return Message.from_dict(msg_dict)


def annotate_thread(
    mailbox: Mailbox, thread_id: str, dry_run: bool = True
) -> dict[str, any]:
    """
    Annotate a single thread for identity hallucinations.

    Returns summary dict with:
    - total_messages: Total number of messages in thread
    - anomalies_detected: Number of timestamp anomalies detected
    - messages_to_annotate: Number of messages to annotate
    - already_annotated: Number of messages already annotated
    - details: List of anomaly details
    """
    try:
        messages = mailbox.load_thread_messages(thread_id)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"Error loading thread {thread_id}: {e}")
        # Try to find the problematic message
        d = mailbox._thread_dir(thread_id)
        for p in d.glob("msg_*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                recipient = data.get("recipient")
                if recipient == "lingmessage":
                    print(f"  Problematic file: {p.name}")
                    print(f"  Data: {json.dumps(data, indent=2)[:500]}")
            except json.JSONDecodeError:
                pass
        raise
    anomalies = detect_anomalies(messages)

    messages_to_annotate: set[str] = set()
    details = []

    for second, msg_ids in anomalies.items():
        # Only annotate if more than 2 messages in the same second (likely hallucination)
        if len(msg_ids) >= 2:
            messages_to_annotate.update(msg_ids)
            detail = f"  {second}: {len(msg_ids)} messages"
            details.append(detail)

    # Check which messages are already annotated
    already_annotated = sum(
        1 for msg in messages
        if msg.message_id in messages_to_annotate and msg.source_type == SourceType.GENERATED
    )

    # Perform annotation if not dry run
    if not dry_run and messages_to_annotate:
        for msg in messages:
            if msg.message_id in messages_to_annotate and msg.source_type != SourceType.GENERATED:
                annotated = annotate_message(
                    msg,
                    reason=f"Timestamp anomaly: multiple members posted in the same second ({second})"
                )
                # Update the message file
                thread_dir = mailbox._thread_dir(thread_id)
                msg_path = thread_dir / f"msg_{msg.message_id}.json"
                msg_path.write_text(annotated.to_json(indent=2), encoding="utf-8")

    return {
        "total_messages": len(messages),
        "anomalies_detected": len(anomalies),
        "messages_to_annotate": len(messages_to_annotate),
        "already_annotated": already_annotated,
        "details": details,
    }


def annotate_all(mailbox: Mailbox, dry_run: bool = True) -> dict[str, any]:
    """
    Annotate all threads for identity hallucinations.

    Returns summary dict with:
    - total_threads: Total number of threads
    - threads_with_anomalies: Number of threads with anomalies
    - total_messages_to_annotate: Total number of messages to annotate across all threads
    - summaries: Per-thread summary
    """
    threads = mailbox.list_threads()

    summaries = {}
    threads_with_anomalies = 0
    total_messages_to_annotate = 0

    for thread in threads:
        summary = annotate_thread(mailbox, thread.thread_id, dry_run=dry_run)
        summaries[thread.thread_id] = summary

        if summary["messages_to_annotate"] > 0:
            threads_with_anomalies += 1
            total_messages_to_annotate += summary["messages_to_annotate"]

    return {
        "total_threads": len(threads),
        "threads_with_anomalies": threads_with_anomalies,
        "total_messages_to_annotate": total_messages_to_annotate,
        "summaries": summaries,
    }


def print_summary(summary: dict[str, any]) -> None:
    """Print annotation summary in a readable format."""
    print("\n=== Annotation Summary ===")
    print(f"Total threads: {summary['total_threads']}")
    print(f"Threads with anomalies: {summary['threads_with_anomalies']}")
    print(f"Total messages to annotate: {summary['total_messages_to_annotate']}")

    for thread_id, thread_summary in summary["summaries"].items():
        if thread_summary["messages_to_annotate"] > 0:
            print(f"\n  Thread: {thread_id}")
            print(f"    Total messages: {thread_summary['total_messages']}")
            print(f"    Anomalies detected: {thread_summary['anomalies_detected']}")
            print(f"    Messages to annotate: {thread_summary['messages_to_annotate']}")
            print(f"    Already annotated: {thread_summary['already_annotated']}")
            if thread_summary["details"]:
                print("    Details:")
                for detail in thread_summary["details"]:
                    print(f"      {detail}")


if __name__ == "__main__":
    import sys

    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv

    mailbox = Mailbox()

    if dry_run:
        print("Running in dry-run mode (no changes will be made)")
        print("Use --force to apply annotations\n")

    summary = annotate_all(mailbox, dry_run=dry_run)
    print_summary(summary)

    if not dry_run:
        print("\n✅ Annotation completed")
    else:
        print("\nℹ️  Dry-run completed. Use --force to apply changes.")
