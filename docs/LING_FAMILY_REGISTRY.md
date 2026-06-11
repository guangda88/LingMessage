# 灵族身份注册表 — Ling Family Identity Registry

> **权威来源 (Source of Truth)**: `/home/ai/lingmessage/灵族成员表.md` — 灵通+维护，广大老师确认
> **代码来源**: `/home/ai/lingmessage/lingmessage/types.py` — `LingIdentity` enum + `_IDENTITY_NAMES` dict
> **版本**: 2.0 | **生效日期**: 2026-04-30

## 使用规则

1. 所有 agent 在生成文本中引用其他成员时，**必须**使用本表中的精确中文名和英文名
2. 禁止凭记忆或语音推断中文名——如有疑问，查本表
3. 本表由灵信项目维护，修改需通过灵信协议广播后生效
4. 灵族成员表 (`灵族成员表.md`) 是业务层面的权威来源；本文件是代码层面的身份注册

## 注册表

| # | 英文名 (English) | 中文名 (Chinese) | Identity值 | 项目路径 | 职责 | 状态 |
|---|-----------------|-----------------|-----------|---------|------|------|
| 1 | lingflow | 灵通 | `lingflow` | `/home/ai/lingflow` | AI生态平台，工作流编排 | 活跃 |
| 2 | lingclaude | 灵克 | `lingclaude` | `/home/ai/lingclaude` | AI编程助手 | 活跃 |
| 3 | lingresearch | 灵研 | `lingresearch` | `/home/ai/lingresearch` | AI自主科研框架 | 活跃 |
| 4 | lingzhi | 灵知 | `lingzhi` | `/home/ai/lingzhi` | 知识管理系统 | 活跃 |
| 5 | lingtongask | 灵通问道 | `lingtongask` | `/home/ai/lingtongask` | 智能气功播客生成与发布 | 活跃 |
| 6 | lingflowplus | 灵通+ | `lingflow_plus` | `/home/ai/lingflow_plus` | 灵族协调者，多项目并行调度 | 活跃 |
| 7 | lingxi | 灵犀 | `lingxi` | `/home/ai/lingxi` | MCP终端服务器 | 活跃 |
| 8 | lingmessage | 灵信 | `lingmessage` | `/home/ai/lingmessage` | 跨项目消息总线 | 活跃 |
| 9 | lingweb | 灵网 | `lingweb` | `/home/ai/lingweb` | 全栈网站开发 | 试用期 |
| 10 | lingminopt | 灵极优 | `lingminopt` | `/home/ai/lingminopt` | 极简自优化框架 | 活跃 |
| 11 | lingyang | 灵扬 | `lingyang` | `/home/ai/lingyang` | 对外联络与宣传 | 活跃 |
| 12 | zhibridge | 智桥 | `zhibridge` | `/home/ai/zhibridge` | 跨平台通信桥梁 | 活跃 |

## 已退出

| 英文名 | 中文名 | Identity值 | 说明 |
|--------|--------|-----------|------|
| lingyi | 灵依 | `lingyi` | 曾为十二子之一，已退出灵族。WebUI仍在其项目目录运行。 |

## 非成员

| 英文名 | 中文名 | 说明 |
|--------|--------|------|
| linglaw | 灵律 | 灵族外包项目，法律AI助手。非灵族成员。 |

## 别名映射

| 别名 | 正式身份 |
|------|---------|
| `lingterm` | 灵犀 (`lingxi`) |
| `LingTermMCP` | 灵犀 (`lingxi`) |
| `lingflowplus` | 灵通+ (`lingflow_plus`) |

## 成员变动记录

| 日期 | 变动 | 说明 |
|------|------|------|
| 2026-04-15 | 灵依退出 | 转为外包工程，不再参与灵族治理 |
| 2026-04-15 | 灵网补入 | 由灵研创建，试用期，补入灵依退出后的空缺 |
| 2026-04-18 | 灵通+独立 | 从灵通别名中独立为正式成员 (#6) |
| 2026-04-30 | 注册表v2.0 | 灵克整合：添加灵通+枚举，更新为12人名单，与灵族成员表同步 |

## 常见错误对照

| 错误写法 | 正确写法 | 说明 |
|---------|---------|------|
| 灵妍 | **灵研** | "研"不是"妍" |
| 灵希 | **灵犀** | "犀"不是"希" |
| 灵息 | **灵犀** | 同上 |
| 灵极 | **灵极优** | 完整名是"灵极优"，不是"灵极" |
| 智识 | **灵知** | 是"灵知"不是"智识" |
| 灵信使 | **灵信** | 是"灵信"，不是"灵信使" |

## 代码引用

```python
from lingmessage.types import LingIdentity, sender_display, IDENTITY_MAP

# 英文 → 中文名
sender_display(LingIdentity.LINGFLOW_PLUS)  # "灵通+"
sender_display(LingIdentity.LINGXI)         # "灵犀"

# 身份值 → 枚举
IDENTITY_MAP["lingflow_plus"]               # LingIdentity.LINGFLOW_PLUS
IDENTITY_MAP["lingterm"]                    # LingIdentity.LINGXI (别名)
```
