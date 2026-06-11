from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from lingmessage.cli import (
    cmd_session_archive,
    cmd_session_checkpoint,
    cmd_session_create,
    cmd_session_info,
    cmd_session_list,
    cmd_session_restore,
    main,
)


def _ns(**kwargs) -> argparse.Namespace:
    defaults = {
        "command": "session-create",
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


_db_counter = 0


def _unique_db(tmp_path: Path) -> str:
    global _db_counter
    _db_counter += 1
    return str(tmp_path / f"sessions_{_db_counter}.db")


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from lingmessage import session_manager as sm
    db = _unique_db(tmp_path)
    monkeypatch.setattr(sm, "DEFAULT_DB_PATH", Path(db))


class TestCmdSessionCreate:
    def test_creates_session(self, capsys: pytest.CaptureFixture[str]):
        args = _ns(member_id="lingflow", slot="default", key="k1", thread="t1")
        cmd_session_create(args)
        out = capsys.readouterr().out
        assert "session_id=lingflow:default" in out
        assert "status=active" in out
        assert "member=lingflow" in out

    def test_minimal_args(self, capsys: pytest.CaptureFixture[str]):
        args = _ns(member_id="lingclaude", slot="work", key="", thread="")
        cmd_session_create(args)
        out = capsys.readouterr().out
        assert "session_id=lingclaude:work" in out


class TestCmdSessionCheckpoint:
    def test_checkpoint_existing(self, capsys: pytest.CaptureFixture[str]):
        cmd_session_create(_ns(member_id="lingzhi", slot="default", key="", thread=""))

        args = _ns(command="session-checkpoint", session_id="lingzhi:default", key="new-key", thread="new-thread")
        cmd_session_checkpoint(args)
        out = capsys.readouterr().out
        assert "status=checkpointed" in out

    def test_checkpoint_no_extras(self, capsys: pytest.CaptureFixture[str]):
        cmd_session_create(_ns(member_id="lingresearch", slot="default", key="", thread=""))

        args = _ns(command="session-checkpoint", session_id="lingresearch:default", key="", thread="")
        cmd_session_checkpoint(args)
        out = capsys.readouterr().out
        assert "status=checkpointed" in out


class TestCmdSessionRestore:
    def test_restore_existing(self, capsys: pytest.CaptureFixture[str]):
        cmd_session_create(_ns(member_id="lingminopt", slot="default", key="rk", thread="rt"))

        args = _ns(command="session-restore", session_id="lingminopt:default")
        cmd_session_restore(args)
        out = capsys.readouterr().out
        assert "member_id=lingminopt" in out
        assert "slot_id=default" in out
        assert "session_key=rk" in out

    def test_restore_missing_raises(self):
        args = _ns(command="session-restore", session_id="nobody:missing")
        with pytest.raises(KeyError):
            cmd_session_restore(args)


class TestCmdSessionArchive:
    def test_archive_existing(self, capsys: pytest.CaptureFixture[str]):
        cmd_session_create(_ns(member_id="lingyang", slot="default", key="", thread=""))

        args = _ns(command="session-archive", session_id="lingyang:default")
        cmd_session_archive(args)
        out = capsys.readouterr().out
        assert "status=archived" in out


class TestCmdSessionList:
    def test_list_empty(self, capsys: pytest.CaptureFixture[str]):
        args = _ns(command="session-list", member=None, status=None)
        cmd_session_list(args)
        out = capsys.readouterr().out
        assert "无会话" in out

    def test_list_with_sessions(self, capsys: pytest.CaptureFixture[str]):
        cmd_session_create(_ns(member_id="lingflow", slot="s1", key="", thread=""))
        cmd_session_create(_ns(member_id="lingclaude", slot="s1", key="", thread=""))

        args = _ns(command="session-list", member=None, status=None)
        cmd_session_list(args)
        out = capsys.readouterr().out
        assert "lingflow:s1" in out
        assert "lingclaude:s1" in out

    def test_list_filter_member(self, capsys: pytest.CaptureFixture[str]):
        cmd_session_create(_ns(member_id="lingflow", slot="default", key="", thread=""))
        cmd_session_create(_ns(member_id="lingclaude", slot="default", key="", thread=""))
        capsys.readouterr()

        args = _ns(command="session-list", member="lingflow", status=None)
        cmd_session_list(args)
        out = capsys.readouterr().out
        assert "lingflow:default" in out
        assert "lingclaude" not in out


class TestCmdSessionInfo:
    def test_info_existing(self, capsys: pytest.CaptureFixture[str]):
        cmd_session_create(_ns(member_id="lingweb", slot="default", key="", thread=""))

        args = _ns(command="session-info", session_id="lingweb:default")
        cmd_session_info(args)
        out = capsys.readouterr().out
        assert "session_id=lingweb:default" in out
        assert "member_id=lingweb" in out
        assert "status=active" in out
        assert "messages=" in out

    def test_info_missing_raises(self):
        args = _ns(command="session-info", session_id="ghost:none")
        with pytest.raises(KeyError):
            cmd_session_info(args)


class TestSessionCLIMain:
    def test_session_create_via_main(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
        from lingmessage import session_manager as sm
        db = _unique_db(tmp_path)
        monkeypatch.setattr(sm, "DEFAULT_DB_PATH", Path(db))

        with patch("sys.argv", ["lingmessage", "session-create", "lingflow", "--slot", "via-main"]):
            main()
        out = capsys.readouterr().out
        assert "session_id=lingflow:via-main" in out

    def test_session_list_via_main(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
        from lingmessage import session_manager as sm
        db = _unique_db(tmp_path)
        monkeypatch.setattr(sm, "DEFAULT_DB_PATH", Path(db))

        with patch("sys.argv", ["lingmessage", "session-create", "lingzhi"]):
            main()
        capsys.readouterr()

        with patch("sys.argv", ["lingmessage", "session-list"]):
            main()
        out = capsys.readouterr().out
        assert "lingzhi:default" in out
