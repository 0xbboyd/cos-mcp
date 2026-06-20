# COS-MCP — Shared Infrastructure for Hermes Agent Plugins

## What This Is

**cos-mcp** is a Python package providing shared infrastructure for Hermes Agent plugins — circuit breaker, backend abstraction, formatting, and base classes for memory providers and context engines.

Four plugins extend this infrastructure:

1. **HydraDB Memory Provider** — cloud-backed persistent memory using HydraDB's managed graph database. Graph-enriched semantic retrieval with auto-fact-extraction. One HydraDB tenant isolates per-profile memories via sub-tenant IDs.

2. **MuninnDB Memory Provider** — local cognitive memory using MuninnDB's neuroscience-inspired engine. ACT-R temporal scoring, Hebbian co-activation learning, Bayesian confidence tracking, and 16 typed relationship types — all engine-native.

3. **HydraDB Context Engine** — graph-backed context compression and retrieval. Full compress() pipeline with pure-Python entity extraction, fire-and-forget graph ingest, and tool-based context_search/context_expand with graph traversal.

4. **MuninnDB Context Engine** — cognitive-backed context compression and retrieval. Full compress() pipeline with 16 relationship type classification, synchronous engram storage, and Bayesian confidence-gated retrieval tools.

## Current Milestone: v0.2.0 — Context Engines Complete

**Completed phases:**
- Phase 1-4: HydraDB Memory Provider (v1.0)
- Phase 5: Shared infrastructure extraction — cos_mcp package with BaseMemoryProvider, BaseContextEngine, backends, formatters
- Phase 6: HydraDB Context Engine — full compress() pipeline, entity extraction, tools, lifecycle
- Phase 7: MuninnDB Context Engine — full cognitive compress pipeline, 16 relationship types
- Phase 8: Testing — 115 tests, zero failures, fake backends

**Target features delivered:**
- HydraDB Context Engine — graph-backed compress() pathway, context_search/context_expand tools, session lifecycle, circuit breaker ✓
- MuninnDB Context Engine — cognitive-backed compress() with ACT-R decay + Hebbian learning, context_search/context_expand tools ✓
- Shared cos_mcp infra — BaseContextEngine, CircuitBreaker, config loading, formatting ✓
- Test suites — fake backends for both engines, 100% requirement coverage ✓
- In-tree deployment — `plugins/context_engine/hydradb-context/` and `plugins/context_engine/muninn-context/` ✓

## Requirements

### Validated — Shared Infrastructure

- ✓ `cos_mcp` package: CircuitBreaker, MemoryBackend ABC, MemoryFormatter ABC, ContextFormatter ABC
- ✓ `BaseMemoryProvider(MemoryProvider)` — threading, circuit breaker, read/write paths, shared tool helpers
- ✓ `BaseContextEngine(ContextEngine)` — token tracking, compression gating, lifecycle, config loading
- ✓ `HydraDBBackend`, `MuninnDBBackend` — both implement MemoryBackend ABC
- ✓ Formatters: HydraDBFormatter, MuninnDBFormatter, HydraDBContextFormatter, MuninnDBContextFormatter

### Validated — Memory Providers

- ✓ HydraDB: config, lifecycle, read/write paths, tools (hydradb_search, hydradb_profile, hydradb_conclude), circuit breaker
- ✓ MuninnDB: config, lifecycle, read/write paths, tools (muninn_search, muninn_profile, muninn_remember), circuit breaker
- ✓ Both providers extend BaseMemoryProvider — ~284 and ~384 lines respectively

### Validated — Context Engines

- ✓ HydraDB Context Engine: compress() pipeline, entity extraction (topics, decisions, facts, relationships), tools (hydradb_context_search, hydradb_context_expand), circuit breaker
- ✓ MuninnDB Context Engine: compress() pipeline, 16 relationship types, tools (muninn_context_search, muninn_context_expand), synchronous execution
- ✓ Both engines extend BaseContextEngine — ~973 and ~1007 lines respectively

### Validated — Testing

- ✓ 115 tests, zero failures, using fake backends
- ✓ Tests cover: shared infra, config, circuit breaker, lifecycle, compress/entity extraction, tools

### Active

- [ ] Live API verification: all MuninnDB REST calls confirmed against running MuninnDB instance
- [ ] HydraDB provider: expose `recency_bias`, `alpha`, `metadata_filters` as tool params (cookbook research complete)
- [ ] MuninnDB provider: live integration test suite

### Out of Scope

- Shared sub-tenant / cross-vault promotion path — deferred to future milestone
- Same-turn write visibility cache — v2 enhancement
- Batch query or memory deduplication — v2
- Async SDK clients — sync-only per Hermes provider contract

## Context

**Codebase: ~4567 lines** across shared package + 4 plugins + tests.

**Shared infrastructure:** `cos_mcp/` — circuit_breaker.py (103 lines), base_provider.py (352), base_context_engine.py (341), backends/hydradb.py (245), backends/muninn.py (217), formatting/ (6 files, 542 lines total).

**Thin plugins:** hydradb-memory/ (284 lines), muninn-memory/ (384 lines), hydradb-context/ (973 lines), muninn-context/ (1007 lines).

**Tests:** 115 tests across 7 modules using FakeMemoryBackend — no live API calls.

**Research:** `research/hydradb-provider-design.md` (architecture blueprint), `research/hydradb-v2-research.md` (HydraDB API reference), `research/hermes-memory-provider-research.md` (provider contract research).

**Codebase map:** `.planning/codebase/` (7 documents) — stack, architecture, structure, conventions, integrations, testing, concerns.

**Target deployment:** Plugins copied to `~/.hermes/hermes-agent/plugins/memory/` and `~/.hermes/hermes-agent/plugins/context_engine/`.

## Constraints

- **Tech stack:** Python 3.12+, sync only (no asyncio)
- **Plugin contract:** Must implement Hermes Agent ABCs — never hardcode `~/.hermes`, use `hermes_home` kwarg
- **Secrets:** API keys in `~/.hermes/.env`, never committed
- **Tool naming:** Prefix tools with provider prefix to avoid core-tool collisions (`hydradb_*`, `muninn_*`)
- **Memory format:** Clean prose extraction from retrieval results — no framing overhead
- **Code style:** 4-space indent, Google-style docstrings, `from __future__ import annotations`, PEP 563

## Key Decisions

| Decision | Rationale | Outcome |
|---|---|---|
| Shared infrastructure + thin plugins | Eliminate ~60% code duplication between providers | ✓ Good |
| BaseMemoryProvider / BaseContextEngine | Subclasses only define backend-specific config, tools, handlers | ✓ Good |
| MemoryBackend ABC | Uniform interface — swap backends without changing provider code | ✓ Good |
| MemoryFormatter / ContextFormatter ABCs | Backend-specific formatting isolated from provider/engine logic | ✓ Good |
| Dual circuit breaker (read/write independent) | Read failures shouldn't block writes and vice versa | ✓ Good |
| Configurable breaker thresholds | Providers use 5 failures, context engines use 3 (lower I/O freq) | ✓ Good |
| FakeBackend for tests | No live API calls — fast, deterministic, testable failure modes | ✓ Good |
| hydradb-sdk vs requests.Session | Cloud SDK for HydraDB, sync HTTP for MuninnDB | ✓ Good |

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

*Last updated: 2026-06-20 — v0.2.0 context engines complete, documentation pass*
