# HydraDB Memory Provider for Hermes Agent

## Overview

Build a Hermes Agent memory provider plugin backed by HydraDB v2 — a managed cloud graph database for AI memory. The plugin enables persistent cross-session memory with semantic search, graph-enriched retrieval, and auto-fact-extraction across all Hermes profiles.

## Current State

The provider is **complete and deployed**. The implementation at `hydradb-memory/__init__.py` (284 lines) is a thin adapter extending `cos_mcp.BaseMemoryProvider`. All shared infrastructure (circuit breaker, threading, config loading, read/write path patterns) lives in the `cos_mcp` package.

The plugin defines:
- Config layer (`_load_config` — env + JSON merge)
- Subclass hooks (`_create_backend` → HydraDBBackend, `_create_formatter` → HydraDBFormatter)
- Tool schemas + handlers (`hydradb_search`, `hydradb_profile`, `hydradb_conclude`)
- System prompt block
- `get_config_schema()` + `save_config()`
- `register(ctx)` entry point

A companion **MuninnDB provider** is also available at `../muninn-memory/` — it implements the same `MemoryProvider` ABC against MuninnDB's local cognitive engine.

Additional **context engine plugins** at `../plugins/context_engine/` provide graph/cognitive-backed context compression and retrieval.

`plugin.yaml` and `README.md` also exist.

## Architecture Decisions

1. **In-tree plugin** at `~/.hermes/hermes-agent/plugins/memory/hydradb/` — discovered by all profiles
2. **Topology**: One shared tenant `"hermes"`, one `sub_tenant_id` per profile (1:1). `sub_tenant_id` auto-resolves to `agent_identity` (profile name). Zero-config per-profile isolation.
3. **Sync SDK only** — HydraDB (sync), not AsyncHydraDB. Hermes providers are synchronous.
4. **Fire-and-forget writes** on daemon threads. Reads: `queue_prefetch` → background query, `prefetch` → cached result.
5. **Dual circuit breaker**: Independent read and write gauges, each 5 consecutive failures → 120s cooldown. Tool calls check read breaker; writes check write breaker.
6. **infer mode**: `sync_turn` uses `infer=true` (auto-extract), `on_memory_write` uses `infer=false` (verbatim).
7. **`HydraDBFormatter.format()`** instead of `build_string()` — `build_string` has 72-89% framing overhead. Formatter extracts `chunk_content` directly from SDK response.
8. **`upsert="true"`** (string, not bool). **metadata as JSON string** for type=memory.
9. **Content-hash IDs** on `on_memory_write` for deterministic upsert/delete (`hashlib.sha256`).
10. **Batched `on_session_end`**: ingests last 10 user/assistant messages from last 20 total.
11. **Shared infrastructure**: BaseMemoryProvider in `cos_mcp` handles circuit breaker, threading, config loading, read/write paths, and session hooks. Provider is a thin subclass (~284 lines).

## Verified Facts

- HydraDB SDK: `hydradb-sdk==2.0.1`, `from hydra_db import HydraDB`
- API: `https://api.hydradb.com`, API-Version: 2 header, Bearer token auth
- Free tier: $0/mo, unlimited API calls, storage-based pricing
- Ingest latency: ~500ms, query: ~2-2.5s, queryable: 1-5s
- Tenant list: `client.tenants.list().data.tenant_ids` (Optional[List[str]])
- Query result chunks: `chunk_content`, `id`, `relevancy_score`, `metadata`, `source_type`

## Phase Completion

### Phase 1: Core Provider Implementation ✓ COMPLETE

- ✓ `HydraDBMemoryProvider` class extending `BaseMemoryProvider`
- ✓ `HydraDBFormatter` extracting clean memory text from query results (in `cos_mcp`)
- ✓ `HydraDBBackend` with tenant auto-provisioning, 409 conflict handling, 5-minute readiness poll (in `cos_mcp`)
- ✓ Independent read/write circuit breakers (in `cos_mcp`)
- ✓ All SDK calls verified against HydraDB Cloud
- ✓ Fire-and-forget write threads with content-hash IDs

### Phase 2: Integration Testing — In Progress

- ✓ Test suite at `tests/plugins/memory/test_hydradb_provider.py` with fake backend
- Tests for config, queries, writes, circuit breaker, shutdown
- Tests for per-profile `sub_tenant_id` resolution
- Tests for metadata JSON string encoding gotcha
- [ ] Verify against live HydraDB API with real API key

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

### Phase 5: Shared Infrastructure (cos_mcp) ✓ COMPLETE

- ✓ `BaseMemoryProvider` with threading, circuit breaker, read/write paths, shared tool helpers
- ✓ `MemoryBackend` ABC — uniform interface for backends
- ✓ `MemoryFormatter` ABC — backend-specific formatting
- ✓ `HydraDBBackend` and `HydraDBFormatter` in cos_mcp package
- ✓ HydraDB provider refactored to thin adapter (~284 lines)

## Key Files

- Shared infrastructure: `cos_mcp/base_provider.py`, `cos_mcp/backends/hydradb.py`, `cos_mcp/formatting/hydradb.py`
- Implementation: `hydradb-memory/__init__.py`, `hydradb-memory/plugin.yaml`, `hydradb-memory/README.md`
- MuninnDB sibling: `../muninn-memory/__init__.py`
- Context engines: `../plugins/context_engine/hydradb-context/`, `../plugins/context_engine/muninn-context/`
- Design doc: `research/hydradb-provider-design.md`
- HydraDB research: `research/hydradb-v2-research.md`
- Hermes research: `research/hermes-memory-provider-research.md`

## Constraints

- All methods synchronous (no asyncio)
- Never hardcode `~/.hermes` — use `hermes_home` kwarg
- Tool names must not shadow core tools (prefix with `hydradb_`)
- Fail-open: never crash the agent, catch all exceptions
- `sync_turn` must be non-blocking (daemon thread)
- `prefetch` must be fast (return cached, don't query)
