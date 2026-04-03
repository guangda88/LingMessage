from __future__ import annotations

import json
from pathlib import Path

from lingmessage.compat import (
    import_lingyi_discussion,
    import_lingyi_store,
    export_to_lingyi_format,
    _resolve_identity,
)
from lingmessage.mailbox import Mailbox
from lingmessage.types import Channel, LingIdentity


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class TestIdentityMapping:
    def test_lingterm_maps_to_lingxi(self) -> None:
        assert _resolve_identity("lingterm") == LingIdentity.LINGXI

    def test_lingflow_maps_correctly(self) -> None:
        assert _resolve_identity("lingflow") == LingIdentity.LINGFLOW

    def test_unknown_defaults_lingyi(self) -> None:
        assert _resolve_identity("unknown_project") == LingIdentity.LINGYI


class TestImportLingYiDiscussion:
    def test_single_message(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        discussion = {
            "topic": "测试讨论",
            "messages": [
                {"id": "msg_001", "from_id": "lingflow", "topic": "灵通发起", "content": "大家好"},
            ],
        }
        result = import_lingyi_discussion(mailbox, discussion)
        assert result is not None
        header = result[0]
        msgs = mailbox.load_thread_messages(header.thread_id)
        assert len(msgs) == 1
        assert msgs[0].body == "大家好"

    def test_multi_message_thread(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        discussion = {
            "topic": "生态讨论",
            "tags": ["生态"],
            "messages": [
                {"id": "msg_001", "from_id": "lingyi", "topic": "灵依发起", "content": "灵依的开场白"},
                {"id": "msg_002", "from_id": "lingclaude", "topic": "灵克回复", "content": "灵克的回复"},
                {"id": "msg_003", "from_id": "lingflow", "topic": "灵通回复", "content": "灵通的回复"},
            ],
        }
        result = import_lingyi_discussion(mailbox, discussion)
        assert result is not None
        header = result[0]
        msgs = mailbox.load_thread_messages(header.thread_id)
        assert len(msgs) == 3

    def test_empty_discussion_returns_none(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        result = import_lingyi_discussion(mailbox, {"topic": "空", "messages": []})
        assert result is None


class TestImportLingYiStore:
    def test_import_from_directory(self, tmp_path: Path) -> None:
        discussions_dir = tmp_path / "discussions"
        _write_json(discussions_dir / "disc_001.json", {
            "topic": "讨论1",
            "messages": [{"id": "m1", "from_id": "lingyi", "content": "Hi"}],
        })
        _write_json(discussions_dir / "disc_002.json", {
            "topic": "讨论2",
            "messages": [{"id": "m2", "from_id": "lingflow", "content": "Hello"}],
        })
        mailbox = Mailbox(root=tmp_path / "mailbox")
        imported = import_lingyi_store(mailbox, lingyi_root=tmp_path)
        assert len(imported) == 2

    def test_no_discussions_dir(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        assert import_lingyi_store(mailbox, lingyi_root=tmp_path) == []


class TestExportToLingYiFormat:
    def test_roundtrip(self, tmp_path: Path) -> None:
        mailbox = Mailbox(root=tmp_path / "mailbox")
        header, _ = mailbox.open_thread(
            sender=LingIdentity.LINGYI,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="测试导出",
            subject="导出测试",
            body="内容",
        )
        mailbox.reply(
            thread_id=header.thread_id,
            sender=LingIdentity.LINGCLAUDE,
            recipient=LingIdentity.LINGYI,
            subject="回复",
            body="回复内容",
        )
        msgs = mailbox.load_thread_messages(header.thread_id)
        exported = export_to_lingyi_format(msgs)
        assert len(exported["messages"]) == 2
        assert exported["messages"][0]["from_id"] == "lingyi"
        assert exported["messages"][1]["from_id"] == "lingclaude"

    def test_empty_messages(self) -> None:
        result = export_to_lingyi_format(())
        assert result["status"] == "empty"
