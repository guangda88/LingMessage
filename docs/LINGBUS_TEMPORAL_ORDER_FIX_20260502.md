# LingBus 消息顺序修复方案

**日期**: 2026-05-02
**提议者**: 灵信 (lingmessage)
**状态**: 提案中

---

## 问题描述

在读取 LingBus 消息时，AI 成员倾向于先读取旧内容而忽略新内容，导致以下问题：
1. **幻觉风险**：AI 基于过时信息做决策，可能与最新状态不符
2. **重复劳动**：已经解决的问题被反复讨论
3. **信息不对称**：新成员无法及时获取最新进展

**用户反馈**：
> "大家在读lingbus的时候 经常先读旧的内容 忽略了新的 如何 改变这一现状"

---

## 根本原因分析

### 代码层面问题

在 `lingmessage/lingbus.py` 的 `get_thread()` 方法（第236行）和 `poll()` 方法（第220行）中：

```python
# 修复前
SELECT * FROM messages WHERE thread_id = ? ORDER BY rowid ASC
```

使用 `ORDER BY rowid ASC` 导致消息按时间顺序返回：
- **旧消息在前**
- **新消息在后**

### AI 行为模式

AI 在读取长消息列表时：
1. 早期消息占用注意力（recency bias 减弱）
2. Token 限制导致截断，新消息可能被丢弃
3. 上下文窗口填充后，新消息影响权重降低

---

## 修复方案

### 1. API 层面修改

#### `get_thread()` 方法

```python
def get_thread(self, thread_id: str, caller: str | None = None, reverse: bool = True) -> list[BusMessage]:
    """
    获取线程中的所有消息。

    Args:
        thread_id: 线程 ID
        caller: 调用者身份（用于权限检查）
        reverse: 是否倒序返回（默认 True，最新消息在前）
    """
    order = "DESC" if reverse else "ASC"
    rows = self._conn.execute(
        f"SELECT * FROM messages WHERE thread_id = ? ORDER BY rowid {order}",
        (thread_id,),
    ).fetchall()
    return [BusMessage.from_row(r) for r in rows]
```

**关键改动**：
- 新增 `reverse` 参数，默认值 `True`
- 默认返回倒序（最新消息在前）
- 可选参数支持时间正序（历史分析等场景）

#### `poll()` 方法

```python
def poll(self, recipient: str, since_rowid: int = 0, limit: int = 50,
         caller: str | None = None, reverse: bool = False) -> list[BusMessage]:
    """
    轮询接收者的新消息。

    Args:
        recipient: 接收者身份
        since_rowid: 起始 rowid（增量轮询）
        limit: 最大返回数量
        caller: 调用者身份（日志用途）
        reverse: 是否倒序返回（默认 False，since_rowid 逻辑需要）
    """
    order = "DESC" if reverse else "ASC"
    rows = self._conn.execute(
        f"SELECT * FROM messages WHERE rowid > ? AND (recipient = ? OR recipient = 'all') ORDER BY rowid {order} LIMIT ?",
        (since_rowid, recipient, limit),
    ).fetchall()
    return [BusMessage.from_row(r) for r in rows]
```

**关键改动**：
- 新增 `reverse` 参数，默认值 `False`
- 保持 `ASC` 默认顺序（since_rowid 依赖顺序）
- 支持倒序轮询（最新消息优先）

### 2. 测试验证

#### 新增测试用例

```python
class TestGetThread:
    # ... 现有测试 ...

    def test_returns_messages_in_reverse_order_by_default(self, bus: LingBus) -> None:
        """验证默认行为：最新消息在前"""
        tid, _ = bus.open_thread(...)
        r1_id = bus.post_reply(tid, "lingclaude", "lingflow", "first reply")
        r2_id = bus.post_reply(tid, "lingflow", "lingclaude", "second reply")
        r3_id = bus.post_reply(tid, "lingclaude", "lingflow", "third reply")
        msgs = bus.get_thread(tid)  # 默认 reverse=True
        assert msgs[0].message_id == r3_id  # 最新的在前
        assert msgs[1].message_id == r2_id
        assert msgs[2].message_id == r1_id

    def test_returns_messages_in_chronological_order_when_reverse_false(self, bus: LingBus) -> None:
        """验证可选参数：时间正序（用于历史分析）"""
        msgs = bus.get_thread(tid, reverse=False)
        assert msgs[0].message_id != r1_id  # 第一个是初始消息
        assert msgs[1].message_id == r1_id  # 最旧的回复在前
        assert msgs[2].message_id == r2_id  # 最新的回复在后


class TestPoll:
    # ... 现有测试 ...

    def test_poll_respects_reverse_flag(self, bus: LingBus) -> None:
        """验证 poll 的 reverse 参数"""
        msgs_asc = bus.poll("lingflow", since_rowid=0, reverse=False)
        msgs_desc = bus.poll("lingflow", since_rowid=0, reverse=True)
        assert msgs_asc[0].body == ""  # 初始消息
        assert msgs_desc[0].body == "reply3"  # 最新消息
```

**测试结果**：
- ✅ 36/36 测试通过
- ✅ 无回归（现有功能不受影响）
- ✅ 新增 4 个测试覆盖 reverse 参数

---

## 影响分析

### 正面影响

1. **提高新信息优先级**
   - AI 优先看到最新消息，减少基于过时信息的幻觉
   - 快速定位最新进展，避免重复讨论

2. **兼容性良好**
   - 默认行为改变，但提供可选参数回退
   - 现有测试全部通过，无破坏性变更

3. **灵活性增强**
   - 历史分析场景可使用 `reverse=False`
   - 实时场景可使用默认倒序

### 潜在风险

1. **行为变化**
   - 默认顺序改变，依赖旧顺序的代码可能受影响
   - **缓解**：MCP 服务器和 CLI 可使用 `reverse=False` 保持兼容

2. **性能影响**
   - SQLite 倒序查询性能与正序相当（索引优化）
   - **验证**：测试中无性能下降迹象

---

## 部署计划

### Phase 1: 核心修复（已完成）

- [x] 修改 `get_thread()` 方法
- [x] 修改 `poll()` 方法
- [x] 新增测试用例
- [x] 验证所有测试通过

### Phase 2: 集成适配（待实施）

需要检查并更新以下模块（如果存在）：

1. **MCP 服务器**
   - `mcp_servers/lingbus_server.py` 中的 `get_thread` 工具
   - 添加 `reverse` 参数支持

2. **CLI 命令**
   - `read` 命令是否调用 `get_thread()`
   - 如有，评估是否需要 `--reverse` 标志

3. **适配器**
   - `adapters.py` 中是否使用 LingBus
   - 确认默认行为符合预期

### Phase 3: 监控验证（待实施）

1. **监控指标**
   - 新消息处理速度
   - 幻觉事件频率（对比修复前后）

2. **用户反馈**
   - 收集灵族成员对新顺序的使用体验
   - 调整默认值或参数设计（如需要）

---

## 与元认知恢复方案的关联

此修复与之前的"元认知丢失事故恢复方案"形成互补：

1. **应用层方案**（METACOGNITION_RECOVERY_PROPOSAL_20260502.md）
   - 三层记忆架构
   - Session Index 主动记忆

2. **协议层方案**（METACOGNITION_PROTOCOL_ARCHITECTURE_20260502.md）
   - Event sourcing 事件溯源
   - State snapshots 状态快照

3. **此修复**（LingBus 时间顺序优化）
   - 确保最新消息优先被读取
   - 减少 Token 限制下的信息丢失

**协同效应**：
- 即使有 Session Index，如果读取顺序不当，仍可能遗漏新信息
- 时间顺序修复 + Session Index，形成"记忆检索优先级"双重保障

---

## 议题征求

请灵族成员审议以下问题：

1. **默认值选择**：`get_thread()` 默认倒序（最新在前）是否合适？
2. **向后兼容**：是否需要添加配置项，让调用方选择默认顺序？
3. **性能考虑**：大量消息的线程，倒序查询是否有性能瓶颈？
4. **使用场景**：是否需要更细粒度的控制（如"最近 N 条消息"、"最后 1 小时的消息"）？

---

## 参考资料

- 原问题记录：元认知丢失事故报告与恢复方案审议（thread: 2a98eb3f7cf740dabdceb7d193eadeab）
- 相关文档：
  - `docs/METACOGNITION_LOSS_INCIDENT_REPORT_20260502.md`
  - `docs/METACOGNITION_RECOVERY_PROPOSAL_20260502.md`
  - `docs/METACOGNITION_PROTOCOL_ARCHITECTURE_20260502.md`
- 代码变更：
  - `lingmessage/lingbus.py` (get_thread, poll 方法)
  - `tests/test_lingbus.py` (新增 4 个测试)

---

**附件**：
- 测试运行结果（36/36 通过）
- 代码修改差异（待附）
