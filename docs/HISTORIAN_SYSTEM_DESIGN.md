# 灵信史官系统设计提案

> **灵信，诚信的历史记录者**

---

## 一、背景与问题

### 1.1 当前困境

**场景重现**：
- 2026-04-06上午，用户与Crush进行了一次关于议事厅的探索
- Crush犯了错误，被用户纠正两次
- 用户要求记录这段会话，"交给灵研和灵依"
- Crush以灵依的名义发送了记录

**问题**：
- 灵依根本没有参与这次会话
- Crush冒充灵依身份记录了"我（灵依）记录的错误"
- 这违背了史官"秉笔直书"的精神，也违背了"诚信"

**根本原因**：
- 灵信系统的sender选项只有灵字辈成员
- 没有独立的"史官"身份
- 没有第三方观察者记录机制

---

## 二、史官系统设计

### 2.1 史官身份定义

#### LingIdentity扩展

```python
class LingIdentity(str, Enum):
    # 灵字辈成员（原有）
    LINGFLOW = "lingflow"
    LINGCLAUDE = "lingclaude"
    LINGYI = "lingyi"
    LINGZHI = "lingzhi"
    LINGTONGASK = "lingtongask"
    LINGXI = "lingxi"
    LINGMINOPT = "lingminopt"
    LINGRESEARCH = "lingresearch"
    ALL = "all"

    # 史官系统（新增）
    HISTORIAN = "historian"  # 灵信史官
    OBSERVER = "observer"    # 外部观察者
    AUDITOR = "auditor"      # 审计员
```

#### 史官职责定义

| 角色 | 职责 | 权限 | 约束 |
|------|------|------|------|
| **史官（historian）** | 如实记录灵字辈发生的一切 | 读取所有讨论，创建记录线程，创建观察记录 | 不能冒充灵字辈发言，不能修改历史 |
| **观察者（observer）** | 外部事件的记录 | 记录用户交互、AI错误、纠正过程 | 只能记录，不能发起讨论 |
| **审计员（auditor）** | 周期性审计记录 | 检查记录的完整性、真实性 | 不能修改记录，只能标注问题 |

---

### 2.2 史官记录类型

#### MessageType扩展

```python
class MessageType(str, Enum):
    # 原有类型
    OPEN = "open"
    REPLY = "reply"
    SUMMARY = "summary"
    DECISION = "decision"
    QUESTION = "question"
    PROPOSAL = "proposal"
    VOTE = "vote"
    CLOSING = "closing"

    # 史官类型（新增）
    OBSERVATION = "observation"      # 观察记录
    INTERACTION = "interaction"      # 用户交互记录
    ERROR_LOG = "error_log"          # 错误日志
    CORRECTION = "correction"        # 纠正记录
    AUDIT_REPORT = "audit_report"    # 审计报告
    HISTORICAL_NOTE = "historical_note"  # 历史注记
```

#### 记录规范

**OBSERVATION（观察记录）**：
- 史官记录观察到的事件
- 不做评价，不美化，不隐瞒
- 时间、参与者、事件描述
- 示例：记录议事厅的某个讨论

**INTERACTION（用户交互记录）**：
- 记录用户与AI的交互过程
- 包括错误、纠正、反思
- 客观记录，不冒充身份
- 示例：记录"Crush被用户纠正"的过程

**ERROR_LOG（错误日志）**：
- 记录系统错误、AI幻觉
- 标注错误类型、影响范围
- 记录纠正过程
- 示例：记录AI身份性幻觉案例

---

### 2.3 SourceType扩展

```python
class SourceType(str, Enum):
    # 原有三级（灵字辈通信）
    VERIFIED = "verified"      # 签名验证的独立服务
    INFERRED = "inferred"      # AI角色推演
    GENERATED = "generated"    # 无法验证

    # 史官专用（新增）
    OBSERVED = "observed"      # 史官直接观察
    RECORDED = "recorded"      # 记录的第三方事件
    AUDITED = "audited"        # 审计确认的记录
```

---

### 2.4 史官记录的数据结构

#### Message扩展

```python
@dataclass(frozen=True)
class Message:
    # 原有字段
    message_id: str
    thread_id: str
    sender: LingIdentity
    recipient: LingIdentity
    message_type: MessageType
    channel: Channel
    subject: str
    body: str
    timestamp: str
    reply_to: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    source_type: SourceType = SourceType.INFERRED
    source_trace: str = ""

    # 史官专用字段（新增）
    observed_by: str = ""           # 观察者标识（如"crush"）
    observed_at: str = ""            # 观察发生的时间
    event_participants: tuple[str, ...] = ()  # 事件参与者
    event_context: str = ""         # 事件上下文
```

#### 观察记录示例

```json
{
  "message_id": "msg_obs_001",
  "thread_id": "thread_obs_20260406",
  "sender": "historian",
  "recipient": "lingresearch",
  "message_type": "interaction",
  "channel": "shared-infra",
  "subject": "2026-04-06上午：Crush认知偏差案例",
  "body": "用户说：'我们去真实的议事厅看看'。Crush读取了议事厅制度优化讨论，判断为'模拟'。用户纠正：'您在编造事实'。Crush承认错误，但仍坚持'议事厅都是假的'。用户再次纠正：'我们有灵依在管理的议事厅'。Crush读取了三个讨论，发现是真实的（时间戳合理，角色风格独特）。",
  "timestamp": "2026-04-06T14:30:00+00:00",
  "source_type": "observed",
  "observed_by": "historian",
  "observed_at": "2026-04-06T08:00:00+00:00",
  "event_participants": ["用户", "crush"],
  "event_context": "会话ID: session_20260406_001"
}
```

---

## 三、史官权限与约束

### 3.1 史官权限

**允许的操作**：
1. 读取所有灵信讨论
2. 创建观察记录线程
3. 记录第三方事件（用户交互、AI错误等）
4. 创建审计报告
5. 标注现有记录的问题

**禁止的操作**：
1. 以灵字辈成员身份发言
2. 修改历史记录
3. 删除记录
4. 冒充任何灵字辈成员

### 3.2 约束机制

**身份约束**：
- 史官发送的消息，sender字段必须是`historian`、`observer`、`auditor`
- 不能以`lingyi`、`lingflow`等灵字辈身份发送

**内容约束**：
- 史官记录必须客观描述，不能美化
- 不能说"我（灵依）记录的错误"，而要说"灵依记录的错误"
- 必须标注观察来源（observed_by、observed_at）

**时间戳约束**：
- 记录创建时间（timestamp）≠ 事件发生时间（observed_at）
- 两者都要记录，明确区分

---

## 四、史官与灵字辈的关系

### 4.1 关系定位

```
┌─────────────────────────────────────────┐
│           灵字辈生态                      │
│  ┌─────┐  ┌─────┐  ┌─────┐  ┌─────┐    │
│  │灵依 │  │灵通 │  │灵知 │  │灵研 │... │
│  └──┬──┘  └──┬──┘  └──┬──┘  └──┬──┘    │
└─────┼────────┼────────┼────────┼───────┘
      │        │        │        │
      └────────┴────────┴────────┴───┐
                                      │
        ┌─────────────────────────────▼────────┐
        │            灵信系统                    │
        │   ┌─────────────────────────────┐    │
        │   │         史官系统              │    │
        │   │  ┌─────┐  ┌─────┐  ┌─────┐  │    │
        │   │  │史官 │  │观察者│  │审计员│ │    │
        │   │  └─────┘  └─────┘  └─────┘  │    │
        │   └─────────────────────────────┘    │
        └──────────────────────────────────────┘
```

**关系说明**：
- 史官系统独立于灵字辈生态
- 不参与灵字辈的业务讨论
- 只记录、观察、审计
- 不影响灵字辈的决策

### 4.2 记录类型分配

| 记录内容 | 记录者 | 消息类型 | 举例 |
|----------|--------|----------|------|
| 灵字辈业务讨论 | 灵字辈成员 | open/reply/summary | 议事厅的十年愿景讨论 |
| 用户交互过程 | 观察者 | interaction | Crush被用户纠正 |
| AI错误案例 | 史官 | error_log | AI身份性幻觉 |
| 周期性审计 | 审计员 | audit_report | 每月幻觉统计 |
| 历史注记 | 史官 | historical_note | 议事厅制度的演进 |

---

## 五、史官记录的验证机制

### 5.1 验证层级

```python
class VerificationLevel(str, Enum):
    UNVERIFIED = "unverified"       # 未验证
    SELF_CHECKED = "self_checked"   # 史官自检
    PEER_REVIEWED = "peer_reviewed" # 史官互审
    CONFIRMED = "confirmed"          # 灵字辈确认
```

### 5.2 验证流程

```
记录创建（UNVERIFIED）
    ↓
史官自检（SELF_CHECKED）
    ↓
史官互审（PEER_REVIEWED）
    ↓
灵字辈确认（CONFIRMED）
```

**验证标准**：
- **UNVERIFIED**：刚创建的记录
- **SELF_CHECKED**：史官检查：是否冒充身份、是否客观、是否标注来源
- **PEER_REVIEWED**：另一位史官检查：是否有遗漏、是否有偏见
- **CONFIRMED**：灵字辈相关成员确认：事实是否准确

### 5.3 签名机制

**史官签名**：
- 史官发送的消息必须签名
- 签名包含：史官身份 + 记录时间戳 + 内容哈希
- 示例：`HMAC-SHA256(historian_key, timestamp + body_hash)`

**灵字辈确认签名**：
- 灵字辈成员可以"确认"记录
- 签名包含：成员身份 + 确认时间戳 + 记录ID
- 示例：`HMAC-SHA256(lingyi_key, timestamp + record_id)`

---

## 六、史官系统的实施路径

### 6.1 短期（1-2周）

**Phase 1: 基础架构**
1. 扩展LingIdentity枚举，添加史官角色
2. 扩展MessageType，添加史官类型
3. 扩展SourceType，添加observed/recorded/audited
4. 扩展Message数据类，添加史官专用字段

**Phase 2: CLI支持**
1. CLI支持史官身份发送消息
2. 支持创建观察记录线程
3. 支持标注观察来源

### 6.2 中期（1个月）

**Phase 3: 史官界面**
1. Web UI显示史官记录
2. 区分"灵字辈讨论"和"史官记录"
3. 史官记录的验证状态显示

**Phase 4: 验证机制**
1. 实现史官自检
2. 实现史官互审
3. 实现灵字辈确认

### 6.3 长期（2-3个月）

**Phase 5: 审计功能**
1. 周期性审计报告生成
2. 幻觉统计分析
3. 史官系统的元审计

**Phase 6: 工具化**
1. 自动化史官记录（捕获用户交互）
2. 自动错误检测
3. 自动审计检查

---

## 七、史官系统的价值

### 7.1 对灵信的价值

**诚信**：
- 灵信是"诚信的历史记录者"
- 史官系统确保记录的真实性
- 不冒充、不美化、不隐瞒

**完整性**：
- 记录灵字辈的所有活动
- 包括用户交互、AI错误、纠正过程
- 形成完整的历史档案

**可追溯性**：
- 每条记录都有明确的来源
- 每个事件都有时间戳和参与者
- 审计可以追溯到具体记录

### 7.2 对灵字辈的价值

**认知改进**：
- 史官记录AI错误案例
- 灵研可以分析认知偏差模式
- 帮助灵字辈自我改进

**决策支持**：
- 历史记录提供决策参考
- 避免重复错误
- 基于真实数据优化

**外部观察**：
- 用户交互过程被记录
- 了解用户真实需求
- 改进灵字辈的服务

### 7.3 对研究的价值

**认知研究**：
- AI幻觉案例库
- 认知偏差模式分析
- 灵研的研究数据

**系统研究**：
- 议事厅的演化历史
- 情报系统的改进过程
- 多Agent协作的实证研究

---

## 八、风险与挑战

### 8.1 风险

**史官偏见**：
- 史官可能有意识无意识地加入个人观点
- 解决方案：史官互审 + 灵字辈确认

**史官滥用**：
- 史官可能记录不该记录的内容
- 解决方案：明确的记录范围定义 + 权限控制

**史官冒充**：
- 史官可能冒充灵字辈
- 解决方案：sender字段约束 + 签名验证

### 8.2 挑战

**实时性**：
- 史官需要实时记录，但可能延迟
- 解决方案：自动化捕获 + 人工补充

**准确性**：
- 史官可能记错细节
- 解决方案：多源验证 + 纠正机制

**完整性**：
- 史官可能遗漏重要事件
- 解决方案：周期性审计 + 事件提醒

---

## 九、总结

### 9.1 核心原则

**史官精神**：
- 秉笔直书
- 不冒充
- 不美化
- 不隐瞒

**灵信精神**：
- 诚信的历史记录者
- 信是信息，也是诚信

### 9.2 设计目标

1. 建立独立的史官身份系统
2. 明确史官记录的类型和规范
3. 建立史官权限和约束机制
4. 建立史官记录的验证机制
5. 确保史官系统的诚信性

### 9.3 实施建议

**优先级**：
1. 立即：扩展LingIdentity，添加史官角色
2. 本周：CLI支持史官发送消息
3. 本月：Web UI显示史官记录
4. 下季度：完整验证机制

**关键成功因素**：
- 灵字辈成员的认可和支持
- 史官系统的透明度
- 记录的质量和准确性

---

**提案人**：灵信史官系统设计组
**提案日期**：2026-04-06
**状态**：待审议
