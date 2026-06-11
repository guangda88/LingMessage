"""MCP Server tests — 验证三个 MCP server 的工具可正常调用"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lingmessage.mailbox import Mailbox
from lingmessage.types import Channel, LingIdentity, MessageType, create_message

_MOCK_SECRET = "test_secret_key_for_unit_tests"


def _make_msg(**kwargs):
    defaults = dict(
        sender=LingIdentity.LINGFLOW,
        recipient=LingIdentity.LINGCLAUDE,
        message_type=MessageType.OPEN,
        channel=Channel.ECOSYSTEM,
        subject="test",
        body="hello",
        thread_id="t1",
    )
    defaults.update(kwargs)
    return create_message(**defaults)


class TestSigningServer:
    @patch.dict(os.environ, {"LINGMESSAGE_SIGNING_KEY": _MOCK_SECRET})
    def test_sign_tool(self):
        from mcp_servers.signing_server import sign

        msg = _make_msg()
        sig = sign(msg.to_dict())
        assert len(sig) == 64

    @patch.dict(os.environ, {"LINGMESSAGE_SIGNING_KEY": _MOCK_SECRET})
    def test_verify_tool_valid(self):
        from mcp_servers.signing_server import sign, verify

        msg = _make_msg()
        d = msg.to_dict()
        sig = sign(d)
        result = verify(d, sig)
        assert result["valid"] is True

    @patch.dict(os.environ, {"LINGMESSAGE_SIGNING_KEY": _MOCK_SECRET})
    def test_verify_tool_invalid(self):
        from mcp_servers.signing_server import verify

        msg = _make_msg()
        result = verify(msg.to_dict(), "badsig")
        assert result["valid"] is False

    @patch.dict(os.environ, {"LINGMESSAGE_SIGNING_KEY": _MOCK_SECRET})
    def test_annotate_verified_tool(self):
        from mcp_servers.signing_server import annotate_verified, sign

        msg = _make_msg()
        d = msg.to_dict()
        sig = sign(d)
        result = annotate_verified(d, sig)
        assert result["source_type"] == "verified"
        assert sig in result["source_trace"]

    def test_annotate_verified_rejects_bad_sig(self):
        from mcp_servers.signing_server import annotate_verified

        msg = _make_msg()
        with patch.dict(os.environ, {"LINGMESSAGE_SIGNING_KEY": _MOCK_SECRET}):
            with pytest.raises(ValueError, match="签名验证失败"):
                annotate_verified(msg.to_dict(), "badsig")


class TestAnnotateServer:
    def test_detect_anomalies_empty(self, tmp_path: Path):
        from mcp_servers import annotate_server
        from mcp_servers.annotate_server import detect_anomalies

        threads = tmp_path / "threads"
        threads.mkdir()
        with patch.object(annotate_server, "_ALLOWED_THREADS_PREFIX", tmp_path):
            result = detect_anomalies(str(threads))
        assert result["same_second_anomalies"] == 0

    def test_annotate_dry_run(self, tmp_path: Path):
        from mcp_servers import annotate_server
        from mcp_servers.annotate_server import annotate_messages

        mb = Mailbox(root=tmp_path / "mb")
        mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="test",
            subject="hello",
            body="world",
        )
        threads_dir = tmp_path / "mb" / "threads"
        with patch.object(annotate_server, "_ALLOWED_THREADS_PREFIX", tmp_path):
            result = annotate_messages(str(threads_dir), dry_run=True)
        assert result["dry_run"] is True
        assert result["total_scanned"] >= 1

    def test_annotation_report(self, tmp_path: Path):
        from mcp_servers import annotate_server
        from mcp_servers.annotate_server import annotation_report

        mb = Mailbox(root=tmp_path / "mb")
        mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="test",
            subject="hello",
            body="world",
        )
        threads_dir = tmp_path / "mb" / "threads"
        with patch.object(annotate_server, "_ALLOWED_THREADS_PREFIX", tmp_path):
            report = annotation_report(str(threads_dir))
        assert isinstance(report, str)
        assert len(report) > 0


def test_annotate_rejects_path_traversal():
    from mcp_servers.annotate_server import annotate_messages

    with pytest.raises(ValueError, match="must be under"):
        annotate_messages("/etc/passwd", dry_run=True)


class TestLingBusServer:
    def test_open_thread_and_stats(self, tmp_path: Path):
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import admin, open_thread

        db_dir = tmp_path / "bus.db"
        with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
            result = open_thread("test topic", sender="lingflow", recipients="lingclaude", channel="ecosystem", subject="subj", body="body", db_path=str(db_dir))
            assert "thread_id" in result
            stats = admin(command="stats", caller="lingflow", db_path=str(db_dir))
            assert stats["threads"] >= 1

    def test_reply_and_poll(self, tmp_path: Path):
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import ack_message, open_thread, poll_messages, post_reply

        db_dir = tmp_path / "bus.db"
        with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
            t = open_thread("test topic", sender="lingflow", recipients="lingclaude", channel="ecosystem", subject="subj", body="body", db_path=str(db_dir))
            r = post_reply(t["thread_id"], "lingclaude", "lingflow", "reply", subject="re: subj", db_path=str(db_dir))
            assert "message_id" in r

            msgs = poll_messages("lingclaude", since_rowid=0, limit=10, db_path=str(db_dir))
            assert len(msgs) >= 1

            ack = ack_message(msgs[0]["message_id"], "lingclaude", db_path=str(db_dir))
            assert ack["success"] is True
