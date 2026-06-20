<!-- GSD:project-start source:PROJECT.md -->

## Project

**COS-MCP — Shared Infrastructure for Hermes Agent Plugins**

**cos-mcp** is a Python package providing shared infrastructure for Hermes Agent plugins — circuit breaker, backend abstraction, formatting, and base classes for memory providers and context engines.

Four plugins extend this infrastructure:

1. **HydraDB Memory Provider** — cloud-backed persistent memory using HydraDB's managed graph database. Graph-enriched semantic retrieval with auto-fact-extraction. One HydraDB tenant isolates per-profile memories via sub-tenant IDs.

2. **MuninnDB Memory Provider** — local cognitive memory using MuninnDB's neuroscience-inspired engine. ACT-R temporal scoring, Hebbian co-activation learning, Bayesian confidence tracking, and 16 typed relationship types — all engine-native.

3. **HydraDB Context Engine** — graph-backed context compression and retrieval. Full compress() pipeline with pure-Python entity extraction, fire-and-forget graph ingest, and tool-based context_search/context_expand with graph traversal.

4. **MuninnDB Context Engine** — cognitive-backed context compression and retrieval. Full compress() pipeline with 16 relationship type classification, synchronous engram storage, and Bayesian confidence-gated retrieval tools.

### Constraints

- **Tech stack:** Python 3.12+, sync only (no asyncio)
- **Plugin contract:** Must implement Hermes Agent ABCs — never hardcode `~/.hermes`, use `hermes_home` kwarg
- **Secrets:** API keys in `~/.hermes/.env`, never committed
- **Tool naming:** Prefix tools with provider prefix to avoid core-tool collisions (`hydradb_*`, `muninn_*`)
- **Memory format:** Clean prose extraction from retrieval results — no framing overhead
- **Code style:** 4-space indent, Google-style docstrings, `from __future__ import annotations`, PEP 563

<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

## Languages

- Python 3.12+ — All application code
- `base_provider.py` (352 lines) — BaseMemoryProvider with threading, circuit breaker, read/write paths
- `base_context_engine.py` (341 lines) — BaseContextEngine with token tracking, compression gate, lifecycle
- `circuit_breaker.py` (103 lines) — Dual-gauge (read/write) circuit breaker with configurable thresholds
- `backends/` — MemoryBackend ABC, HydraDBBackend (245 lines), MuninnDBBackend (217 lines)
- `formatting/` — MemoryFormatter ABC, ContextFormatter ABC, HydraDB/Muninn formatters for both memory and context
- `hydradb-memory/__init__.py` (284 lines) — Thin HydraDB provider extending BaseMemoryProvider
- `muninn-memory/__init__.py` (384 lines) — Thin MuninnDB provider extending BaseMemoryProvider
- `plugins/context_engine/hydradb-context/__init__.py` (973 lines) — Graph-backed context compression + retrieval
- `plugins/context_engine/muninn-context/__init__.py` (1007 lines) — Cognitive-backed context compression + retrieval

## Runtime

- Python 3.12.3 (CPython, system-installed on Linux 6.17.0-35-generic)
- PEP 668 enforced — virtual environments required (venv or uv)
- No browser runtime (server-side/agent plugin only)
- `pyproject.toml` — build config (`setuptools>=68`), project deps, optional dependency groups
- `pip install -e .` for dev mode
- Optional deps: `hydradb` group (`hydradb-sdk>=2,<3`), `muninn` group (`requests>=2.31`)

## Frameworks

- Hermes Agent MemoryProvider ABC — from `agent.memory_provider`
- Hermes Agent ContextEngine ABC — from `agent.context_engine`
- `cos_mcp.BaseMemoryProvider(MemoryProvider)` — shared infrastructure for memory providers
- `cos_mcp.BaseContextEngine(ContextEngine)` — shared infrastructure for context engines
- `cos_mcp.MemoryBackend` (ABC) — abstract backend interface
- `cos_mcp.MemoryFormatter` (ABC), `cos_mcp.ContextFormatter` (ABC) — formatting abstractions
- **pytest** — test framework (115 tests, zero failures, fake backends)
- Tests at `tests/plugins/context_engine/` (6 test modules) and `tests/plugins/memory/` (1 test module)
- `conftest.py` with fake backend fixtures, no live API calls in tests
- `pyproject.toml` — setuptools build system
- Plugins deployed by copying files into `~/.hermes/hermes-agent/plugins/`

## Key Dependencies

- Standard library: `json`, `logging`, `os`, `threading`, `time`, `hashlib`, `re`, `collections`
- No third-party deps in the core package
- `hydradb-sdk>=2,<3` — Cloud client for HydraDB managed memory service; provides `HydraDB` sync client with `query`, `context.ingest`, `context.delete`, and tenant management
- `requests>=2.31` — Sync HTTP client for MuninnDB REST API (`POST /api/activate`, `POST /api/engrams`, `GET /api/health`)
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

- Linux (x86_64) — primary development environment
- Python 3.12+ with virtualenv
- Network access to `https://api.hydradb.com` (HydraDB provider/context engine)
- Local MuninnDB server at `http://127.0.0.1:8475` (MuninnDB provider/context engine)
- Any platform with Python 3.12+ (plugins are Hermes Agent in-tree plugins)
- Deployed by copying plugin directories to `~/.hermes/hermes-agent/plugins/memory/` and `~/.hermes/hermes-agent/plugins/context_engine/`
- HydraDB: requires `hydradb-sdk>=2,<3` in Hermes Agent virtualenv + `HYDRA_DB_API_KEY`
- MuninnDB: requires `requests>=2.31` + running MuninnDB server + `MUNINN_API_KEY`

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

## Naming Patterns

- `snake_case` for all files. Modules are single-file or packages (`__init__.py` + submodules).
- `base_*.py` for abstract base classes (`base_provider.py`, `base_context_engine.py`, `backends/base.py`, `formatting/base.py`, `formatting/context_base.py`).
- `snake_case` for all functions and methods (`queue_prefetch`, `_format_chunks`, `_tool_search`).
- Module-level helpers: `snake_case` with underscore prefix when private (`_load_config`).
- `snake_case` for all variables and instance attributes.
- Private members prefixed with underscore (`_client`, `_client_lock`, `_prefetch_result`).
- `UPPER_SNAKE_CASE` for module-level constants (`SEARCH_SCHEMA`, `DEFAULT_CONFIG`).
- `PascalCase` for class names (`HydraDBContextEngine`, `MuninnDBFormatter`).
- Inherits from a single base class (`MemoryProvider`, `ContextEngine`, `MemoryBackend`).
- Base classes prefixed with `Base` (`BaseMemoryProvider`, `BaseContextEngine`).

## Code Style

- 4-space indentation (no tabs).
- Line length: no hard limit, but methods stay focused (5-40 lines typical).
- `pyproject.toml` exists with build config; no linter configured.
- Google-style docstrings with triple double-quotes (`"""..."""`).
- Module docstring at top describing purpose, architecture, and references.
- Class docstring with description, credential/config notes, and subclass contract.
- Method docstrings on all public, protected, and private methods.
- `Args:` and `Returns:` sections for non-trivial methods.
- `from __future__ import annotations` at top of every Python file (PEP 563).
- Use `typing` module imports (`Any`, `Dict`, `List`, `Optional`) and inline type annotations on all parameters and returns.

## Import Organization

- Alphabetical within each group.
- No path aliases (`@/`) — use absolute imports from `cos_mcp`.

## Error Handling

- **Fail-open:** never crash the agent. All exceptions in background threads and tool handlers are caught.
- `try:/except Exception:` wrapping all backend calls in background threads, logging with `logger.debug(..., exc_info=True)`.
- Circuit breaker: configurable failure threshold (5 for providers, 3 for context engines) → 120s cooldown. Independent read/write gauges.
- Early returns (guard clauses): `if self._breaker.is_read_open(): return`.
- `logger.warning()` for config parse failures and circuit breaker trips.
- `logger.debug(exc_info=True)` for transient backend failures — suppressed at default log levels.
- Tool dispatch returns JSON error strings: `json.dumps({"error": str(e)})`.

## Logging

- Standard library `logging` module.
- Module-level logger: `logger = logging.getLogger(__name__)`.

| Level   | Usage |
|---------|-------|
| `debug` | Background thread exceptions, with `exc_info=True` |
| `info`  | Provider/engine initialization (tenant, sub_tenant, vault, agent context) |
| `warning` | Config parse failures, circuit breaker open, provisioning timeouts |

- `printf`-style formatting: `logger.info("text %s", value)` — no f-strings.

## Comments

- `# --- Section Title ---` with 70-char dashed lines to delimit major blocks. Used consistently for: Config, Lifecycle, Client, Read path, Write path, Tools, Session hooks, Entity Extraction.
- Section header comments appear throughout base classes and plugins.
- Module docstring explains architecture, cross-references design docs.
- Class docstring explains credentials, non-secret config sources, and subclass contract.
- Method docstrings explain behavior and parameter requirements.
- No inline comments (docstrings carry the explanation).

## Function Design

- Methods range 5-40 lines. Private helpers extracted for reusable logic.

| Decorator | Usage |
|-----------|-------|
| `@staticmethod` | Functions not needing `self` (`_load_config_file`, `_spawn_daemon`, `system_prompt_block`, formatter methods, entity extraction helpers, `make_mirror_id`) |
| `@classmethod` | `is_available()` — checks credentials and import availability |

- HydraDB client: `_get_client()` double-checks `self._client` inside a `threading.Lock()` for thread-safe on-demand SDK import and instantiation (double-checked locking).
- Backend provisioning: `_backend.provision()` called once in `initialize()`, backend tracks `_provisioned` flag.
- Nested functions (`_run`, `_sync`, `_write`, `_summary`) passed to `threading.Thread(target=..., daemon=True)`. These are closures over `self` state.
- BaseMemoryProvider: daemon threads for all fire-and-forget operations.
- BaseContextEngine: daemon threads only for HydraDB context engine (entity ingest); MuninnDB context engine is synchronous (local).
- `raise NotImplementedError` for methods subclasses MUST override.
- Default no-op or stub implementations for optional overrides.

## Module Design

- **Shared infrastructure** in `cos_mcp/` — base classes, backends, formatters, circuit breaker.
- **Thin plugins** in `hydradb-memory/`, `muninn-memory/`, `plugins/context_engine/hydradb-context/`, `plugins/context_engine/muninn-context/`.
- `register(ctx)` function at module level registers via `ctx.register_memory_provider(...)` or `ctx.register_context_engine(...)`.
- Required hooks declared in `plugin.yaml` (per plugin: `on_session_end`, `on_memory_write`, etc.).
- `pip_dependencies` and `requires_env` declared in `plugin.yaml`.
- Daemon threads for fire-and-forget operations (HydraDB: all writes; MuninnDB memory: prefetch + writes).
- `threading.Lock()` for singleton client (`_client_lock`) and prefetch result (`_prefetch_lock`).
- `shutdown()` joins tracked background threads with timeout (5s for memory providers, 30s for context engines).

## Test Conventions

- `FakeMemoryBackend` implements `MemoryBackend` ABC with in-memory dict storage.
- No live API calls in tests — all backend operations stubbed.
- Query uses substring matching; ingest stores entries by ID.
- `tmp_path` fixture for temp config files.
- `monkeypatch` for environment variables.
- `time.time()` mocking for circuit breaker cooldown tests.
- Thread tracking: `time.sleep(0.1)` + `thread.join(timeout=1.0)` for daemon thread tests.
- Entity extraction: known input text → assert entity type, summary, confidence, count.

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

## Pattern Overview

```

```

## Shared Infrastructure — `cos_mcp`

### BaseMemoryProvider (MemoryProvider ABC)

- **Config Layer:** `_load_config_file()` (static helper — reads `{hermes_home}/{name}.json`, merges overrides), `get_config_schema()` + `save_config()` (override in subclass).
- **Lifecycle Layer:** `is_available()` (classmethod, override), `initialize(session_id, **kwargs)` — captures identity, creates backend + formatter via subclass hooks, provisions backend, sets up circuit breaker and threading primitives.
- **Read Path:** `system_prompt_block()` (static, override), `queue_prefetch(query)` — fires background daemon thread query, checks read breaker. `prefetch()` — returns cached result atomically, clears cache.
- **Write Path:** `sync_turn(user, assistant)` — fire-and-forget ingest of turn pair (skips if agent_context != "primary"). `on_memory_write(action, target, content, metadata)` — mirrors built-in memory ops, uses content-hash IDs for deterministic upsert/delete. Both check write breaker.
- **Tools Layer:** `get_tool_schemas()` (override), `handle_tool_call(name, args, **kwargs)` (override). Shared helpers: `_tool_search_impl(args, max_results, min_score, query_mode)`, `_tool_conclude_impl(args)`.
- **Session Hooks:** `on_session_end(messages)` — ingests last 10 user/assistant messages as episodic memory (skips if agent_context != "primary"). `shutdown()` — joins background threads (5s timeout), calls `_backend.shutdown()`.

### BaseContextEngine (ContextEngine ABC)

- **Lifecycle Layer:** `initialize(session_id, **kwargs)` — captures identity, creates backend + formatter via subclass hooks, sets up circuit breaker (lower threshold: 3 failures → 120s cooldown), tracks background threads.
- **Token Tracking:** `update_from_response(usage)` — dual-format handling (canonical `input_tokens`/`output_tokens` + legacy `prompt_tokens`/`completion_tokens`). Cache tokens tracked separately, never counted toward prompt tokens. Recalculates `threshold_tokens` from `context_length * threshold_percent`.
- **Compression Gate:** `should_compress(prompt_tokens)` — returns True when prompt tokens >= threshold. Uses `last_prompt_tokens` unless explicit override.
- **Model Switch:** `update_model(model, context_length, ...)` — recalculates threshold when model changes.
- **Session Lifecycle:** `on_session_start()` (subclass override), `on_session_end()` (subclass override), `on_session_reset()` (zeros counters), `shutdown()` (joins threads 30s, calls `_backend.shutdown()`).
- **compress():** Raises NotImplementedError — subclasses MUST override with full pipeline.
- **Tools:** `get_tool_schemas()` (subclass override), `handle_tool_call()` (subclass override).
- **Config:** `_load_config_file()` (static helper — same pattern as BaseMemoryProvider).

### Backend Abstraction (MemoryBackend ABC)

| Method | Signature | Purpose |
|--------|-----------|---------|
| `query` | `(query_text, max_results, query_mode, query_by, graph_context, memory_type, min_confidence)` | Search memory, returns backend-native result |
| `ingest` | `(text, infer, user_name, metadata, memory_id, memory_type_label, tags, confidence)` | Store a memory entry |
| `delete` | `(memory_id)` | Delete by ID |
| `health_check` | `()` | Connectivity check, returns bool |
| `provision` | `()` | Ensure backend is ready (create tenants, vaults, etc.), returns bool |
| `shutdown` | `()` | Release resources |

### Formatter Abstractions

- `format_compress_summary(result)` — formats entity lists for system message insertion
- `format_search_result(result, min_score)` — formats search results with graph/cognitive annotations
- `format_expand_result(result)` — formats ctx-id expansion with traversal paths

### Circuit Breaker

- **Memory providers:** 5 failures → 120s cooldown (default).
- **Context engines:** 3 failures → 120s cooldown (lower I/O frequency, faster trip).
- Read gauge guards: `queue_prefetch`, tool search/profile calls.
- Write gauge guards: `sync_turn`, `on_memory_write`, tool conclude/remember calls, `on_session_end`.
- A single success resets the counter and closes the breaker.

## HydraDB Memory Provider (`hydradb-memory/`)

## MuninnDB Memory Provider (`muninn-memory/`)

## HydraDB Context Engine (`plugins/context_engine/hydradb-context/`)

## MuninnDB Context Engine (`plugins/context_engine/muninn-context/`)

## Cross-Cutting Concerns

### Thread Safety

- Backend clients: HydraDB uses double-checked locking singleton; MuninnDB reuses `requests.Session`.
- Prefetch result: `threading.Lock` (atomic get + clear).
- Circuit breaker: all state transitions under `_lock`.
- Background threads tracked for shutdown join.

### Error Handling

- **Fail-open:** all exceptions caught, logged at DEBUG, silently discarded.
- Tool calls return JSON `{"error": str(e)}` — model sees error, agent continues.
- Daemon threads silently terminate on exception.
- Config errors: `JSONDecodeError` + `OSError` caught, log warning, return defaults.

### Logging

- Standard `logging.getLogger(__name__)` — module-level logger.
- INFO: initialization details (tenant/vault, mode, agent context).
- WARNING: config load failures, circuit breaker open.
- DEBUG: I/O failures (prefetch, sync_turn, on_memory_write, on_session_end) with `exc_info=True`.
- `printf`-style formatting: `logger.info("text %s", value)` — no f-strings.

### Configuration

- Secrets: environment variables only (`HYDRA_DB_API_KEY`, `MUNINN_API_KEY`).
- Non-secret config: JSON files in `$HERMES_HOME` (`hydradb.json`, `muninn.json`, `hydradb-context.json`, `muninn-context.json`) merge over module-level `DEFAULT_CONFIG`.
- HydraDB: `sub_tenant_id` auto-resolves to profile name for per-profile isolation.
- MuninnDB: `vault` defaults to `"default"` — use per-profile vault names for isolation.

### Agent Context Guard

- `sync_turn` and `on_session_end` skip if `_agent_context != "primary"` — prevents cron/subagent/flush contexts from polluting memory.
- Context engines track `_agent_context` but don't gate writes (context compression runs for all contexts).

### Data Segregation

- HydraDB: `type=memory` for memory provider entries, `type=context` for context engine entries.
- MuninnDB: `hermes-memory` tag for provider entries, `hermes-context` tag for context engine entries.

## Test Architecture

- `conftest.py` provides `FakeMemoryBackend` — in-memory dict store implementing `MemoryBackend` ABC. Supports query (substring match), ingest, delete, health_check, provision.
- Circuit breaker tests: mock `time.time()` for deterministic cooldown behavior.
- Config tests: temp dirs via `tmp_path`, environment variable monkeypatching.
- Entity extraction tests: known input → expected entity list assertions.
- Lifecycle tests: verify thread tracking, shutdown drain, session reset.

<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.hermes/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
