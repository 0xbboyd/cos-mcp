# Architecture

**Analysis Date:** 2026-06-20
**Updated:** 2026-06-20 — documentation pass (shared infrastructure, context engines, tests)

## Pattern Overview

**Overall:** Shared infrastructure + thin plugins. The `cos_mcp` package provides base classes, backends, formatters, and a circuit breaker. Four thin plugins extend them — two memory providers, two context engines — each backed by a different storage engine (HydraDB Cloud or MuninnDB local).

```
cos_mcp/                          # Shared infrastructure (4567 lines)
├── circuit_breaker.py            # Dual-gauge circuit breaker
├── base_provider.py              # BaseMemoryProvider (MemoryProvider ABC)
├── base_context_engine.py        # BaseContextEngine (ContextEngine ABC)
├── backends/
│   ├── base.py                   # MemoryBackend ABC
│   ├── hydradb.py                # HydraDBBackend
│   └── muninn.py                 # MuninnDBBackend
└── formatting/
    ├── base.py                   # MemoryFormatter ABC
    ├── context_base.py           # ContextFormatter ABC
    ├── hydradb.py                # HydraDBFormatter
    ├── hydradb_context.py        # HydraDBContextFormatter
    ├── muninn.py                 # MuninnDBFormatter
    └── muninn_context.py         # MuninnDBContextFormatter

hydradb-memory/                   # Thin HydraDB memory provider (~284 lines)
muninn-memory/                    # Thin MuninnDB memory provider (~384 lines)
plugins/context_engine/
├── hydradb-context/              # Thin HydraDB context engine (~973 lines)
└── muninn-context/               # Thin MuninnDB context engine (~1007 lines)
```

Every plugin follows the same pattern: subclass the base, create a backend + formatter, define tool schemas + handlers.

## Shared Infrastructure — `cos_mcp`

### BaseMemoryProvider (MemoryProvider ABC)

The thick base that handles everything a memory provider needs — subclasses (hydradb-memory, muninn-memory) are thin adapters (~280-380 lines) that only define backend-specific config, tool schemas, handlers, and system prompt text.

**Layers:**

- **Config Layer:** `_load_config_file()` (static helper — reads `{hermes_home}/{name}.json`, merges overrides), `get_config_schema()` + `save_config()` (override in subclass).
- **Lifecycle Layer:** `is_available()` (classmethod, override), `initialize(session_id, **kwargs)` — captures identity, creates backend + formatter via subclass hooks, provisions backend, sets up circuit breaker and threading primitives.
- **Read Path:** `system_prompt_block()` (static, override), `queue_prefetch(query)` — fires background daemon thread query, checks read breaker. `prefetch()` — returns cached result atomically, clears cache.
- **Write Path:** `sync_turn(user, assistant)` — fire-and-forget ingest of turn pair (skips if agent_context != "primary"). `on_memory_write(action, target, content, metadata)` — mirrors built-in memory ops, uses content-hash IDs for deterministic upsert/delete. Both check write breaker.
- **Tools Layer:** `get_tool_schemas()` (override), `handle_tool_call(name, args, **kwargs)` (override). Shared helpers: `_tool_search_impl(args, max_results, min_score, query_mode)`, `_tool_conclude_impl(args)`.
- **Session Hooks:** `on_session_end(messages)` — ingests last 10 user/assistant messages as episodic memory (skips if agent_context != "primary"). `shutdown()` — joins background threads (5s timeout), calls `_backend.shutdown()`.

**Subclass contract (hydradb-memory, muninn-memory):**
1. Set `name` class attr
2. Implement `_create_backend(kwargs)` → MemoryBackend
3. Implement `_create_formatter()` → MemoryFormatter
4. Implement `is_available()` (classmethod)
5. Implement `get_tool_schemas()`, `handle_tool_call()`, `system_prompt_block()`
6. Implement `get_config_schema()`, `save_config()`

### BaseContextEngine (ContextEngine ABC)

The thick base that handles token tracking, compression gating, circuit breaker, and session lifecycle — subclasses (hydradb-context, muninn-context) add entity extraction, compress() assembly, and tool schemas/handlers.

**Layers:**

- **Lifecycle Layer:** `initialize(session_id, **kwargs)` — captures identity, creates backend + formatter via subclass hooks, sets up circuit breaker (lower threshold: 3 failures → 120s cooldown), tracks background threads.
- **Token Tracking:** `update_from_response(usage)` — dual-format handling (canonical `input_tokens`/`output_tokens` + legacy `prompt_tokens`/`completion_tokens`). Cache tokens tracked separately, never counted toward prompt tokens. Recalculates `threshold_tokens` from `context_length * threshold_percent`.
- **Compression Gate:** `should_compress(prompt_tokens)` — returns True when prompt tokens >= threshold. Uses `last_prompt_tokens` unless explicit override.
- **Model Switch:** `update_model(model, context_length, ...)` — recalculates threshold when model changes.
- **Session Lifecycle:** `on_session_start()` (subclass override), `on_session_end()` (subclass override), `on_session_reset()` (zeros counters), `shutdown()` (joins threads 30s, calls `_backend.shutdown()`).
- **compress():** Raises NotImplementedError — subclasses MUST override with full pipeline.
- **Tools:** `get_tool_schemas()` (subclass override), `handle_tool_call()` (subclass override).
- **Config:** `_load_config_file()` (static helper — same pattern as BaseMemoryProvider).

**Subclass contract (hydradb-context, muninn-context):**
1. Set `name` class attr
2. Implement `_create_backend(kwargs)` → MemoryBackend (same interface as memory providers)
3. Implement `_create_formatter()` → ContextFormatter
4. Implement `is_available()` (classmethod)
5. Implement `compress(messages, current_tokens, focus_topic)` → list of messages
6. Implement entity extraction, tool schemas, tool handlers

### Backend Abstraction (MemoryBackend ABC)

Uniform interface for storage engines. Six methods:

| Method | Signature | Purpose |
|--------|-----------|---------|
| `query` | `(query_text, max_results, query_mode, query_by, graph_context, memory_type, min_confidence)` | Search memory, returns backend-native result |
| `ingest` | `(text, infer, user_name, metadata, memory_id, memory_type_label, tags, confidence)` | Store a memory entry |
| `delete` | `(memory_id)` | Delete by ID |
| `health_check` | `()` | Connectivity check, returns bool |
| `provision` | `()` | Ensure backend is ready (create tenants, vaults, etc.), returns bool |
| `shutdown` | `()` | Release resources |

**HydraDBBackend** — wraps `hydradb-sdk` sync client. Lazy thread-safe singleton via double-checked locking. Tenant auto-provisioning: creates tenant if missing (handles 409 conflict), polls until `ready_for_ingestion` (5s interval, 5min max). `upsert="true"` (string, not bool). Metadata JSON-encoded string.

**MuninnDBBackend** — wraps MuninnDB REST API via `requests.Session`. Trims content to 15KB limit. Tags normalized to hyphenated-lowercase. Automatic `hermes-context` / `hermes-memory` tagging for data segregation. Delete is a no-op (MuninnDB API limitation). Provision just health-checks; vaults created via CLI.

### Formatter Abstractions

**MemoryFormatter ABC** — single method `format(result, min_score)` → clean prose text. Backend-specific implementations know the shape of response objects.

**ContextFormatter ABC** — three methods for context operations:
- `format_compress_summary(result)` — formats entity lists for system message insertion
- `format_search_result(result, min_score)` — formats search results with graph/cognitive annotations
- `format_expand_result(result)` — formats ctx-id expansion with traversal paths

**HydraDBFormatter** — extracts `chunk_content` from SDK response `result.data.chunks`, filters by `relevancy_score >= min_score`.

**HydraDBContextFormatter** — adds graph context annotations: `[ctx-id: ...]` anchors, hop depth, relationship edges, multi-hop path traversal with hierarchical indentation.

**MuninnDBFormatter** — formats activation dicts with concept headers, confidence warnings (`[confidence: X%]` for scores < 0.6), dormant entry filtering.

**MuninnDBContextFormatter** — adds cognitive annotations: confidence weights, memory type labels, ACT-R activation chains, Bayesian confidence gating.

### Circuit Breaker

Dual-gauge with independent read/write tracking. Configurable thresholds (`failure_threshold`, `cooldown_seconds`). Thread-safe via `_lock`.

- **Memory providers:** 5 failures → 120s cooldown (default).
- **Context engines:** 3 failures → 120s cooldown (lower I/O frequency, faster trip).
- Read gauge guards: `queue_prefetch`, tool search/profile calls.
- Write gauge guards: `sync_turn`, `on_memory_write`, tool conclude/remember calls, `on_session_end`.
- A single success resets the counter and closes the breaker.

## HydraDB Memory Provider (`hydradb-memory/`)

Thin adapter (~284 lines) extending BaseMemoryProvider. Config: `HYDRA_DB_API_KEY` env + `~/.hermes/hydradb.json`. Tools: `hydradb_search`, `hydradb_profile`, `hydradb_conclude`. Backend: `HydraDBBackend`. Formatter: `HydraDBFormatter`.

## MuninnDB Memory Provider (`muninn-memory/`)

Thin adapter (~384 lines) extending BaseMemoryProvider. Config: `MUNINN_API_KEY` env + `~/.hermes/muninn.json`. Tools: `muninn_search` (with memory_type + min_confidence), `muninn_profile` (dual query: preferences + identity), `muninn_remember` (concept + content + type + tags, 12 memory type enums). Backend: `MuninnDBBackend`. Formatter: `MuninnDBFormatter`.

## HydraDB Context Engine (`plugins/context_engine/hydradb-context/`)

Thin adapter (~973 lines) extending BaseContextEngine. Full compress() pipeline:

1. **Entity Extraction** — Pure Python heuristics (no LLM). Extracts topics (capitalized phrases, quoted phrases with frequency scoring), decisions (marker-word matching), facts (copula detection), relationships (verb patterns). Configurable modes: conservative/balanced/aggressive. Per-message cap, global trigram Jaccard dedup.
2. **Fire-and-forget graph ingest** — Entities written to HydraDB on daemon thread (`_entity_thread`), tagged as `type=context` for data segregation. Generates stable ctx-id anchors from content hash.
3. **Summary block assembly** — Formatted entity list inserted as system message, replacing compressed message window.

Tools: `hydradb_context_search` (graph-aware search), `hydradb_context_expand` (ctx-id expansion with multi-hop traversal).

## MuninnDB Context Engine (`plugins/context_engine/muninn-context/`)

Thin adapter (~1007 lines) extending BaseContextEngine. Similar compress() pipeline but synchronous (local REST API, no daemon threads). Uses MuninnDB's 16 relationship types for cognitive entity classification. Tools: `muninn_context_search` (with Bayesian confidence gating), `muninn_context_expand` (activation chain expansion).

## Cross-Cutting Concerns

### Thread Safety
- Backend clients: HydraDB uses double-checked locking singleton; MuninnDB reuses `requests.Session`.
- Prefetch result: `threading.Lock` (atomic get + clear).
- Circuit breaker: all state transitions under `_lock`.
- Background threads tracked for shutdown join.

### Error Handling
- **Fail-open:** all exceptions caught, logged at DEBUG, silently discarded.
- Tool calls return JSON `{"error": str(e)}` — model sees error, agent continues.
- Daemon threads silently terminate on exception.
- Config errors: `JSONDecodeError` + `OSError` caught, log warning, return defaults.

### Logging
- Standard `logging.getLogger(__name__)` — module-level logger.
- INFO: initialization details (tenant/vault, mode, agent context).
- WARNING: config load failures, circuit breaker open.
- DEBUG: I/O failures (prefetch, sync_turn, on_memory_write, on_session_end) with `exc_info=True`.
- `printf`-style formatting: `logger.info("text %s", value)` — no f-strings.

### Configuration
- Secrets: environment variables only (`HYDRA_DB_API_KEY`, `MUNINN_API_KEY`).
- Non-secret config: JSON files in `$HERMES_HOME` (`hydradb.json`, `muninn.json`, `hydradb-context.json`, `muninn-context.json`) merge over module-level `DEFAULT_CONFIG`.
- HydraDB: `sub_tenant_id` auto-resolves to profile name for per-profile isolation.
- MuninnDB: `vault` defaults to `"default"` — use per-profile vault names for isolation.

### Agent Context Guard
- `sync_turn` and `on_session_end` skip if `_agent_context != "primary"` — prevents cron/subagent/flush contexts from polluting memory.
- Context engines track `_agent_context` but don't gate writes (context compression runs for all contexts).

### Data Segregation
- HydraDB: `type=memory` for memory provider entries, `type=context` for context engine entries.
- MuninnDB: `hermes-memory` tag for provider entries, `hermes-context` tag for context engine entries.

## Test Architecture

Tests at `tests/plugins/context_engine/` (6 modules, 115 tests) and `tests/plugins/memory/` (1 module). Use **fake backends** (no live API calls):

- `conftest.py` provides `FakeMemoryBackend` — in-memory dict store implementing `MemoryBackend` ABC. Supports query (substring match), ingest, delete, health_check, provision.
- Circuit breaker tests: mock `time.time()` for deterministic cooldown behavior.
- Config tests: temp dirs via `tmp_path`, environment variable monkeypatching.
- Entity extraction tests: known input → expected entity list assertions.
- Lifecycle tests: verify thread tracking, shutdown drain, session reset.

---

*Architecture analysis: 2026-06-20*
*Update when major patterns change*
