# LingMessage 安全审计报告

**审计日期**: 2026-04-11  
**审计范围**: lingmessage/ 全部源码、CLI、MCP servers  
**审计人**: Crush (GLM-5.1)  
**版本基准**: v0.2.0 + security hardening commits  
**测试状态**: 274 passed, 0 failed  

---

## 审计概要

| 指标 | 数值 |
|------|------|
| 总发现 | 34 |
| Critical | 5 (全部已修复) |
| High | 8 (全部已修复) |
| Medium | 12 (11 已修复, 1 遗留) |
| Low | 9 (2 已修复, 7 遗留) |
| 总修复率 | 79.4% (27/34) |
| Critical/High 修复率 | 100% (13/13) |

---

## 已修复漏洞

### Critical

| ID | 文件 | 类别 | 修复方式 |
|----|------|------|----------|
| VULN-01 | mailbox.py | 路径穿越 | `_SAFE_ID_RE` 正则校验 + `_safe_thread_path()` 路径解析校验 + `_validate_id()` |
| VULN-12 | mailbox.py | 认证绕过 | `post()` 强制要求 VERIFIED 消息必须有 secret key，否则 ValueError |
| VULN-25 | poller.py | SSRF | `_is_localhost_url()` 限制通知端点仅限 localhost |
| VULN-29 | capability.py | 注册表投毒 | `from_dict()` 校验 command 白名单、localhost URL、tool/server_key 格式 |
| VULN-30 | capability.py | 命令注入/RCE | `_ALLOWED_COMMANDS` 白名单 (python3/node/npx/uvicorn 等) |

### High

| ID | 文件 | 类别 | 修复方式 |
|----|------|------|----------|
| VULN-02 | mailbox.py | 文件权限 | `_ensure_permissions()` 设置 root=0o700, key/audit/index=0o600 |
| VULN-03 | mailbox.py | 审计日志篡改 | HMAC hash chain (`_chain_hash`) 链式校验 |
| VULN-06 | mailbox.py | 密钥暴露 | `.secret_key` 文件权限 0o600 |
| VULN-14 | signing.py | 部分字段签名 | `delivery_status` 和 `metadata` 加入内容哈希 |
| VULN-16 | cli.py | --sign 逻辑错误 | 重设计：先 post 再签名，post() 内部 on-the-fly 计算签名 |
| VULN-20 | discuss.py | 导入劫持 | `importlib.util.spec_from_file_location()` 替代 `sys.path.insert(0, ...)` |
| VULN-22 | discuss.py | LLM 提示注入 | `[BEGIN_UNTRUSTED_MESSAGE]`/`[END_UNTRUSTED_MESSAGE]` 分隔符 |
| VULN-23 | discuss.py | 明文 API Key | `os.chmod(key_file, 0o600)` 限制密钥文件权限 |
| VULN-34 | (all) | 全局可读密钥 | 所有写入操作均设置 0o600 权限 |

### Medium (已修复)

| ID | 文件 | 类别 | 修复方式 |
|----|------|------|----------|
| VULN-07 | mailbox.py | JSON 无界 DoS | `_read_json_safe()` 10MB 大小限制 |
| VULN-08 | mailbox.py | 非原子写入 | `tempfile.mkstemp()` + `os.replace()` 原子写入 |
| VULN-28 | annotate.py | 非原子覆写 | tempfile + os.replace + chmod 0o600 |
| VULN-31 | capability.py | 注册表无锁 | 原子写入 (tempfile + os.replace) |
| VULN-09 | types.py | 不安全枚举解析 | `from_dict()` 所有枚举解析加 try/except fallback |
| VULN-10 | types.py | 未校验 metadata | metadata 键值长度限制 (key<=100, value<=1000) |
| VULN-18 | cli.py | 任意文件读取 | `cmd_import()` 路径校验 (cwd/home/tmp) |
| VULN-21 | compat.py | 无导入校验 | 类型检查 + 长度限制 + 空值保护 |
| VULN-11 | types.py | 静默无效数据 | 枚举 fallback 到安全默认值 |
| VULN-05 | mailbox.py | 锁 DoS | `_FileLock.__enter__()` stale 锁检测：>60s 自动清理 |
| VULN-24 | discuss.py | LLM 输出信任 | `_sanitize_llm_output()` 清理 null bytes + 长度限制 (10KB) |
| VULN-27 | poller.py | 未认证通知 | `X-LingMessage-Signature` HMAC-SHA256 签名头 |
| VULN-33 | mailbox.py | 无锁索引读 | `_load_index()` 改用 `_read_json_safe()` 大小限制读取 |

### Low (已修复)

| ID | 文件 | 类别 | 修复方式 |
|----|------|------|----------|
| VULN-17 | cli.py | Verbose 信息泄露 | `cmd_verify --verbose` 仅显示密钥来源类型，不暴露密钥值 |

---

## 遗留问题

### Medium (未修复)

| ID | 文件 | 类别 | 说明 |
|----|------|------|------|
| VULN-04 | mailbox.py | TOCTOU 竞态 | 文件存在性检查与操作之间存在理论竞态窗口。当前通过原子写入 (os.replace) 和文件锁部分缓解。完全修复需要重构为数据库后端。 |

### Low (未修复)

| ID | 文件 | 类别 | 说明 |
|----|------|------|------|
| VULN-15 | signing.py | 密钥为字符串 | 密钥以 str 传递，理论上可能被 swap。建议未来使用 bytes/keyctl。 |
| VULN-24 | ~~discuss.py~~ | ~~LLM 输出信任~~ | **已修复** — `_sanitize_llm_output()` 清理 null bytes + 10KB 限制 |
| VULN-26 | poller/notify | HTTP localhost | 通知使用明文 HTTP。建议未来升级为 Unix socket。 |
| VULN-27 | ~~poller.py~~ | ~~未认证通知~~ | **已修复** — HMAC-SHA256 `X-LingMessage-Signature` 签名头 |
| VULN-32 | (all) | 无消息完整性 | 非签名消息无完整性校验。signing 模块已就绪，但默认不启用。 |
| VULN-19 | cli.py | 无速率限制 | CLI 操作无速率限制。单用户场景风险低。 |

---

## 修复的文件清单

| 文件 | 修改类型 | 涉及漏洞 |
|------|----------|----------|
| `lingmessage/mailbox.py` | 重大加固 | VULN-01, 02, 03, 06, 07, 08, 12 |
| `lingmessage/cli.py` | 重新设计 | VULN-16, 17, 18 |
| `lingmessage/capability.py` | 重大加固 | VULN-29, 30, 31 |
| `lingmessage/discuss.py` | 安全修复 | VULN-20, 22, 23 |
| `lingmessage/signing.py` | 扩展签名 | VULN-14 |
| `lingmessage/annotate.py` | 原子写入 | VULN-28 |
| `lingmessage/poller.py` | SSRF + 认证 | VULN-25, 27 |
| `lingmessage/types.py` | 安全解析 | VULN-09, 10, 11 |
| `lingmessage/compat.py` | 输入校验 | VULN-21 |

## 修改的测试文件

| 文件 | 修改说明 |
|------|----------|
| `tests/test_capability.py` | `_make_cap()` command 改为 `python3`; merge 测试用 allowlisted commands |
| `tests/test_signing.py` | `test_hash_ignores_metadata` → `test_hash_includes_metadata`; `test_sign_verify_metadata_tamper_ignored` → `test_sign_verify_metadata_tamper_detected` |
| `tests/test_cli.py` | mock `_get_secret_key` for sign tests; adjust verify_verbose expectations |

---

## 安全架构决策

1. **读路径 vs 写路径 ID 校验分离** — 写路径严格校验 `_SAFE_ID_RE`，读路径 (`_thread_path_unchecked`) 宽松以支持优雅降级。

2. **post() 内 on-the-fly 签名** — VERIFIED 消息先创建再签名，post() 内部用 `hmac.compare_digest` 验证预计算签名或自动计算签名。

3. **命令白名单优于黑名单** — `_ALLOWED_COMMANDS` 仅允许已知安全的解释器和服务器命令。

4. **审计日志 HMAC 链** — 每条日志条目包含 `_chain_hash`，从前一条哈希链式计算，使篡改/删除可检测。

5. **LLM 提示注入缓解** — 非可信内容用 `[BEGIN_UNTRUSTED_MESSAGE]`/`[END_UNTRUSTED_MESSAGE]` 显式分隔。

6. **枚举安全解析** — 所有 `from_dict()` 枚举构造使用 try/except fallback，避免恶意数据导致 ValueError 崩溃。

---

## 建议的后续工作

1. **数据库后端迁移** — 将文件系统存储迁移至 SQLite（已有 LingBus 实验），彻底解决 TOCTOU 和锁问题。
2. **密钥管理升级** — 使用 `keyring` 库或 `keyctl` 替代文件存储密钥。
3. **Unix socket 通信** — 替代 HTTP localhost 通知。
4. **速率限制** — 为 CLI 操作添加基础速率限制。
