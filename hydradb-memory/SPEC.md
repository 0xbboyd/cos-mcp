# HydraDB Memory Provider for Hermes Agent

## Overview

Build a Hermes Agent memory provider plugin backed by HydraDB v2 — a managed cloud graph database for AI memory. The plugin enables persistent cross-session memory with semantic search, graph-enriched retrieval, and auto-fact-extraction across all Hermes profiles.

## Current State

A 558-line skeleton implementation exists at `hydradb-memory/__init__.py` with:
- Config layer (_load_config, get_config_schema, save_config)
- Lifecycle (name, is_available, initialize with tenant auto-provisioning)
- Lazy thread-safe client with circuit breaker (5 failures / 120s cooldown)
- Read path: prefetch (returns cached), queue_prefetch (background query with _format_chunks)
- Write path: sync_turn (fire-and-forget, infer=true), on_memory_write (mirror built-in, infer=false)
- Three tools: hydradb_search, hydradb_profile, hydradb_conclude
- Session hooks: on_session_end, shutdown
- register(ctx) entry point

plugin.yaml and README.md also exist. Git repo initialized with files staged.

## Architecture Decisions

1. **In-tree plugin** at `~/.hermes/hermes-agent/plugins/memory/hydradb/` — discovered by all profiles
2. **Topology**: One shared tenant "hermes", one sub_tenant_id per profile (1:1). sub_tenant_id auto-resolves to agent_identity (profile name). Zero-config per-profile isolation.
3. **Sync SDK only** — HydraDB (sync), not AsyncHydraDB. Hermes providers are synchronous.
4. **Fire-and-forget writes** on daemon threads. Reads: queue_prefetch → background query, prefetch → cached result.
5. **Circuit breaker**: 5 consecutive failures → 120s cooldown.
6. **infer mode**: sync_turn uses infer=true (auto-extract), on_memory_write uses infer=false (verbatim).
7. **_format_chunks()** instead of build_string() — build_string has 72-89% framing overhead.
8. **upsert="true"** (string, not bool). **metadata as JSON string** for type=memory.

## Verified Facts

- HydraDB SDK: `hydradb-sdk==2.0.1`, `from hydra_db import HydraDB`
- API: `https://api.hydradb.com`, API-Version: 2 header, Bearer token auth
- Free tier: $0/mo, unlimited API calls, storage-based pricing
- Ingest latency: ~500ms, query: ~2-2.5s, queryable: 1-5s
- Tenant list: `client.tenants.list().data.tenant_ids` (Optional[List[str]])
- Query result chunks: `chunk_content`, `id`, `relevancy_score`, `metadata`, `source_type`

## What Needs to Be Built

### Phase 1: Core Provider Implementation
- Complete and test the HydraDBMemoryProvider class
- Verify all SDK calls work against live HydraDB API
- Implement _format_chunks() to extract clean memory text from query results
- Handle tenant auto-provisioning with 409 conflict handling
- Handle FILE_NOT_FOUND race on context.status for first 1-2s after ingest

### Phase 2: Integration Testing
- Write test suite (test_hydradb_provider.py) with fake client
- Test config, queries, writes, circuit breaker, shutdown
- Test per-profile sub_tenant_id resolution
- Test metadata JSON string encoding gotcha
- Verify against live HydraDB API with real API key

### Phase 3: Hermes Integration
- Install plugin in-tree at ~/.hermes/hermes-agent/plugins/memory/hydradb/
- Run `hermes memory setup hydradb` to activate
- Verify with `hermes memory status` and `hermes doctor`
- Test memory injection into system prompt via prefetch
- Test tool calls (hydradb_search, hydradb_profile, hydradb_conclude)
- Test on_memory_write mirroring

### Phase 4: Cross-Profile Activation
- Activate provider on all profiles (config.yaml memory.provider: hydradb)
- Verify per-profile sub_tenant_id isolation
- Migrate existing built-in MEMORY.md/USER.md entries to HydraDB
- Verify gateway profiles (estate, bridge-coder, cos-mcp) work with provider

## Key Files
- Design doc: `research/hydradb-provider-design.md`
- Hermes research: `research/hermes-memory-provider-research.md`
- HydraDB research: `research/hydradb-v2-research.md`
- Scaffold: `hydradb-memory/__init__.py`, `hydradb-memory/plugin.yaml`, `hydradb-memory/README.md`

## Constraints
- All methods synchronous (no asyncio)
- Never hardcode ~/.hermes — use hermes_home kwarg
- Tool names must not shadow core tools (prefix with hydradb_)
- Fail-open: never crash the agent, catch all exceptions
- sync_turn must be non-blocking (daemon thread)
- prefetch must be fast (return cached, don't query)
