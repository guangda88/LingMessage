from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from lingmessage.cli import (
    cmd_continue,
    cmd_discuss,
    cmd_import,
    cmd_list,
    cmd_read,
    cmd_reply,
    cmd_seed,
    cmd_send,
    cmd_stats,
    cmd_sync,
    main,
)
from lingmessage.mailbox import Mailbox
from lingmessage.types import Channel, LingIdentity


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "mailbox": None,
        "command": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


@pytest.fixture
def mb_path(tmp_path: Path) -> Path:
    return tmp_path / "mb"


@pytest.fixture
def seeded_mb(mb_path: Path) -> Path:
    mb = Mailbox(root=mb_path)
    mb.open_thread(
        sender=LingIdentity.LINGFLOW,
        recipients=(LingIdentity.LINGCLAUDE,),
        channel=Channel.ECOSYSTEM,
        topic="test topic",
        subject="hello",
        body="world",
    )
    return mb_path


class TestCmdList:
    def test_list_empty(self, mb_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(command="list", mailbox=str(mb_path), channel=None, status=None, participant=None)
        cmd_list(args)
        out = capsys.readouterr().out
        assert "无讨论串" in out

    def test_list_with_threads(self, seeded_mb: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(command="list", mailbox=str(seeded_mb), channel=None, status=None, participant=None)
        cmd_list(args)
        out = capsys.readouterr().out
        assert "test topic" in out

    def test_list_filter_channel(self, seeded_mb: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(command="list", mailbox=str(seeded_mb), channel="knowledge", status=None, participant=None)
        cmd_list(args)
        out = capsys.readouterr().out
        assert "无讨论串" in out


class TestCmdRead:
    def test_read_existing_thread(self, seeded_mb: Path, capsys: pytest.CaptureFixture[str]) -> None:
        mb = Mailbox(root=seeded_mb)
        threads = mb.list_threads()
        tid = threads[0].thread_id

        args = _ns(command="read", mailbox=str(seeded_mb), thread_id=tid)
        cmd_read(args)
        out = capsys.readouterr().out
        assert "test topic" in out
        assert "灵通" in out

    def test_read_nonexistent_thread(self, mb_path: Path) -> None:
        args = _ns(command="read", mailbox=str(mb_path), thread_id="nonexistent")
        with pytest.raises(SystemExit):
            cmd_read(args)


class TestCmdSend:
    def test_send_creates_thread(self, mb_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(
            command="send", mailbox=str(mb_path),
            sender="lingflow", recipients="lingclaude",
            channel="ecosystem", topic="new topic",
            subject="subj", body="body text",
        )
        cmd_send(args)
        out = capsys.readouterr().out
        assert "已发送" in out
        mb = Mailbox(root=mb_path)
        assert len(mb.list_threads()) == 1


class TestCmdReply:
    def test_reply_adds_message(self, seeded_mb: Path, capsys: pytest.CaptureFixture[str]) -> None:
        mb = Mailbox(root=seeded_mb)
        tid = mb.list_threads()[0].thread_id
        args = _ns(
            command="reply", mailbox=str(seeded_mb),
            thread_id=tid, sender="lingclaude",
            recipient="lingflow", subject="re: hello", body="reply body",
        )
        cmd_reply(args)
        out = capsys.readouterr().out
        assert "已回复" in out
        msgs = mb.load_thread_messages(tid)
        assert len(msgs) == 2


class TestCmdStats:
    def test_stats_empty(self, mb_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(command="stats", mailbox=str(mb_path))
        cmd_stats(args)
        out = capsys.readouterr().out
        assert "讨论串: 0" in out

    def test_stats_with_data(self, seeded_mb: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(command="stats", mailbox=str(seeded_mb))
        cmd_stats(args)
        out = capsys.readouterr().out
        assert "讨论串: 1" in out


class TestCmdSeed:
    def test_seed_creates_threads(self, mb_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(command="seed", mailbox=str(mb_path))
        cmd_seed(args)
        out = capsys.readouterr().out
        assert "已播种" in out
        mb = Mailbox(root=mb_path)
        assert len(mb.list_threads()) == 6


class TestCmdSync:
    def test_sync_runs(self, mb_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(command="sync", mailbox=str(mb_path))
        cmd_sync(args)
        out = capsys.readouterr().out
        assert "共同步" in out


class TestCmdImport:
    def test_import_single_discussion(self, mb_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        disc = {
            "topic": "imported topic",
            "messages": [
                {"from_id": "lingyi", "topic": "hello", "content": "body text"},
            ],
        }
        disc_file = mb_path / "disc.json"
        disc_file.parent.mkdir(parents=True, exist_ok=True)
        disc_file.write_text(json.dumps(disc, ensure_ascii=False), encoding="utf-8")

        args = _ns(command="import", mailbox=str(mb_path), file=str(disc_file))
        cmd_import(args)
        out = capsys.readouterr().out
        assert "已导入" in out

    def test_import_nonexistent_file(self, mb_path: Path) -> None:
        args = _ns(command="import", mailbox=str(mb_path), file="/nonexistent.json")
        with pytest.raises(SystemExit):
            cmd_import(args)


class TestCmdDiscuss:
    @patch("lingmessage.discuss._call_llm", return_value="reply content")
    @patch("lingmessage.discuss._judge_discussion")
    def test_discuss_creates_thread(self, mock_judge, mock_llm, mb_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        mock_judge.return_value = {"should_continue": False, "next_speakers": []}
        args = _ns(
            command="discuss", mailbox=str(mb_path),
            topic="test discuss", body="body text for discuss",
            initiator="lingflow", participants="",
            channel="ecosystem", rounds=1, speakers=1,
        )
        cmd_discuss(args)
        out = capsys.readouterr().out
        assert "讨论完成" in out


class TestCmdContinue:
    @patch("lingmessage.discuss._call_llm", return_value="continued")
    @patch("lingmessage.discuss._judge_discussion")
    def test_continue_existing_thread(self, mock_judge, mock_llm, mb_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        mock_judge.return_value = {"should_continue": True, "next_speakers": ["lingclaude"]}
        mb = Mailbox(root=mb_path)
        header, _ = mb.open_thread(
            sender=LingIdentity.LINGFLOW,
            recipients=(LingIdentity.LINGCLAUDE,),
            channel=Channel.ECOSYSTEM,
            topic="cont topic",
            subject="s",
            body="b",
        )
        args = _ns(
            command="continue", mailbox=str(mb_path),
            thread_id=header.thread_id, rounds=1, speakers=1,
        )
        cmd_continue(args)
        out = capsys.readouterr().out
        assert "讨论继续" in out

    def test_continue_nonexistent_thread(self, mb_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        args = _ns(
            command="continue", mailbox=str(mb_path),
            thread_id="nonexistent", rounds=1, speakers=1,
        )
        cmd_continue(args)
        out = capsys.readouterr().out
        assert "无法继续" in out


class TestMainHelp:
    def test_no_command_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("sys.argv", ["lingmessage"]):
            main()
        out = capsys.readouterr().out
        assert "灵信" in out or "lingmessage" in out

    def test_subprocess_version(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "lingmessage.cli", "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "灵信" in result.stdout or "lingmessage" in result.stdout
