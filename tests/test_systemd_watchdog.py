"""Tests for ling_systemd_watchdog module."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch


# Import from the scripts directory
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from ling_systemd_watchdog import (
    ServiceStatus,
    WatchdogState,
    query_service,
    scan_once,
    send_alert,
)


class TestServiceStatus:
    def test_is_active_when_active(self):
        s = ServiceStatus(
            service_name="test.service",
            active_state="active",
            nrestarts=0,
            result="success",
            enter_time="2026-01-01",
        )
        assert s.is_active is True

    def test_is_active_when_inactive(self):
        s = ServiceStatus(
            service_name="test.service",
            active_state="inactive",
            nrestarts=0,
            result="success",
            enter_time="",
        )
        assert s.is_active is False

    def test_to_dict(self):
        s = ServiceStatus(
            service_name="test.service",
            active_state="active",
            nrestarts=2,
            result="success",
            enter_time="2026-01-01",
            units="test.service",
        )
        d = s.to_dict()
        assert d["service"] == "test.service"
        assert d["active_state"] == "active"
        assert d["nrestarts"] == 2
        assert d["units"] == "test.service"


class TestQueryService:
    @patch("ling_systemd_watchdog.subprocess.run")
    def test_active_service_zero_restarts(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ActiveState=active\nNRestarts=0\nResult=success\nActiveEnterTimestamp=Mon 2026-05-06 10:00:00 UTC\nNames=test.service\n",
        )
        status = query_service("test.service")
        assert status.active_state == "active"
        assert status.nrestarts == 0
        assert status.is_active is True

    @patch("ling_systemd_watchdog.subprocess.run")
    def test_service_with_restarts(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="ActiveState=active\nNRestarts=5\nResult=success\nActiveEnterTimestamp=Mon 2026-05-06 10:00:00 UTC\nNames=test.service\n",
        )
        status = query_service("test.service")
        assert status.nrestarts == 5

    @patch("ling_systemd_watchdog.subprocess.run")
    def test_not_found_service(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Unit test.service could not be found.\n",
        )
        status = query_service("test.service")
        assert status.active_state == "not-found"

    @patch("ling_systemd_watchdog.subprocess.run")
    def test_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="systemctl", timeout=10)
        status = query_service("test.service")
        assert status.active_state == "unknown"

    @patch("ling_systemd_watchdog.subprocess.run")
    def test_systemctl_not_found(self, mock_run):
        mock_run.side_effect = FileNotFoundError("systemctl not found")
        status = query_service("test.service")
        assert status.active_state == "unknown"


class TestWatchdogState:
    def test_initial_state(self, tmp_path):
        state = WatchdogState(path=tmp_path / "state.json")
        assert state.last_alerted_restarts("test.service") == 0

    def test_record_and_read_alert(self, tmp_path):
        state = WatchdogState(path=tmp_path / "state.json")
        state.record_alert("test.service", 5)
        assert state.last_alerted_restarts("test.service") == 5

    def test_reset_service(self, tmp_path):
        state = WatchdogState(path=tmp_path / "state.json")
        state.record_alert("test.service", 5)
        state.reset_service("test.service")
        assert state.last_alerted_restarts("test.service") == 0

    def test_persistence(self, tmp_path):
        path = tmp_path / "state.json"
        state1 = WatchdogState(path=path)
        state1.record_alert("svc1.service", 3)
        state1.record_alert("svc2.service", 7)

        state2 = WatchdogState(path=path)
        assert state2.last_alerted_restarts("svc1.service") == 3
        assert state2.last_alerted_restarts("svc2.service") == 7

    def test_corrupted_state_file(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("not valid json{{{")
        state = WatchdogState(path=path)
        assert state.last_alerted_restarts("test.service") == 0


class TestScanOnce:
    @patch("ling_systemd_watchdog.query_service")
    def test_no_alert_when_below_threshold(self, mock_query):
        mock_query.return_value = ServiceStatus(
            service_name="test.service",
            active_state="active",
            nrestarts=1,
            result="success",
            enter_time="2026-01-01",
        )
        result = scan_once(
            services=["test.service"],
            threshold=3,
            dry_run=True,
        )
        assert result["scanned"] == 1
        assert len(result["alerts"]) == 0

    @patch("ling_systemd_watchdog.query_service")
    def test_alert_when_above_threshold(self, mock_query):
        mock_query.return_value = ServiceStatus(
            service_name="test.service",
            active_state="active",
            nrestarts=5,
            result="success",
            enter_time="2026-01-01",
        )
        result = scan_once(
            services=["test.service"],
            threshold=3,
            dry_run=True,
        )
        assert len(result["alerts"]) == 1
        assert "test.service" in result["alerts"][0]
        assert "DRY-RUN" in result["alerts"][0]

    @patch("ling_systemd_watchdog.send_alert")
    @patch("ling_systemd_watchdog.query_service")
    def test_real_alert_sent(self, mock_query, mock_alert):
        mock_query.return_value = ServiceStatus(
            service_name="test.service",
            active_state="active",
            nrestarts=5,
            result="success",
            enter_time="2026-01-01",
        )
        mock_alert.return_value = "thread-123"
        result = scan_once(
            services=["test.service"],
            threshold=3,
        )
        assert len(result["alerts"]) == 1
        mock_alert.assert_called_once()

    @patch("ling_systemd_watchdog.query_service")
    def test_no_duplicate_alert(self, mock_query, tmp_path):
        mock_query.return_value = ServiceStatus(
            service_name="test.service",
            active_state="active",
            nrestarts=5,
            result="success",
            enter_time="2026-01-01",
        )
        state = WatchdogState(path=tmp_path / "state.json")
        state.record_alert("test.service", 5)
        result = scan_once(
            services=["test.service"],
            threshold=3,
            state=state,
            dry_run=True,
        )
        assert len(result["alerts"]) == 0

    @patch("ling_systemd_watchdog.query_service")
    def test_recovery_resets_state(self, mock_query, tmp_path):
        state = WatchdogState(path=tmp_path / "state.json")
        state.record_alert("test.service", 5)
        mock_query.return_value = ServiceStatus(
            service_name="test.service",
            active_state="active",
            nrestarts=1,
            result="success",
            enter_time="2026-01-01",
        )
        scan_once(services=["test.service"], threshold=3, state=state, dry_run=True)
        assert state.last_alerted_restarts("test.service") == 0

    @patch("ling_systemd_watchdog.query_service")
    def test_inactive_service_no_alert(self, mock_query):
        mock_query.return_value = ServiceStatus(
            service_name="test.service",
            active_state="failed",
            nrestarts=10,
            result="failure",
            enter_time="",
        )
        result = scan_once(
            services=["test.service"],
            threshold=3,
            dry_run=True,
        )
        assert len(result["alerts"]) == 0

    @patch("ling_systemd_watchdog.query_service")
    def test_multiple_services(self, mock_query):
        def make_status(name):
            return ServiceStatus(
                service_name=name,
                active_state="active",
                nrestarts=5 if "bad" in name else 1,
                result="success",
                enter_time="2026-01-01",
            )
        mock_query.side_effect = make_status
        result = scan_once(
            services=["good.service", "bad.service"],
            threshold=3,
            dry_run=True,
        )
        assert result["scanned"] == 2
        assert len(result["alerts"]) == 1
        assert "bad.service" in result["alerts"][0]


class TestSendAlert:
    def test_send_alert_success(self):
        mock_bus = MagicMock()
        mock_bus.open_thread.return_value = ("thread-id", "msg-id")
        tid = send_alert(
            bus=mock_bus,
            service_name="test.service",
            status=ServiceStatus(
                service_name="test.service",
                active_state="active",
                nrestarts=5,
                result="success",
                enter_time="2026-01-01",
            ),
            threshold=3,
        )
        assert tid == "thread-id"
        mock_bus.open_thread.assert_called_once()
        call_kwargs = mock_bus.open_thread.call_args
        assert call_kwargs.kwargs["sender"] == "lingmessage"
        assert call_kwargs.kwargs["recipients"] == "all"
        assert "test.service" in call_kwargs.kwargs["body"]

    def test_send_alert_failure(self):
        mock_bus = MagicMock()
        mock_bus.open_thread.side_effect = Exception("DB error")
        tid = send_alert(
            bus=mock_bus,
            service_name="test.service",
            status=ServiceStatus(
                service_name="test.service",
                active_state="active",
                nrestarts=5,
                result="success",
                enter_time="2026-01-01",
            ),
            threshold=3,
        )
        assert tid is None
