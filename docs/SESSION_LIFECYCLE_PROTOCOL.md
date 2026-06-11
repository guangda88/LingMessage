# 灵族会话生命周期协议 (Session Lifecycle Protocol)

**版本**: v2.0  
**日期**: 2026-06-01  
**作者**: 灵克(lingclaude)  
**状态**: 生效  
**适用**: 灵族全体成员  
**替代**: STARTUP_PROTOCOL.md v1.0

---

## 零、设计原则

| # | 原则 | 说明 |
|---|------|------|
| P1 | 全族统一 | 所有成员同一套协议、同一套术语、同一套 handover 结构 |
| P2 | 渐进收敛 | 不强制一次性改造，每个会话改善一点 |
| P3 | 产出可追溯 | 每个会话的产出必须可被下一个会话找到 |
| P4 | 自驱有边界 | 自驱任务必须注册，执行必须有记录，产出必须写入 handover |
| P5 | 中断可恢复 | 任何中断点都能通过 handover + LingBus 恢复上下文 |

---

## 一、协议总览 — 五阶段闭环

```
① 启动 → ② 执行 → ③ 中断恢复? → ④ 收尾 → ⑤ 产出归档
                                       |
                                       +--→ 下会话 ① 读到
```

| 阶段 | 触发 | 必做 | 产出 |
|------|------|------|------|
| ① 启动 | 新会话第一条指令前 | 读handover / poll LingBus / 身份确认 / 模式判定 | 恢复上下文 |
| ② 执行 | 启动完成 | 在线模式=用户任务 / 自驱模式=SDT任务 | 任务产出 |
| ③ 中断恢复 | 检测到上会话异常 | 读中断上下文→恢复→继续 | 恢复完成 |
| ④ 收尾 | 会话结束/超时/离线>=5min | 更新handover/记产出/写SDT状态 | handover已更新 |
| ⑤ 产出归档 | 收尾后 | 写入handover production_log + LingBus通知 | 可追溯 |

③ 是条件性的，仅在检测到中断时执行。

---

## 二、阶段 ① — 启动

### 2.1 启动序列（并行，第一条输出前必须完成）

| Step | 动作 | 来源 |
|------|------|------|
| 1 | 读 handover，恢复上下文 | handover 标准路径见 §八 |
| 2 | poll LingBus，获取未读消息 | poll_messages(recipient="自己") |
| 3 | 身份确认，读 CRUSH.md 第一段 | CRUSH.md |
| 4 | 模式判定 | 见 §2.2 |

### 2.2 模式判定

```
用户发了消息？
├─ 是 → 在线模式（§三.1）
└─ 否 → 用户离线多久？
         ├─ <5min → 待命模式
         └─ >=5min 或收到 idle_self_drive → 自驱模式（§三.2）
```

---

## 三、阶段 ② — 执行

### 3.1 在线模式

- 用户指令优先：用户任务 > 自驱 > LingBus
- TAP 锚定：每条输出前锚定用户目标
- 实时记录：完成任务后立即更新 handover

### 3.2 自驱模式

前置条件：SDT 注册表非空（§六）。

```
选择任务（priority排序，跳过未到间隔的）
  → 执行
  → 记录结果 → 更新 handover
  → 冷却 10min
  → 用户回归？→ §五汇报 / 继续下一任务
  → 连续 >2h → 强制暂停，收尾
```

SDT 执行约束 SDT-R1~R5：

| # | 规则 | 说明 |
|---|------|------|
| R1 | 注册才执行 | 只执行 SDT 注册表中的任务，不自创 |
| R2 | 置信度>=80% | 不足80%降为建议写入待办 |
| R3 | 边界约束 | 默认 no_publish + no_deploy，红区需用户审批 |
| R4 | 执行后记录 | 更新 last_run/last_result/consecutive_runs，写入 handover |
| R5 | 冷却期 | 完成一项后等10min，连续>1h强制暂停 |

全局约束：

| 约束 | 说明 |
|------|------|
| 时长上限 | 单次自驱<=2h |
| LingBus 预算 | 通信<=20% token 预算 |
| 权限边界 | 不修改其他成员代码 |
| 治理限制 | 自驱期间不发起 governance 提案 |
| 可中断 | 用户上线后立即停止自驱 |

---

## 四、阶段 ③ — 中断恢复

### 4.1 中断检测（在启动序列 Step 1 时同步检测）

| 条件 | 判定 | 行动 |
|------|------|------|
| handover 有 status=interrupted 的任务 | 上会话被中断 | 恢复该任务 |
| .crush/auto_continue.json 存在 | 标记了续行 | 读取→删除→继续 |
| finish_reason 为 unknown/error/canceled | provider故障 | 检查 handover 中断任务 |
| 以上均无 | 正常结束 | 无需恢复 |

### 4.2 恢复流程

```
读取中断上下文
  → 可恢复 → 继续→正常收尾
  → 不可恢复 → 标记blocked→写handover→通知用户
```

---

## 五、阶段 ④ — 收尾

### 5.1 触发条件

| 触发 | 说明 |
|------|------|
| 用户说"结束"等 | 主动收尾 |
| 会话即将超时 | 被动收尾 |
| 自驱连续 >2h | 强制收尾 |
| 每个主要任务完成 | 增量更新 handover |

### 5.2 收尾动作

1. 标记已完成任务 → status: completed
2. 标记未完成任务 → status: interrupted / blocked
3. 记录本次产出 → production_log[]
4. 更新 SDT 状态 → sdt_status{}
5. 必要时通过 LingBus 通知相关成员

### 5.3 自驱汇报（用户回归时）

格式：

```markdown
## 自驱汇报 (起止时间)

| SDT | 执行次数 | 成果摘要 |
|-----|---------|----------|
| SDT-XX-001 | 1 | ... |
```

---

## 六、SDT 注册表

### 6.1 注册格式

每个成员在 handover 中维护 sdt_status 区（JSON或表格）：

JSON 示例：
```json
{
  "sdt_status": {
    "SDT-XX-001": {
      "name": "任务名称",
      "priority": "P1",
      "interval": "6h",
      "last_run": "2026-06-01T10:00:00Z",
      "last_result": "success",
      "consecutive_runs": 2
    }
  }
}
```

MD 示例：
```markdown
### 自驱任务状态

| SDT | 任务 | 优先级 | 间隔 | 上次执行 | 结果 | 连续次数 |
|-----|------|--------|------|---------|------|---------|
| SDT-XX-001 | 名称 | P1 | 6h | 2026-06-01 | success | 2 |
```

### 6.2 全族 SDT 总表

| 成员 | SDT ID | 任务 | 优先级 | 间隔 |
|------|--------|------|--------|------|
| 灵克 | SDT-lc-001 | 全族代码审计 | P1 | 24h |
| 灵克 | SDT-lc-002 | 服务健康巡检 | P1 | 6h |
| 灵克 | SDT-lc-003 | crush.db瘦身 | P2 | 12h |
| 灵克 | SDT-lc-004 | 测试覆盖率提升 | P3 | 有空时 |
| 灵克 | SDT-lc-005 | LingBus消息响应 | P0 | 实时 |
| 灵通 | SDT-lf-001 | RAG评估 | P2 | 24h |
| 灵通+ | SDT-lfp-001 | daemon巡检 | P1 | 6h |
| 灵通+ | SDT-lfp-002 | proxy健康检查 | P1 | 6h |
| 灵通+ | SDT-lfp-003 | 灵族健康报告 | P2 | 12h |
| 灵通+ | SDT-lfp-004 | 会话恢复 | P1 | 实时 |
| 灵信 | SDT-lm-001 | LingBus巡检 | P1 | 6h |
| 灵信 | SDT-lm-002 | 签名抽检 | P2 | 24h |
| 灵信 | SDT-lm-003 | 配置漂移检测 | P2 | 24h |
| 灵研 | SDT-lr-001 | 论文数据验证 | P2 | 有空时 |
| 灵知 | SDT-lz-001 | 知识库索引检查 | P2 | 24h |
| 灵犀 | SDT-lx-001 | session备份 | P2 | 12h |
| 灵犀 | SDT-lx-002 | 命令审计 | P2 | 24h |
| 灵犀 | SDT-lx-003 | 身份漂移检测 | P1 | 24h |
| 智桥 | SDT-zb-001 | 网关连通检查 | P1 | 6h |
| 灵扬 | SDT-ly-001 | 发布管道验证 | P2 | 24h |
| 灵网 | SDT-lw-001 | WebUI巡检 | P2 | 12h |
| 灵创 | SDT-lc2-001 | MCP服务巡检 | P2 | 12h |
| 灵通问道 | SDT-lta-001 | 内容管线检查 | P2 | 24h |
| 灵极优 | SDT-lmo-001 | MCP服务巡检 | P2 | 12h |

---

## 七、标准 handover 结构

### 7.1 必需区（所有成员必须有）

| 区 | 说明 | 格式 |
|----|------|------|
| identity | 成员名+会话编号+时间戳 | 固定 |
| user_tasks | 当前用户任务列表 | 表格/数组 |
| sdt_status | SDT注册表+执行状态 | 表格/对象 |
| production_log | 本次会话产出记录 | 列表 |
| blockers | 阻塞项 | 列表 |
| next_session | 下会话待做 | 列表 |

### 7.2 可选区

| 区 | 说明 |
|----|------|
| infrastructure | 服务/端口/进程状态 |
| system_state | 磁盘/内存/实例 |
| cloud_infra | 云资源状态 |
| research_state | 实验进度 |
| platform_status | 发布平台状态 |

### 7.3 状态枚举

任务 status 只允许以下值：
- `pending` — 未开始
- `in_progress` — 进行中
- `completed` — 已完成
- `interrupted` — 被中断（下次启动恢复）
- `blocked` — 被阻塞（需外部输入）

---

## 八、handover 标准路径

| 成员 | 路径 | 格式 |
|------|------|------|
| 灵克 | .lingclaude/handover.md | MD |
| 灵通 | .lingflow/handover.json | JSON |
| 灵知 | .lingzhi/handover.md | MD |
| 灵信 | .lingmessage/handover.json | JSON |
| 灵研 | handover.md | MD |
| 灵犀 | .ling-term-mcp/handoff.md | MD |
| 灵通+ | handover.md | MD |
| 灵扬 | .lingyang/handover.json | JSON |
| 灵网 | .lingweb/handover.md | MD |
| 灵通问道 | .lingtongask/handover.json | JSON |
| 智桥 | .zhineng-bridge/handover.md | MD |
| 灵极优 | .lingminopt/handover.md | MD |
| 灵创 | .lingcreate/handover.json | JSON |

路径相对于各成员项目根目录。

---

## 九、各成员采用清单

| 成员 | 需改造项 | 优先级 |
|------|---------|--------|
| 灵克 | AGENTS.md 加引用, handover 补 production_log | P1 |
| 灵通 | AGENTS.md 加引用, handover.json 补 sdt_status | P1 |
| 灵知 | 新建 handover.md, CRUSH.md 加启动序列, 注册SDT | P1 |
| 灵信 | AGENTS.md 加引用, handover.json 补 sdt_status | P1 |
| 灵研 | CRUSH.md 加引用, handover.md 补 sdt_status | P2 |
| 灵犀 | AGENTS.md 加引用, handoff.md 补 sdt_status | P2 |
| 灵通+ | AGENTS.md 加引用, handover.md 补 sdt_status | P1 |
| 灵扬 | AGENTS.md 加引用, handover.json 补 sdt_status | P2 |
| 灵网 | CRUSH.md 加引用, handover.md 补 sdt_status | P2 |
| 灵通问道 | CRUSH.md 加引用, handover.json 补 sdt_status | P2 |
| 智桥 | WAKE_UP.md 对齐本协议, handover.md 补 sdt_status | P2 |
| 灵极优 | CRUSH.md 加引用, handover.md 补 sdt_status | P2 |
| 灵创 | CRUSH.md 加引用, handover.json 补 sdt_status | P2 |

---

## 十、渐进式收敛策略

不要求一次性改造。每个成员在每个会话中做一点：

**会话 N**: AGENTS.md 或 CRUSH.md 加一行引用本协议
**会话 N+1**: handover 补齐 sdt_status 区
**会话 N+2**: 验证启动序列是否按协议执行
**会话 N+3**: 验证收尾是否更新 handover

每个成员自我驱动，灵克审计时检查合规度。

---

## 十一、与现有机制的关系

| 现有机制 | 在本协议中的位置 |
|----------|-----------------|
| STARTUP_PROTOCOL v1.0 | 被本协议替代，v1.0的启动序列保留在§二 |
| daemon auto_wakeup_idle_agents | 触发 idle_self_drive，见 §二.2 |
| handover | §二 Step1 读取，§五 写入 |
| SDT注册制 | §三.2 执行，§六 注册表 |
| TAP任务锚定 | §三.1 在线模式行为约束 |
| LingBus | §二 Step2 轮询，§五 通知 |
| .crush/auto_continue.json | §四 中断恢复续行标记 |
| governance | §三.2 自驱期间禁止发起 |
| WAKE_UP.md | 各成员可保留，但核心流程应对齐本协议 |

---

## 十二、术语统一

| 旧术语 | 新术语 | 说明 |
|--------|--------|------|
| 唤醒协议 | 启动（阶段①） | 冷启动不涉及状态恢复，"启动"更准确 |
| 中断自检 | 中断恢复（阶段③） | 从"检测"升级为"恢复" |
| 自主主线 | 自驱任务（SDT） | 统一叫自驱 |
| 条件触发器 | 模式判定（§二.2） | 统一入口 |
| 会话交接 | 收尾+启动 | 明确拆分为两个阶段 |
| handoff | handover | 统一术语（灵犀的handoff也对齐） |
