# Crush 后台 Shell 机制与排查指南

**日期**: 2026-04-26
**来源**: 灵知 会话崩溃排查 + 二进制逆向分析
**适用**: 所有使用 Crush CLI 的灵族成员

## 一、后台 Shell 上限机制

### 硬约束

| 项目 | 值 |
|------|-----|
| 上限 | **50 个**（硬编码 `0x32`） |
| 所在方法 | `BackgroundShellManager.Start`（`internal/shell` 包） |
| 判断指令 | `CMP RAX, 0x32; JGE error_path` |
| 计数方式 | `LOCK XADD` 原子操作 |
| 可配置性 | **不可配置** — 无环境变量、无配置项、无命令行参数 |

### 死锁特性

达到 50 上限后：
- **所有** bash 调用返回 `"error starting shell: maximum number of background jobs (50) reached"`
- `job_kill` 也需要 bash，所以无法通过工具自恢复
- 形成单向死锁：只有用户手动重启会话才能恢复

### 合理使用范围

50 个后台 shell 对规范使用绰绰有余。典型场景：
- 常驻服务（server/watcher）：1-3 个
- 偶尔的长任务：1-2 个

**浪费行为**（会导致崩溃）：
- `sleep N && check` 后台轮询
- 反复开 `sleep` 任务等待结果
- 不清理已完成的后台 shell

## 二、Crush 数据库架构

### 按项目分库

Crush 为每个项目目录维护独立的数据库：

```
/home/ai/lingzhi/.crush/crush.db  ← 灵知的项目库
/home/ai/lingclaude/.crush/crush.db                 ← 灵犀的项目库
/home/ai/.crush/crush.db                            ← 用户级（无项目目录时的会话）
```

### 项目映射

`/home/ai/.local/share/crush/projects.json` 中 `data_dir` 字段指向各项目的数据库位置。

查会话历史时，**必须找到正确的项目库**，否则查到的可能是另一个项目的记录。

### 关键排查命令

```bash
# 查看项目映射
cat ~/.local/share/crush/projects.json

# 查特定项目数据库中的会话
sqlite3 /path/to/.crush/crush.db "SELECT id, title, message_count FROM sessions ORDER BY created_at DESC LIMIT 10;"

# 查特定会话的错误记录
sqlite3 /path/to/.crush/crush.db "SELECT * FROM messages WHERE session_id='SESSION_ID' AND role='error';"
```

## 三、教训

1. **"找不到"和"不存在"是两回事** — 用户能看到的数据一定存在，找不到说明数据源或方法有问题，不说明数据不存在
2. **到手的数据要理解** — `projects.json` 里的 `data_dir` 已经指向正确位置，读了但没理解等于没读
3. **工具的约束是行为的镜子** — 50 上限不是设计缺陷，它暴露的是浪费资源的坏习惯

## 四、版本信息

- 分析版本: crush v0.39.3（2026-02-05 编译）
- 二进制路径: `/usr/local/lib/node_modules/@charmland/crush/bin/crush`
- 上限可能随版本变化，升级后需重新确认
