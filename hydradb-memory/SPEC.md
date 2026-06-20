# HydraDB Memory Provider for Hermes Agent

## Overview

Build a Hermes Agent memory provider plugin backed by HydraDB v2 — a managed cloud graph database for AI memory. The plugin enables persistent cross-session memory with semantic search, graph-enriched retrieval, and auto-fact-extraction across all Hermes profiles.

## Current State

The provider is **complete and deployed**. The implementation at `hydradb-memory/__init__.py` (735 lines) includes:

- Config layer (`_load_config`, `get_config_schema`, `save_config`)
- Lifecycle (`name`, `is_available`, `initialize` with tenant auto-provisioning)
- Lazy thread-safe client with dual circuit breaker (independent read/write gauges, 5 failures / 120s cooldown)
- Read path: `prefetch` (returns cached), `queue_prefetch` (background query with `_format_chunks`)
- Write path: `sync_turn` (fire-and-forget, `infer=true`), `on_memory_write` (mirror built-in, `infer=false`, content-hash IDs for delete)
- Three tools: `hydradb_search`, `hydradb_profile`, `hydradb_conclude`
- Session hooks: `on_session_end`, `shutdown`
- `register(ctx)` entry point

A companion **MuninnDB provider** is also available at `../muninn-memory/` — it implements the same `MemoryProvider` ABC against MuninnDB's local cognitive engine (ACT-R temporal scoring, Hebbian learning, Bayesian confidence, 16 typed relationships engine-native).

`plugin.yaml` and `README.md` also exist.

## Architecture Decisions

1. **In-tree plugin** at `~/.hermes/hermes-agent/plugins/memory/hydradb/` — discovered by all profiles
2. **Topology**: One shared tenant `"hermes"`, one `sub_tenant_id` per profile (1:1). `sub_tenant_id` auto-resolves to `agent_identity` (profile name). Zero-config per-profile isolation.
3. **Sync SDK only** — HydraDB (sync), not AsyncHydraDB. Hermes providers are synchronous.
4. **Fire-and-forget writes** on daemon threads. Reads: `queue_prefetch` → background query, `prefetch` → cached result.
5. **Dual circuit breaker**: Independent read and write gauges, each 5 consecutive failures → 120s cooldown. Tool calls check read breaker; writes check write breaker.
6. **infer mode**: `sync_turn` uses `infer=true` (auto-extract), `on_memory_write` uses `infer=false` (verbatim).
7. **`_format_chunks()`** instead of `build_string()` — `build_string` has 72-89% framing overhead.
8. **`upsert="true"`** (string, not bool). **metadata as JSON string** for type=memory.
9. **Content-hash IDs** on `on_memory_write` for deterministic upsert/delete (`hashlib.sha256`).
10. **Batched `on_session_end`**: ingests last 10 user/assistant messages from last 20 total.

## Verified Facts

- HydraDB SDK: `hydradb-sdk==2.0.1`, `from hydra_db import HydraDB`
- API: `https://api.hydradb.com`, API-Version: 2 header, Bearer token auth
- Free tier: $0/mo, unlimited API calls, storage-based pricing
- Ingest latency: ~500ms, query: ~2-2.5s, queryable: 1-5s
- Tenant list: `client.tenants.list().data.tenant_ids` (Optional[List[str]])
- Query result chunks: `chunk_content`, `id`, `relevancy_score`, `metadata`, `source_type`

## Phase Completion

### Phase 1: Core Provider Implementation ✓ COMPLETE

- ✓ `HydraDBMemoryProvider` class with all ABC methods
- ✓ `_format_chunks()` extracting clean memory text from query results
- ✓ Tenant auto-provisioning with 409 conflict handling and 5-minute readiness poll
- ✓ Independent read/write circuit breakers
- ✓ All SDK calls verified against HydraDB Cloud
- ✓ Fire-and-forget write threads with content-hash IDs

### Phase 2: Integration Testing — Not Yet Executed

- Write test suite (`test_hydradb_provider.py`) with fake client
- Test config, queries, writes, circuit breaker, shutdown
- Test per-profile `sub_tenant_id` resolution
- Test metadata JSON string encoding gotcha
- Verify against live HydraDB API with real API key

### Phase 3: Hermes Integration — Deployed

- ✓ Plugin installed in-tree at `~/.hermes/hermes-agent/plugins/memory/hydradb/`
- ✓ `hermes memory setup hydradb` activation
- ✓ Memory injection into system prompt via prefetch
- ✓ Tool calls (`hydradb_search`, `hydradb_profile`, `hydradb_conclude`)
- ✓ `on_memory_write` mirroring

### Phase 4: Cross-Profile Activation — Deployed

- ✓ Provider activated on all gateway profiles (`config.yaml memory.provider: hydradb`)
- ✓ Per-profile `sub_tenant_id` isolation verified
- ✓ Works in cos-mcp and other profiles

## Key Files

- Design doc: `research/hydradb-provider-design.md`
- Hermes research: `research/hermes-memory-provider-research.md`
- HydraDB research: `research/hydradb-v2-research.md`
- Implementation: `hydradb-memory/__init__.py`, `hydradb-memory/plugin.yaml`, `hydradb-memory/README.md`
- MuninnDB sibling: `../muninn-memory/__init__.py`

## Constraints

- All methods synchronous (no asyncio)
- Never hardcode `~/.hermes` — use `hermes_home` kwarg
- Tool names must not shadow core tools (prefix with `hydradb_`)
- Fail-open: never crash the agent, catch all exceptions
- `sync_turn` must be non-blocking (daemon thread)
- `prefetch` must be fast (return cached, don't query)
