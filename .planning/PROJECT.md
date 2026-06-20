# Memory Provider Plugins for Hermes Agent

## What This Is

Two Hermes Agent memory provider plugins:

1. **HydraDB Memory Provider** — cloud-backed persistent memory using HydraDB's managed graph database. Replaces Hermes' built-in file-based memory with persistent, cross-session, graph-enriched semantic retrieval shared across all profiles. One HydraDB tenant isolates per-profile memories via sub-tenant IDs.

2. **MuninnDB Memory Provider** — local cognitive memory using MuninnDB's neuroscience-inspired engine. ACT-R temporal scoring (frequent access strengthens recall; stale memories fade), Hebbian co-activation learning (memories used together auto-associate), Bayesian confidence tracking (contradicted memories are discounted), and 16 typed relationship types — all engine-native. One Muninn vault per profile for isolation.

## Core Value

Persistent searchable memory that survives across Hermes sessions and profiles — replacing ephemeral per-session context with durable, retrievable knowledge. Two backends serving the same contract — swap by changing one config value.

## Requirements

### Validated — HydraDB Provider

- ✓ Config layer: env (`HYDRA_DB_API_KEY`) + JSON (`hydradb.json`) — implemented
- ✓ Lifecycle: `name`, `is_available()`, `initialize()` — implemented
- ✓ Read path: `system_prompt_block()`, `prefetch()`, `queue_prefetch()` with `_format_chunks()` — implemented
- ✓ Write path: `sync_turn()` (infer=true) — implemented
- ✓ `on_memory_write()` add/replace/delete paths with content-hash IDs — implemented
- ✓ Tools: `hydradb_search`, `hydradb_profile`, `hydradb_conclude` with OpenAI schemas — implemented
- ✓ Circuit breaker: dual read/write gauges, 5 consecutive failures → 120s cooldown — implemented
- ✓ Lazy thread-safe client via `threading.Lock` — implemented
- ✓ Tenant auto-provisioning: create if missing, poll until ready, handle 409 conflict — implemented
- ✓ `on_session_end()` ingest session summary as episodic memory — implemented
- ✓ `shutdown()` drain-threads: join background threads with 5s timeout — implemented
- ✓ Hermes integration: plugin installed in-tree, `hermes memory setup hydradb`, `hermes doctor` clean — deployed
- ✓ Cross-profile activation: all gateway profiles use hydradb provider — deployed

### Validated — MuninnDB Provider

- ✓ Config layer: env (`MUNINN_API_KEY`) + JSON (`muninn.json`) — implemented
- ✓ Lifecycle: `name`, `is_available()`, `initialize()` — implemented
- ✓ Read path: `system_prompt_block()`, `prefetch()`, `queue_prefetch()` with `_format_activations()` — implemented
- ✓ Write path: `sync_turn()`, `on_memory_write()`, `on_session_end()` — implemented
- ✓ Tools: `muninn_search` (with memory_type + min_confidence), `muninn_profile` (dual query: preferences + identity), `muninn_remember` (concept + content + type + tags) — implemented
- ✓ Circuit breaker: dual read/write gauges — implemented
- ✓ HTTP session management via `requests.Session` with bearer auth — implemented
- ✓ 12 memory type enums exposed to model via tool schemas — implemented

### Active

- [ ] Live API verification: all MuninnDB REST calls confirmed against running MuninnDB instance
- [ ] Test suite: fake clients for both providers
- [ ] HydraDB provider: expose `recency_bias`, `alpha`, `metadata_filters` as tool params (cookbook research complete)

### Out of Scope

- Shared sub-tenant / cross-vault promotion path — deferred to future milestone
- Same-turn write visibility cache — v2 enhancement
- Batch query or memory deduplication — v2
- Async SDK clients — sync-only per Hermes provider contract
- Self-hosted HydraDB — cloud-only, free tier sufficient

## Context

**HydraDB provider:** 735-line implementation at `hydradb-memory/__init__.py` — complete MemoryProvider with config, lifecycle, read/write paths, tools, session hooks, and dual circuit breaker. Plugin deployed in-tree and active on all Hermes profiles.

**MuninnDB provider:** 760-line implementation at `muninn-memory/__init__.py` — complete MemoryProvider backed by MuninnDB REST API. All cognitive features (ACT-R scoring, Hebbian learning, confidence tracking) are engine-native — the plugin is a thin HTTP adapter.

**Research:** `research/hydradb-provider-design.md` (architecture blueprint), `research/hydradb-v2-research.md` (HydraDB API reference), `research/hermes-memory-provider-research.md` (provider contract research). Cookbook research completed for `recency_bias`, `alpha`, `metadata_filters`, `graph_context` enhancements.

**Codebase map:** `.planning/codebase/` (7 documents) — stack, architecture, structure, conventions, integrations, testing, concerns.

**Target deployment:** `~/.hermes/hermes-agent/plugins/memory/hydradb/` and `.../muninn/` — in-tree plugins discovered by all profiles.

## Constraints

- **Tech stack:** Python 3.12+, sync only (no asyncio)
- **Plugin contract:** Must implement Hermes Agent `MemoryProvider` ABC — never hardcode `~/.hermes`, use `hermes_home` kwarg
- **Secrets:** API keys in `~/.hermes/.env`, never committed
- **Tool naming:** Prefix memory tools with provider prefix to avoid core-tool collisions (`hydradb_*`, `muninn_*`)
- **Memory format:** Clean prose extraction from retrieval results — no framing overhead

## Key Decisions

| Decision | Rationale | Outcome |
|---|---|---|
| Two providers, one ABC | Different trade-offs: cloud convenience vs cognitive depth. Swap by config. | ✓ Good |
| In-tree plugins at `~/.hermes/hermes-agent/plugins/memory/` | Cross-profile — in-tree discovered by every profile | ✓ Good |
| HydraDB: one tenant, one sub_tenant per profile | Start isolated, promote to "shared" for universal facts | ✓ Good |
| Muninn: one vault per profile | Flat isolation model, no sub-tenant equivalent | ✓ Good |
| Sync-only SDKs | Hermes providers are synchronous — no asyncio | ✓ Good |
| Fire-and-forget writes on daemon threads | `sync_turn()` and `on_memory_write()` must not block | ✓ Good |
| `queue_prefetch()` → background query, `prefetch()` returns cached | Same pattern as mem0 — read is non-blocking | ✓ Good |
| Dual circuit breaker (read/write independent) | Read failures shouldn't block writes and vice versa | ✓ Good |
| `sync_turn` uses `infer: true`, `on_memory_write` uses `infer: false` | Auto-extraction for conversation, verbatim for curated entries | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone:**
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---

*Last updated: 2026-06-20 — documentation pass*
