# Context Engine Plugin Feature Landscape

> **Milestone:** v1.1 — Context Engine Plugins
> **Project:** cos-mcp (Memory Provider Plugins for Hermes Agent)
> **Date:** 2026-06-20
> **Status:** Research complete — ready for planning
>
> Categorizes every feature for HydraDB and MuninnDB context engine plugins into
> table stakes, differentiators, and anti-features. Prioritizes ruthlessly:
> what must ship vs. what would be nice vs. what we explicitly reject.

---

## 0. ContextEngine ABC Contract (Reference)

From `~/.hermes/hermes-agent/agent/context_engine.py` (226 lines). One engine
active at a time, selected via `context.engine` in `config.yaml`.

| Method | Required | Purpose |
|---|---|---|
| `name` (property) | **Yes** | Short identifier (`"hydradb"`, `"muninn"`) |
| `update_from_response(usage)` | **Yes** | Track token counts after each LLM response |
| `should_compress(prompt_tokens) → bool` | **Yes** | Gate: should compaction fire this turn? |
| `compress(messages, current_tokens, focus_topic) → List[Dict]` | **Yes** | Main entry — compact message list, return shorter list |
| `on_session_start(session_id, **kwargs)` | No | Load persisted state for session |
| `on_session_end(session_id, messages)` | No | Flush state, close connections |
| `on_session_reset()` | No | Reset counters on `/new` or `/reset` |
| `get_tool_schemas() → List[Dict]` | No | Expose engine tools to agent |
| `handle_tool_call(name, args, **kwargs) → str` | No | Dispatch tool calls |
| `update_model(model, context_length, ...)` | No | Recalculate thresholds on model switch |
| `get_status() → Dict` | No | Status for display/logging |
| `should_compress_preflight(messages) → bool` | No | Quick pre-API-call estimate |
| `has_content_to_compress(messages) → bool` | No | Guard for `/compress` command |

---

## 1. Table Stakes — MUST Ship

These are non-negotiable. Without them, the plugins do not function as context
engines. Every item here is either a hard ABC requirement or an operational
necessity discovered during v1.0 memory provider development.

### 1.1 ABC Compliance (Both Engines)

| # | Feature | Rationale |
|---|---|---|
| TS-01 | `name` property returns `"hydradb"` / `"muninn"` | ABC abstract. Must be short, unique, snake_case-safe. |
| TS-02 | `update_from_response(usage)` tracks `last_prompt_tokens`, `last_completion_tokens`, `last_total_tokens` | ABC abstract. run_agent.py reads these directly. Must handle legacy keys (`prompt_tokens`) and canonical buckets (`input_tokens`, `cache_read_tokens`). |
| TS-03 | `should_compress(prompt_tokens) → bool` | ABC abstract. Default logic: `prompt_tokens >= threshold_tokens` where `threshold_tokens = threshold_percent × context_length`. Default 75%. Must respect optional override when `prompt_tokens` arg is provided. |
| TS-04 | `compress(messages, current_tokens, focus_topic) → List[Dict]` | ABC abstract. Must return valid OpenAI-format message list. Must preserve system prompt + `protect_first_n` messages + `protect_last_n` messages. Must increment `compression_count`. |
| TS-05 | Maintain ABC class attributes | `last_prompt_tokens`, `last_completion_tokens`, `last_total_tokens`, `threshold_tokens`, `context_length`, `compression_count` — all read directly by run_agent.py. |
| TS-06 | `threshold_percent`, `protect_first_n`, `protect_last_n` | ABC defaults: 0.75, 3, 6. Must be settable for config-driven tuning. |
| TS-07 | `on_session_reset()` zeroes counters | Default from ABC: reset token counts + `compression_count`. |
| TS-08 | `get_status()` returns standard dict | ABC default implementation is sufficient; override only if engine-specific fields are needed. |

### 1.2 Plugin Registration (Both Engines)

| # | Feature | Rationale |
|---|---|---|
| TS-09 | Module-level `register(ctx)` function | Hermes plugin contract. Calls `ctx.register_context_engine(MyEngine())`. Pattern proven by v1.0 memory providers. |
| TS-10 | `plugin.yaml` manifest | Required by Hermes plugin loader. Fields: `name`, `version`, `description`. Optional: `pip_dependencies`, `external_dependencies`, `requires_env`, `hooks`. |
| TS-11 | In-tree deployment at `plugins/context_engine/<name>/` | Match v1.0 memory provider pattern. Cross-profile discovery. Structure: `__init__.py` + `plugin.yaml` + optional `README.md`. |

### 1.3 Config & Credentials

| # | Feature | Rationale |
|---|---|---|
| TS-12 | API key from env var | `HYDRA_DB_API_KEY` (HydraDB), `MUNINN_API_KEY` (MuninnDB). Secrets never committed. Same pattern as memory providers. |
| TS-13 | Non-secret config from JSON | `hydradb.json`, `muninn.json` in `$HERMES_HOME`. Merge: defaults → JSON → env. |
| TS-14 | Config-driven activation | `context.engine: "hydradb"` or `"muninn"` in `config.yaml`. Only one active. |

### 1.4 Resilience

| # | Feature | Rationale |
|---|---|---|
| TS-15 | Circuit breaker | Independent read/write gauges. 5 consecutive failures → 120s cooldown. Reuse `cos_mcp.circuit_breaker.CircuitBreaker` directly — proven in v1.0. |
| TS-16 | Graceful degradation on backend failure | When circuit breaker is open, `compress()` still returns a valid (uncompressed but truncated) message list rather than raising. Tool calls return JSON error objects. |
| TS-17 | Logging with `logging.getLogger(__name__)` | Debug-level for failures (with `exc_info=True`), info-level for lifecycle events, warning-level for breaker trips. Match v1.0 pattern. |

### 1.5 Engine-Specific: HydraDB Context Engine

| # | Feature | Rationale |
|---|---|---|
| TS-H01 | `compress()` extracts entities via LLM call | Entity + relationship extraction from the message list. Required before graph ingest — same pattern as LCM. |
| TS-H02 | `compress()` ingests entities into HydraDB graph | Fire-and-forget daemon thread via `backend.ingest()`. Non-blocking — returns compressed summary immediately. Type tag `"hermes-context"` to separate from memory chunks. |
| TS-H03 | `compress()` returns compressed summary block | Single system-role message summarizing the compressed content. Short reference blob so agent knows what was ingested. |
| TS-H04 | `hydradb_context_search` tool | Agent-callable. Queries HydraDB graph for context on demand. Parameters: `query` (string). Enables graph_context=True for rich traversal. |
| TS-H05 | `hydradb_context_expand` tool | Agent-callable. Pulls full context from compressed nodes given a node/topic reference. Parameters: `reference` (string). |
| TS-H06 | `get_tool_schemas()` returns OpenAI schemas | Both tools with proper `type: function` schemas, typed parameters, `required` arrays. |
| TS-H07 | `handle_tool_call()` dispatches to search/expand | JSON-string results. Circuit breaker gating on reads. |
| TS-H08 | `on_session_start()` loads knowledge graph | Provisions tenant if missing, establishes connection. Param: `session_id`, **kwargs with `hermes_home`. |
| TS-H09 | `on_session_end()` flushes final state | Ingest any remaining extracted entities. Close SDK client. Supports `messages` kwarg for final ingest. |
| TS-H10 | Shared HydraDBBackend with memory provider | Uses same `cos_mcp.backends.hydradb.HydraDBBackend` class. Same tenant + sub_tenant. Module-level `_shared_backend` avoids duplicate connections. |

### 1.6 Engine-Specific: MuninnDB Context Engine

| # | Feature | Rationale |
|---|---|---|
| TS-M01 | `compress()` extracts entities via LLM call | Same extraction pattern as HydraDB. Entity + relationship extraction from message list. |
| TS-M02 | `compress()` stores engrams in MuninnDB | Synchronous `backend.ingest()` — localhost, no network latency, no daemon thread needed. Type tag `"hermes-context"`. |
| TS-M03 | `compress()` returns compressed summary block | Synthesized from high-confidence retrievals after engram storage. Single system-role message. |
| TS-M04 | `muninn_context_search` tool | Agent-callable. Queries MuninnDB via `/api/activate`. Parameters: `query` (string). Leverages ACT-R decay + Bayesian confidence for relevance. |
| TS-M05 | `muninn_context_expand` tool | Agent-callable. Expands compressed context nodes. Parameters: `reference` (string). |
| TS-M06 | `get_tool_schemas()` returns OpenAI schemas | Both tools with proper schemas. |
| TS-M07 | `handle_tool_call()` dispatches to search/expand | JSON-string results. Circuit breaker gating on reads. |
| TS-M08 | `on_session_start()` connects to MuninnDB | Health check via `backend.health_check()`. Verifies vault accessibility. Param: `session_id`, **kwargs. |
| TS-M09 | `on_session_end()` flushes and closes | Final engram flush, close HTTP session. |
| TS-M10 | Shared MuninnDBBackend with memory provider | Uses same `cos_mcp.backends.muninn.MuninnDBBackend` class. Same vault + API key. Separate backend instance (separate HTTP session). |

### 1.7 Shared cos_mcp Infrastructure

| # | Feature | Rationale |
|---|---|---|
| TS-S01 | `BaseContextEngine` class | Reduces boilerplate. Encapsulates circuit breaker, config loading, `_load_config()`, default `update_model()`, `get_status()`, `on_session_reset()`. Subclasses override `_compress_impl()` + tool schemas. Same pattern as `BaseMemoryProvider`. |
| TS-S02 | Config loading (`_load_config()`) | Merge defaults → env vars → JSON file. Shared logic between both engines. |
| TS-S03 | Circuit breaker reuse | `cos_mcp.circuit_breaker.CircuitBreaker` used directly. No new abstraction needed. |
| TS-S04 | Tool schema helpers | Utility to construct OpenAI function-calling schemas with consistent patterns. Optional — engines can inline if preferred. |

### 1.8 Testing

| # | Feature | Rationale |
|---|---|---|
| TS-T01 | Fake HydraDB client for context engine tests | Extends v1.0 pattern from `tests/plugins/memory/conftest.py`. Fake query results, fake ingest tracking, configurable failure injection. |
| TS-T02 | Fake MuninnDB client for context engine tests | Fakes the REST API layer. Tracks engram storage, returns configurable activation results. |
| TS-T03 | 100% requirement coverage | Every TS-* requirement has at least one test. Match v1.0 bar: 65 tests, zero failures. |
| TS-T04 | Config loading tests | Test defaults, env var override, JSON override, merge order, missing files, invalid JSON. |
| TS-T05 | Circuit breaker tests | Consecutive failure counting, trip at 5, 120s cooldown, success reset, independent read/write gauges. |
| TS-T06 | `compress()` tests | Verify message list is shorter after compression, system prompt preserved, head/tail protection respected, compression_count incremented. |
| TS-T07 | Tool handler tests | Valid tool calls return JSON results, invalid tools return error JSON, circuit breaker open returns error. |
| TS-T08 | Session lifecycle tests | `on_session_start`, `on_session_end`, `on_session_reset` behavior verified. |

---

## 2. Differentiators — What Makes Us Special

These features distinguish our context engines from the built-in
`ContextCompressor` and from each other. They are the reason to choose a cos-mcp
engine over the default.

### 2.1 HydraDB Differentiators

| # | Feature | Why It Matters |
|---|---|---|
| D-H01 | **Lossless compression (LCM pattern)** | Built-in `ContextCompressor` uses lossy LLM summarization — old messages are summarized and discarded. HydraDB ingests them as graph nodes; nothing is lost. The agent queries the graph on demand. |
| D-H02 | **Cross-session knowledge graph persistence** | The knowledge graph survives across sessions. `on_session_start()` reloads it. Conversations compound value over time — the graph gets richer, not reset. |
| D-H03 | **Graph-enriched retrieval** | `graph_context=True` enables multi-hop traversal. Agent queries return not just direct matches but connected entities and relationships — richer context. |
| D-H04 | **Shared tenant with memory provider** | Context graph and memory chunks live in the same HydraDB tenant (different type tags). Cross-domain associations emerge naturally. No separate infrastructure. |
| D-H05 | **Cloud-backed (zero local setup)** | No local process to run. `HYDRA_DB_API_KEY` is the only requirement. Free tier sufficient for personal use. |

### 2.2 MuninnDB Differentiators

| # | Feature | Why It Matters |
|---|---|---|
| D-M01 | **Cognitive-native compression** | ACT-R temporal decay naturally ages old context — no manual pruning, no TTL management, no threshold tuning. Stale context fades without explicit deletion. |
| D-M02 | **Hebbian self-organizing graph** | Frequently co-referenced facts strengthen their associative edges automatically. The context graph adapts to the user's recurring topics without intervention. |
| D-M03 | **Bayesian confidence gating** | Retrievals are quality-scored. Contradicted or low-confidence memories are automatically suppressed. Compressed summaries reflect the most reliable context — not just the most recent. |
| D-M04 | **Local, synchronous compress()** | MuninnDB runs on `localhost:8475`. No network latency means `compress()` can be fully synchronous — engram storage completes before the compressed message list is returned. No daemon threading, no fire-and-forget. Deterministic, testable. |
| D-M05 | **Predictive Activation (PAS)** | MuninnDB pre-activates context likely needed in the next turn. Retrieval is warm when `compress()` or tool calls query it. Lower latency, higher relevance. |
| D-M06 | **16 typed relationship types** | Engine-native `causes`, `contradicts`, `supports`, `depends_on`, `precedes`, etc. capture richer inter-concept structure than flat text summaries. |
| D-M07 | **Co-located memory + context** | Same vault as memory provider. Hebbian learning builds cross-domain associations between memories and compressed context automatically. |
| D-M08 | **No cloud dependency** | Fully local. Works offline. No API key billing. No network calls. Privacy-preserving. |

### 2.3 Shared Differentiators

| # | Feature | Why It Matters |
|---|---|---|
| D-S01 | **Tool-augmented retrieval** | The built-in compressor dumps one summary block and hopes the agent can work with it. Our engines expose tools so the agent can pull context on demand — `context_search` to query, `context_expand` to drill in. Agent stays in control. |
| D-S02 | **Two backends, one contract** | Swap HydraDB ↔ MuninnDB by changing one config value (`context.engine`). Same ABC, same tools, same lifecycle. No code changes needed. |
| D-S03 | **Thin plugin, thick shared infra** | `BaseContextEngine` handles circuit breaker, config loading, formatting, default hooks. Each engine plugin is ~300-400 lines of backend-specific code. Same architecture as v1.0 memory providers (HydraDB: 284 lines, MuninnDB: 384 lines). |
| D-S04 | **Independent circuit breakers per engine** | Context engine failures don't trip the memory provider's breaker, and vice versa. Each engine gets its own `CircuitBreaker` instance. |
| D-S05 | **100% fake-backend test coverage** | Tests run with zero live API calls. Fast, deterministic, CI-friendly. Matching v1.0 quality bar (65 tests for HydraDB memory provider). |
| D-S06 | **config.yaml-driven, not hardcoded** | Engine selection, threshold_percent, protect_first_n/protect_last_n all configurable. No code changes to tune behavior. |
| D-S07 | **Separate concerns from memory** | Memory Provider = retrieval (what does the agent remember?). Context Engine = compression (how do we fit everything in the context window?). Both coexist. No coupling. Clear responsibility boundary. |

---

## 3. Anti-Features — What We Explicitly Reject

These are features someone might reasonably expect, but we deliberately exclude
them from v1.1. Each has a concrete reason — not "we'll get to it later," but
"here's why it's a bad idea or belongs in a fundamentally different scope."

### 3.1 Architectural Anti-Features

| # | Anti-Feature | Why Rejected |
|---|---|---|
| AF-01 | **Async/await in any public API** | Hermes provider contract is sync-only. `compress()` is called synchronously by `run_agent.py`. No `asyncio`, no `AsyncHydraDB`. |
| AF-02 | **Cross-engine coordination** | Only one context engine is active at a time (Hermes enforces this). No need for engines to talk to each other or coordinate compression. |
| AF-03 | **Built-in migration between engines** | Config swap is a one-line change. No data migration needed — engines use the same backends with different type tags. |
| AF-04 | **Context engine as memory provider** | Context engines compress; memory providers retrieve. These are separate ABCs with separate lifecycles. Combining them would violate the single-responsibility principle and create a monolithic plugin. |
| AF-05 | **Same-turn write visibility** | When `compress()` ingests into the graph, those nodes are not immediately visible to tool calls in the same turn. This avoids consistency headaches and matches the existing fire-and-forget pattern. Deferred to v2+. |
| AF-06 | **Multi-model compression** | `compress()` may use an LLM call for entity extraction — but only one model. No per-engine model selection, no fallback chains. The agent's active model handles extraction. |

### 3.2 HydraDB-Specific Anti-Features

| # | Anti-Feature | Why Rejected |
|---|---|---|
| AF-H01 | **Self-hosted HydraDB support** | HydraDB is cloud-only in v1.1. Self-hosted deployments would require an entirely different connection model, auth flow, and provisioning path. Cloud free tier is sufficient for all current use cases. |
| AF-H02 | **Batch query or memory deduplication** | The graph handles deduplication naturally (upsert by content-hash). Complex batch query optimization belongs in v2 when we have real-world performance data. |
| AF-H03 | **Cross-tenant context sharing** | Each profile gets its own sub_tenant for isolation. Shared context across profiles would require a promotion path, consensus on what's "shared-worthy," and conflict resolution — a v2+ concern. |
| AF-H04 | **Blocking compress() (synchronous ingest)** | HydraDB is cloud-backed with ~500ms ingest latency. `compress()` must not block the agent loop. Ingest is fire-and-forget on a daemon thread. The compressed summary is returned immediately. This is an intentional architectural choice, not a missing feature. |

### 3.3 MuninnDB-Specific Anti-Features

| # | Anti-Feature | Why Rejected |
|---|---|---|
| AF-M01 | **Manual pruning / TTL controls** | ACT-R temporal decay handles aging automatically. Adding manual pruning controls would fight the cognitive model. If you want explicit TTL, use HydraDB. |
| AF-M02 | **Explicit engram deletion** | MuninnDB's current API lacks delete-by-ID. The `delete()` method on `MuninnDBBackend` silently no-ops. Context engine relies on ACT-R decay to naturally age out stale context. Deletion would require a MuninnDB API change. |
| AF-M03 | **Multi-vault context** | One vault per profile — same as memory provider. Cross-vault context would require MuninnDB to support cross-vault querying, which it doesn't. |
| AF-M04 | **Cloud fallback for MuninnDB** | MuninnDB is local-only by design. If you need cloud, use HydraDB. The two engines are complementary, not redundant. |

### 3.4 Testing Anti-Features

| # | Anti-Feature | Why Rejected |
|---|---|---|
| AF-T01 | **Live API integration tests in CI** | v1.0 memory provider tests are 100% fake-backend. Live API verification is a manual step. This keeps CI fast and deterministic. Live tests require API keys and running MuninnDB instances. |
| AF-T02 | **Performance benchmarks** | Premature optimization. Build correct compression first, measure later. v2 can add benchmark tests once we have real-world usage data. |
| AF-T03 | **End-to-end Hermes Agent integration tests** | Tests run against the plugin directly with fake Hermes runtime stubs. Full end-to-end tests require the Hermes Agent runtime and are outside cos-mcp scope. |

### 3.5 UX Anti-Features

| # | Anti-Feature | Why Rejected |
|---|---|---|
| AF-U01 | **Interactive compression preview** | `compress()` is automatic (triggered by `should_compress()`). Manual `/compress` is a Hermes built-in feature that calls our engine — we don't add a separate preview step. |
| AF-U02 | **Compression visualization / dashboard** | Out of scope for a context engine plugin. Belongs in a separate observability tool. |
| AF-U03 | **User-facing compression configuration UI** | Config is file-based (`config.yaml`, `hydradb.json`, `muninn.json`). No TUI/GUI planned. Hermes `hermes config` handles basic setup. |

---

## 4. Feature Matrix — Both Engines Side by Side

| Capability | HydraDB Context Engine | MuninnDB Context Engine |
|---|---|---|
| **Compression model** | Graph-backed (lossless LCM) | Cognitive engram (ACT-R + Hebbian) |
| **compress() latency** | ~500ms (fire-and-forget ingest) | ~5-20ms (local sync) |
| **Cross-session persistence** | Yes — knowledge graph survives | Yes — engrams persist in vault |
| **Aging/pruning** | Manual (graph stays unless deleted) | Automatic (ACT-R decay) |
| **Retrieval quality** | Semantic + BM25 + graph traversal | Bayesian confidence + PAS + ACT-R activation |
| **Relationship types** | Flexible (entity extraction defines) | 16 engine-native types |
| **Infrastructure** | Cloud (HydraDB API) | Local (localhost:8475) |
| **Offline capable** | No | Yes |
| **Sync/async compress** | Async (daemon thread ingest) | Sync (local, no latency) |
| **Tools** | `hydradb_context_search`, `hydradb_context_expand` | `muninn_context_search`, `muninn_context_expand` |
| **Shared backend with memory** | Yes (HydraDBBackend) | Yes (MuninnDBBackend) |
| **Type tag** | `"hermes-context"` | `"hermes-context"` |
| **Circuit breaker** | Independent read/write | Independent read/write |

---

## 5. Priority Ranking — Build Order

Within v1.1, features should be built in this order:

### Phase 1: Shared Infrastructure (must ship first)
1. `BaseContextEngine` in `cos_mcp/` — circuit breaker, config loading, default hooks
2. Tool schema helpers
3. Fake backend test infrastructure

### Phase 2: HydraDB Context Engine
1. `compress()` with entity extraction + graph ingest
2. `should_compress()` gate
3. `update_from_response()` token tracking
4. Tools: `hydradb_context_search`, `hydradb_context_expand`
5. Session lifecycle hooks
6. Plugin registration + manifest
7. Full test suite

### Phase 3: MuninnDB Context Engine
1. `compress()` with entity extraction + engram storage + ACT-R
2. `should_compress()` gate
3. `update_from_response()` token tracking
4. Tools: `muninn_context_search`, `muninn_context_expand`
5. Session lifecycle hooks
6. Plugin registration + manifest
7. Full test suite

### Phase 4: Integration & Polish
1. In-tree deployment of both plugins
2. `hermes doctor` validation
3. Cross-profile activation
4. Documentation

**Rationale:** HydraDB first because its backend and patterns are already battle-tested
from v1.0 memory provider. MuninnDB second — it benefits from the shared
infrastructure built in Phase 1 and any lessons learned from HydraDB.

---

## 6. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM entity extraction quality varies by model | Medium | High — bad extraction → useless graph | Test with multiple models; extraction prompt is tunable; fallback to raw text ingest |
| HydraDB graph ingest latency spikes under load | Low | Medium — daemon thread handles it; compression isn't blocked | Circuit breaker protects; fire-and-forget means compress() never waits |
| MuninnDB local process not running | Medium | High — context engine unavailable | `on_session_start()` health check fails fast; clear error message; suggest `muninn start` |
| Tool schema collisions with memory provider tools | Low | Medium — confusing for agent | Prefix all tools (`hydradb_context_*`, `muninn_context_*`); memory tools use different prefixes (`hydradb_search`, `muninn_search`) |
| `protect_first_n` / `protect_last_n` interaction with compression | Low | Medium — may over-trim or under-compress | ABC defaults (3/6) are well-tested; override in config if needed |
| Shared backend thread safety with memory provider | Low | High — data corruption | Each plugin gets independent backend instance; `CircuitBreaker` is thread-safe; HydraDB SDK client is a module-level singleton guarded by `threading.Lock` |

---

## 7. Success Criteria

1. **Both engines pass 100% of ABC contract tests** — every required method, every class attribute, correct return types
2. **Both engines deploy in-tree** at `~/.hermes/hermes-agent/plugins/context_engine/`
3. **`hermes doctor` reports no issues** with either engine active
4. **Config swap works** — changing `context.engine` from `"hydradb"` to `"muninn"` (and back) with no errors
5. **Fake-backend tests pass with zero live API calls**
6. **Compression actually reduces context size** — `compress()` output has fewer messages than input for realistic inputs
7. **Agent can retrieve compressed context** via tool calls after compression

---

*Last updated: 2026-06-20 — v1.1 research phase*
