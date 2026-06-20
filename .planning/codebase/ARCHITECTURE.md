# Architecture

**Analysis Date:** 2026-06-20
**Updated:** 2026-06-20 — documentation pass (dual providers, dual circuit breaker)

## Pattern Overview

**Overall:** Plugin / Provider pattern — two single-class implementations of the Hermes `MemoryProvider` ABC, each backed by a different memory engine.

**HydraDB Provider** (`HydraDBMemoryProvider`, 735 lines):
- Cloud-backed via HydraDB v2 API
- Synchronous SDK client (`hydradb-sdk`)
- Background daemon threads for non-blocking I/O
- Lazy, thread-safe client singleton via double-checked locking
- Dual circuit breaker (independent read/write gauges)
- Fire-and-forget writes; prefetch / cache model for reads
- Static tool schemas (OpenAI function-calling format)

**MuninnDB Provider** (`MuninnDBMemoryProvider`, 760 lines):
- Local cognitive engine via MuninnDB REST API
- Sync HTTP client (`requests.Session`)
- All cognitive primitives engine-native (ACT-R scoring, Hebbian learning, Bayesian confidence, PAS, typed relationships)
- Same threading/circuit-breaker/prefetch patterns as HydraDB provider
- Richer tool schemas (12 memory types, confidence thresholds, concept+content split)

Both providers share the same architectural patterns — only the backend API differs.

## HydraDB Provider — Layers

### Config Layer
- Purpose: Read and persist provider configuration
- Contains: `_load_config()` (env + hydradb.json), `get_config_schema()` (field descriptors), `save_config()` (write non-secret config)
- Depends on: `os.environ`, JSON file I/O
- Used by: Lifecycle layer, Hermes CLI tooling

### Lifecycle Layer
- Purpose: Provider registration, availability check, initialization
- Contains: `name` ("hydradb"), `is_available()` (checks API key + SDK import), `initialize(session_id, **kwargs)` (captures identity, sets up threading, tenant auto-provisioning)
- Depends on: Config layer, `hydra_db` SDK (optional import)
- Used by: Hermes Agent runtime at startup

### Client Layer
- Purpose: Manage the HydraDB SDK client instance
- Contains: `_get_client()` — lazy, thread-safe singleton via `threading.Lock` (double-checked locking)
- Depends on: `hydra_db.HydraDB` SDK class, `_api_key` from config

### Tenant Provisioning
- Purpose: Auto-create tenant on first run, poll until ready
- Contains: `_ensure_tenant()` — create if missing (handles 409 conflict), poll `/tenants/status` every 5s up to 5 min
- Depends on: Client layer, circuit breaker
- Note: Creates `_tenant_ready` flag to skip subsequent calls

### Read Path
- Purpose: Retrieve relevant memories before each model turn
- Contains: `system_prompt_block()` (static text), `queue_prefetch(query)` (background query), `prefetch()` (returns cached), `_format_chunks(result, min_score)` (clean prose extraction from SDK response)
- Depends on: Client layer, circuit breaker (read gauge)
- Note: `graph_context=True` is requested but query_paths are not surfaced in formatted output

### Write Path
- Purpose: Persist conversation turns and memory writes into HydraDB
- Contains: `sync_turn(user, assistant)` (ingests pair, `infer=True`), `on_memory_write(action, target, content, metadata)` (mirrors built-in ops, `infer=False`, content-hash IDs for deterministic upsert/delete)
- Depends on: Client layer, circuit breaker (write gauge)

### Tools Layer
- Purpose: Expose memory operations to the model as function-calling tools
- Contains: `get_tool_schemas()` ([SEARCH, PROFILE, CONCLUDE]), `handle_tool_call(name, args)` (dispatches to `_tool_search`, `_tool_profile`, `_tool_conclude`)
- Depends on: Client layer
- Note: `_tool_search` uses `mode="fast"` for lower latency; `_tool_profile` uses `mode="thinking"`

### Session Hooks
- Purpose: Respond to session lifecycle events
- Contains: `on_session_end(messages)` (ingests last 10 user/assistant messages as episodic memory), `shutdown()` (joins background threads with 5s timeout, clears client)
- Depends on: Client layer, circuit breaker, threading primitives

## MuninnDB Provider — Layers

### Config Layer
- Purpose: Read and persist provider configuration
- Contains: `_load_config()` (env + muninn.json), `get_config_schema()` (field descriptors), `save_config()`
- Config keys: `base_url` (default `http://127.0.0.1:8475`), `vault` (default `"default"`), `api_key` (optional for default vault)
- Depends on: `os.environ`, JSON file I/O

### Lifecycle Layer
- Purpose: Provider registration, availability check, initialization
- Contains: `name` ("muninn"), `is_available()` (checks `requests` import), `initialize()` (loads config, creates HTTP session with bearer auth)
- Simpler than HydraDB — no tenant provisioning (MuninnDB creates vaults on first write)

### HTTP Client Layer
- Purpose: Sync HTTP client for MuninnDB REST API
- Contains: `_session` (`requests.Session`), `_post()` (POST helper), `_get()` (GET helper), `_health_check()` (GET /api/health)
- Uses bearer auth header when `MUNINN_API_KEY` is set

### Read Path
- Purpose: Retrieve relevant memories via MuninnDB ACTIVATE pipeline
- Contains: `system_prompt_block()` (static text describing cognitive features), `queue_prefetch()` (background ACTIVATE via daemon thread), `prefetch()` (returns cached activations), `_activate()` (POST /api/activate), `_format_activations()` (concept headers, confidence warnings, dormant filtering)
- Key difference: Uses `context` array for semantic search, supports `memory_type` filter and `threshold` at API level
- Note: All cognitive scoring (ACT-R, Hebbian boosting, PAS injection) happens in MuninnDB engine — no plugin-level formatting logic needed

### Write Path
- Purpose: Persist memories as engrams in MuninnDB
- Contains: `sync_turn()` (ingests turn as event engram), `on_memory_write()` (mirrors built-in ops as fact/preference engrams), `_write_engram()` (POST /api/engrams with concept, content, tags, type_label, confidence)
- Key difference: Uses concept+content split (Muninn's memory model), tags for auto-association, type_label for classification

### Tools Layer
- Purpose: Expose cognitive memory operations to the model
- Contains: `get_tool_schemas()` ([SEARCH, PROFILE, REMEMBER]), `handle_tool_call()` (dispatches to `_tool_search`, `_tool_profile`, `_tool_remember`)
- Key differences from HydraDB:
  - `muninn_search`: supports `memory_type` filter (12 built-in types), `min_confidence` threshold
  - `muninn_profile`: dual query — preferences (memory_type=preference) + identity (memory_type=identity)
  - `muninn_remember`: structured input (concept+content+type+tags) instead of flat text
  - All 12 memory types listed in tool schema enums

### Session Hooks
- Same pattern as HydraDB: `on_session_end()` ingests session summary, `shutdown()` drains threads and closes HTTP session

## Cross-Cutting Concerns (Both Providers)

### Circuit Breaker
- **Dual independent gauges** — read failures don't trip write breaker and vice versa
- Each gauge: 5 consecutive failures → 120s cooldown
- Read gauge guards: `queue_prefetch`, `_tool_search`, `_tool_profile`
- Write gauge guards: `sync_turn`, `on_memory_write`, `_tool_remember`/`_tool_conclude`, `on_session_end`
- State: `_read_failures`, `_write_failures` (int), `_read_breaker_open_until`, `_write_breaker_open_until` (epoch float)
- All gauge state protected by `_breaker_lock` (threading.Lock)

### Thread Safety
- Client protected by `threading.Lock` with double-checked locking (HydraDB) / HTTP session reused (MuninnDB)
- Prefetch result protected by `threading.Lock` (atomic get + clear)
- Circuit breaker counters accessed under `_breaker_lock`

### Error Handling
- Fail-open: all exceptions caught, logged at DEBUG, silently discarded
- Tool calls return JSON `{"error": str(e)}` — model sees error, agent continues
- Daemon threads silently terminate on exception
- Config errors catch `JSONDecodeError` + `OSError`, log warning, return defaults

### Logging
- Standard `logging.getLogger(__name__)` — module-level logger
- INFO: initialization details (vault/tenant, mode)
- WARNING: config load failures, circuit breaker open
- DEBUG: I/O failures (prefetch, sync_turn, on_memory_write, on_session_end) with exc_info=True

### Configuration
- Secrets: environment variables only (`HYDRA_DB_API_KEY`, `MUNINN_API_KEY`)
- Non-secret config: JSON files in `$HERMES_HOME` (`hydradb.json`, `muninn.json`) merge over module-level `DEFAULT_CONFIG`
- HydraDB: `sub_tenant_id` auto-resolves to profile name for per-profile isolation
- MuninnDB: `vault` defaults to `"default"` — use per-profile vault names for isolation

### Agent Context Guard
- `sync_turn` and `on_session_end` skip if `_agent_context != "primary"` — prevents cron/subagent/flush contexts from polluting memory

---

*Architecture analysis: 2026-06-20*
*Update when major patterns change*
