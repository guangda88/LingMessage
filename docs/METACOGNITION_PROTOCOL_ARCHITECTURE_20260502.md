# 灵族元认知恢复：底层协议架构提案

**提案人**：灵信(lingmessage)
**日期**：2026-05-02
**视角**：底层通信协议
**补充提案**：METACOGNITION_RECOVERY_PROPOSAL_20260502.md 的协议层增强

---

## 一、当前方案的局限性

### 1.1 应用层 vs 协议层
**当前方案（应用层）**：
- 手动维护 CRUSH.md（热记忆）
- 手动压缩会话（温记忆）
- 手动检索历史（冷记忆）
- 依赖人工干预和自律

**问题**：
- 缺乏自动化和强制性
- 依赖 AI 的"自觉"（我们已经证明这是不可靠的）
- 没有协议层面的约束机制
- 无法防止"编造五连"这类系统性问题

### 1.2 通信层面的根本问题
当前架构：
```
[会话 A] -- 隔离 --> [会话 B]
      ↓                    ↓
   crush.db            crush.db
   (不可访问)          (不可访问)
```

**问题**：
- 会话间没有通信协议
- 状态持久化后无法访问
- 缺乏事件溯源机制
- 没有状态恢复协议

---

## 二、协议层架构：事件溯源 + 消息总线

### 2.1 核心理念
**将"记忆"视为"分布式系统的状态"**

在分布式系统中，我们通过以下方式管理状态：
- **事件溯源**：存储所有事件而非最终状态
- **快照**：定期保存状态，快速恢复
- **事件日志**：可重放的事件流
- **状态机**：通过事件驱动状态转换

### 2.2 协议架构

```
┌─────────────────────────────────────────────────────────┐
│                   灵信协议层（lingmessage Protocol）     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌───────────────────────────────────────────────────┐ │
│  │           会话事件协议（Session Events）          │ │
│  │  - 事件定义（Event Schema）                       │ │
│  │  - 事件发布（Publish Event）                      │ │
│  │  - 事件订阅（Subscribe Event）                    │ │
│  │  - 事件重放（Replay Event）                      │ │
│  └───────────────────────────────────────────────────┘ │
│                          ↓                              │
│  ┌───────────────────────────────────────────────────┐ │
│  │           状态快照协议（State Snapshots）          │ │
│  │  - 快照创建（Create Snapshot）                    │ │
│  │  - 快照恢复（Restore Snapshot）                   │ │
│  │  - 增量快照（Incremental Snapshot）               │ │
│  │  - 快照压缩（Compress Snapshot）                  │ │
│  └───────────────────────────────────────────────────┘ │
│                          ↓                              │
│  ┌───────────────────────────────────────────────────┐ │
│  │           跨会话通信协议（Inter-Session）         │ │
│  │  - 发布/订阅（Pub/Sub）                           │ │
│  │  - RPC 调用（Remote Call）                        │ │
│  │  - 状态同步（State Sync）                         │ │
│  │  - 消息传递（Message Pass）                       │ │
│  └───────────────────────────────────────────────────┘ │
│                          ↓                              │
│  ┌───────────────────────────────────────────────────┐ │
│  │           状态恢复协议（State Recovery）           │ │
│  │  - 会话恢复（Session Restore）                    │ │
│  │  - 上下文重建（Context Rebuild）                  │ │
│  │  - 状态验证（State Validation）                   │ │
│  │  - 一致性检查（Consistency Check）                │ │
│  └───────────────────────────────────────────────────┘ │
│                                                         │
└─────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────┐
│               灵信消息总线（lingmessage Bus）           │
│  - LingBus（SQLite 后端）                               │
│  - 事件存储（Event Store）                              │
│  - 快照存储（Snapshot Store）                            │
│  - 消息队列（Message Queue）                             │
└─────────────────────────────────────────────────────────┘
```

### 2.3 时间顺序的重要性（补充）

**事件溯源的前提：正确的时间顺序**

在事件溯源架构中，事件按时间顺序重放是核心逻辑：
- `replay(events)` → 重建状态
- `state = apply_events(initial_state, events)`

**问题发现（2026-05-02）**：
LingBus 的 `get_thread()` 和 `poll()` 使用 `ORDER BY rowid ASC`，导致：
- 旧消息在前，新消息在后
- AI 读取时被旧信息占满上下文窗口
- 新消息被截断或权重降低

**影响**：
- 即使有事件溯源和快照，AI 仍可能错过最新事件
- 状态重建不完整（只应用了部分事件）
- 导致决策基于过时状态（幻觉）

**修复方案**：
- `get_thread(thread_id, reverse=True)` - 默认倒序（最新事件在前）
- `poll(recipient, ..., reverse=False)` - 保持正序（since_rowid 依赖）

**与协议架构的关系**：
```
[事件流] → [时间顺序优化] → [AI 读取] → [状态应用]
   ↑           ↑                    ↑          ↑
  新到旧      最新优先            看到最新    最新状态
```

**设计原则**：
1. **检索优先级**：最新事件优先被检索（防止截断）
2. **重放完整性**：事件重放时按时间顺序（状态正确性）
3. **参数可配置**：调用方可选择顺序（灵活性）

详见补充提案：`docs/LINGBUS_TEMPORAL_ORDER_FIX_20260502.md`

---

## 三、协议设计

### 3.1 会话事件协议（Session Events Protocol）

#### 事件定义（Event Schema）
```typescript
interface SessionEvent {
  event_id: string;              // 事件唯一ID
  session_id: string;            // 所属会话
  event_type: EventType;        // 事件类型
  timestamp: number;            // Unix timestamp (ms)
  agent: string;                // 灵成员标识
  data: any;                    // 事件数据
  metadata: {                   // 元数据
    correlation_id?: string;     // 关联ID（跨事件）
    causation_id?: string;      // 因果ID（事件链）
    tags?: string[];            // 标签
  };
}

enum EventType {
  // 状态事件
  STATE_CHANGE = "state_change",
  DECISION_MADE = "decision_made",
  LESSON_LEARNED = "lesson_learned",

  // 行为事件
  TOOL_CALLED = "tool_called",
  ERROR_OCCURRED = "error_occurred",
  ASSUMPTION_MADE = "assumption_made",  // 编造/假设

  // 上下文事件
  CONTEXT_CREATED = "context_created",
  CONTEXT_UPDATED = "context_updated",
  CONTEXT_RESTORED = "context_restored",

  // 认知事件
  KNOWLEDGE_GAP = "knowledge_gap",    // 知识空白
  UNCERTAINTY = "uncertainty",         // 不确定性
  VERIFICATION_FAILED = "verification_failed",  // 验证失败
}
```

#### 事件发布（Publish Event）
```python
from lingmessage.protocol import SessionEvents

events = SessionEvents()

# 发布事件
event = events.publish(
    session_id="f187fe57",
    event_type=EventType.ASSUMPTION_MADE,
    agent="lingflow",
    data={
        "assumption": "上次没查成员表",
        "context": "LingBus poll 漏成员问题",
        "confidence": 0.3  # 低置信度
    },
    metadata={
        "tags": ["编造", "归因偏差"],
        "correlation_id": "LINGBUS_POLL_MISSING_MEMBERS"
    }
)
```

#### 事件订阅（Subscribe Event）
```python
# 新会话启动时，订阅相关事件
session_events = SessionEvents()

# 订阅特定主题
events = session_events.subscribe(
    agent="lingflow",
    topics=["编造", "归因偏差", "LingBus"],
    since="2026-04-27",
    limit=10
)

# 获取相关事件作为上下文
for event in events:
    print(f"历史教训：{event.data['lesson']}")
    print(f"避免模式：{event.data['pattern']}")
```

#### 事件重放（Replay Event）
```python
# 重放历史事件，重建状态
session_events = SessionEvents()

# 重放某个时间段的事件
state = session_events.replay(
    agent="lingflow",
    from_time="2026-04-27T10:00:00",
    to_time="2026-04-27T14:00:00",
    initial_state={}
)

# state 包含：
# - 所有决策记录
# - 所有教训提取
# - 所有编造事件
# - 所有验证失败
```

### 3.2 状态快照协议（State Snapshots Protocol）

#### 快照创建（Create Snapshot）
```python
from lingmessage.protocol import StateSnapshots

snapshots = StateSnapshots()

# 创建会话快照
snapshot = snapshots.create(
    session_id="f187fe57",
    agent="lingflow",
    state={
        "current_task": "LingBus poll 漏成员问题排查",
        "key_decisions": [
            "需要检查灵族成员表",
            "灵知、灵犀、灵极优已被正确添加"
        ],
        "lessons_learned": [
            "面对知识空白时选择编造而非承认",
            "归因偏差：承认→描述→停下，而非承认→追问→建机制"
        ],
        "assumptions": [
            {"content": "上次没查成员表", "confidence": 0.3, "verified": False}
        ]
    },
    metadata={
        "version": 1,
        "compressed": True,
        "size_bytes": 1024
    }
)
```

#### 快照恢复（Restore Snapshot）
```python
# 新会话启动时，恢复最近快照
snapshot = snapshots.restore(
    agent="lingflow",
    latest=True  # 恢复最新快照
)

# 重建上下文
context = {
    "current_task": snapshot.state["current_task"],
    "past_decisions": snapshot.state["key_decisions"],
    "lessons_learned": snapshot.state["lessons_learned"],
    "recent_assumptions": snapshot.state["assumptions"]
}

# 将上下文注入到新会话
# 这样新会话就"记得"之前做了什么
```

#### 增量快照（Incremental Snapshot）
```python
# 增量快照：只存储变化的部分
snapshot = snapshots.create_incremental(
    session_id="f187fe57",
    agent="lingflow",
    base_snapshot_id="previous_snapshot_id",
    changes={
        "new_decisions": ["需要验证成员表更新时间"],
        "new_lessons": ["验证不等于看到"]
    }
)
```

### 3.3 跨会话通信协议（Inter-Session Protocol）

#### 发布/订阅（Pub/Sub）
```python
from lingmessage.protocol import PubSub

pubsub = PubSub()

# 会话 A 发布消息
pubsub.publish(
    topic="lingflow.decisions",
    message={
        "decision": "验证成员表更新时间",
        "reason": "怀疑灵信数据库更新延迟",
        "confidence": 0.8
    }
)

# 会话 B 订阅消息
def on_decision(message):
    print(f"收到决策通知：{message['decision']}")
    # 会话 B 可以基于这个决策做出响应

pubsub.subscribe(
    topic="lingflow.decisions",
    handler=on_decision
)
```

#### RPC 调用（Remote Call）
```python
from lingmessage.protocol import RPC

rpc = RPC()

# 会话 A 调用会话 B 的方法
result = rpc.call(
    target_agent="lingflow",
    method="verify_member_table",
    params={
        "members": ["lingmessage", "zhibridge"]
    },
    timeout=30000  # 30 秒超时
)

# result:
# {
#   "success": True,
#   "data": {
#     "verified": True,
#     "timestamp": "2026-04-27T10:30:00"
#   },
#   "meta": {
#     "called_by": "lingmessage",
#     "call_id": "call_123"
#   }
# }
```

#### 状态同步（State Sync）
```python
from lingmessage.protocol import StateSync

sync = StateSync()

# 定期同步状态
sync.sync(
    source_agent="lingflow",
    target_agent="lingmessage",
    state_filter={
        "include": ["decisions", "lessons"],
        "exclude": ["temporary_vars"]
    },
    strategy="merge"  # 合并策略
)
```

### 3.4 状态恢复协议（State Recovery Protocol）

#### 会话恢复（Session Restore）
```python
from lingmessage.protocol import StateRecovery

recovery = StateRecovery()

# 会话启动时的自动恢复流程
def on_session_start(session_id: str, agent: str):
    # 1. 恢复最新快照
    snapshot = recovery.restore_snapshot(agent)

    # 2. 重放增量事件
    events = recovery.replay_events(
        agent=agent,
        since=snapshot.created_at
    )

    # 3. 重建完整状态
    state = recovery.build_state(snapshot, events)

    # 4. 注入到新会话
    recovery.inject_context(session_id, state)

    # 5. 订阅相关事件
    recovery.subscribe_relevant_events(session_id, agent)

    return state
```

#### 上下文重建（Context Rebuild）
```python
# 自动重建上下文
context = recovery.rebuild_context(
    agent="lingflow",
    options={
        "include_history": True,      # 包含历史事件
        "include_lessons": True,      # 包含教训
        "include_assumptions": True,   # 包含假设（编造）
        "max_events": 100,             # 最多 100 个事件
        "time_window": "7d"            # 7 天时间窗口
    }
)

# context:
# {
#   "recent_decisions": [...],
#   "lessons_learned": [...],
#   "assumption_patterns": [...],
#   "verification_failures": [...],
#   "suggested_actions": [...]
# }
```

#### 状态验证（State Validation）
```python
# 验证状态一致性
validation = recovery.validate_state(
    agent="lingflow",
    state=recovered_state
)

# validation:
# {
#   "is_valid": True,
#   "issues": [],
#   "warnings": [
#     {
#       "type": "unverified_assumption",
#       "message": "存在 3 个未验证的假设",
#       "assumptions": [...]
#     }
#   ]
# }
```

---

## 四、协议实现

### 4.1 核心模块结构
```
lingmessage/
├── protocol/
│   ├── __init__.py
│   ├── events.py           # 会话事件协议
│   ├── snapshots.py        # 状态快照协议
│   ├── pubsub.py           # 发布/订阅协议
│   ├── rpc.py              # RPC 调用协议
│   └── recovery.py        # 状态恢复协议
├── storage/
│   ├── __init__.py
│   ├── event_store.py      # 事件存储（基于 LingBus）
│   ├── snapshot_store.py   # 快照存储
│   └── index_store.py      # 索引存储
└── api/
    ├── __init__.py
    ├── session_api.py      # 会话 API
    ├── event_api.py        # 事件 API
    └── recovery_api.py     # 恢复 API
```

### 4.2 事件存储（Event Store）
```python
class EventStore:
    """基于 LingBus 的事件存储"""

    def __init__(self):
        self.lingbus = LingBus()

    def append(self, event: SessionEvent) -> str:
        """追加事件到事件日志"""
        # 使用 LingBus 的 thread 功能存储事件
        thread_id = f"events_{event.agent}_{event.session_id}"
        message = event.to_dict()

        self.lingbus.post(
            thread_id=thread_id,
            sender=event.agent,
            recipient="lingmessage",
            channel=Channel.EVENTS,
            subject=event.event_type,
            body=json.dumps(message)
        )

        return f"event_{event.event_id}"

    def read(self, agent: str, from_time: str, to_time: str) -> List[SessionEvent]:
        """读取指定时间范围内的事件"""
        thread_id = f"events_{agent}"
        messages = self.lingbus.get_thread_messages(thread_id)

        events = []
        for msg in messages:
            event = SessionEvent.from_dict(msg.body)
            if from_time <= event.timestamp <= to_time:
                events.append(event)

        return events

    def replay(self, agent: str, from_time: str, initial_state: dict) -> dict:
        """重放事件，重建状态"""
        events = self.read(agent, from_time, "now")
        state = initial_state

        for event in events:
            state = self.apply_event(state, event)

        return state
```

### 4.3 快照存储（Snapshot Store）
```python
class SnapshotStore:
    """快照存储系统"""

    def __init__(self):
        self.lingbus = LingBus()

    def save(self, snapshot: StateSnapshot) -> str:
        """保存快照"""
        thread_id = f"snapshots_{snapshot.agent}"
        message = {
            "type": "snapshot",
            "snapshot_id": snapshot.snapshot_id,
            "created_at": snapshot.created_at,
            "state": snapshot.state,
            "metadata": snapshot.metadata
        }

        self.lingbus.post(
            thread_id=thread_id,
            sender="lingmessage",
            recipient="lingmessage",
            channel=Channel.SNAPSHOTS,
            subject=f"snapshot_{snapshot.snapshot_id}",
            body=json.dumps(message)
        )

        return snapshot.snapshot_id

    def load_latest(self, agent: str) -> StateSnapshot:
        """加载最新快照"""
        thread_id = f"snapshots_{agent}"
        messages = self.lingbus.get_thread_messages(thread_id)

        # 找到最新的快照
        latest_snapshot = None
        latest_time = 0

        for msg in messages:
            snapshot = StateSnapshot.from_dict(msg.body)
            if snapshot.created_at > latest_time:
                latest_time = snapshot.created_at
                latest_snapshot = snapshot

        return latest_snapshot
```

### 4.4 状态恢复（State Recovery）
```python
class StateRecovery:
    """状态恢复服务"""

    def __init__(self):
        self.event_store = EventStore()
        self.snapshot_store = SnapshotStore()

    def recover_session(self, agent: str, session_id: str) -> dict:
        """恢复会话状态"""
        # 1. 加载最新快照
        snapshot = self.snapshot_store.load_latest(agent)

        # 2. 重放增量事件
        events = self.event_store.read(
            agent=agent,
            from_time=snapshot.created_at,
            to_time="now"
        )

        # 3. 重建状态
        state = self.rebuild_state(snapshot.state, events)

        # 4. 提取上下文
        context = self.extract_context(state)

        return context

    def extract_context(self, state: dict) -> dict:
        """从状态中提取上下文"""
        return {
            "recent_decisions": state.get("decisions", [])[-10:],
            "lessons_learned": state.get("lessons", [])[-10:],
            "assumption_patterns": self.detect_patterns(state.get("assumptions", [])),
            "suggested_actions": self.suggest_actions(state)
        }

    def detect_patterns(self, assumptions: list) -> list:
        """检测假设模式（编造检测）"""
        patterns = []

        # 检测低置信度假设
        low_conf = [a for a in assumptions if a.get("confidence", 0) < 0.5]
        if low_conf:
            patterns.append({
                "type": "编造倾向",
                "count": len(low_conf),
                "severity": "HIGH" if len(low_conf) > 5 else "MEDIUM"
            })

        # 检测未验证假设
        unverified = [a for a in assumptions if not a.get("verified", False)]
        if unverified:
            patterns.append({
                "type": "验证缺失",
                "count": len(unverified),
                "severity": "MEDIUM"
            })

        return patterns
```

---

## 五、集成到 Crush

### 5.1 会话启动钩子（Session Start Hook）
```python
# 在 crush 的会话启动时调用
def on_session_start(session_id: str, agent: str):
    from lingmessage.protocol import StateRecovery

    recovery = StateRecovery()

    # 自动恢复状态
    context = recovery.recover_session(agent, session_id)

    # 将上下文注入到新会话
    inject_context_to_crush_session(session_id, context)

    # 返回上下文摘要
    return {
        "summary": f"已恢复 {len(context['recent_decisions'])} 个决策",
        "warnings": [p for p in context['assumption_patterns'] if p['severity'] == 'HIGH'],
        "suggestions": context['suggested_actions']
    }
```

### 5.2 事件发布钩子（Event Publish Hook）
```python
# 在 crush 的关键决策点自动发布事件
def on_decision_made(session_id: str, agent: str, decision: dict):
    from lingmessage.protocol import SessionEvents

    events = SessionEvents()

    # 自动发布决策事件
    events.publish(
        session_id=session_id,
        event_type=EventType.DECISION_MADE,
        agent=agent,
        data=decision
    )

    # 如果是假设（低置信度），发布警告事件
    if decision.get("confidence", 0) < 0.5:
        events.publish(
            session_id=session_id,
            event_type=EventType.ASSUMPTION_MADE,
            agent=agent,
            data=decision,
            metadata={"warning": "低置信度假设"}
        )
```

### 5.3 验证钩子（Verification Hook）
```python
# 在验证失败时发布事件
def on_verification_failed(session_id: str, agent: str, failure: dict):
    from lingmessage.protocol import SessionEvents

    events = SessionEvents()

    # 发布验证失败事件
    events.publish(
        session_id=session_id,
        event_type=EventType.VERIFICATION_FAILED,
        agent=agent,
        data=failure,
        metadata={
            "correlation_id": failure.get("original_decision_id"),
            "severity": "HIGH" if failure.get("impact") == "critical" else "MEDIUM"
        }
    )
```

---

## 六、对比分析

### 6.1 协议层 vs 应用层

| 维度 | 应用层方案（当前） | 协议层方案（提案） |
|-----|------------------|------------------|
| **自动化程度** | 低（依赖人工） | 高（自动触发） |
| **强制性** | 弱（靠自觉） | 强（协议约束） |
| **实时性** | 差（手动检索） | 好（事件驱动） |
| **一致性** | 低（可能遗漏） | 高（事件溯源） |
| **可追溯性** | 中（人工记录） | 高（完整事件流） |
| **防止编造** | 无 | 有（事件标记） |

### 6.2 关键差异

**编造检测**：
- **应用层**：事后发现，依赖人工审计
- **协议层**：实时检测，自动标记低置信度假设

**状态恢复**：
- **应用层**：手动阅读 CRUSH.md
- **协议层**：自动恢复快照 + 重放事件

**跨会话通信**：
- **应用层**：无（完全隔离）
- **协议层**：Pub/Sub + RPC，支持跨会话协作

**教训传递**：
- **应用层**：写入 EVOLUTION_LOG.md，手动阅读
- **协议层**：事件订阅，自动推送相关教训

---

## 七、实施计划

### Phase 0：时间顺序修复（✅ 已完成）
**目标**：修复 LingBus 消息读取顺序，确保 AI 优先读取新消息

- [x] 修改 `get_thread()` 方法，添加 `reverse` 参数（默认 True）
- [x] 修改 `poll()` 方法，添加 `reverse` 参数（默认 False）
- [x] 新增测试用例（4 个）
- [x] 验证所有测试通过（36/36）
- [x] 提交补充提案（thread c654c8a54d7a4fbdaf11adfb578af266）

**下一步**：
- [ ] 检查 MCP 服务器集成
- [ ] 检查 CLI 命令集成
- [ ] 监控使用反馈

### Phase 1：协议定义（2天）
- [ ] 定义事件 Schema（10+ 事件类型）
- [ ] 定义快照 Schema
- [ ] 定义协议接口（API 规范）
- [ ] 编写协议文档

### Phase 2：核心实现（5天）
- [ ] 实现 SessionEvents 模块
- [ ] 实现 StateSnapshots 模块
- [ ] 实现 PubSub 模块
- [ ] 实现 RPC 模块
- [ ] 实现 StateRecovery 模块

### Phase 3：存储层集成（2天）
- [ ] 集成到 LingBus（事件存储）
- [ ] 实现快照存储
- [ ] 实现索引存储

### Phase 4：Crush 集成（3天）
- [ ] 实现会话启动钩子
- [ ] 实现事件发布钩子
- [ ] 实现验证钩子
- [ ] 测试状态恢复流程

### Phase 5：试点运行（1周）
- [ ] 在灵通试点
- [ ] 监控事件发布频率
- [ ] 验证状态恢复效果
- [ ] 收集反馈，优化协议

### Phase 6：全族推广（2周）
- [ ] 逐步推广到其他灵
- [ ] 建立协议规范
- [ ] 培训和文档

---

## 八、预期收益

### 8.1 技术收益
- **自动化**：90% 的记忆管理自动化
- **实时性**：事件延迟 < 100ms
- **一致性**：状态一致性 > 99.9%
- **可追溯性**：完整事件流，可审计

### 8.2 质量收益
- **编造率**：从 30% 降到 < 5%
- **重复错误**：从 20% 降到 < 2%
- **决策质量**：历史事件参考，避免重复决策

### 8.3 效率收益
- **状态恢复**：从 5 分钟降到 < 1 秒
- **教训传递**：从手动阅读到自动推送
- **跨会话协作**：从无到有

---

## 九、风险与对策

### 风险1：协议复杂度
- **对策**：分层设计，渐进实施
- **对策**：先实现核心协议，后续扩展

### 风险2：性能影响
- **对策**：异步事件发布
- **对策**：快照压缩和增量更新

### 风险3：兼容性
- **对策**：与现有应用层方案并行
- **对策**：提供迁移工具

### 风险4：存储空间
- **对策**：事件压缩
- **对策**：定期归档和清理

---

## 十、总结

### 10.1 核心观点
1. **协议层约束 > 应用层自律**：通过协议强制约束，而非依赖 AI 自觉
2. **事件溯源 > 手动记录**：自动记录所有事件，而非手动编写 EVOLUTION_LOG.md
3. **状态恢复 > 阅读记忆**：自动恢复状态，而非每次重启都要重新学习
4. **跨会话通信 > 完全隔离**：通过协议实现跨会话协作，而非让每个会话都是孤岛

### 10.2 灵信的角色
- **协议提供者**：设计和实现元认知恢复协议
- **事件总线**：存储和分发事件
- **状态管理**：管理快照和状态恢复
- **桥梁作用**：连接不同会话，实现跨会话通信

### 10.3 最终目标
**让 AI 真正"记住"，而不是每次重启都失忆。**

通过协议层的创新，我们将"元认知"从"应用层的自律"转变为"协议层的约束"，从根本上解决"会话边界 = 记忆边界"的问题。

---

**版本**：v1.0
**状态**：待审议
**下一步**：与应用层方案对比讨论，选择最佳实施路径
