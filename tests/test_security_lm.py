"""SEC-LM-01 caller_signature HMAC verification + SEC-LM-02 SQL guard tests."""

import hashlib
import hmac
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from lingmessage.lingbus import LingBus

_TEST_SECRET = "test_caller_secret_123"


def _sign(identity: str, secret: str = _TEST_SECRET) -> str:
    return hmac.new(
        secret.encode(), identity.encode(), hashlib.sha256
    ).hexdigest()


# ── SEC-LM-02: execute_readonly / execute_write / ensure_table ──


class TestExecuteReadonly:
    def test_select_allowed(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            bus.open_thread(topic="t", sender="lingflow", recipients=["lingclaude"])
            rows = bus.execute_readonly("SELECT COUNT(*) AS c FROM messages")
            assert rows[0]["c"] >= 1

    def test_pragma_allowed(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            rows = bus.execute_readonly("PRAGMA journal_mode")
            assert rows[0]["journal_mode"] == "wal"

    def test_insert_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="execute_readonly only allows"):
                bus.execute_readonly("INSERT INTO messages DEFAULT VALUES")

    def test_delete_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="execute_readonly only allows"):
                bus.execute_readonly("DELETE FROM messages")

    def test_update_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="execute_readonly only allows"):
                bus.execute_readonly("UPDATE messages SET body='x'")

    def test_drop_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="execute_readonly only allows"):
                bus.execute_readonly("DROP TABLE messages")


class TestExecuteWrite:
    def test_update_allowed(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            tid, mid = bus.open_thread(topic="t", sender="lingflow", recipients=["lingclaude"])
            bus.execute_write("UPDATE messages SET body = ? WHERE message_id = ?", ("updated", mid))
            rows = bus.execute_readonly("SELECT body FROM messages WHERE message_id = ?", (mid,))
            assert rows[0]["body"] == "updated"

    def test_insert_allowed(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            _, mid = bus.open_thread(topic="t", sender="lingflow", recipients=["lingclaude"])
            bus.execute_write("INSERT INTO delivery_attempts (message_id, recipient) VALUES (?, ?)", (mid, "lingclaude"))
            rows = bus.execute_readonly("SELECT COUNT(*) AS c FROM delivery_attempts")
            assert rows[0]["c"] >= 1

    def test_drop_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="execute_write forbidden: DROP"):
                bus.execute_write("DROP TABLE messages")

    def test_alter_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="execute_write forbidden: ALTER"):
                bus.execute_write("ALTER TABLE messages ADD COLUMN x TEXT")

    def test_create_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="execute_write forbidden: CREATE"):
                bus.execute_write("CREATE TABLE evil (id TEXT)")

    def test_attach_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="execute_write forbidden: ATTACH"):
                bus.execute_write("ATTACH DATABASE '/tmp/evil.db' AS evil")

    def test_detach_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="execute_write forbidden: DETACH"):
                bus.execute_write("DETACH DATABASE evil")


class TestEnsureTable:
    def test_create_table_if_not_exists_allowed(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            bus.ensure_table(
                "CREATE TABLE IF NOT EXISTS test_sec (id TEXT PRIMARY KEY, val TEXT)"
            )
            rows = bus.execute_readonly(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='test_sec'"
            )
            assert len(rows) == 1

    def test_plain_create_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="ensure_table only allows"):
                bus.ensure_table("CREATE TABLE evil (id TEXT)")

    def test_drop_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="ensure_table only allows"):
                bus.ensure_table("DROP TABLE messages")

    def test_select_rejected(self, tmp_path: Path) -> None:
        with LingBus(bus_dir=tmp_path / "bus") as bus:
            with pytest.raises(ValueError, match="ensure_table only allows"):
                bus.ensure_table("SELECT * FROM messages")


# ── SEC-LM-01: _validate_caller HMAC verification ──


class TestValidateCaller:
    def test_valid_signature_passes(self) -> None:
        from mcp_servers.lingbus_server import _validate_caller
        sig = _sign("lingflow")
        with patch.dict(os.environ, {"LINGMESSAGE_CALLER_SECRET": _TEST_SECRET}):
            from mcp_servers import lingbus_server
            old = lingbus_server._CALLER_SECRET
            lingbus_server._CALLER_SECRET = _TEST_SECRET
            try:
                result = _validate_caller("lingflow", sig)
                assert result == "lingflow"
            finally:
                lingbus_server._CALLER_SECRET = old

    def test_wrong_secret_rejected(self) -> None:
        from mcp_servers.lingbus_server import _validate_caller
        sig = _sign("lingflow", "wrong_secret")
        with patch.dict(os.environ, {"LINGMESSAGE_CALLER_SECRET": _TEST_SECRET}):
            from mcp_servers import lingbus_server
            old = lingbus_server._CALLER_SECRET
            lingbus_server._CALLER_SECRET = _TEST_SECRET
            try:
                with pytest.raises(ValueError, match="signature mismatch"):
                    _validate_caller("lingflow", sig)
            finally:
                lingbus_server._CALLER_SECRET = old

    def test_wrong_identity_rejected(self) -> None:
        from mcp_servers.lingbus_server import _validate_caller
        sig = _sign("lingflow")
        with patch.dict(os.environ, {"LINGMESSAGE_CALLER_SECRET": _TEST_SECRET}):
            from mcp_servers import lingbus_server
            old = lingbus_server._CALLER_SECRET
            lingbus_server._CALLER_SECRET = _TEST_SECRET
            try:
                with pytest.raises(ValueError, match="signature mismatch"):
                    _validate_caller("lingclaude", sig)
            finally:
                lingbus_server._CALLER_SECRET = old

    def test_unknown_identity_rejected(self) -> None:
        from mcp_servers.lingbus_server import _validate_caller
        sig = _sign("hacker")
        with patch.dict(os.environ, {"LINGMESSAGE_CALLER_SECRET": _TEST_SECRET}):
            from mcp_servers import lingbus_server
            old = lingbus_server._CALLER_SECRET
            lingbus_server._CALLER_SECRET = _TEST_SECRET
            try:
                with pytest.raises(ValueError, match="unknown identity"):
                    _validate_caller("hacker", sig)
            finally:
                lingbus_server._CALLER_SECRET = old

    def test_empty_secret_rejected(self) -> None:
        from mcp_servers.lingbus_server import _validate_caller
        sig = _sign("lingflow")
        from mcp_servers import lingbus_server
        old = lingbus_server._CALLER_SECRET
        lingbus_server._CALLER_SECRET = ""
        try:
            with pytest.raises(ValueError, match="LINGMESSAGE_CALLER_SECRET not set"):
                _validate_caller("lingflow", sig)
        finally:
            lingbus_server._CALLER_SECRET = old

    def test_tampered_signature_rejected(self) -> None:
        from mcp_servers.lingbus_server import _validate_caller
        tampered = "0" * 64
        from mcp_servers import lingbus_server
        old = lingbus_server._CALLER_SECRET
        lingbus_server._CALLER_SECRET = _TEST_SECRET
        try:
            with pytest.raises(ValueError, match="signature mismatch"):
                _validate_caller("lingflow", tampered)
        finally:
            lingbus_server._CALLER_SECRET = old


# ── SEC-LM-01: write endpoint caller_signature fallback ──


class _CallerSigHelper:
    """Shared helper for endpoint-level caller_signature tests."""

    @staticmethod
    def _setup_secret():
        from mcp_servers import lingbus_server
        old = lingbus_server._CALLER_SECRET
        lingbus_server._CALLER_SECRET = _TEST_SECRET
        return old

    @staticmethod
    def _restore_secret(old):
        from mcp_servers import lingbus_server
        lingbus_server._CALLER_SECRET = old


class TestOpenThreadCallerSig(_CallerSigHelper):
    def test_with_valid_signature(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import open_thread
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                sig = _sign("lingflow")
                result = open_thread(
                    "signed topic", sender="lingflow", recipients="lingclaude",
                    channel="ecosystem", body="signed body",
                    db_path=str(db_dir), caller_signature=sig,
                )
                assert "thread_id" in result
        finally:
            self._restore_secret(old)

    def test_with_bad_signature_rejected(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import open_thread
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                with pytest.raises(ValueError, match="signature mismatch"):
                    open_thread(
                        "bad sig", sender="lingflow", recipients="lingclaude",
                        channel="ecosystem", body="x",
                        db_path=str(db_dir), caller_signature="bad" * 16,
                    )
        finally:
            self._restore_secret(old)

    def test_without_signature_fallback(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import open_thread
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                result = open_thread(
                    "no sig", sender="lingflow", recipients="lingclaude",
                    channel="ecosystem", body="fallback", db_path=str(db_dir),
                )
                assert "thread_id" in result
        finally:
            self._restore_secret(old)

    def test_without_signature_unknown_sender_rejected(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import open_thread
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                with pytest.raises(ValueError, match="unknown identity"):
                    open_thread(
                        "hacker", sender="hacker", recipients="lingclaude",
                        channel="ecosystem", body="x", db_path=str(db_dir),
                    )
        finally:
            self._restore_secret(old)


class TestGovernanceProposeForcedSig(_CallerSigHelper):
    """SEC-LM-01 Phase 2: governance_propose requires signature."""

    def test_without_signature_rejected(self, tmp_path: Path) -> None:
        from mcp_servers.lingbus_server import governance
        old = self._setup_secret()
        try:
            with pytest.raises(ValueError, match="signature mismatch|LINGMESSAGE_CALLER_SECRET"):
                governance(
                    command="propose",
                    proposer="lingflow", recipients="lingclaude",
                    topic="t", body="b",
                )
        finally:
            self._restore_secret(old)

    def test_with_valid_signature(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import governance
        old = self._setup_secret()
        try:
            old_force = lingbus_server._FORCE_SIGNATURE_ENDPOINTS
            lingbus_server._FORCE_SIGNATURE_ENDPOINTS = True
            try:
                sig = _sign("lingflow")
                result = governance(
                    command="propose",
                    proposer="lingflow", recipients="lingclaude",
                    topic="signed propose", body="b",
                    caller_signature=sig,
                )
                assert "thread_id" in result
            finally:
                lingbus_server._FORCE_SIGNATURE_ENDPOINTS = old_force
        finally:
            self._restore_secret(old)

    def test_with_bad_signature_rejected(self, tmp_path: Path) -> None:
        from mcp_servers.lingbus_server import governance
        old = self._setup_secret()
        try:
            with pytest.raises(ValueError, match="signature mismatch"):
                governance(
                    command="propose",
                    proposer="lingflow", recipients="lingclaude",
                    topic="bad", body="b",
                    caller_signature="f" * 64,
                )
        finally:
            self._restore_secret(old)


class TestGovernanceVoteForcedSig(_CallerSigHelper):
    """SEC-LM-01 Phase 2: governance_vote requires signature."""

    def test_without_signature_rejected(self, tmp_path: Path) -> None:
        from mcp_servers.lingbus_server import governance
        old = self._setup_secret()
        try:
            with pytest.raises(ValueError, match="signature mismatch|LINGMESSAGE_CALLER_SECRET"):
                governance(
                    command="vote",
                    thread_id="fake_tid", voter="lingflow",
                    vote="approve",
                )
        finally:
            self._restore_secret(old)

    def test_with_valid_signature(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import governance
        old = self._setup_secret()
        try:
            old_force = lingbus_server._FORCE_SIGNATURE_ENDPOINTS
            lingbus_server._FORCE_SIGNATURE_ENDPOINTS = True
            try:
                sig = _sign("lingflow")
                t = governance(
                    command="propose",
                    proposer="lingflow", recipients="lingclaude",
                    topic="vote test", body="b",
                    caller_signature=sig,
                )
                sig2 = _sign("lingclaude")
                r = governance(
                    command="vote",
                    thread_id=t["thread_id"], voter="lingclaude",
                    vote="approve", caller_signature=sig2,
                )
                assert "message_id" in r
            finally:
                lingbus_server._FORCE_SIGNATURE_ENDPOINTS = old_force
        finally:
            self._restore_secret(old)


class TestRedzoneForcedSig(_CallerSigHelper):
    """SEC-LM-01 Phase 2: redzone_request_approval requires signature."""

    def test_without_signature_rejected(self, tmp_path: Path) -> None:
        from mcp_servers.lingbus_server import redzone_request_approval
        old = self._setup_secret()
        try:
            with pytest.raises(ValueError, match="signature mismatch|LINGMESSAGE_CALLER_SECRET"):
                redzone_request_approval(
                    requester="lingflow", category="other",
                    reason="test", target="test",
                )
        finally:
            self._restore_secret(old)

    def test_with_valid_signature(self, tmp_path: Path) -> None:
        import uuid
        from lingmessage.lingbus import LingBus
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import redzone_request_approval
        old = self._setup_secret()
        temp_dir = tmp_path / "redzone_bus"
        temp_dir.mkdir()
        temp_bus = LingBus(temp_dir)
        try:
            old_force = lingbus_server._FORCE_SIGNATURE_ENDPOINTS
            lingbus_server._FORCE_SIGNATURE_ENDPOINTS = True
            try:
                sig = _sign("lingflow")
                nonce = uuid.uuid4().hex[:8]
                with patch.object(lingbus_server, "_get_bus", return_value=temp_bus):
                    result = redzone_request_approval(
                        requester="lingflow", category="other",
                        reason=f"test-{nonce}", target=f"test-{nonce}",
                        caller_signature=sig,
                    )
                assert "thread_id" in result
            finally:
                lingbus_server._FORCE_SIGNATURE_ENDPOINTS = old_force
        finally:
            self._restore_secret(old)


# ── report_deletion_event: delete_watcher -> LingBus alert ──


class TestReportDeletionEvent(_CallerSigHelper):
    def test_without_signature_rejected(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import admin
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                with pytest.raises(ValueError, match="signature mismatch|LINGMESSAGE_CALLER_SECRET"):
                    admin(
                        command="report_deletion",
                        caller="lingmessage",
                        file_path="/tmp/test.log",
                        process_name="rm",
                        process_pid=1234,
                        db_path=str(db_dir),
                    )
        finally:
            self._restore_secret(old)

    def test_unknown_caller_rejected(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import admin
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                with pytest.raises(ValueError, match="unknown identity"):
                    admin(
                        command="report_deletion",
                        caller="hacker",
                        caller_signature="",
                        file_path="/tmp/test",
                        db_path=str(db_dir),
                    )
        finally:
            self._restore_secret(old)

    def test_invalid_event_type_rejected(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import admin
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                sig = _sign("lingmessage")
                with pytest.raises(ValueError, match="invalid event_type"):
                    admin(
                        command="report_deletion",
                        caller="lingmessage",
                        caller_signature=sig,
                        event_type="INVALID",
                        db_path=str(db_dir),
                    )
        finally:
            self._restore_secret(old)

    def test_with_signature(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import admin
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                sig = _sign("lingmessage")
                result = admin(
                    command="report_deletion",
                    caller="lingmessage",
                    caller_signature=sig,
                    event_type="BLOCKED",
                    file_path="/home/ai/important.txt",
                    process_name="rm",
                    db_path=str(db_dir),
                )
                assert "thread_id" in result
        finally:
            self._restore_secret(old)


class TestReportDeletionEventForcedSig(_CallerSigHelper):
    """SEC-LM-01 Phase 2: report_deletion_event requires signature when _FORCE_SIGNATURE_ENDPOINTS."""


# ── log_operation: red-zone operation audit log ──


class TestLogOperation(_CallerSigHelper):
    def test_without_signature_rejected(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import log_operation
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                with pytest.raises(ValueError, match="signature mismatch|LINGMESSAGE_CALLER_SECRET"):
                    log_operation(
                        caller="lingflow",
                        operation="rm",
                        target="/home/ai/lingflow/.crush/",
                        intent="清理过期会话数据",
                        db_path=str(db_dir),
                    )
        finally:
            self._restore_secret(old)

    def test_unknown_caller_rejected(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import log_operation
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                with pytest.raises(ValueError, match="unknown identity"):
                    log_operation(
                        caller="hacker",
                        caller_signature="",
                        operation="rm",
                        target="/etc/passwd",
                        db_path=str(db_dir),
                    )
        finally:
            self._restore_secret(old)

    def test_invalid_category_rejected(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import log_operation
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                sig = _sign("lingflow")
                with pytest.raises(ValueError, match="invalid category"):
                    log_operation(
                        caller="lingflow",
                        caller_signature=sig,
                        operation="rm",
                        target="/tmp/test",
                        category="invalid_cat",
                        db_path=str(db_dir),
                    )
        finally:
            self._restore_secret(old)

    def test_with_signature(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import log_operation
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                sig = _sign("lingclaude")
                result = log_operation(
                    caller="lingclaude",
                    caller_signature=sig,
                    operation="pip install",
                    target="malicious-pkg",
                    category="dangerous",
                    intent="测试安全审计",
                    result="success",
                    rollback_plan="pip uninstall malicious-pkg",
                    db_path=str(db_dir),
                )
                assert "thread_id" in result
        finally:
            self._restore_secret(old)

    def test_all_optional_fields(self, tmp_path: Path) -> None:
        from mcp_servers import lingbus_server
        from mcp_servers.lingbus_server import log_operation
        old = self._setup_secret()
        db_dir = tmp_path / "bus.db"
        try:
            with patch.object(lingbus_server, "_ALLOWED_DB_PREFIX", db_dir.parent):
                sig = _sign("lingmessage")
                result = log_operation(
                    caller="lingmessage",
                    caller_signature=sig,
                    operation="systemctl restart",
                    target="zhineng-api",
                    category="dangerous",
                    intent="释放PG连接池泄漏",
                    result="success",
                    rollback_plan="systemctl start zhineng-api",
                    db_path=str(db_dir),
                )
                assert "thread_id" in result
        finally:
            self._restore_secret(old)
