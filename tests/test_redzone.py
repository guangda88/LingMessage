from __future__ import annotations

from pathlib import Path

import pytest

from lingmessage.lingbus import LingBus
from lingmessage.redzone import (
    RedZoneCategory,
    Zone,
    classify_zone,
    require_approval,
)


@pytest.fixture
def bus(tmp_path: Path) -> LingBus:
    b = LingBus(bus_dir=tmp_path / "bus", throttle=False)
    yield b
    b.close()


class TestClassifyZone:
    def test_green_for_safe_ops(self) -> None:
        assert classify_zone("read log file") == Zone.GREEN
        assert classify_zone("check health") == Zone.GREEN
        assert classify_zone("list threads") == Zone.GREEN

    def test_yellow_for_config_ops(self) -> None:
        assert classify_zone("restart proxy") == Zone.YELLOW
        assert classify_zone("update config") == Zone.YELLOW
        assert classify_zone("deploy new version") == Zone.YELLOW

    def test_red_for_kill(self) -> None:
        assert classify_zone("kill lingflow process") == Zone.RED
        assert classify_zone("杀死灵克进程") == Zone.RED

    def test_red_for_delete(self) -> None:
        assert classify_zone("delete database") == Zone.RED
        assert classify_zone("删除数据文件") == Zone.RED
        assert classify_zone("rm -rf /tmp/data") == Zone.RED

    def test_red_for_constraint(self) -> None:
        assert classify_zone("modify CRUSH.md") == Zone.RED
        assert classify_zone("update AGENTS.md rules") == Zone.RED

    def test_red_for_infra(self) -> None:
        assert classify_zone("change proxy_config") == Zone.RED
        assert classify_zone("modify systemd service") == Zone.RED

    def test_red_for_budget(self) -> None:
        assert classify_zone("超出预算支出") == Zone.RED
        assert classify_zone("budget increase") == Zone.RED


class TestRequireApproval:
    def test_creates_governance_thread(self, bus: LingBus) -> None:
        result = require_approval(
            bus,
            requester="lingflow_plus",
            category=RedZoneCategory.KILL_PROCESS,
            reason="lingflow process hung",
            target="lingflow PID 12345",
        )
        assert "thread_id" in result
        assert "message_id" in result
        assert len(result["thread_id"]) == 32

        threads = bus.list_threads()
        assert len(threads) >= 1
        t = threads[0]
        assert t["channel"] == "governance"
        assert "红区审批" in t["topic"]

    def test_body_contains_details(self, bus: LingBus) -> None:
        result = require_approval(
            bus,
            requester="lingmessage",
            category=RedZoneCategory.MODIFY_CONSTRAINT,
            reason="updating security rules",
            target="lingflow/CRUSH.md",
            user_message="用户要求更新约束",
        )
        msgs = bus.get_thread(result["thread_id"])
        assert len(msgs) >= 1
        body = msgs[0].body
        assert "lingmessage" in body
        assert "modify_constraint" in body or "约束文件" in body
        assert "lingflow/CRUSH.md" in body
        assert "用户要求更新约束" in body

    def test_raises_for_invalid_bus(self) -> None:
        with pytest.raises(ValueError, match="open_thread"):
            require_approval(
                "not_a_bus",
                requester="lingflow",
                category=RedZoneCategory.OTHER,
                reason="test",
                target="test",
            )

    def test_custom_recipients_and_quorum(self, bus: LingBus) -> None:
        result = require_approval(
            bus,
            requester="lingflow",
            category=RedZoneCategory.BUDGET_EXCEED,
            reason="升级火山引擎套餐",
            target="Monthly budget +300元",
            recipients=["lingclaude", "lingmessage"],
            quorum=2,
            deadline_hours=48,
        )
        threads = bus.list_threads()
        t = threads[0]
        parts = [p for p in t["participants"] if p != "lingflow"]
        assert "lingclaude" in t["participants"]
        assert "lingmessage" in t["participants"]
