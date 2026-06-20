# Technology Stack

**Analysis Date:** 2026-06-20
**Updated:** 2026-06-20 — documentation pass (shared infrastructure, context engines, tests)

## Languages

**Primary:**
- Python 3.12+ — All application code

**Shared Infrastructure** (`cos_mcp/` package, ~4567 lines total across all modules):
- `base_provider.py` (352 lines) — BaseMemoryProvider with threading, circuit breaker, read/write paths
- `base_context_engine.py` (341 lines) — BaseContextEngine with token tracking, compression gate, lifecycle
- `circuit_breaker.py` (103 lines) — Dual-gauge (read/write) circuit breaker with configurable thresholds
- `backends/` — MemoryBackend ABC, HydraDBBackend (245 lines), MuninnDBBackend (217 lines)
- `formatting/` — MemoryFormatter ABC, ContextFormatter ABC, HydraDB/Muninn formatters for both memory and context

**Memory Provider Plugins:**
- `hydradb-memory/__init__.py` (284 lines) — Thin HydraDB provider extending BaseMemoryProvider
- `muninn-memory/__init__.py` (384 lines) — Thin MuninnDB provider extending BaseMemoryProvider

**Context Engine Plugins:**
- `plugins/context_engine/hydradb-context/__init__.py` (973 lines) — Graph-backed context compression + retrieval
- `plugins/context_engine/muninn-context/__init__.py` (1007 lines) — Cognitive-backed context compression + retrieval

## Runtime

**Environment:**
- Python 3.12.3 (CPython, system-installed on Linux 6.17.0-35-generic)
- PEP 668 enforced — virtual environments required (venv or uv)
- No browser runtime (server-side/agent plugin only)

**Package Manager:**
- `pyproject.toml` — build config (`setuptools>=68`), project deps, optional dependency groups
- `pip install -e .` for dev mode
- Optional deps: `hydradb` group (`hydradb-sdk>=2,<3`), `muninn` group (`requests>=2.31`)

## Frameworks

**Core:**
- Hermes Agent MemoryProvider ABC — from `agent.memory_provider`
- Hermes Agent ContextEngine ABC — from `agent.context_engine`
- `cos_mcp.BaseMemoryProvider(MemoryProvider)` — shared infrastructure for memory providers
- `cos_mcp.BaseContextEngine(ContextEngine)` — shared infrastructure for context engines
- `cos_mcp.MemoryBackend` (ABC) — abstract backend interface
- `cos_mcp.MemoryFormatter` (ABC), `cos_mcp.ContextFormatter` (ABC) — formatting abstractions

**Testing:**
- **pytest** — test framework (115 tests, zero failures, fake backends)
- Tests at `tests/plugins/context_engine/` (6 test modules) and `tests/plugins/memory/` (1 test module)
- `conftest.py` with fake backend fixtures, no live API calls in tests

**Build/Dev:**
- `pyproject.toml` — setuptools build system
- Plugins deployed by copying files into `~/.hermes/hermes-agent/plugins/`

## Key Dependencies

**Shared package (cos_mcp):**
- Standard library: `json`, `logging`, `os`, `threading`, `time`, `hashlib`, `re`, `collections`
- No third-party deps in the core package

**HydraDB Provider + Context Engine:**
- `hydradb-sdk>=2,<3` — Cloud client for HydraDB managed memory service; provides `HydraDB` sync client with `query`, `context.ingest`, `context.delete`, and tenant management

**MuninnDB Provider + Context Engine:**
- `requests>=2.31` — Sync HTTP client for MuninnDB REST API (`POST /api/activate`, `POST /api/engrams`, `GET /api/health`)

**Imports from agent:**
- `agent.memory_provider.MemoryProvider` — The ABC memory providers inherit from
- `agent.context_engine.ContextEngine` — The ABC context engines inherit from

## Configuration

### HydraDB Provider
- `HYDRA_DB_API_KEY` — required; Bearer token, stored in `~/.hermes/.env`
- `~/.hermes/hydradb.json` — non-secret: `tenant_id`, `sub_tenant_id`, `query_mode`, `query_by`, `max_results`
- `~/.hermes/config.yaml` — `memory.provider: "hydradb"`

### HydraDB Context Engine
- `HYDRA_DB_API_KEY` — required; Bearer token, stored in `~/.hermes/.env`
- `~/.hermes/hydradb-context.json` — non-secret: `tenant_id`, `sub_tenant_id`, `query_mode`, `entity_extraction_mode`, thresholds
- `~/.hermes/config.yaml` — `compression.provider: "hydradb-context"`

### MuninnDB Provider
- `MUNINN_API_KEY` — optional; Bearer token (not needed for default vault), stored in `~/.hermes/.env`
- `~/.hermes/muninn.json` — non-secret: `base_url` (default `http://127.0.0.1:8475`), `vault` (default `"default"`)
- `~/.hermes/config.yaml` — `memory.provider: "muninn"`

### MuninnDB Context Engine
- `MUNINN_API_KEY` — required; Bearer token, stored in `~/.hermes/.env`
- `~/.hermes/muninn-context.json` — non-secret: `base_url`, `vault`, `entity_extraction_mode`, thresholds
- `~/.hermes/config.yaml` — `compression.provider: "muninn-context"`

## Platform Requirements

**Development:**
- Linux (x86_64) — primary development environment
- Python 3.12+ with virtualenv
- Network access to `https://api.hydradb.com` (HydraDB provider/context engine)
- Local MuninnDB server at `http://127.0.0.1:8475` (MuninnDB provider/context engine)

**Production:**
- Any platform with Python 3.12+ (plugins are Hermes Agent in-tree plugins)
- Deployed by copying plugin directories to `~/.hermes/hermes-agent/plugins/memory/` and `~/.hermes/hermes-agent/plugins/context_engine/`
- HydraDB: requires `hydradb-sdk>=2,<3` in Hermes Agent virtualenv + `HYDRA_DB_API_KEY`
- MuninnDB: requires `requests>=2.31` + running MuninnDB server + `MUNINN_API_KEY`

---

*Stack analysis: 2026-06-20*
*Update after major dependency changes*
