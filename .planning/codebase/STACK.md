# Technology Stack

**Analysis Date:** 2026-06-20

## Languages

**Primary:**
- Python 3.12 - All application code (hydradb-memory/__init__.py, 558 lines)

**Secondary:**
- None (no build scripts, no templating, no shell tooling beyond standard Linux utilities)

## Runtime

**Environment:**
- Python 3.12.3 (CPython, system-installed on Linux 6.17.0-35-generic)
- PEP 668 enforced — virtual environments required (venv or uv)
- No browser runtime (server-side/agent plugin only)

**Package Manager:**
- pip (via virtualenv) — no pyproject.toml, setup.py, or requirements.txt present; dependency declared in plugin.yaml: `hydradb-sdk>=2,<3`
- Lockfile: None (hydradb-sdk installed directly into .venv)

## Frameworks

**Core:**
- Hermes Agent MemoryProvider ABC — from `agent.memory_provider`; the plugin implements `HydraDBMemoryProvider(MemoryProvider)` with lifecycle methods (is_available, initialize, shutdown), read path (prefetch, queue_prefetch, system_prompt_block), write path (sync_turn, on_memory_write, on_session_end), and tool dispatch (get_tool_schemas, handle_tool_call)

**Testing:**
- None (no test framework in project; test suite planned in SPEC.md Phase 2)

**Build/Dev:**
- None (plugin is deployed by copying files into `~/.hermes/hermes-agent/plugins/memory/hydradb/`)

## Key Dependencies

**Critical:**
- hydradb-sdk 2.0.1 (installed in .venv) — Cloud client for HydraDB managed memory service; provides `HydraDB` sync client with query, context.ingest, and tenant management

**Standard Library:**
- json — Config serialization, tool call results, ingest payloads
- logging — Structured logging via `logging.getLogger(__name__)`
- os — Environment variable reads (HYDRA_DB_API_KEY, HERMES_HOME)
- threading — Daemon threads for fire-and-forget writes, background prefetch queries, thread-safe client singleton

**Imports from agent:**
- `agent.memory_provider.MemoryProvider` — The ABC the provider inherits from

## Configuration

**Environment:**
- `HYDRA_DB_API_KEY` — required; HydraDB API key (Bearer token), stored in `~/.hermes/.env`
- `HERMES_HOME` — optional; defaults to `~/.hermes`; used to locate `hydradb.json` and `.env`

**Config file:**
- `~/.hermes/hydradb.json` — non-secret config: `tenant_id` (default "hermes"), `sub_tenant_id` (auto = profile name), `query_mode` ("thinking" or "fast"), `query_by` ("hybrid"), `max_results` (10)

**Activation:**
- `~/.hermes/config.yaml` — `memory.provider: "hydradb"` activates the provider per-profile

**Build:**
- None (no build step; pure Python plugin)

## Platform Requirements

**Development:**
- Linux (x86_64) — primary development environment
- Python 3.12+ with virtualenv
- Network access to `https://api.hydradb.com` for live testing

**Production:**
- Any platform with Python 3.12+ (the plugin is a Hermes Agent in-tree plugin)
- Deployed by copying `hydradb-memory/` to `~/.hermes/hermes-agent/plugins/memory/hydradb/`
- Requires `pip install hydradb-sdk>=2,<3` in the Hermes Agent virtualenv
- Requires valid `HYDRA_DB_API_KEY` environment variable

---

*Stack analysis: 2026-06-20*
*Update after major dependency changes*
