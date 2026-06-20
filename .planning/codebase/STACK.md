# Technology Stack

**Analysis Date:** 2026-06-20
**Updated:** 2026-06-20 — documentation pass (added MuninnDB provider)

## Languages

**Primary:**
- Python 3.12 — All application code

**HydraDB Provider** (`hydradb-memory/__init__.py`, 735 lines):
- Pure Python, single-file module

**MuninnDB Provider** (`muninn-memory/__init__.py`, 760 lines):
- Pure Python, single-file module

## Runtime

**Environment:**
- Python 3.12.3 (CPython, system-installed on Linux 6.17.0-35-generic)
- PEP 668 enforced — virtual environments required (venv or uv)
- No browser runtime (server-side/agent plugin only)

**Package Manager:**
- pip (via virtualenv) — no pyproject.toml, setup.py, or requirements.txt present
- Dependencies declared in plugin.yaml per provider

## Frameworks

**Core:**
- Hermes Agent MemoryProvider ABC — from `agent.memory_provider`
- HydraDB provider: `HydraDBMemoryProvider(MemoryProvider)` — 735 lines
- MuninnDB provider: `MuninnDBMemoryProvider(MemoryProvider)` — 760 lines

**Testing:**
- None (no test framework in project; test suite planned)

**Build/Dev:**
- None (plugins deployed by copying files into `~/.hermes/hermes-agent/plugins/memory/`)

## Key Dependencies

**HydraDB Provider:**
- `hydradb-sdk>=2,<3` — Cloud client for HydraDB managed memory service; provides `HydraDB` sync client with `query`, `context.ingest`, and tenant management
- Standard library: `json`, `logging`, `os`, `threading`, `time`, `hashlib`

**MuninnDB Provider:**
- `requests>=2.31` — Sync HTTP client for MuninnDB REST API (`POST /api/engrams`, `POST /api/activate`, `GET /api/health`)
- Standard library: `json`, `logging`, `os`, `threading`, `time`

**Imports from agent:**
- `agent.memory_provider.MemoryProvider` — The ABC both providers inherit from

## Configuration

### HydraDB Provider
- `HYDRA_DB_API_KEY` — required; Bearer token, stored in `~/.hermes/.env`
- `~/.hermes/hydradb.json` — non-secret: `tenant_id`, `sub_tenant_id`, `query_mode`, `query_by`, `max_results`
- `~/.hermes/config.yaml` — `memory.provider: "hydradb"`

### MuninnDB Provider
- `MUNINN_API_KEY` — optional; Bearer token (not needed for default vault), stored in `~/.hermes/.env`
- `~/.hermes/muninn.json` — non-secret: `base_url` (default `http://127.0.0.1:8475`), `vault` (default `"default"`)
- `~/.hermes/config.yaml` — `memory.provider: "muninn"`

## Platform Requirements

**Development:**
- Linux (x86_64) — primary development environment
- Python 3.12+ with virtualenv
- Network access to `https://api.hydradb.com` (HydraDB provider)
- Local MuninnDB server at `http://127.0.0.1:8475` (MuninnDB provider)

**Production:**
- Any platform with Python 3.12+ (plugins are Hermes Agent in-tree plugins)
- Deployed by copying `hydradb-memory/` and/or `muninn-memory/` to `~/.hermes/hermes-agent/plugins/memory/`
- HydraDB: requires `hydradb-sdk>=2,<3` in Hermes Agent virtualenv + `HYDRA_DB_API_KEY`
- MuninnDB: requires `requests>=2.31` + running MuninnDB server

---

*Stack analysis: 2026-06-20*
*Update after major dependency changes*
