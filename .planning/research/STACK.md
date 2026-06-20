# Stack Research — Context Engine Plugins (v1.1)

**Domain:** Hermes Agent context engine plugins (HydraDB + MuninnDB backends)
**Researched:** 2026-06-20
**Confidence:** HIGH

## Executive Summary

**The stack does not change.** Context engine plugins reuse the exact same technology
stack as the v1.0 memory providers — no new dependencies, no new SDKs, no new external
services. The only additions are shared infrastructure inside `cos_mcp` (a
`BaseContextEngine` class) and two new plugin directories.

This is a deliberate design choice: HydraDB's graph-enriched search and MuninnDB's
cognitive activation pipeline serve both memory retrieval *and* context management
equally well. A `compress()` call becomes a semantic search/traversal against stored
context rather than a lossy summarization — same backend, different usage pattern.

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.12+ | Application runtime | System Python on target host (Linux 6.17); PEP 668 enforced — virtualenv required. Hermes Agent runs on 3.12. Sync-only — the `ContextEngine` ABC has no `async` methods. |
| hydradb-sdk | 2.0.1 (pin exact) | HydraDB Cloud API client | **Reused from v1.0 memory provider.** HydraDB Context Engine uses the same `client.query()` for graph-backed compression and `client.context.ingest()` for context persistence. Declared as `>=2,<3` in `plugin.yaml`. |
| requests | 2.31+ | MuninnDB REST API client | **Reused from v1.0 memory provider.** MuninnDB Context Engine uses `POST /api/activate` for cognitive-backed compression and `POST /api/engrams` for context persistence. |
| Hermes Agent ContextEngine ABC | (from host runtime) | Plugin contract | Imported as `from agent.context_engine import ContextEngine`. Defines the full lifecycle: `update_from_response()`, `should_compress()`, `compress()`, plus optional `get_tool_schemas()`, `handle_tool_call()`, session hooks. No version pinning — provided by the running Hermes Agent installation. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Standard Library: `json` | (stdlib) | Config serialization, tool call results, compress output | Config files (`hydradb-context.json`, `muninn-context.json`), tool result envelopes, JSON output from compress. |
| Standard Library: `logging` | (stdlib) | Structured logging | Module-level `logger = logging.getLogger(__name__)`. WARNING for circuit breaker trips, DEBUG for transient failures. |
| Standard Library: `os` | (stdlib) | Environment variable reads | `HYDRA_DB_API_KEY`, `MUNINN_API_KEY`, `HERMES_HOME`. Never hardcode `~/.hermes` — use kwargs. |
| Standard Library: `threading` | (stdlib) | Background I/O, thread safety | `threading.Thread(daemon=True)` for background context indexing. `threading.Lock` for shared state (circuit breaker, client singleton). |
| Standard Library: `time` | (stdlib) | Circuit breaker cooldown timing | `time.monotonic()` for breaker cooldown clock. |
| Standard Library: `typing` | (stdlib) | Type annotations | `Any`, `Dict`, `List`, `Optional` for method signatures. |
| Standard Library: `unittest.mock` | (stdlib) | Testing with fake backends | `MagicMock`, `Mock`, `patch` for faking backend calls. Fake backend pattern from v1.0 carries forward. |
| **cos_mcp** | **≥0.3.0 (new)** | Shared context engine infrastructure | `BaseContextEngine`, `CircuitBreaker`, `ContextBackend` (abstract), `ContextFormatter` (abstract). New shared base class mirroring `BaseMemoryProvider` pattern. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| pytest | Test runner | Same as v1.0. Run as `python3 -m pytest tests/`. |
| Python venv | Virtual environment | PEP 668 requires virtualenvs. Reuse existing `.venv`. |
| pip | Package installer | No new packages to install — `hydradb-sdk` and `requests` already present. |

## What's New vs. v1.0

### No New External Dependencies

**This is the key finding.** Context engine plugins require zero new pip packages.
The same HydraDB SDK (`hydradb-sdk>=2,<3`) and MuninnDB HTTP client (`requests>=2.31`)
used by the v1.0 memory providers are reused for context management. The backends
(`HydraDBBackend`, `MuninnDBBackend`) already expose the operations needed:

- **HydraDB:** `client.query(type="context", ...)` for graph-backed compression,
  `client.context.ingest()` for context persistence, `graph_context=True` for
  relationship-enriched retrieval — all existing SDK calls.
- **MuninnDB:** `POST /api/activate` for cognitive activation (context retrieval),
  `POST /api/engrams` for context storage — all existing REST endpoints.

### New Shared Infrastructure (cos_mcp package)

The `cos_mcp` package grows one new module and two new abstract classes:

```
cos_mcp/
├── __init__.py              # Extended exports: BaseContextEngine, ContextBackend, ContextFormatter
├── base_provider.py         # BaseMemoryProvider (v1.0, unchanged)
├── base_context_engine.py   # NEW: BaseContextEngine — shared infrastructure for context engine plugins
├── circuit_breaker.py       # CircuitBreaker (v1.0, reused as-is — dual read/write gauges)
├── backends/
│   ├── base.py              # MemoryBackend ABC (v1.0) + NEW: ContextBackend ABC
│   ├── hydradb.py           # HydraDBBackend (v1.0, reused for context ops)
│   └── muninn.py            # MuninnDBBackend (v1.0, reused for context ops)
└── formatting/
    ├── base.py              # MemoryFormatter ABC (v1.0) + NEW: ContextFormatter ABC
    ├── hydradb.py           # HydraDBFormatter (v1.0, memory formatting)
    ├── muninn.py            # MuninnDBFormatter (v1.0, memory formatting)
    ├── hydradb_context.py   # NEW: HydraDB context formatting (graph paths, compression output)
    └── muninn_context.py    # NEW: MuninnDB context formatting (activation chains, confidence weights)
```

**`BaseContextEngine`** mirrors `BaseMemoryProvider` but targets the `ContextEngine` ABC:

```python
class BaseContextEngine(ContextEngine):
    """Shared infrastructure: circuit breaker, threading, config loading."""

    # Subclass hooks:
    #   _create_backend() → ContextBackend
    #   _create_formatter() → ContextFormatter
    #   get_tool_schemas() → list of OpenAI schemas
    #   handle_tool_call() → str

    # Built-in:
    #   Circuit breaker (read gauge for compress, write gauge for store)
    #   Token tracking (last_prompt_tokens, last_completion_tokens, etc.)
    #   should_compress() default (threshold_percent-based)
    #   update_from_response() with usage tracking
    #   Session lifecycle stubs
```

### Why No New Backend Abstraction?

The existing `MemoryBackend` interface (`query`, `ingest`, `delete`, `health_check`,
`provision`, `shutdown`) is almost exactly what context engines need. The mapping:

| Context Engine Need | MemoryBackend Method | Notes |
|---------------------|---------------------|-------|
| Retrieve relevant context for compression | `backend.query(query_text, max_results, ...)` | Same semantic search, different `query_text` (conversation summary vs. turn prompt) |
| Store compressed context summary | `backend.ingest(text, infer=True/False, ...)` | Same ingestion pipeline |
| Delete stale context entries | `backend.delete(memory_id)` | Same delete operation |
| Health / availability check | `backend.health_check()` | Same connectivity check |
| Provision resources | `backend.provision()` | Same tenant/vault setup |

A separate `ContextBackend` ABC would be a thin rename of `MemoryBackend` with no
functional changes. **Recommendation: reuse `MemoryBackend` directly.** Context
engines use the same backends with different query parameters and formatting.

If future backends diverge significantly (e.g., a pure context-graph backend with
no memory operations), add `ContextBackend(ABC)` as a subclass of `MemoryBackend`
or a parallel interface. For v1.1, reuse is the right call.

## Stack Patterns by Variant

### Pattern A: HydraDB Context Engine

**Backend:** `HydraDBBackend` (existing, from `cos_mcp.backends.hydradb`)
**Formatter:** `HydraDBContextFormatter` (new, from `cos_mcp.formatting.hydradb_context`)
**Key operations:**

```
compress(messages, current_tokens, focus_topic) → List[Dict]:
    1. Build query from last N user/assistant messages + focus_topic
    2. backend.query(query_text, max_results=20, mode="thinking", graph_context=True)
    3. Formatter extracts context entries with graph paths as enrichment
    4. Reconstruct message list: system + head (protected) + context summary + tail (protected)
    5. Record token usage, update compression_count
```

**Read path:** `compress()` is synchronous — blocks the main thread. HydraDB query
latency is ~2-2.5s for `mode="thinking"`. Acceptable because compression is triggered
infrequently (at 75% threshold, typically every 10-15 turns).

**Write path:** `on_session_end()` fires background ingest of compressed context
summary (daemon thread, fire-and-forget). `update_from_response()` is synchronous
but cheap (token arithmetic, no I/O).

**Tools:** `context_search(query)` → synchronous backend.query(mode="fast"),
`context_expand(context_id)` → retrieve full context entry with graph paths.

**Circuit breaker:** Read gauge gates `compress()` and tools; write gauge gates
`on_session_end()` ingest. Independent — read failures don't block writes.

### Pattern B: MuninnDB Context Engine

**Backend:** `MuninnDBBackend` (existing, from `cos_mcp.backends.muninn`)
**Formatter:** `MuninnDBContextFormatter` (new, from `cos_mcp.formatting.muninn_context`)
**Key operations:**

```
compress(messages, current_tokens, focus_topic) → List[Dict]:
    1. Build context activation query from conversation tail
    2. backend.query(query_text, max_results=20, memory_type="context")
       → POST /api/activate with context=[query_text]
    3. MuninnDB engine applies ACT-R decay, Hebbian co-activation, Bayesian confidence
    4. Formatter extracts activations with confidence weights, dormant filtering
    5. Reconstruct message list with cognitive-weighted context
```

**Cognitive features (engine-native, no plugin code):**
- **ACT-R temporal scoring:** Frequently accessed contexts strengthen; stale ones fade
- **Hebbian co-activation:** Contexts used together auto-associate — compressing one
  surface retrieves related ones
- **Bayesian confidence:** Contradicted context entries are discounted
- **PAS (Predictive Activation):** MuninnDB predicts which contexts will be needed next

**Write path:** `on_session_end()` stores compressed context as an engram with
`memory_type="context_summary"` for future retrieval.

**Tools:** `context_search(query, memory_type, min_confidence)` → synchronous
backend.query with confidence filtering, `context_expand(context_id)` → full engram
retrieval with relationship chains.

## Installation

```bash
# No new packages needed! Both hydradb-sdk and requests are already installed
# from v1.0 memory providers.

# Verify existing deps:
pip list | grep -E "hydradb-sdk|requests"
# hydradb-sdk      2.0.1
# requests         2.31.0

# cos_mcp is the local shared package — no version bump needed on disk.
# The >=0.3.0 requirement in plugin.yaml is a semantic marker for
# "includes BaseContextEngine, ContextFormatter ABCs, and context formatting modules."
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Reuse `MemoryBackend` ABC for context ops | New `ContextBackend` ABC | Only if context engines need operations the memory backend doesn't provide (e.g., `build_dag()`, `get_graph_path()`). For v1.1, query + ingest cover all needs. Add `ContextBackend` as a future subclass if divergence occurs. |
| `BaseContextEngine` as thin layer over `CircuitBreaker` + threading | Each context engine implements circuit breaker independently | Avoid. Same rationale as v1.0 — shared breaker reduces duplication and ensures consistent failure behavior. The dual read/write gauge pattern from `CircuitBreaker` is directly applicable to context engines. |
| Synchronous `compress()` blocking the main thread | Background `compress()` on daemon thread + cache | Rejected. `compress()` must return the new message list to the agent runtime — it cannot be fire-and-forget. The agent cannot proceed to the LLM call without the compressed messages. Query latency (~2-2.5s) is acceptable for an infrequent operation (every 10-15 turns at 75% threshold). |
| `unittest.mock` (stdlib) | `pytest-mock` plugin | Same as v1.0 — `unittest.mock` covers all mocking needs. Fake backend pattern (hand-rolled `FakeHydraDBBackend` class) is more readable and avoids mock-sprawl. |
| Single-file plugin (`__init__.py` only) | Multi-file plugin (`provider.py`, `client.py`, `tools.py`) | Single-file is correct for v1.1 — same scaling rationale as v1.0. Context engine plugins are expected to be 400-600 lines. Split into sub-modules only if exceeding ~800 lines. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `asyncio` / `async def` / `await` | `ContextEngine` ABC is entirely synchronous. `compress()` must return a value to the caller — cannot be async. Background writes use `threading`, not asyncio. | `threading.Thread(daemon=True)` for fire-and-forget writes; synchronous calls for `compress()` and tools. |
| New external SDKs / libraries | The existing HydraDB SDK and MuninnDB REST API already cover all context management operations. Adding a new library for "context graphs" or "cognitive compression" would duplicate functionality already present in the backends. | Reuse `hydradb-sdk` and `requests` exactly as v1.0 does. |
| `build_string()` from `hydra_db.helpers` | Same 72-89% framing overhead as v1.0 memory formatting. Context compression output goes into the system prompt — token efficiency is even more critical here because compression fires when tokens are already tight. | Manual chunk extraction from `result.data.chunks`, filtered by relevancy, joined with structured but concise context markers. |
| Hardcoded `~/.hermes` path | Hermes supports per-profile `HERMES_HOME`. Context engines must work across profiles. | `kwargs.get("hermes_home")` from `on_session_start()` or `initialize()`. |
| `threading.Thread(daemon=True)` for `compress()` | `compress()` must return the compressed message list synchronously. Backgrounding it would require a blocking wait on the result, defeating the purpose. | Synchronous `compress()` — acceptable because it fires rarely and query latency is bounded. |
| `FastAPI` / `Flask` / web frameworks | Context engines are in-process plugins, not web services. No HTTP server is needed. | N/A — no web framework. |
| ORMs / `SQLAlchemy` / `sqlite3` | All state lives in HydraDB Cloud or MuninnDB local. Context engines are stateless clients (except transient token tracking). | N/A — no local database. |
| `pydantic` / `attrs` for SDK response validation | The SDKs already return typed models. Adding a second validation layer is redundant and couples to specific SDK versions. | Access SDK response fields directly with `getattr` defaults for defensive access. |

## Shared Infrastructure: BaseContextEngine Design

### What BaseContextEngine Provides

Mirroring `BaseMemoryProvider`, the `BaseContextEngine` encapsulates:

| Concern | Implementation | Notes |
|---------|---------------|-------|
| **Circuit breaker** | `CircuitBreaker` (reused from `cos_mcp.circuit_breaker`) | Dual read/write gauges. Read gauge gates `compress()`, `context_search`, `context_expand`. Write gauge gates `on_session_end()` ingest. |
| **Token tracking** | `last_prompt_tokens`, `last_completion_tokens`, `last_total_tokens`, `threshold_tokens`, `context_length`, `compression_count` | Required by `run_agent.py` for display/logging. Updated in `update_from_response()`. |
| **Compaction parameters** | `threshold_percent: 0.75`, `protect_first_n: 3`, `protect_last_n: 6` | Defaults from ContextEngine ABC. Overridable via `__init__`. |
| **`should_compress()` default** | `prompt_tokens / context_length >= threshold_percent` | Works for both engines. Subclasses may override for custom logic. |
| **`update_from_response()`** | Parse usage dict, update token counters, recalculate threshold | Handles both legacy keys (`prompt_tokens`) and canonical buckets (`input_tokens`, `cache_read_tokens`, etc.). |
| **`get_status()`** | Returns standard status dict | `last_prompt_tokens`, `threshold_tokens`, `context_length`, `usage_percent`, `compression_count`. |
| **`update_model()`** | Update `context_length` and recalculate `threshold_tokens` | Called on model switch or fallback activation. |
| **Thread safety** | `threading.Lock` for breaker state, client singletons | Same double-checked locking pattern from v1.0. |
| **Config loading** | `_load_config()` from env + JSON | Subclasses define `DEFAULT_CONFIG` and `get_config_schema()`. |

### What Subclasses Must Implement

| Hook | HydraDB Context Engine | MuninnDB Context Engine |
|------|----------------------|------------------------|
| `name` (property) | `"hydradb-context"` | `"muninn-context"` |
| `is_available()` (classmethod) | Check `HYDRA_DB_API_KEY` + SDK import | Check `requests` import + MuninnDB health |
| `_create_backend(kwargs)` | `HydraDBBackend(api_key, tenant_id, ...)` | `MuninnDBBackend(base_url, vault, ...)` |
| `_create_formatter()` | `HydraDBContextFormatter()` | `MuninnDBContextFormatter()` |
| `compress(messages, current_tokens, focus_topic)` | Graph-backed compression | Cognitive-backed compression |
| `get_tool_schemas()` | `[CONTEXT_SEARCH, CONTEXT_EXPAND]` | `[CONTEXT_SEARCH, CONTEXT_EXPAND]` |
| `handle_tool_call(name, args)` | Dispatch to `_tool_context_search`, `_tool_context_expand` | Same dispatch pattern |

### What's Different from BaseMemoryProvider

| Aspect | BaseMemoryProvider (v1.0) | BaseContextEngine (v1.1) |
|--------|---------------------------|--------------------------|
| ABC | `MemoryProvider` | `ContextEngine` |
| Primary operation | `queue_prefetch()` + `prefetch()` (async read) | `compress()` (sync read/write) |
| Write model | Fire-and-forget daemon threads | `compress()` is synchronous; only `on_session_end()` is fire-and-forget |
| Token tracking | None (provider doesn't track tokens) | Required — `update_from_response()`, `last_prompt_tokens`, etc. |
| System prompt injection | `system_prompt_block()` (static text) | Via `compress()` output (dynamic, context-aware) |
| Tools | `*_search`, `*_profile`, `*_conclude`/`*_remember` | `context_search`, `context_expand` |
| Session hooks | `on_session_end()` (summary ingest) | `on_session_start()`, `on_session_end()` (context persist), `on_session_reset()` |
| `should_compress()` | Not applicable | Built-in default (threshold_percent-based) |

## Plugin Manifests

### hydradb-context/plugin.yaml

```yaml
name: hydradb-context
version: 0.3.0
description: "HydraDB-backed context engine — graph-enriched compression and context management replacing the built-in lossy ContextCompressor."
pip_dependencies:
  - hydradb-sdk>=2,<3
  - cos-mcp>=0.3.0
requires_env:
  - HYDRA_DB_API_KEY
hooks:
  - on_session_end
```

### muninn-context/plugin.yaml

```yaml
name: muninn-context
version: 0.3.0
description: "MuninnDB-backed cognitive context engine — ACT-R decay, Hebbian learning, and Bayesian confidence for intelligent context management."
pip_dependencies:
  - requests>=2.31
  - cos-mcp>=0.3.0
requires_env:
  - MUNINN_API_KEY
hooks:
  - on_session_end
```

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| hydradb-sdk 2.0.1 | Python 3.12 | Same as v1.0. Verified install in `.venv`. |
| requests 2.31+ | Python 3.12 | Same as v1.0. Standard library-compatible. |
| cos-mcp ≥0.3.0 | Python 3.12 | New shared infrastructure — `BaseContextEngine`, context formatters. Not a pip package; lives in the cos-mcp repo. |
| ContextEngine ABC | Hermes Agent (any version with context engine plugin support) | The ABC is the contract. Context engines only depend on the ABC, which is stable. |
| hydradb-sdk 3.x | Provider code (NOT compatible without changes) | Same risk as v1.0 — response shape may change. `plugin.yaml` pins `>=2,<3`. |
| Python 3.13+ free-threaded | Current code (NOT safe) | Same as v1.0 — `CircuitBreaker` adds explicit locks for v1.1, resolving this preemptively. |

## Testing Stack

**Same as v1.0:** pytest with `unittest.mock` and fake backend pattern.

```
tests/context_engine/
├── test_hydradb_context.py      # Fake HydraDB backend, 5 test classes
└── test_muninn_context.py       # Fake MuninnDB backend, 5 test classes
```

**Test classes (per engine):**
1. `TestConfig` — API key loading, JSON config merge, defaults
2. `TestCompress` — message list compression, token threshold, protected head/tail
3. `TestTools` — `context_search`, `context_expand` dispatch
4. `TestCircuitBreaker` — opens after 5 failures, resets on success, gates compress
5. `TestSessionLifecycle` — `on_session_start`, `on_session_end`, `on_session_reset`

**Fake backend pattern (carried forward from v1.0):**
```python
class FakeHydraDBBackend:
    """In-memory backend that returns canned query results."""
    def query(self, query_text, **kwargs):
        return FakeQueryResult(chunks=[...])
    def ingest(self, text, **kwargs):
        self._stored.append(text)
    def health_check(self):
        return True
    def provision(self):
        return True
    def shutdown(self):
        pass
```

## Summary

**Stack delta from v1.0: zero new external dependencies.** Context engine plugins
reuse the exact same SDKs, same backends, same circuit breaker, and same threading
patterns as the memory providers. The only additions are:

1. **`cos_mcp/base_context_engine.py`** — `BaseContextEngine` class (shared infrastructure, ~200 lines)
2. **`cos_mcp/formatting/hydradb_context.py`** — `HydraDBContextFormatter` (~50 lines)
3. **`cos_mcp/formatting/muninn_context.py`** — `MuninnDBContextFormatter` (~60 lines)
4. **Two plugin directories** — `plugins/context_engine/hydradb-context/` and `plugins/context_engine/muninn-context/`

This is a deliberate design bet: graph/cognitive backends are dual-purpose. The
same semantic search that retrieves memories for `prefetch()` can retrieve context
for `compress()`. The same ingestion pipeline that stores conversation turns can
store compressed context summaries. The differentiation is in **how** the backends
are queried and **what** the formatters extract — not in the stack itself.

---

*Stack research for: Context Engine Plugins v1.1 (cos-mcp)*
*Researched: 2026-06-20*
*Confidence: HIGH — all findings cross-verified against ContextEngine ABC source, existing backends, and v1.0 patterns.*
