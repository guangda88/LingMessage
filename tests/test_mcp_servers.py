"""MCP Server tests — 验证三个 MCP server 的工具可正常调用"""

from pathlib import Path

from lingmessage.mailbox import Mailbox
from lingmessage.types import Channel, LingIdentity, MessageType, create_message


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
    def test_sign_tool(self):
        from mcp_servers.signing_server import sign

        msg = _make_msg()
        sig = sign(msg.to_dict(), "secret")
        assert len(sig) == 64

    def test_verify_tool_valid(self):
        from mcp_servers.signing_server import sign, verify

        msg = _make_msg()
        d = msg.to_dict()
        sig = sign(d, "secret")
        result = verify(d, sig, "secret")
        assert result["valid"] is True

    def test_verify_tool_invalid(self):
        from mcp_servers.signing_server import verify

        msg = _make_msg()
        result = verify(msg.to_dict(), "badsig", "secret")
        assert result["valid"] is False

    def test_annotate_verified_tool(self):
        from mcp_servers.signing_server import annotate_verified, sign

        msg = _make_msg()
        d = msg.to_dict()
        sig = sign(d, "secret")
        result = annotate_verified(d, sig)
        assert result["source_type"] == "verified"
        assert sig in result["source_trace"]


class TestAnnotateServer:
    def test_detect_anomalies_empty(self, tmp_path: Path):
        from mcp_servers.annotate_server import detect_anomalies

        threads = tmp_path / "threads"
        threads.mkdir()
        result = detect_anomalies(str(threads))
        assert result["same_second_anomalies"] == 0

    def test_annotate_dry_run(self, tmp_path: Path):
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
        result = annotate_messages(str(threads_dir), dry_run=True)
        assert result["dry_run"] is True
        assert result["total_scanned"] >= 1

    def test_annotation_report(self, tmp_path: Path):
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
        report = annotation_report(str(threads_dir))
        assert isinstance(report, str)
        assert len(report) > 0


class TestLingBusServer:
    def test_open_thread_and_stats(self, tmp_path: Path):
        from mcp_servers.lingbus_server import get_stats, open_thread

        db = str(tmp_path / "bus.db")
        result = open_thread(db, topic="test topic", sender="lingflow", recipients="lingclaude", channel="ecosystem", subject="subj", body="body")
        assert "thread_id" in result
        stats = get_stats(db)
        assert stats["threads"] >= 1

    def test_reply_and_poll(self, tmp_path: Path):
        from mcp_servers.lingbus_server import ack_message, open_thread, poll_messages, post_reply

        db = str(tmp_path / "bus.db")
        t = open_thread(db, topic="test topic", sender="lingflow", recipients="lingclaude", channel="ecosystem", subject="subj", body="body")
        r = post_reply(db, t["thread_id"], "lingclaude", "lingflow", "re: subj", "reply")
        assert "message_id" in r

        msgs = poll_messages(db, "lingclaude", since_rowid=0, limit=10)
        assert len(msgs) >= 1

        ack = ack_message(db, msgs[0]["message_id"], "lingclaude")
        assert ack["success"] is True
