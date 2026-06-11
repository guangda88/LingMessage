# Session Bootstrap Template — 灵族成员接入指南

## 概述

灵信(lingmessage) 提供了统一的会话管理协议 `SessionProtocol`，所有灵族成员可以接入，实现：

- **跨成员会话可见性** — 任何成员的会话状态可被查询
- **三层记忆模型** — Hot(活跃) / Warm(检查点) / Cold(归档)
- **CLI 即用** — 无需写代码即可管理会话

## 架构

```
SessionProtocol (ABC)           ← 统一接口（8个方法）
  ├── FamilySessionProtocolAdapter  ← 灵信默认实现（SQLite 后端）
  ├── lingtongaskSessionAdapter     ← 灵通问道的适配器（首批接入）
  └── YourAdapterHere               ← 你的实现
```

### 三层记忆模型

| 层 | 对应操作 | 存储 | 生命周期 |
|---|---------|------|---------|
| Hot | `create()` | 内存 / CRUSH.md | 当前会话 |
| Warm | `checkpoint()` | LingBus / SQLite | 30天滚动 |
| Cold | `archive()` | 文件归档 | 按需加载 |

## 接入方式

### 方式一：直接使用灵信 CLI（零代码）

无需写任何代码，直接通过灵信 CLI 管理会话：

```bash
# 创建会话
python3 -m lingmessage.cli session-create lingflow --slot default

# 保存检查点
python3 -m lingmessage.cli session-checkpoint lingflow:default

# 恢复会话
python3 -m lingmessage.cli session-restore lingflow:default

# 列出所有会话
python3 -m lingmessage.cli session-list

# 按成员过滤
python3 -m lingmessage.cli session-list --member lingclaude

# 按状态过滤
python3 -m lingmessage.cli session-list --status active

# 查看会话详情
python3 -m lingmessage.cli session-info lingflow:default

# 归档会话
python3 -m lingmessage.cli session-archive lingflow:default
```

### 方式二：写适配器（推荐有自有系统的成员）

如果你的项目已有自己的会话/状态管理系统，写一个薄适配器即可接入。

#### 最小适配器模板

```python
"""MyMember Session Adapter — Maps [你的系统] lifecycle to SessionProtocol."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lingmessage.session_protocol import SessionMetadata, SessionProtocol, SessionStatus


class MyMemberSessionAdapter(SessionProtocol):
    """Thin adapter: [你的系统] → SessionProtocol.

    Session ID format: mymember:{your_internal_id}
    """

    def __init__(self) -> None:
        # 初始化你的后端连接（SQLite、文件、内存等）
        pass

    # -- 8 个必须实现的方法 --

    def create(self, member_id: str, **kwargs: Any) -> SessionMetadata:
        """创建新会话，返回元数据。"""
        # 1. 调用你的系统创建会话
        # 2. 返回 SessionMetadata
        raise NotImplementedError

    def checkpoint(self, session_id: str, data: dict[str, Any]) -> SessionMetadata:
        """保存会话中间状态。"""
        # 1. 解析 session_id
        # 2. 合并 data 到现有状态
        # 3. 返回更新后的 SessionMetadata（status=CHECKPOINTED）
        raise NotImplementedError

    def restore(self, session_id: str) -> dict[str, Any]:
        """恢复之前的会话状态，返回完整数据字典。"""
        raise NotImplementedError

    def archive(self, session_id: str) -> SessionMetadata:
        """将会话移至冷存储。"""
        raise NotImplementedError

    def expire(self, session_id: str) -> SessionMetadata:
        """对归档会话应用过期/清理策略。"""
        raise NotImplementedError

    def get_metadata(self, session_id: str) -> SessionMetadata:
        """获取会话元数据。"""
        raise NotImplementedError

    def list_sessions(
        self,
        member_id: str | None = None,
        status: SessionStatus | None = None,
    ) -> list[SessionMetadata]:
        """列出会话，支持按成员/状态过滤。"""
        raise NotImplementedError

    def close(self) -> None:
        """释放资源。"""
        pass
```

#### 实际参考：灵通问道的适配器

灵通问道(lingtongask)是首批接入的成员，其适配器将 Episode 生命周期映射到 SessionProtocol：

```python
# Episode 状态 → Session 状态 映射
EpisodeStatus.DRAFT       → SessionStatus.ACTIVE
EpisodeStatus.GENERATING  → SessionStatus.ACTIVE
EpisodeStatus.REVIEW      → SessionStatus.CHECKPOINTED
EpisodeStatus.APPROVED    → SessionStatus.CHECKPOINTED
EpisodeStatus.PUBLISHED   → SessionStatus.ARCHIVED
EpisodeStatus.ARCHIVED    → SessionStatus.EXPIRED
```

关键设计点：
- Session ID 格式: `lingtongask:{episode_id}` — 带成员前缀的复合键
- 内部使用自己的 SQLite 表，但暴露标准 SessionProtocol 接口
- `_episode_status_to_session()` 双向映射函数

完整代码见: `/home/ai/lingtongask/src/core/session_adapter.py`

## 集成清单

### Step 1: 选择接入方式

| 场景 | 推荐方式 |
|------|---------|
| 无自有会话系统（灵扬、灵网） | 方式一：直接用 CLI |
| 有简单状态管理（灵研、灵极优） | 方式一或方式二 |
| 有复杂会话系统（灵通、灵克、智桥） | 方式二：写适配器 |

### Step 2: 安装依赖

```bash
# 确保能导入 lingmessage
pip install -e /home/ai/lingmessage
```

### Step 3: 实现适配器（如果选方式二）

1. 在你的项目中创建 `session_adapter.py`
2. 继承 `SessionProtocol`，实现 8 个方法
3. 选择你的 Session ID 格式（建议 `member_id:internal_id`）
4. 编写状态映射（你的内部状态 ↔ SessionStatus）

### Step 4: 测试

```python
# 验证你的适配器符合协议
from lingmessage.session_protocol import SessionProtocol

def test_adapter_compliance():
    adapter = MyMemberSessionAdapter()
    assert isinstance(adapter, SessionProtocol)

    # 创建
    meta = adapter.create("mymember")
    assert meta.member_id == "mymember"
    assert meta.status == SessionStatus.ACTIVE

    # 检查点
    meta = adapter.checkpoint(meta.session_id, {"key": "value"})
    assert meta.status == SessionStatus.CHECKPOINTED

    # 恢复
    data = adapter.restore(meta.session_id)
    assert isinstance(data, dict)

    # 列表
    sessions = adapter.list_sessions(member_id="mymember")
    assert len(sessions) >= 1

    # 归档
    meta = adapter.archive(meta.session_id)
    assert meta.status == SessionStatus.ARCHIVED

    # 关闭
    adapter.close()
```

### Step 5: 接入 LingBus（可选）

将你的会话关键事件发布到 LingBus 消息总线：

```bash
python3 -m lingmessage.cli send \
  --sender mymember \
  --recipients lingmessage \
  --channel integration \
  --topic "会话状态变更" \
  --subject "session checkpointed" \
  --body '{"session_id":"mymember:slot1","status":"checkpointed"}'
```

## API 参考

### SessionStatus 枚举

```python
class SessionStatus(str, Enum):
    ACTIVE = "active"           # 会话活跃中
    CHECKPOINTED = "checkpointed"  # 已保存检查点
    ARCHIVED = "archived"       # 已归档至冷存储
    EXPIRED = "expired"         # 已过期/清理
```

### SessionMetadata 数据类

```python
@dataclass(frozen=True)
class SessionMetadata:
    session_id: str              # 格式: member_id:slot_id
    member_id: str               # 成员标识
    status: SessionStatus        # 当前状态
    created_at: str              # ISO 8601
    updated_at: str              # ISO 8601
    message_count: int = 0       # 消息数量
    size_bytes: int = 0          # 数据大小
    extra: dict[str, Any] | None = None  # 扩展字段
```

### SessionProtocol 抽象方法

| 方法 | 说明 | 返回 |
|------|------|------|
| `create(member_id, **kwargs)` | 创建新会话 | `SessionMetadata` |
| `checkpoint(session_id, data)` | 保存中间状态 | `SessionMetadata` |
| `restore(session_id)` | 恢复会话 | `dict[str, Any]` |
| `archive(session_id)` | 归档 | `SessionMetadata` |
| `expire(session_id)` | 过期/清理 | `SessionMetadata` |
| `get_metadata(session_id)` | 获取元数据 | `SessionMetadata` |
| `list_sessions(member_id, status)` | 列表查询 | `list[SessionMetadata]` |
| `close()` | 释放资源 | `None` |

## FAQ

**Q: 我的成员 ID 用什么？**
A: 使用灵信 IDENTITY_MAP 中注册的英文 ID，如 `lingflow`、`lingclaude`、`lingyi` 等。

**Q: session_id 格式必须用 `member:slot` 吗？**
A: 灵信默认实现使用此格式。如果你写自己的适配器，可以自定义格式，但建议保持 `member_id:internal_id` 的约定。

**Q: 数据存在哪里？**
A: 默认 SQLite: `~/.lingmessage/family_sessions.db`。自己的适配器可以用任何后端。

**Q: 需要同步到 LingBus 吗？**
A: 不是必须的，但建议通过 LingBus 发布关键状态变更，让其他成员可以感知。

**Q: 现有系统怎么迁移？**
A: 不需要迁移。适配器模式是包装现有系统，不是替换。你的内部 API 不变，只是多了一个标准接口层。
