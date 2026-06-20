# Project Research Summary

**Project:** Memory Provider Plugins for Hermes Agent — v1.1 Context Engine Plugins
**Domain:** Hermes Agent context engine plugins (HydraDB + MuninnDB backends)
**Researched:** 2026-06-20
**Confidence:** HIGH

## Executive Summary

The v1.1 milestone adds two context engine plugins — HydraDB and MuninnDB — that implement the Hermes Agent `ContextEngine` ABC, replacing the built-in lossy `ContextCompressor` with graph-enriched and cognitive-backed compression. The research is definitive: **this is a thin extension of the v1.0 stack, not a new build.** No new external dependencies are required. The same HydraDB SDK (`hydradb-sdk>=2,<3`) and MuninnDB HTTP client (`requests>=2.31`) used by the memory providers are reused for context management — the differentiation is in *how* the backends are queried and *what* the formatters extract, not in the stack itself.

The architecture mirrors the v1.0 `BaseMemoryProvider` pattern: a new `BaseContextEngine` class (~200 lines) provides shared infrastructure (circuit breaker, token tracking, config loading, threading), while each plugin is a ~400-line `__init__.py` implementing backend-specific `compress()`, `entity_extract()`, and tool dispatch. HydraDB's `compress()` ingests message entities into a graph DAG for lossless cross-session retrieval; MuninnDB's `compress()` leverages ACT-R temporal decay and Hebbian co-activation for cognitive-native compression that automatically ages stale context. Both engines expose `context_search` and `context_expand` tools so the agent can retrieve compressed context on demand — a key differentiator over the built-in compressor's single summary block.

The critical risks are well-understood from v1.0 experience: **fire-and-forget daemon threads must be tracked and joined in `shutdown()`** (v1.0 Pitfall 6, now repeated), **fake backend tests must simulate real API contract violations and indexing delays** (v1.0 Pitfall 12, amplified), and **the 6 ContextEngine ABC class attributes must be maintained precisely** or `run_agent.py`'s preflight checks and CLI display break silently. These 18 documented pitfalls, 9 of which are v1.0 repeats, form a comprehensive design checklist. The path forward is clear: build `BaseContextEngine` first (Phase 1), implement HydraDB context engine (Phase 2, leaning on battle-tested patterns), then MuninnDB (Phase 3, benefiting from shared infra), and finish with integration testing and in-tree deployment (Phase 4).

**Bottom line:** Context engines are dual-purpose memory backends. The same semantic search that retrieves memories for `prefetch()` can retrieve context for `compress()`. This is a deliberate design bet that keeps the stack small, the codebase coherent, and the implementation risk bounded. Ship HydraDB first (cloud, familiar), MuninnDB second (local, cognitive), and hold the line on zero new dependencies, 100% fake-backend test coverage, and `hermes doctor` cleanliness.

## Key Findings

### Recommended Stack (from STACK.md)

**Delta from v1.0: zero new pip packages.** Context engine plugins reuse the exact same SDKs, same backends, same circuit breaker, and same threading patterns as the memory providers. The only additions are shared infrastructure inside `cos_mcp`:

- **`cos_mcp/base_context_engine.py`** — `BaseContextEngine` (~200 lines), mirrors `BaseMemoryProvider` but targets the `ContextEngine` ABC. Provides circuit breaker, token tracking, `should_compress()` default, config loading, and session lifecycle stubs.
- **`cos_mcp/formatting/hydradb_context.py`** — `HydraDBContextFormatter` (~50 lines)
- **`cos_mcp/formatting/muninn_context.py`** — `MuninnDBContextFormatter` (~60 lines)

**Core technologies:**
- **Python 3.12+**: Sync-only (the `ContextEngine` ABC has no `async` methods). Virtualenv required (PEP 668).
- **`hydradb-sdk>=2,<3`** (pinned 2.0.1): Reused from v1.0 memory provider. Powers graph-backed compression via `client.query(type="context", graph_context=True)` and entity persistence via `client.context.ingest()`.
- **`requests>=2.31`**: Reused from v1.0 memory provider. Powers MuninnDB cognitive compression via `POST /api/activate` and engram storage via `POST /api/engrams`.
- **`cos_mcp>=0.3.0`** (new shared infra marker): `BaseContextEngine`, `ContextFormatter` ABCs, context formatting modules. Not a pip package — lives in the cos-mcp repo.
- **`CircuitBreaker`** (reused as-is): Dual read/write gauges from v1.0. Independent breaker instances per engine. Consider lowering `failure_threshold` from 5 to 3 for context engines (fewer calls than memory providers).
- **`threading`** (stdlib): Daemon threads for fire-and-forget entity storage. `threading.Lock` for breaker state and client singletons.

**Key architectural decision:** Reuse `MemoryBackend` ABC directly — `query()` + `ingest()` + `delete()` + `health_check()` cover all context engine needs. A separate `ContextBackend` ABC would be a thin rename with no functional changes. Add it later only if backends diverge significantly.

### Expected Features (from FEATURES.md)

**Table stakes (MUST ship, 48 items across 8 categories):**

- **ABC compliance** (TS-01 to TS-08): Full `ContextEngine` ABC implementation — `name`, `update_from_response()`, `should_compress()`, `compress()` with valid OpenAI-format message list output, 6 maintained class attributes, protected head/tail message preservation.
- **Plugin registration** (TS-09 to TS-11): Module-level `register(ctx)` function, `plugin.yaml` manifest, in-tree deployment at `plugins/context_engine/<name>/`.
- **Config & resilience** (TS-12 to TS-17): API keys from env vars, non-secret config from JSON files (`hydradb-context.json`, `muninn-context.json`), dual-gauge circuit breaker with graceful degradation.
- **HydraDB engine** (TS-H01 to TS-H10): Entity extraction → graph ingest via fire-and-forget daemon thread, summary block with ctx-id reference, `context_search` + `context_expand` tools, session lifecycle hooks, shared `HydraDBBackend` with memory provider.
- **MuninnDB engine** (TS-M01 to TS-M10): Same pattern adapted for cognitive backend — engram storage with ACT-R decay, local synchronous `compress()`, confidence-gated retrieval, shared `MuninnDBBackend`.
- **Shared infrastructure** (TS-S01 to TS-S04): `BaseContextEngine` reduces boilerplate, shared config loading, circuit breaker reuse, tool schema helpers.
- **Testing** (TS-T01 to TS-T08): Fake HydraDB and MuninnDB clients for context tests, 100% requirement coverage, config/breaker/compress/tool/lifecycle test classes — matching v1.0 bar (65 tests, zero failures).

**Differentiators (what makes us special, 15 items):**

- **HydraDB**: Lossless compression via LCM pattern (no discarded messages), cross-session knowledge graph persistence, graph-enriched multi-hop retrieval, cloud-backed with zero local setup.
- **MuninnDB**: Cognitive-native compression with ACT-R temporal decay (no manual pruning), Hebbian self-organizing graph, Bayesian confidence gating, local synchronous `compress()` with no network latency, 16 typed relationship types, offline-capable.
- **Shared**: Tool-augmented retrieval (agent pulls context on demand vs. static summary), two backends with one contract (swap via single config value), thin plugins with thick shared infra, independent circuit breakers per engine, `config.yaml`-driven tuning.

**Anti-features (explicitly rejected, 14 items):**
No async/await, no cross-engine coordination, no built-in migration between engines, no context engine as memory provider, no same-turn write visibility, no LLM calls inside `compress()`, no self-hosted HydraDB, no manual pruning in MuninnDB, no live API integration tests in CI, no performance benchmarks in v1.1.

**Priority ranking:** Phase 1 (Shared Infrastructure) → Phase 2 (HydraDB Context Engine) → Phase 3 (MuninnDB Context Engine) → Phase 4 (Integration & Polish). HydraDB first because its backend and patterns are already battle-tested from v1.0.

### Architecture Approach (from ARCHITECTURE.md)

The context engine architecture is a 7-layer stack mirroring the v1.0 memory provider design: Config → Lifecycle → Backend → Compression Path → Tools → Circuit Breaker → Formatting. `BaseContextEngine` provides the bottom 3 layers; each plugin implements the top 4.

**The `compress()` pipeline is the core innovation — a 6-step synchronous flow:**

1. **Pre-compression guards**: Circuit breaker check, agent context gating (primary only), increment compression count.
2. **Determine compression window**: Protect first N messages (default 3) and last N messages (default 6). Everything in between is the compression target.
3. **Entity extraction**: Pure Python heuristics (no LLM) extract topics, decisions, facts, and relationships from the compression window. HydraDB extracts graph-aware entities with relationship edges; MuninnDB classifies by memory type with confidence scoring. Per-message cap of 3 entities, global dedup, configurable aggressiveness.
4. **Store entities** (fire-and-forget daemon thread): Backend.ingest() with `type="context"`, metadata including ctx-id, source, compression count. Does NOT block `compress()` return.
5. **Build summary block**: Single system-role message with condensed topics, decisions, and facts. Capped at ~800 tokens. Includes `[ctx-id: session_prefix_count_hash]` for future `context_expand` retrieval.
6. **Assemble and return**: `[system_prompt, protected_head, summary_block, protected_tail]` — brand new list, never mutating the input.

**Data segregation strategy**: Context engines and memory providers share the same backend tenant/vault but use different `type` fields (`"context"` vs `"memory"`) and tag namespaces (`"hermes-context"` vs `"hermes-memory"`). Queries always filter by type — no cross-contamination.

**Plugin pattern**: Single-file `__init__.py` per plugin (~400 lines expected), matching v1.0. Directory structure: `plugins/context_engine/hydradb-context/` and `plugins/context_engine/muninn-context/`, each with `__init__.py` + `plugin.yaml`. Registration via `register(ctx)` calling `ctx.register_context_engine(EngineInstance())`.

**Key anti-patterns to avoid**: No LLM inside `compress()` (adds latency, creates recursion risk), no blocking entity storage (daemon threads required), no mixing context/memory type fields, no using the same config file as memory providers (`hydradb-context.json` ≠ `hydradb.json`), no skipping agent context gating for non-primary agents.

### Critical Pitfalls (from PITFALLS.md)

18 pitfalls documented, 9 are v1.0 repeats (context engines must avoid them from the start), 9 are new (unique to the `compress()` pathway and `ContextEngine` ABC). The top 5 by impact:

1. **`compress()` returns non-shorter message list** [NEW, HIGH]: If the compression window is empty or the summary block is verbose, compression is a no-op but `compression_count` increments. Next turn triggers compression again — infinite loop. **Fix:** Hard guard at top of `compress()`: `if len(returned) >= len(messages): return messages`. Cap summary block at ~800 tokens.

2. **ContextEngine ABC class attributes not maintained** [NEW, HIGH]: `run_agent.py` reads `last_prompt_tokens`, `threshold_tokens`, `compression_count` directly. If the engine doesn't update all 6 attributes, preflight checks fail, CLI shows `0/0 tokens`, and `should_compress()` returns True every turn. **Fix:** `BaseContextEngine.update_from_response()` MUST write to ALL 6 attributes. Test with both legacy and canonical usage dict formats.

3. **Fire-and-forget daemon threads not tracked/joined** [v1.0 REPEAT, HIGH]: Exact same bug as v1.0 Pitfall 6 — `threading.Thread(target=..., daemon=True).start()` without storing a reference. On `shutdown()`, entity storage threads are abandoned, context entities are lost, ctx-ids become dangling references. **Fix:** Store all thread references as instance attributes. `shutdown()` joins them with 30s timeout. Add shutdown test.

4. **Fake backend fidelity in tests** [v1.0 REPEAT, HIGH]: Tests with idealized fake clients pass 100% but real API integration fails on parameter type strictness, indexing delay, response shape edge cases, and network errors. **Fix:** Fake clients must validate parameter types, support error injection, simulate indexing delay (configurable), and exercise the same attribute access patterns as real SDK responses.

5. **Entity extraction quality — over-extraction vs under-extraction** [NEW, MEDIUM]: Without LLM-based extraction, pure Python heuristics can produce 200+ entities from 40 messages (bloating the backend, increasing query latency) or 2 entities (missing important context). **Fix:** Per-message entity cap (max 3), global dedup via trigram Jaccard > 0.7, minimum entity weight (10+ chars, no stopword-only), configurable extraction aggressiveness (`"conservative"` / `"balanced"` / `"aggressive"`), entity type distribution tracking with auto-rebalancing.

Other notable pitfalls: `compress()` mutating input message list (construct new list, never mutate), independent circuit breakers with wrong thresholds for context engine I/O frequency (lower failure_threshold from 5→3, make configurable), token tracking double-counting cache/read tokens (prefer `input_tokens` over `prompt_tokens`, never count `cache_read_tokens` toward context window), config name drift between directory name, `engine.name`, and `config.yaml` value (use single module constant), ctx-id collisions across sessions (include session-scoped component), tool name collisions between two installed context engines (gate to active engine only or prefix with engine name).

## Implications for Roadmap

Based on research synthesis, the recommended phase structure with rationale:

### Phase 1: Shared Infrastructure + Foundation
**Rationale:** Must ship first — everything depends on `BaseContextEngine`. Extends `cos_mcp` with shared infrastructure that both engines inherit. This is the highest-leverage phase because it prevents 9 of the 18 pitfalls from ever being written (circuit breaker, token tracking, config loading, agent context gating, thread tracking, provisioning idempotency).
**Delivers:** `BaseContextEngine` class (~200 lines), `ContextFormatter` ABC, backends extended with `type` parameter and idempotent `provision()`, config layer with separate config files per engine, `update_from_response()` with dual-format token tracking, `should_compress()` default with minimum message guard.
**Addresses:** TS-S01 to TS-S04 (shared infra), TS-12 (config), TS-15 (breaker), TS-17 (logging), TS-02-03,05-06 (ABC compliance).
**Avoids:** Pitfalls 2 (ABC attributes), 5 (thread safety), 7 (thread tracking), 9 (config drift), 10 (token tracking), 15 (agent context gating), 18 (provisioning double-runs).

### Phase 2: HydraDB Context Engine
**Rationale:** HydraDB first — its backend and patterns (cloud API, daemon threads, graph ingestion) are battle-tested from v1.0 memory provider. Build the complete engine with `compress()`, tools, session lifecycle, and tests. Any design issues discovered here inform Phase 3 MuninnDB implementation.
**Delivers:** `HydraDBContextEngine` in `plugins/context_engine/hydradb-context/__init__.py` (~400 lines), `HydraDBContextFormatter`, `plugin.yaml`, 5 test classes (~30 tests). Full `compress()` pipeline: entity extraction → graph ingest → summary block → message list assembly. Tools: `context_search`, `context_expand` with graph_context=True traversal.
**Uses:** `hydradb-sdk>=2,<3`, `HydraDBBackend` (existing), `HydraDBContextFormatter` (new), `CircuitBreaker` (reused).
**Implements:** Architecture components [3] Backend Layer, [4] Compression Path, [5] Tool Layer, [7] Formatting Layer.
**Avoids:** Pitfalls 1 (non-shorter list), 3 (input mutation), 4 (entity extraction quality), 8 (registration conflicts), 16 (ctx-id resolution), 17 (tool collisions).

### Phase 3: MuninnDB Context Engine
**Rationale:** MuninnDB second — benefits from shared infrastructure built in Phase 1 and any lessons learned from Phase 2 HydraDB implementation. The cognitive features (ACT-R, Hebbian, Bayesian) are engine-native — the plugin code is thinner than HydraDB's because the backend does the heavy lifting. Local synchronous `compress()` is simpler than HydraDB's cloud fire-and-forget.
**Delivers:** `MuninnDBContextEngine` in `plugins/context_engine/muninn-context/__init__.py` (~400 lines), `MuninnDBContextFormatter`, `plugin.yaml`, 5 test classes (~30 tests). Cognitive `compress()`: entity classification by memory type → engram storage → ACT-R decay scoring → summary block. Tools: `context_search` with confidence filtering, `context_expand` with relationship chains.
**Uses:** `requests>=2.31`, `MuninnDBBackend` (existing), `MuninnDBContextFormatter` (new).
**Implements:** Same architecture layers as Phase 2, adapted for local sync cognitive backend.

### Phase 4: Integration & Polish
**Rationale:** Both engines built and unit-tested. Now verify they work in the Hermes Agent runtime — in-tree deployment, `hermes doctor` validation, config swap between engines, cross-profile activation. Also: live API verification (manual, not CI), documentation, and the "Looks Done But Isn't" checklist (14 items from pitfalls research).
**Delivers:** In-tree deployment of both plugins, `hermes doctor` passing, `test_base_context_engine.py`, fake backend fidelity improvements (parameter validation, error injection, indexing delay simulation), live API integration test file (opt-in, skipped in CI), docs.
**Addresses:** TS-07-08 (lifecycle), TS-T01 to TS-T08 (testing), all remaining pitfalls not yet verified.
**Avoids:** Pitfalls 6 (breaker thresholds), 12 (tool JSON format), 13 (fake backend fidelity), 14 (deployment paths).

### Phase Ordering Rationale

This order is driven by three principles discovered in research:

1. **Dependency inversion**: `BaseContextEngine` must exist before any engine code is written — it encapsulates the ABC contract compliance that `run_agent.py` depends on. Building engines first and extracting shared infra later duplicates work and risks inconsistent ABC attribute maintenance.

2. **Complexity graduation**: HydraDB's cloud async model (daemon threads, fire-and-forget, 1-3s latency) is more complex than MuninnDB's local sync model (<50ms, deterministic). Building the harder one first surfaces threading and error-handling edge cases that inform the simpler one. The v1.0 memory providers followed this order successfully.

3. **Pitfall prevention by phase placement**: 9 of 18 pitfalls are v1.0 repeats — placing them in Phase 1 (foundation) prevents them from being written at all. 7 new pitfalls are compression-path-specific — placing them in Phase 2 (first engine) catches them before they're duplicated in Phase 3.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2**: Entity extraction heuristics — the quality of pure-Python extraction vs. conversation domain density. May need tuning during live testing with realistic multi-turn conversations. Consider adding an `entity_extraction_mode` config parameter for user-adjustable aggressiveness.
- **Phase 4**: Hermes Agent runtime integration — the plugin loader's single-select behavior for context engines, tool registration when both engines are installed, and `hermes doctor` validation paths are documented but untested against the actual runtime.

Phases with standard patterns (skip research-phase):
- **Phase 1**: `BaseContextEngine` mirrors `BaseMemoryProvider` exactly — the pattern is proven, the code is a template. No new research needed.
- **Phase 3**: MuninnDB engine is structurally identical to HydraDB engine — the plugin architecture, tool dispatch, and test structure are copy-paste with backend-specific implementations.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All findings cross-verified against `ContextEngine` ABC source (226 lines), existing backends, and v1.0 patterns. Zero new dependencies confirmed. `hydradb-sdk 2.0.1` and `requests 2.31` already installed in `.venv`. |
| Features | HIGH | 48 table-stakes items mapped directly to ABC methods and v1.0 operational necessities. Feature matrix (FEATURES.md §4) cleanly separates HydraDB vs MuninnDB capabilities. Anti-features explicitly rejected with concrete reasons — no "defer to v2" ambiguity. |
| Architecture | HIGH | 7-layer design grounded in existing codebase patterns. `compress()` 6-step pipeline documented with actual data flow examples. Component boundaries, data segregation, and plugin directory structure all verified against v1.0 reference implementations. |
| Pitfalls | HIGH | All 18 pitfalls grounded against v1.0 reference (9 PRESENT repeats), ABC source (ContextEngine contract violations), and architecture research (anti-patterns). Each pitfall has concrete fix code, warning signs, phase placement, and test verification strategy. Recovery cost estimated for all 18. |

**Overall confidence:** HIGH — this is one of the most thoroughly-researched milestones in the cos-mcp project. The combination of a stable ABC contract, battle-tested backend SDKs, proven v1.0 patterns, and comprehensive pitfall documentation (learned from real v1.0 bugs) means implementation risk is well-bounded. The unknowns are in entity extraction tuning (heuristic quality varies by domain) and Hermes runtime integration behavior (plugin loader single-select, tool registration) — both addressable in Phase 4 live testing.

### Gaps to Address

- **Entity extraction precision/recall**: Pure Python heuristics are fast and free but quality varies by conversation domain. The `entity_extraction_mode` config (`"conservative"` / `"balanced"` / `"aggressive"`) provides a tuning knob, but optimal defaults need real-world usage data. Mitigation: ship with `"balanced"` as default, collect extraction counts and `context_search` hit rates during Phase 4 live testing, tune aggressiveness per-engine before v1.1 final.
- **Hermes runtime tool registration behavior**: Research documents that Hermes supports only one active context engine, but it's unclear whether `get_tool_schemas()` is called on ALL registered engines or only the active one. If the former, both `hydradb-context` and `muninn-context` would register duplicate `context_search`/`context_expand` tools. Mitigation: implement defensive gating (`if not active: return []`) in Phase 2, verify behavior in Phase 4 with both plugins installed.
- **Circuit breaker thresholds for context engine I/O frequency**: Memory providers call backends every turn (prefetch + sync); context engines call backends only on compression (every 10-15 turns) and tool invocations (on demand). The same `failure_threshold=5, cooldown=120` may never trip for context engines. Mitigation: make thresholds configurable per engine, default `failure_threshold=3` for context engines, test with both thresholds in Phase 1.
- **Live API integration tests**: Fake backend tests achieve 100% coverage but don't validate real API contract compliance (response shapes, indexing delay, error codes). v1.0 memory providers shipped with this gap and it caused 2 live integration issues. Mitigation: create a separate `test_hydradb_context_live.py` and `test_muninn_context_live.py` in Phase 4 that run only when `HYDRA_DB_API_KEY` / `MUNINN_API_KEY` env vars are set — skipped in CI, run manually before release.
- **`context_expand` performance with large message bodies**: If `context_expand` retrieves full original messages by full-text search rather than metadata lookup, latency could be 2-5s for large body sizes. Mitigation: store ctx-id as a metadata key, query by metadata filter (`metadata.ctx_id == "..."`), not full-text. Verify query pattern in Phase 2 HydraDB engine first.

## Sources

### Primary (HIGH confidence)
- `agent/context_engine.py` (Hermes source, 226 lines) — Authoritative on `ContextEngine` ABC contract, method signatures, class attributes, lifecycle hooks.
- `cos_mcp/base_provider.py` (352 lines) — `BaseMemoryProvider` reference implementation. Direct template for `BaseContextEngine`.
- `cos_mcp/circuit_breaker.py` (87 lines) — `CircuitBreaker` with dual read/write gauges. Reused as-is.
- `cos_mcp/backends/hydradb.py` (241 lines) — `HydraDBBackend` concrete implementation. Tenant provisioning, ingest with metadata, query patterns.
- `cos_mcp/backends/muninn.py` (210 lines) — `MuninnDBBackend` concrete implementation. Tag-based segregation, ACTIVATE pipeline, engram storage.
- `hydradb-memory/__init__.py` (284 lines) — Completed v1.0 HydraDB memory provider. Reference for plugin structure, config loading, tool schemas, register() pattern.
- `muninn-memory/__init__.py` (384 lines) — Completed v1.0 MuninnDB memory provider. Reference for cognitive features, memory type enums, tag management.
- `.planning/research/STACK.md` (369 lines) — Stack decisions and alternatives.
- `.planning/research/FEATURES.md` (314 lines) — Feature landscape with priorities.
- `.planning/research/ARCHITECTURE.md` (936 lines) — Architecture blueprint, data flows, anti-patterns.
- `.planning/research/PITFALLS.md` (915 lines) — 18 documented pitfalls with fixes.
- `.planning/codebase/CONCERNS.md` (270 lines) — v1.0 tech debt patterns that must not recur.

### Secondary (MEDIUM confidence)
- `.planning/PROJECT.md` (122 lines) — v1.1 milestone scope and constraints.
- `.planning/STATE.md` (92 lines) — v1.0 completion metrics (48/50 verified, 65 tests, zero failures).

### Tertiary (LOW confidence)
- None. All findings are grounded in existing source code and documented v1.0 patterns. No external documentation or community sources were needed — the ABC contract, SDK APIs, and v1.0 reference implementations are self-contained and authoritative.

---

*Research completed: 2026-06-20*
*Ready for roadmap: yes*
*Next: Use this summary as context for roadmap creation. Phase suggestions here are the starting point; the roadmap planner should adopt them unless specific constraints demand reordering.*
