# Changelog

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
