# HydraDB Memory Provider

## What This Is

A Hermes Agent memory provider plugin backed by HydraDB Cloud — a managed graph database. Replaces Hermes' built-in file-based memory with persistent, cross-session, graph-enriched semantic retrieval shared across all profiles. One HydraDB tenant isolates per-profile memories via sub-tenant IDs, with a future path to promote universal facts to a shared sub-tenant.

## Core Value

Persistent searchable memory that survives across Hermes sessions and profiles — replacing ephemeral per-session context with durable, retrievable knowledge.

## Requirements

### Validated

<!-- Existing implementation — tested against design spec and live API -->

- ✓ Config layer: env (`HYDRA_DB_API_KEY`) + JSON (`hydradb.json`) — implemented
- ✓ Lifecycle: `name`, `is_available()`, `initialize()` — implemented
- ✓ Read path: `system_prompt_block()`, `prefetch()`, `queue_prefetch()` with `_format_chunks()` — implemented
- ✓ Write path: `sync_turn()` (infer=true) — implemented
- ✓ `on_memory_write()` add/replace path (infer=false) — implemented
- ✓ Tools: `hydradb_search`, `hydradb_profile`, `hydradb_conclude` with OpenAI schemas — implemented
- ✓ Circuit breaker: 5 consecutive failures → 120s cooldown — implemented
- ✓ Lazy thread-safe client via `threading.Lock` — implemented

### Active

<!-- Current scope — building toward these -->

- [ ] Tenant auto-provisioning (`_ensure_tenant`): create if missing, poll until ready, handle 409 conflict
- [ ] `on_memory_write()` delete path with stable content-hash IDs
- [ ] `shutdown()` drain-threads: join `_prefetch_thread`, `_sync_thread`, `_mirror_thread`
- [ ] `on_session_end()` ingest session summary as episodic memory
- [ ] Test suite: fake HydraDB client, 5 test classes (config, queries, writes, circuit breaker, shutdown)
- [ ] Live API verification: all SDK calls confirmed against HydraDB Cloud
- [ ] Hermes integration: install plugin in-tree, `hermes memory setup hydradb`, `hermes doctor` clean
- [ ] Cross-profile activation: all gateway profiles use hydradb provider

### Out of Scope

- Shared sub-tenant promotion path — deferred to future milestone (requires taxonomy discovery first)
- Same-turn write visibility cache — v2 enhancement, not needed for cross-session memory
- Batch query or memory deduplication — v2
- AsyncHydraDB client — sync-only per Hermes provider contract
- Self-hosted HydraDB — cloud-only, free tier sufficient for personal use

## Context

**Existing codebase:** 558-line skeleton at `hydradb-memory/__init__.py` implements the core provider class with config, lifecycle, read/write paths, tools, and circuit breaker. `plugin.yaml` declares the dependency (`hydradb-sdk>=2,<3`). No tests exist.

**Design reference:** `research/hydradb-provider-design.html` — comprehensive architecture document with 10 design decisions, method specifications, data flow diagrams, SDK reference, testing strategy, and open questions. All research complete.

**Codebase map:** `.planning/codebase/` (7 documents) — stack (Python 3.12, hydradb-sdk 2.0.1), architecture (Plugin/Provider pattern, 7 layers), structure (single-file monolith), conventions (snake_case, Google docstrings, threading patterns), integrations (HydraDB Cloud API v2), concerns (9 tech debt items including untracked threads, race conditions, bare except blocks).

**Target deployment:** `~/.hermes/hermes-agent/plugins/memory/hydradb/` — in-tree plugin discovered by all profiles.

## Constraints

- **Tech stack:** Python 3.12+, sync only (no asyncio), `hydradb-sdk==2.0.1`
- **Plugin contract:** Must implement Hermes Agent `MemoryProvider` ABC — never hardcode `~/.hermes`, use `hermes_home` kwarg
- **Secrets:** `HYDRA_DB_API_KEY` in `~/.hermes/.env`, never committed
- **Tool naming:** Prefix all memory tools with `hydradb_` to avoid core-tool collisions
- **API:** `upsert` is `Optional[str]` — pass `"true"` (string), not `True`. Metadata for type=memory must be JSON-encoded string, not object.
- **Memory format:** `_format_chunks()` extracts clean prose from raw chunks — `build_string()` rejected (72-89% framing overhead)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| In-tree plugin at `~/.hermes/hermes-agent/plugins/memory/hydradb/` | Cross-profile requirement — in-tree is discovered by every profile | ✓ Good |
| One tenant `"hermes"`, one `sub_tenant_id` per profile (1:1) | Start isolated, promote to `"shared"` when taxonomy understood | ✓ Good |
| `HydraDB` (sync), not `AsyncHydraDB` | Hermes providers are synchronous — no asyncio | ✓ Good |
| Fire-and-forget writes on daemon threads | `sync_turn()` and `on_memory_write()` must not block agent runtime | ✓ Good |
| `queue_prefetch()` → background query, `prefetch()` returns cached | Same pattern as mem0 — read is non-blocking | ✓ Good |
| Circuit breaker: 5 failures → 120s cooldown | Cloud provider — same pattern as mem0 | ✓ Good |
| `sync_turn` uses `infer: true`, `on_memory_write` uses `infer: false` | sync_turn sends raw conversation for auto-extraction; on_memory_write stores verbatim | ✓ Good |
| Three tools: search, profile, conclude | Same pattern as mem0, prefixed to avoid collisions | ✓ Good |
| `_format_chunks()` instead of `build_string()` | build_string has 72-89% overhead — user prefers clean prose | ✓ Good |
| `metadata` as JSON string for type=memory | SDK requires JSON-encoded string, not object — returns 400 otherwise | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-20 after initialization*
