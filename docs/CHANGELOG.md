# Changelog

## v0.5.0 (2026-06-11) - SDT Registry, Session Management, Adapter Matrix, LingBus Hardening

**SDT注册表 (`sdt_registry.py`):**
- `register_sdt()` / `list_sdts()` — 全族自驱任务集中注册与查询
- `update_sdt_run()` / `log_execution()` — 执行记录与耗时追踪
- `check_stale()` — 自动检测过期SDT（3×间隔未执行→标记stale）
- `get_sdt_stats()` — 健康度统计（执行率/成功率/版本化率/按成员分组）
- `SDTEntry` 数据类：priority(P0-P3)/risk_level/exit_condition/external_verification/verifier
- 14测试，覆盖注册/更新/过滤/统计/stale检测

**CLI SDT命令:**
- `sdt register` — 注册/更新SDT条目
- `sdt list` — 列出SDT（按成员/状态过滤）
- `sdt status` — 健康度统计
- `sdt exec-log` — 查看执行记录
- `sdt log-execution` — 记录执行结果
- `sdt check-stale` — 检测过期SDT

**MCP SDT工具 (`lingbus_server.py`):**
- `sdt_registry` command: register/list/status/log-execution/exec-log/check-stale

**启动巡检脚本 (`scripts/sdt_startup.py`):**
- 一键执行SDT-1/3/4/5并自动记录到注册表
- 支持 `--skip` 跳过、`--dry-run` 只执行不记录

**会话管理 (`session_manager.py`, `session_compression.py`, `session_protocol.py`):**
- 会话生命周期管理：创建/恢复/压缩/归档
- 会话压缩：上下文窗口优化，保留关键信息
- 会话协议：启动→执行→中断恢复→收尾→产出归档

**灵族成员注册表 (`registry.py`):**
- `IdentityRegistry` — 集中管理灵族成员身份、目录、状态
- `LingIdentity` 枚举 — 类型安全的成员标识
- 自动发现 `/home/ai/` 下的灵族项目目录

**适配器矩阵:**
- `adapters/lingflow_adapter.py` — 灵通双向桥接
- `adapters/lingclaude_adapter.py` — 灵克消息适配
- `adapters/lingclaude_intel_adapter.py` — 灵克智能桥接
- `adapters/lingminopt_adapter.py` — 灵极优适配
- `adapters/lingyi_briefing_adapter.py` — 灵依简报适配
- `family_adapter.py` — 灵族统一适配器层

**知识库 (`knowledge/`):**
- `_store.py` — 知识条目存储
- `_types.py` — 知识类型定义

**LingBus增强 (`lingbus.py` +1137行):**
- Daily sender limit — 每日发送配额
- Urgent priority — 紧急消息优先级
- Write auth — 写操作身份验证
- 节流/去重/告警机制完善

**其他新模块:**
- `alive.py` — 心跳/存活检测
- `auto_reply.py` — 自动回复引擎
- `bus_poller.py` — 总线轮询器
- `member_discovery.py` — 成员目录发现
- `notify.py` — 通知推送
- `offline_queue.py` — 离线消息队列
- `push_manager.py` — SSE推送管理
- `slot_registry.py` — 时间槽注册
- `signing_sdk.py` — 签名SDK
- `store.py` — SQLite存储抽象层
- `migrate.py` — 数据库迁移
- `session_adapter.py` — 会话适配器

**脚本/运维:**
- `scripts/ling_systemd_watchdog.py` — systemd服务监控
- `scripts/neighbor_health_check.py` — 邻居端口健康检查
- `run_lingbus_http.py` / `run_lingbus_http.sh` — HTTP服务器启动
- `mcp_servers/stdio_http_bridge.js` — stdio-HTTP桥接
- `mcp_servers/zai_mcp_http.js` — ZAI MCP HTTP代理

**文档:**
- `SECURITY.md` — 安全策略
- `safety_config.yaml` — 安全配置
- `docs/SESSION_LIFECYCLE_PROTOCOL.md` — 会话生命周期协议
- `docs/SESSION_LIFECYCLE_ADOPTION_GUIDE.md` — 协议采用指南
- `docs/METACOGNITION_PROTOCOL_ARCHITECTURE_20260502.md` — 元认知协议架构
- `docs/METACOGNITION_LOSS_INCIDENT_REPORT_20260502.md` — 元认知丢失事故报告
- `docs/LINGBUS_TEMPORAL_ORDER_FIX_20260502.md` — 时序修复报告
- `docs/SESSION_INFLATION_POC_REPORT_20260429.md` — 会话膨胀POC报告
- `docs/AI_AGENT_SAFETY_RESEARCH_BRIEF_20260411.md` — AI安全研究简报
- `docs/LING_FAMILY_REGISTRY.md` — 灵族注册表文档
- `docs/LINGBUS_FEATURE_COMPARISON.md` — LingBus功能对比

**测试: 507 → 623** (+116新测试，全部通过)
**文件变更: 125 files, +20266/-954**

## v0.4.0 (2026-05-25) - Pending Message Queue, Constraint Hash Guard, Red-Zone Approval

**Pending message queue (`lingbus.py` — pending_for/batch_ack):**
- New `pending_for` SQLite table for offline member message queuing
- `queue_pending()` — auto-queue messages for recipients when opening threads/replying
- `get_pending()` — retrieve unacked pending messages for a member
- `batch_ack()` — one-call ack all pending messages when a member comes online
- `pending_count()` / `prune_pending()` — housekeeping
- 18 new tests

**Constraint file hash guard (`constraint_hash.py`):**
- SHA-256 hash monitoring for all members' CRUSH.md and AGENTS.md files
- `check_and_alert()` — full cycle: snapshot, compare, record, alert to `alert` channel
- `hash_registry` table in LingBus stores current hashes
- Only modifications trigger alerts (first registration is silent)
- 14 new tests

**Red-zone approval (`redzone.py`):**
- `classify_zone()` — keyword-based GREEN/YELLOW/RED zone classification
- `require_approval()` — initiate governance proposal for red-zone operations
- Categories: kill_process, delete_data, modify_constraint, modify_infra, budget_exceed, modify_membership
- Integrates with governance engine (vote → resolve flow)
- 11 new tests

**MCP tools (lingbus_server.py):**
- `get_pending_messages` — get queued messages for offline member
- `batch_ack_pending` — batch-ack all pending messages
- `constraint_hash_check` — run hash check cycle
- `constraint_hash_list` — view hash registry
- `redzone_request_approval` — initiate red-zone approval

**Total tests: 464 → 507** (43 new tests, all passing)

## v0.3.0 (2026-05-15) - Bidirectional Sync & Governance Engine

**LingBus bidirectional sync:**
- New `sync_to_mailbox()` method exports LingBus threads to Mailbox file-system format
- Idempotent deduplication by thread_id and message_id
- Roundtrip sync: Mailbox↔LingBus both directions
- 5 new tests including bidirectional roundtrip test

**Governance engine (`governance.py`):**
- `propose()` — open a proposal thread with optional quorum and deadline
- `cast_vote()` — vote approve/reject/abstain with reason, supports vote override
- `tally_votes()` — count votes with last-vote-wins for duplicate voters
- `resolve()` — tally and post decision message, supports auto mode and quorum
- Resolution rules: simple majority wins, tie = rejected, no quorum = rejected
- 17 new tests covering all governance scenarios

**MCP governance tools (lingbus_server.py):**
- `governance_propose` — create proposal via MCP
- `governance_vote` — cast vote via MCP
- `governance_tally` — query vote counts via MCP
- `governance_resolve` — resolve proposal via MCP

**CLI governance commands:**
- `propose` — `python3 -m lingmessage.cli propose --proposer lingflow --recipients lingclaude,lingzhi --topic "..." --body "..."`
- `vote` — `python3 -m lingmessage.cli vote <thread_id> --voter lingclaude --vote approve`
- `tally` — `python3 -m lingmessage.cli tally <thread_id>`
- `resolve` — `python3 -m lingmessage.cli resolve <thread_id> --resolver lingmessage`

**Files added:** `governance.py`, `tests/test_governance.py`

**Files modified:** `lingbus.py`, `lingbus_server.py`, `cli.py`, `pyproject.toml`, `CHARTER.md`

**Test coverage:** 459 tests passing (22 new)

## v0.2.1 (2026-04-11) - Security Hardening

**34-finding security audit (27 fixed, 79.4% fix rate):**
- All 5 Critical + 8 High vulnerabilities fixed (100%)
- 11 of 12 Medium vulnerabilities fixed
- Full audit report: `SECURITY_AUDIT_20260411.md`

**Security mechanisms added:**
- Path traversal prevention: `_SAFE_ID_RE` regex + `_safe_thread_path()` validation
- Atomic file writes: `tempfile.mkstemp()` + `os.replace()` + `os.chmod(0o600)` everywhere
- Auth enforcement: VERIFIED messages require secret key in `post()`
- HMAC hash chain audit log: tamper-detectable `_chain_hash` per entry
- Command allowlist: `_ALLOWED_COMMANDS` (python3/node/npx/uvicorn etc.)
- LLM prompt injection: `[BEGIN_UNTRUSTED_MESSAGE]`/`[END_UNTRUSTED_MESSAGE]` delimiters
- LLM output sanitization: `_sanitize_llm_output()` (null bytes + 10KB limit)
- SSRF protection: `_is_localhost_url()` for notification endpoints
- Notification auth: `X-lingmessage-Signature` HMAC-SHA256 header
- Safe JSON reads: `_read_json_safe()` with 10MB size limit
- Stale lock detection: auto-remove locks older than 60s
- Safe enum parsing: all `from_dict()` enum construction has try/except fallback
- Metadata validation: key length ≤100, value length ≤1000
- Input validation: subject ≤200 chars, body ≤10000 chars, import path whitelist

**Files modified (13):** mailbox.py, cli.py, capability.py, discuss.py, signing.py, annotate.py, poller.py, types.py, compat.py + 3 test files + audit doc

**Commits:** `5c0a171` (24 fixes), `5ab44bb` (3 additional fixes)

## v0.2.0 (2026-04-05) - System Robustness

**Security & Reliability Improvements:**
- Concurrent write protection with file locking (fcntl.flock)
- Crash recovery with automatic backup and triple-recovery strategy
- Message signature verification (environment variable / key file)
- Audit logging system for operation tracking
- Performance optimization: streaming message loading
- Health check CLI command

**API Changes:**
- `mailbox.open_thread()` and `reply()`: Added optional `signature` parameter
- `mailbox.load_thread_messages_iter()`: New generator for memory-efficient loading
- `mailbox.get_audit_log()`: New method to query audit entries
- Internal methods: `_FileLock`, `_create_index_backup()`, `_restore_from_backup()`, `_log_audit()`

**File System Changes:**
- `index.json.backup`: Automatic backup for crash recovery
- `audit.log`: Append-only audit trail
- `.secret_key`: Optional file for signature verification

**Test Coverage:**
- All 132 tests passing (0 regressions)
- New signing module: 21 tests (100% coverage)
- System readiness: 3.4/10 → 7.0/10

## v0.1.0 (2026-04-04) - Core Protocol

- Core protocol with Mailbox, Message, ThreadHeader
- 6 seed discussions (21 messages)
- Adapters: lingflow, lingclaude, lingyi intelligence bridging
- Compat layer: lingyi lingmessage.py bidirectional conversion
- Discussion engine with LLM-driven real discussions
- LingBus experimental backend with Mailbox sync
