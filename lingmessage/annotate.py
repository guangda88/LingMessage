#!/usr/bin/env python3
"""Historical message source annotation for LingMessage.

Annotates messages with source_type and source_trace according to
the council hall consensus (Step 3 of identity verification plan):

1. Same-second multi-member batches -> GENERATED (identity hallucination)
2. Discuss engine threads (disc_*) -> INFERRED
3. All other unannotated messages -> INFERRED (historical backfill)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lingmessage.types import SourceType

logger = logging.getLogger(__name__)

GENERATED_GAP_THRESHOLD_SECS = 0
INFERRED_RAPID_THRESHOLD_SECS = 60


@dataclass
class AnnotationResult:
    total_scanned: int = 0
    already_annotated: int = 0
    annotated_generated: int = 0
    annotated_inferred: int = 0
    skipped: int = 0
    errors: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "total_scanned": self.total_scanned,
            "already_annotated": self.already_annotated,
            "annotated_generated": self.annotated_generated,
            "annotated_inferred": self.annotated_inferred,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def _load_raw_messages(threads_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Load all raw message JSON dicts from disk."""
    results: list[tuple[Path, dict[str, Any]]] = []
    if not threads_dir.exists():
        return results
    for thread_dir in sorted(threads_dir.iterdir()):
        if not thread_dir.is_dir():
            continue
        for msg_file in sorted(thread_dir.glob("msg_*.json")):
            try:
                data = json.loads(msg_file.read_text(encoding="utf-8"))
                results.append((msg_file, data))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load %s: %s", msg_file, e)
    return results


def detect_same_second_anomalies(
    messages: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Detect same-second batches with multiple distinct senders.

    Returns dict mapping "thread_id|second" to list of message_ids
    where >= 2 distinct senders posted in the exact same second.
    """
    bucket: dict[str, list[dict[str, str]]] = defaultdict(list)

    for msg in messages:
        ts = msg.get("timestamp", "")
        second = ts[:19] if len(ts) >= 19 else ts
        tid = msg.get("thread_id", "")
        key = f"{tid}|{second}"
        bucket[key].append({
            "message_id": msg.get("message_id", ""),
            "sender": msg.get("sender", ""),
        })

    anomalies: dict[str, list[str]] = {}
    for key, entries in bucket.items():
        senders = {e["sender"] for e in entries}
        if len(senders) >= 2:
            anomalies[key] = [e["message_id"] for e in entries]

    return anomalies


def detect_rapid_succession_batches(
    messages: list[dict[str, Any]],
    threshold_secs: float = INFERRED_RAPID_THRESHOLD_SECS,
) -> list[set[str]]:
    """Detect rapid-succession message batches from different senders.

    Returns list of sets, each set containing message_ids in a batch
    where different senders posted within threshold_secs of each other.
    """
    from datetime import datetime

    by_thread: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for msg in messages:
        by_thread[msg.get("thread_id", "")].append(msg)

    batches: list[set[str]] = []
    for tid, thread_msgs in by_thread.items():
        thread_msgs.sort(key=lambda m: m.get("timestamp", ""))
        current_batch: list[dict[str, Any]] = []

        for msg in thread_msgs:
            if not current_batch:
                current_batch.append(msg)
                continue

            try:
                t1 = datetime.fromisoformat(current_batch[-1].get("timestamp", ""))
                t2 = datetime.fromisoformat(msg.get("timestamp", ""))
                gap = abs((t2 - t1).total_seconds())
            except (ValueError, TypeError):
                gap = float("inf")

            senders = {m.get("sender", "") for m in current_batch}
            new_sender = msg.get("sender", "")

            if gap < threshold_secs and (new_sender != senders or len(senders) > 1):
                current_batch.append(msg)
            else:
                if len({m.get("sender", "") for m in current_batch}) >= 2:
                    batches.append({m.get("message_id", "") for m in current_batch})
                current_batch = [msg]

        if len({m.get("sender", "") for m in current_batch}) >= 2:
            batches.append({m.get("message_id", "") for m in current_batch})

    return batches


def _build_source_trace(
    reason: str,
    extra: dict[str, Any] | None = None,
) -> str:
    """Build a JSON source_trace string."""
    trace: dict[str, Any] = {"annotation_reason": reason}
    if extra:
        trace.update(extra)
    return json.dumps(trace, ensure_ascii=False)


def _write_annotated_message(
    msg_path: Path,
    data: dict[str, Any],
    source_type: SourceType,
    source_trace: str,
) -> None:
    """Write annotated message back to disk using atomic write."""
    data["source_type"] = source_type.value
    data["source_trace"] = source_trace
    content = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=str(msg_path.parent), suffix=".tmp")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, msg_path)
        os.chmod(msg_path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def annotate_all(
    threads_dir: Path,
    dry_run: bool = True,
) -> AnnotationResult:
    """Annotate all messages in threads_dir with source_type and source_trace.

    Classification rules:
    1. Already has source_type -> skip
    2. In same-second multi-member batch -> GENERATED
    3. In rapid-succession batch (<60s, different senders) -> INFERRED (discuss engine)
    4. Thread starts with disc_ -> INFERRED (discuss engine)
    5. Everything else -> INFERRED (historical backfill)
    """
    result = AnnotationResult()
    raw_messages = _load_raw_messages(threads_dir)
    result.total_scanned = len(raw_messages)

    if not raw_messages:
        return result

    all_dicts = [data for _, data in raw_messages]

    anomalies = detect_same_second_anomalies(all_dicts)
    generated_ids: set[str] = set()
    for msg_ids in anomalies.values():
        generated_ids.update(msg_ids)

    rapid_batches = detect_rapid_succession_batches(all_dicts)
    rapid_ids: set[str] = set()
    for batch in rapid_batches:
        rapid_ids.update(batch)

    for msg_path, data in raw_messages:
        msg_id = data.get("message_id", "")
        tid = data.get("thread_id", "")

        if "source_type" in data:
            result.already_annotated += 1
            continue

        if msg_id in generated_ids:
            reason = "same_second_multi_member"
            extra = {
                "anomaly_key": f"{tid}|{data.get('timestamp', '')[:19]}",
            }
            trace = _build_source_trace(reason, extra)
            if not dry_run:
                _write_annotated_message(msg_path, data, SourceType.GENERATED, trace)
            result.annotated_generated += 1
            logger.info("GENERATED: %s in %s", msg_id[:12], tid[:12])

        elif msg_id in rapid_ids:
            reason = "rapid_succession_discussion"
            trace = _build_source_trace(reason)
            if not dry_run:
                _write_annotated_message(msg_path, data, SourceType.INFERRED, trace)
            result.annotated_inferred += 1
            logger.info("INFERRED (rapid): %s in %s", msg_id[:12], tid[:12])

        elif tid.startswith("disc_"):
            reason = "discuss_engine"
            trace = _build_source_trace(reason)
            if not dry_run:
                _write_annotated_message(msg_path, data, SourceType.INFERRED, trace)
            result.annotated_inferred += 1
            logger.info("INFERRED (disc_): %s in %s", msg_id[:12], tid[:12])

        else:
            reason = "historical_backfill"
            trace = _build_source_trace(reason)
            if not dry_run:
                _write_annotated_message(msg_path, data, SourceType.INFERRED, trace)
            result.annotated_inferred += 1
            logger.info("INFERRED (backfill): %s in %s", msg_id[:12], tid[:12])

    return result


def print_report(result: AnnotationResult) -> None:
    """Print annotation report."""
    print("=== 历史数据标注报告 ===")
    print(f"  扫描消息总数: {result.total_scanned}")
    print(f"  已有标注 (跳过): {result.already_annotated}")
    print(f"  新标注 GENERATED: {result.annotated_generated}")
    print(f"  新标注 INFERRED: {result.annotated_inferred}")
    print(f"  错误: {result.errors}")
    newly = result.annotated_generated + result.annotated_inferred
    print(f"  新标注合计: {newly}")
    print(f"  未标注剩余: {result.total_scanned - result.already_annotated - newly}")
