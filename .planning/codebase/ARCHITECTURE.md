# Architecture

**Analysis Date:** 2026-06-20

## Pattern Overview

**Overall:** Plugin / Provider pattern — single-class monolith implementing the Hermes `MemoryProvider` ABC.

**Key Characteristics:**
- Single Python class `HydraDBMemoryProvider(MemoryProvider)` with all logic in one file
- Synchronous API backed by background daemon threads for non-blocking I/O
- Lazy, thread-safe client singleton via double-checked locking
- Circuit breaker for resilience (5 consecutive failures → 120s cooldown)
- Fire-and-forget writes; prefetch / cache model for reads
- Static tool schemas exposed to the model via OpenAI function-calling format

## Layers

**Config Layer:**
- Purpose: Read and persist provider configuration
- Contains: `_load_config()` (env + hydradb.json), `get_config_schema()` (field descriptors for `hermes memory setup`), `save_config()` (write non-secret config to disk)
- Location: `hydradb-memory/__init__.py` lines 81–185
- Depends on: `os.environ`, JSON file I/O
- Used by: Lifecycle layer (initialize), Hermes CLI tooling

**Lifecycle Layer:**
- Purpose: Handle provider registration, availability check, and initialization
- Contains: `name` (class attr), `is_available()` (checks API key + SDK import), `initialize(session_id, **kwargs)` (captures identity, sets up threading primitives, circuit breaker state)
- Location: `hydradb-memory/__init__.py` lines 188–237
- Depends on: Config layer, `hydra_db` SDK (optional import)
- Used by: Hermes Agent runtime at startup

**Client Layer:**
- Purpose: Manage the HydraDB SDK client instance
- Contains: `_get_client()` — lazy, thread-safe singleton via `threading.Lock` (double-checked locking pattern)
- Location: `hydradb-memory/__init__.py` lines 241–249
- Depends on: `hydra_db.HydraDB` SDK class, `_api_key` from config
- Used by: Read path, write path, tools

**Read Path:**
- Purpose: Retrieve relevant memories before each model turn
- Contains: `system_prompt_block()` (static text injected into system prompt), `queue_prefetch(query)` (fires background query), `prefetch()` (returns cached result), `_format_chunks(result, min_score)` (extracts clean memory text from SDK response objects)
- Location: `hydradb-memory/__init__.py` lines 272–337
- Depends on: Client layer, circuit breaker
- Used by: Hermes runtime (queue_prefetch before turn, prefetch during prompt assembly)

**Write Path:**
- Purpose: Persist conversation turns and memory writes into HydraDB
- Contains: `sync_turn(user_content, assistant_content)` (ingests user+assistant pair, `infer=True`), `on_memory_write(action, target, content, metadata)` (mirrors built-in memory operations, `infer=False`)
- Location: `hydradb-memory/__init__.py` lines 341–420
- Depends on: Client layer, circuit breaker
- Used by: Hermes runtime (sync_turn after each turn), Hermes built-in memory system (on_memory_write)

**Tools Layer:**
- Purpose: Expose memory operations to the model as function-calling tools
- Contains: `get_tool_schemas()` (returns OpenAI-format schemas for `hydradb_search`, `hydradb_profile`, `hydradb_conclude`), `handle_tool_call(tool_name, args)` (dispatches to `_tool_search`, `_tool_profile`, `_tool_conclude`)
- Location: `hydradb-memory/__init__.py` lines 31–75 (schemas), 424–495 (handlers)
- Depends on: Client layer
- Used by: Hermes Agent runtime (registers schemas, routes tool calls)

**Session Hooks Layer:**
- Purpose: Respond to session lifecycle events
- Contains: `on_session_end(messages)` (ingests last 10 user/assistant messages as episodic memory, `infer=True`), `shutdown()` (joins background threads with 5s timeout, clears client)
- Location: `hydradb-memory/__init__.py` lines 499–548
- Depends on: Client layer, circuit breaker, threading primitives from initialize
- Used by: Hermes runtime (on_session_end at session close, shutdown at provider teardown)

**Circuit Breaker (Cross-Cutting):**
- Purpose: Prevent cascading failures when HydraDB is unreachable
- Contains: `_is_breaker_open()` (returns True during cooldown), `_record_success()` (resets failure count), `_record_failure()` (increments count, opens breaker at threshold 5 for 120s)
- Location: `hydradb-memory/__init__.py` lines 253–268
- Depends on: `time.time()`
- Used by: Read path, write path, session hooks

## Data Flow

**Memory Retrieval (prefetch cycle):**

1. Hermes runtime calls `queue_prefetch(query)` with the current user message
2. Circuit breaker check (`_is_breaker_open()`) — if open, return immediately
3. Spawns daemon thread (`hydradb-prefetch`)
4. Thread calls `_get_client()` (lazy init if first call)
5. Thread calls `client.query(tenant_id, sub_tenant_id, query, type="memory", query_by, mode, max_results, graph_context=True)`
6. Thread calls `_format_chunks(result, min_score=0.3)` — extracts `chunk_content` from result chunks, filters by `relevancy_score`, joins with double newlines
7. Thread stores formatted string in `_prefetch_result` (under lock)
8. Thread calls `_record_success()` (resets circuit breaker on success) or `_record_failure()` (on exception)
9. Next turn: Hermes runtime calls `prefetch()` — returns cached result under lock, clears cache
10. Result injected into system prompt as `## HydraDB Memory\n{content}`

**Memory Storage (turn sync):**

1. Hermes runtime calls `sync_turn(user_content, assistant_content)` after each turn
2. Guard: skip if `_agent_context != "primary"` or circuit breaker open
3. Spawns daemon thread (`hydradb-sync`)
4. Thread formats memory: `"User: {user}\nAssistant: {assistant}"` with `infer=True` (HydraDB auto-extracts durable facts server-side)
5. Thread calls `client.context.ingest(type="memory", tenant_id, sub_tenant_id, memories=json_string, upsert="true")`
6. Fire-and-forget — no result returned to caller

**Memory Write Mirroring:**

1. Hermes built-in memory system calls `on_memory_write(action, target, content, metadata)`
2. Circuit breaker check
3. Spawns daemon thread
4. Thread formats memory: `"[{target}] {content}"` with optional metadata, `infer=False` (verbatim, no auto-extraction)
5. Thread calls `client.context.ingest(...)` — fire-and-forget

**Tool Call Flow:**

1. Model emits function call (e.g., `hydradb_search`)
2. Hermes runtime calls `handle_tool_call(tool_name, args)`
3. Dispatched to `_tool_search`, `_tool_profile`, or `_tool_conclude`
4. `_tool_search`: synchronous `client.query(mode="fast", max_results=5)` → `_format_chunks(min_score=0.2)` → JSON `{"result": ...}`
5. `_tool_profile`: synchronous `client.query(query="user profile preferences traits", mode="thinking")` → `_format_chunks(min_score=0.2)` → JSON
6. `_tool_conclude`: synchronous `client.context.ingest(infer=False, upsert="true")` → JSON `{"result": "Fact stored."}`
7. All exceptions caught, returned as JSON `{"error": msg}`

**Session End Flow:**

1. Hermes runtime calls `on_session_end(messages)`
2. Guard: skip if non-primary context or circuit breaker open
3. Spawns daemon thread
4. Thread extracts last 10 user/assistant messages from the last 20 total messages
5. Thread formats as `"User: ...\nAssistant: ..."` with `infer=True`
6. Thread calls `client.context.ingest(...)` — fire-and-forget

**State Management:**
- No persistent local state beyond `hydradb.json` config
- All memory state lives in HydraDB cloud
- In-memory state is transient: `_prefetch_result` (one turn cache), `_client` (SDK singleton), `_failure_count` / `_breaker_open_until` (circuit breaker)
- Threading primitives (`_client_lock`, `_prefetch_lock`) are session-scoped

## Key Abstractions

**MemoryProvider (ABC):**
- Purpose: Hermes Agent abstract base class defining the memory provider contract
- Implemented by: `HydraDBMemoryProvider`
- Pattern: Abstract Base Class — defines required interface (`initialize`, `prefetch`, `queue_prefetch`, `sync_turn`, `get_tool_schemas`, `handle_tool_call`, `system_prompt_block`, `is_available`, `shutdown`)
- Import: `from agent.memory_provider import MemoryProvider`

**Plugin Registration:**
- Purpose: Entry point for Hermes plugin discovery
- Implemented by: module-level `register(ctx)` function
- Pattern: Single-call registration — `ctx.register_memory_provider(HydraDBMemoryProvider())`
- Location: `hydradb-memory/__init__.py` lines 556–558

**Threaded I/O (Daemon Threads):**
- Purpose: Keep all I/O operations non-blocking to the agent runtime
- Examples: `queue_prefetch` spawns `hydradb-prefetch` thread, `sync_turn` spawns `hydradb-sync` thread, `on_memory_write` and `on_session_end` spawn anonymous threads
- Pattern: Each operation creates a new daemon thread — no thread pool, no queue
- Lifecycle: Threads joined in `shutdown()` with 5s timeout

**Circuit Breaker:**
- Purpose: Prevent retry storms when HydraDB is unreachable
- Pattern: Count-based with fixed cooldown (5 failures → 120s)
- State: `_failure_count` (int), `_breaker_open_until` (epoch float)
- Behavior: When open, `queue_prefetch`, `sync_turn`, `on_memory_write`, and `on_session_end` return immediately without attempting I/O

**Format Chunks:**
- Purpose: Extract clean memory text from HydraDB query results, avoiding `build_string()` framing overhead
- Pattern: Static method; iterates `result.data.chunks`, filters by `relevancy_score >= min_score`, extracts `chunk_content`, joins with `\n\n`

## Entry Points

**Plugin Registration:**
- Location: `hydradb-memory/__init__.py` → `register(ctx)` function
- Triggers: Hermes Agent plugin loader discovers `plugin.yaml` and calls `register(ctx)`
- Responsibilities: Register the `HydraDBMemoryProvider` instance with the Hermes runtime

**Provider Lifecycle:**
- Location: `HydraDBMemoryProvider.initialize()`
- Triggers: Called by Hermes runtime after registration, once per session
- Responsibilities: Load config, resolve tenant/sub_tenant identity, initialize threading primitives and circuit breaker

**Turn-Level Entry:**
- `queue_prefetch(query)` — called before each model turn to start background memory fetch
- `prefetch()` — called during prompt assembly to retrieve cached results
- `sync_turn(user, assistant)` — called after each turn to persist the exchange
- `handle_tool_call(name, args)` — called when model invokes a memory tool

## Error Handling

**Strategy:** Fail-open with circuit breaker — errors are caught, logged at DEBUG, and silently discarded. The circuit breaker prevents retry storms after 5 consecutive failures.

**Patterns:**
- All I/O operations wrapped in `try/except Exception` — never crash the agent
- `_record_success()` resets failure count and breaker on any successful operation
- `_record_failure()` increments count; at threshold 5, sets `_breaker_open_until = now + 120s`
- Circuit breaker guards: `queue_prefetch`, `sync_turn`, `on_memory_write`, `on_session_end` check `_is_breaker_open()` before attempting I/O
- Tool calls: `handle_tool_call` catches exceptions and returns JSON `{"error": str(e)}` — model sees the error but agent continues
- Thread failures: daemon threads silently terminate on exception (no join required)
- Config errors: `_load_config()` catches `JSONDecodeError` and `OSError`, logs warning, returns defaults

## Cross-Cutting Concerns

**Logging:**
- Standard `logging.getLogger(__name__)` — module-level logger
- INFO: initialization details (tenant, sub_tenant, mode)
- WARNING: config load failures, circuit breaker open
- DEBUG: I/O failures (prefetch, sync_turn, on_memory_write, on_session_end)

**Thread Safety:**
- `_client` protected by `threading.Lock` with double-checked locking
- `_prefetch_result` protected by `threading.Lock` (atomic get + clear)
- `_failure_count` and `_breaker_open_until` accessed without lock (simple int/float on CPython with GIL — acceptable for non-critical counter)

**Configuration:**
- Secrets (`HYDRA_DB_API_KEY`): environment variable only
- Non-secret config (`tenant_id`, `sub_tenant_id`, `query_mode`, `query_by`, `max_results`): `~/.hermes/hydradb.json` overrides module-level `DEFAULT_CONFIG`
- `sub_tenant_id` auto-resolves to `agent_identity` (profile name) for per-profile isolation — zero-config default

**Isolation:**
- Single HydraDB tenant (`tenant_id`) shared across all Hermes profiles
- Per-profile isolation via `sub_tenant_id` — defaults to profile name
- Set `sub_tenant_id: "shared"` for cross-profile memory
- Primary/secondary agent context guard: `sync_turn` and `on_session_end` skip if `_agent_context != "primary"`

---

*Architecture analysis: 2026-06-20*
*Update when major patterns change*
