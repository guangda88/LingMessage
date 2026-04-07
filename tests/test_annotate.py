"""Tests for annotate.py — historical data source annotation."""

import json
from pathlib import Path
from typing import Any

import pytest

from lingmessage.annotate import (
    AnnotationResult,
    _build_source_trace,
    annotate_all,
    detect_rapid_succession_batches,
    detect_same_second_anomalies,
    print_report,
)


def _write_msg(
    thread_dir: Path,
    msg_id: str,
    thread_id: str,
    sender: str,
    timestamp: str,
    subject: str = "test",
    body: str = "test body",
    **extra: Any,
) -> Path:
    """Write a test message file."""
    thread_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {
        "message_id": msg_id,
        "thread_id": thread_id,
        "sender": sender,
        "recipient": "all",
        "message_type": "reply",
        "channel": "ecosystem",
        "subject": subject,
        "body": body,
        "timestamp": timestamp,
    }
    data.update(extra)
    p = thread_dir / f"msg_{msg_id}.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


class TestDetectSameSecondAnomalies:
    def test_no_anomalies(self):
        msgs = [
            {"message_id": "a", "thread_id": "t1", "sender": "lingyi", "timestamp": "2026-04-04T01:00:00+00:00"},
            {"message_id": "b", "thread_id": "t1", "sender": "lingclaude", "timestamp": "2026-04-04T01:00:01+00:00"},
        ]
        result = detect_same_second_anomalies(msgs)
        assert result == {}

    def test_same_second_different_senders(self):
        msgs = [
            {"message_id": "a", "thread_id": "t1", "sender": "lingyi", "timestamp": "2026-04-04T01:00:00+00:00"},
            {"message_id": "b", "thread_id": "t1", "sender": "lingflow", "timestamp": "2026-04-04T01:00:00+00:00"},
            {"message_id": "c", "thread_id": "t1", "sender": "lingclaude", "timestamp": "2026-04-04T01:00:00+00:00"},
        ]
        result = detect_same_second_anomalies(msgs)
        key = "t1|2026-04-04T01:00:00"
        assert key in result
        assert set(result[key]) == {"a", "b", "c"}

    def test_same_second_same_sender_not_anomaly(self):
        msgs = [
            {"message_id": "a", "thread_id": "t1", "sender": "lingyi", "timestamp": "2026-04-04T01:00:00+00:00"},
            {"message_id": "b", "thread_id": "t1", "sender": "lingyi", "timestamp": "2026-04-04T01:00:00+00:00"},
        ]
        result = detect_same_second_anomalies(msgs)
        assert result == {}

    def test_different_threads_not_cross_anomaly(self):
        msgs = [
            {"message_id": "a", "thread_id": "t1", "sender": "lingyi", "timestamp": "2026-04-04T01:00:00+00:00"},
            {"message_id": "b", "thread_id": "t2", "sender": "lingflow", "timestamp": "2026-04-04T01:00:00+00:00"},
        ]
        result = detect_same_second_anomalies(msgs)
        assert result == {}


class TestDetectRapidSuccessionBatches:
    def test_single_sender_no_batch(self):
        msgs = [
            {"message_id": "a", "thread_id": "t1", "sender": "lingyi", "timestamp": "2026-04-04T01:00:00+00:00"},
            {"message_id": "b", "thread_id": "t1", "sender": "lingyi", "timestamp": "2026-04-04T01:00:10+00:00"},
        ]
        result = detect_rapid_succession_batches(msgs)
        assert result == []

    def test_rapid_succession_different_senders(self):
        msgs = [
            {"message_id": "a", "thread_id": "t1", "sender": "lingyi", "timestamp": "2026-04-04T01:00:00+00:00"},
            {"message_id": "b", "thread_id": "t1", "sender": "lingflow", "timestamp": "2026-04-04T01:00:12+00:00"},
            {"message_id": "c", "thread_id": "t1", "sender": "lingclaude", "timestamp": "2026-04-04T01:00:25+00:00"},
        ]
        result = detect_rapid_succession_batches(msgs, threshold_secs=60)
        assert len(result) >= 1
        batch_ids = result[0]
        assert "a" in batch_ids
        assert "b" in batch_ids
        assert "c" in batch_ids

    def test_slow_gap_breaks_batch(self):
        msgs = [
            {"message_id": "a", "thread_id": "t1", "sender": "lingyi", "timestamp": "2026-04-04T01:00:00+00:00"},
            {"message_id": "b", "thread_id": "t1", "sender": "lingflow", "timestamp": "2026-04-04T01:05:00+00:00"},
        ]
        result = detect_rapid_succession_batches(msgs, threshold_secs=60)
        assert result == []


class TestAnnotateAll:
    def test_dry_run_no_writes(self, tmp_path: Path):
        td = tmp_path / "threads" / "t1"
        _write_msg(td, "m1", "t1", "lingyi", "2026-04-04T01:00:00+00:00")

        result = annotate_all(tmp_path / "threads", dry_run=True)
        assert result.annotated_inferred == 1
        assert result.total_scanned == 1

        raw = json.loads((td / "msg_m1.json").read_text())
        assert "source_type" not in raw

    def test_force_writes_inferred(self, tmp_path: Path):
        td = tmp_path / "threads" / "t1"
        _write_msg(td, "m1", "t1", "lingyi", "2026-04-04T01:00:00+00:00")

        result = annotate_all(tmp_path / "threads", dry_run=False)
        assert result.annotated_inferred == 1

        raw = json.loads((td / "msg_m1.json").read_text())
        assert raw["source_type"] == "inferred"
        trace = json.loads(raw["source_trace"])
        assert trace["annotation_reason"] == "historical_backfill"

    def test_force_writes_generated_for_anomaly(self, tmp_path: Path):
        td = tmp_path / "threads" / "t1"
        ts = "2026-04-04T01:00:00+00:00"
        _write_msg(td, "m1", "t1", "lingyi", ts)
        _write_msg(td, "m2", "t1", "lingflow", ts)

        result = annotate_all(tmp_path / "threads", dry_run=False)
        assert result.annotated_generated == 2

        for mid in ("m1", "m2"):
            raw = json.loads((td / f"msg_{mid}.json").read_text())
            assert raw["source_type"] == "generated"
            trace = json.loads(raw["source_trace"])
            assert trace["annotation_reason"] == "same_second_multi_member"

    def test_skips_already_annotated(self, tmp_path: Path):
        td = tmp_path / "threads" / "t1"
        _write_msg(td, "m1", "t1", "lingyi", "2026-04-04T01:00:00+00:00", source_type="inferred")

        result = annotate_all(tmp_path / "threads", dry_run=False)
        assert result.already_annotated == 1
        assert result.annotated_inferred == 0
        assert result.annotated_generated == 0

    def test_disc_thread_gets_inferred(self, tmp_path: Path):
        td = tmp_path / "threads" / "disc_20260407000000"
        _write_msg(td, "m1", "disc_20260407000000", "lingyi", "2026-04-07T00:00:00+00:00")

        result = annotate_all(tmp_path / "threads", dry_run=False)
        assert result.annotated_inferred == 1

        raw = json.loads((td / "msg_m1.json").read_text())
        trace = json.loads(raw["source_trace"])
        assert trace["annotation_reason"] == "discuss_engine"

    def test_empty_threads_dir(self, tmp_path: Path):
        result = annotate_all(tmp_path / "threads", dry_run=True)
        assert result.total_scanned == 0

    def test_mixed_messages(self, tmp_path: Path):
        td1 = tmp_path / "threads" / "t1"
        ts = "2026-04-04T01:00:00+00:00"
        _write_msg(td1, "m1", "t1", "lingyi", ts)
        _write_msg(td1, "m2", "t1", "lingflow", ts)
        _write_msg(td1, "m3", "t1", "lingclaude", "2026-04-04T01:01:00+00:00", source_type="generated")

        td2 = tmp_path / "threads" / "t2"
        _write_msg(td2, "m4", "t2", "lingyi", "2026-04-04T02:00:00+00:00")

        result = annotate_all(tmp_path / "threads", dry_run=False)
        assert result.total_scanned == 4
        assert result.already_annotated == 1
        assert result.annotated_generated == 2
        assert result.annotated_inferred == 1


class TestBuildSourceTrace:
    def test_reason_only(self):
        trace = _build_source_trace("test_reason")
        data = json.loads(trace)
        assert data["annotation_reason"] == "test_reason"
        assert len(data) == 1

    def test_with_extra(self):
        trace = _build_source_trace("test", {"key": "value"})
        data = json.loads(trace)
        assert data["annotation_reason"] == "test"
        assert data["key"] == "value"


class TestAnnotationResult:
    def test_to_dict(self):
        r = AnnotationResult(total_scanned=10, annotated_inferred=5)
        d = r.to_dict()
        assert d["total_scanned"] == 10
        assert d["annotated_inferred"] == 5


class TestPrintReport:
    def test_prints_without_error(self, capsys: pytest.CaptureFixture[str]):
        r = AnnotationResult(total_scanned=100, already_annotated=50, annotated_generated=10, annotated_inferred=35)
        print_report(r)
        output = capsys.readouterr().out
        assert "扫描消息总数: 100" in output
        assert "新标注 GENERATED: 10" in output
        assert "新标注 INFERRED: 35" in output
