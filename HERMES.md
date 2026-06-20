<!-- GSD:project-start source:PROJECT.md -->

## Project

**HydraDB Memory Provider**

A Hermes Agent memory provider plugin backed by HydraDB Cloud — a managed graph database. Replaces Hermes' built-in file-based memory with persistent, cross-session, graph-enriched semantic retrieval shared across all profiles. One HydraDB tenant isolates per-profile memories via sub-tenant IDs, with a future path to promote universal facts to a shared sub-tenant.

**Core Value:** Persistent searchable memory that survives across Hermes sessions and profiles — replacing ephemeral per-session context with durable, retrievable knowledge.

### Constraints

- **Tech stack:** Python 3.12+, sync only (no asyncio), `hydradb-sdk==2.0.1`
- **Plugin contract:** Must implement Hermes Agent `MemoryProvider` ABC — never hardcode `~/.hermes`, use `hermes_home` kwarg
- **Secrets:** `HYDRA_DB_API_KEY` in `~/.hermes/.env`, never committed
- **Tool naming:** Prefix all memory tools with `hydradb_` to avoid core-tool collisions
- **API:** `upsert` is `Optional[str]` — pass `"true"` (string), not `True`. Metadata for type=memory must be JSON-encoded string, not object.
- **Memory format:** `_format_chunks()` extracts clean prose from raw chunks — `build_string()` rejected (72-89% framing overhead)

<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->

## Technology Stack

## Languages

- Python 3.12 - All application code (hydradb-memory/__init__.py, 558 lines)
- None (no build scripts, no templating, no shell tooling beyond standard Linux utilities)

## Runtime

- Python 3.12.3 (CPython, system-installed on Linux 6.17.0-35-generic)
- PEP 668 enforced — virtual environments required (venv or uv)
- No browser runtime (server-side/agent plugin only)
- pip (via virtualenv) — no pyproject.toml, setup.py, or requirements.txt present; dependency declared in plugin.yaml: `hydradb-sdk>=2,<3`
- Lockfile: None (hydradb-sdk installed directly into .venv)

## Frameworks

- Hermes Agent MemoryProvider ABC — from `agent.memory_provider`; the plugin implements `HydraDBMemoryProvider(MemoryProvider)` with lifecycle methods (is_available, initialize, shutdown), read path (prefetch, queue_prefetch, system_prompt_block), write path (sync_turn, on_memory_write, on_session_end), and tool dispatch (get_tool_schemas, handle_tool_call)
- None (no test framework in project; test suite planned in SPEC.md Phase 2)
- None (plugin is deployed by copying files into `~/.hermes/hermes-agent/plugins/memory/hydradb/`)

## Key Dependencies

- hydradb-sdk 2.0.1 (installed in .venv) — Cloud client for HydraDB managed memory service; provides `HydraDB` sync client with query, context.ingest, and tenant management
- json — Config serialization, tool call results, ingest payloads
- logging — Structured logging via `logging.getLogger(__name__)`
- os — Environment variable reads (HYDRA_DB_API_KEY, HERMES_HOME)
- threading — Daemon threads for fire-and-forget writes, background prefetch queries, thread-safe client singleton
- `agent.memory_provider.MemoryProvider` — The ABC the provider inherits from

## Configuration

- `HYDRA_DB_API_KEY` — required; HydraDB API key (Bearer token), stored in `~/.hermes/.env`
- `HERMES_HOME` — optional; defaults to `~/.hermes`; used to locate `hydradb.json` and `.env`
- `~/.hermes/hydradb.json` — non-secret config: `tenant_id` (default "hermes"), `sub_tenant_id` (auto = profile name), `query_mode` ("thinking" or "fast"), `query_by` ("hybrid"), `max_results` (10)
- `~/.hermes/config.yaml` — `memory.provider: "hydradb"` activates the provider per-profile
- None (no build step; pure Python plugin)

## Platform Requirements

- Linux (x86_64) — primary development environment
- Python 3.12+ with virtualenv
- Network access to `https://api.hydradb.com` for live testing
- Any platform with Python 3.12+ (the plugin is a Hermes Agent in-tree plugin)
- Deployed by copying `hydradb-memory/` to `~/.hermes/hermes-agent/plugins/memory/hydradb/`
- Requires `pip install hydradb-sdk>=2,<3` in the Hermes Agent virtualenv
- Requires valid `HYDRA_DB_API_KEY` environment variable

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

## Naming Patterns

- `snake_case` for all files. Modules are single-file (`__init__.py`).
- `snake_case` for all functions and methods (`queue_prefetch`, `_format_chunks`, `_tool_search`).
- Module-level helpers: `snake_case` with underscore prefix when private (`_load_config`).
- `snake_case` for all variables and instance attributes.
- Private members prefixed with underscore (`_client`, `_client_lock`, `_prefetch_result`).
- `UPPER_SNAKE_CASE` for module-level constants (`SEARCH_SCHEMA`, `DEFAULT_CONFIG`).
- `PascalCase` for class names (`HydraDBMemoryProvider`).
- Inherits from a single base class (`MemoryProvider`).

## Code Style

- 4-space indentation (no tabs).
- No formatter or linter configured. No `pyproject.toml`, `setup.cfg`, or CI config exists.
- Google-style docstrings with triple double-quotes (`"""..."""`).
- Module docstring at top describing purpose, architecture, and references.
- Class docstring with description and credential/config notes.
- Method docstrings on all public and protected methods. Present on `private` helpers as well.
- `from __future__ import annotations` at top of every Python file (PEP 563).
- Use `typing` module imports (`Any`, `Dict`, `List`, `Optional`) and inline type annotations on all parameters and returns.

## Import Organization

- Alphabetical within each group.
- No path aliases (`@/`) — use relative or absolute imports.

## Error Handling

- **Fail-open:** never crash the agent. All exceptions in background threads and tool handlers are caught.
- `try:/except Exception:` wrapping all SDK calls in background threads, logging with `logger.debug(..., exc_info=True)`.
- Circuit breaker: 5 consecutive failures → 120-second cooldown. Tracks via `_failure_count` and `_breaker_open_until`.
- Early returns (guard clauses): `if self._is_breaker_open(): return`.
- `logger.warning()` for config parse failures and circuit breaker trips.
- `logger.debug(exc_info=True)` for transient SDK failures — suppressed at default log levels.
- Tool dispatch returns JSON error strings: `json.dumps({"error": str(e)})`.

## Logging

- Standard library `logging` module.
- Module-level logger: `logger = logging.getLogger(__name__)`.

| Level   | Usage |
|---------|-------|
| `debug` | Background thread exceptions, with `exc_info=True` |
| `info`  | Provider initialization (tenant, sub-tenant, mode) |
| `warning` | Config parse failures, circuit breaker open |

- `printf`-style formatting: `logger.info("text %s", value)` — no f-strings.

## Comments

- `# --- Section Title ---` with 75-char dashed lines to delimit major blocks (Config, Lifecycle, Client, Read path, Write path, Tools).
- Section headers appear at lines 28, 77, 113, 270, 339, 422, 497, 550.
- Module docstring explains architecture, cross-references design docs.
- Class docstring explains credentials and non-secret config sources.
- Method docstrings explain behavior and parameter requirements.
- No inline comments (docstrings carry the explanation).

## Function Design

- Methods range 5–40 lines. Private helpers extracted for reusable logic (`_tool_search`, `_format_chunks`, `_record_success`/`_record_failure`).

| Decorator | Usage |
|-----------|-------|
| `@staticmethod` | Functions not needing `self` (`get_config_schema`, `save_config`, `system_prompt_block`, `get_tool_schemas`, `_format_chunks`) |
| `@classmethod` | Used once for `is_available()` — checks credentials and import availability |

- `_get_client()` double-checks `self._client` inside a `threading.Lock()` for thread-safe on-demand SDK import and instantiation.
- Nested functions (`_run`, `_sync`, `_write`, `_summary`) passed to `threading.Thread(target=..., daemon=True)`. These are closures over `self` state.

## Module Design

- Single-file plugin module: `hydradb-memory/__init__.py` contains everything.
- `register(ctx)` function at module level registers the provider via `ctx.register_memory_provider(HydraDBMemoryProvider())`.
- Required hooks declared in `plugin.yaml` (`on_session_end`, `on_memory_write`).
- `pip_dependencies` and `requires_env` declared in `plugin.yaml`.
- Daemon threads for all fire-and-forget operations (`queue_prefetch`, `sync_turn`, `on_memory_write`, `on_session_end`).
- `threading.Lock()` for singleton client (`_client_lock`) and prefetch result (`_prefetch_lock`).
- `shutdown()` joins active background threads with 5-second timeout.

<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

## Pattern Overview

- Single Python class `HydraDBMemoryProvider(MemoryProvider)` with all logic in one file
- Synchronous API backed by background daemon threads for non-blocking I/O
- Lazy, thread-safe client singleton via double-checked locking
- Circuit breaker for resilience (5 consecutive failures → 120s cooldown)
- Fire-and-forget writes; prefetch / cache model for reads
- Static tool schemas exposed to the model via OpenAI function-calling format

## Layers

- Purpose: Read and persist provider configuration
- Contains: `_load_config()` (env + hydradb.json), `get_config_schema()` (field descriptors for `hermes memory setup`), `save_config()` (write non-secret config to disk)
- Location: `hydradb-memory/__init__.py` lines 81–185
- Depends on: `os.environ`, JSON file I/O
- Used by: Lifecycle layer (initialize), Hermes CLI tooling
- Purpose: Handle provider registration, availability check, and initialization
- Contains: `name` (class attr), `is_available()` (checks API key + SDK import), `initialize(session_id, **kwargs)` (captures identity, sets up threading primitives, circuit breaker state)
- Location: `hydradb-memory/__init__.py` lines 188–237
- Depends on: Config layer, `hydra_db` SDK (optional import)
- Used by: Hermes Agent runtime at startup
- Purpose: Manage the HydraDB SDK client instance
- Contains: `_get_client()` — lazy, thread-safe singleton via `threading.Lock` (double-checked locking pattern)
- Location: `hydradb-memory/__init__.py` lines 241–249
- Depends on: `hydra_db.HydraDB` SDK class, `_api_key` from config
- Used by: Read path, write path, tools
- Purpose: Retrieve relevant memories before each model turn
- Contains: `system_prompt_block()` (static text injected into system prompt), `queue_prefetch(query)` (fires background query), `prefetch()` (returns cached result), `_format_chunks(result, min_score)` (extracts clean memory text from SDK response objects)
- Location: `hydradb-memory/__init__.py` lines 272–337
- Depends on: Client layer, circuit breaker
- Used by: Hermes runtime (queue_prefetch before turn, prefetch during prompt assembly)
- Purpose: Persist conversation turns and memory writes into HydraDB
- Contains: `sync_turn(user_content, assistant_content)` (ingests user+assistant pair, `infer=True`), `on_memory_write(action, target, content, metadata)` (mirrors built-in memory operations, `infer=False`)
- Location: `hydradb-memory/__init__.py` lines 341–420
- Depends on: Client layer, circuit breaker
- Used by: Hermes runtime (sync_turn after each turn), Hermes built-in memory system (on_memory_write)
- Purpose: Expose memory operations to the model as function-calling tools
- Contains: `get_tool_schemas()` (returns OpenAI-format schemas for `hydradb_search`, `hydradb_profile`, `hydradb_conclude`), `handle_tool_call(tool_name, args)` (dispatches to `_tool_search`, `_tool_profile`, `_tool_conclude`)
- Location: `hydradb-memory/__init__.py` lines 31–75 (schemas), 424–495 (handlers)
- Depends on: Client layer
- Used by: Hermes Agent runtime (registers schemas, routes tool calls)
- Purpose: Respond to session lifecycle events
- Contains: `on_session_end(messages)` (ingests last 10 user/assistant messages as episodic memory, `infer=True`), `shutdown()` (joins background threads with 5s timeout, clears client)
- Location: `hydradb-memory/__init__.py` lines 499–548
- Depends on: Client layer, circuit breaker, threading primitives from initialize
- Used by: Hermes runtime (on_session_end at session close, shutdown at provider teardown)
- Purpose: Prevent cascading failures when HydraDB is unreachable
- Contains: `_is_breaker_open()` (returns True during cooldown), `_record_success()` (resets failure count), `_record_failure()` (increments count, opens breaker at threshold 5 for 120s)
- Location: `hydradb-memory/__init__.py` lines 253–268
- Depends on: `time.time()`
- Used by: Read path, write path, session hooks

## Data Flow

- No persistent local state beyond `hydradb.json` config
- All memory state lives in HydraDB cloud
- In-memory state is transient: `_prefetch_result` (one turn cache), `_client` (SDK singleton), `_failure_count` / `_breaker_open_until` (circuit breaker)
- Threading primitives (`_client_lock`, `_prefetch_lock`) are session-scoped

## Key Abstractions

- Purpose: Hermes Agent abstract base class defining the memory provider contract
- Implemented by: `HydraDBMemoryProvider`
- Pattern: Abstract Base Class — defines required interface (`initialize`, `prefetch`, `queue_prefetch`, `sync_turn`, `get_tool_schemas`, `handle_tool_call`, `system_prompt_block`, `is_available`, `shutdown`)
- Import: `from agent.memory_provider import MemoryProvider`
- Purpose: Entry point for Hermes plugin discovery
- Implemented by: module-level `register(ctx)` function
- Pattern: Single-call registration — `ctx.register_memory_provider(HydraDBMemoryProvider())`
- Location: `hydradb-memory/__init__.py` lines 556–558
- Purpose: Keep all I/O operations non-blocking to the agent runtime
- Examples: `queue_prefetch` spawns `hydradb-prefetch` thread, `sync_turn` spawns `hydradb-sync` thread, `on_memory_write` and `on_session_end` spawn anonymous threads
- Pattern: Each operation creates a new daemon thread — no thread pool, no queue
- Lifecycle: Threads joined in `shutdown()` with 5s timeout
- Purpose: Prevent retry storms when HydraDB is unreachable
- Pattern: Count-based with fixed cooldown (5 failures → 120s)
- State: `_failure_count` (int), `_breaker_open_until` (epoch float)
- Behavior: When open, `queue_prefetch`, `sync_turn`, `on_memory_write`, and `on_session_end` return immediately without attempting I/O
- Purpose: Extract clean memory text from HydraDB query results, avoiding `build_string()` framing overhead
- Pattern: Static method; iterates `result.data.chunks`, filters by `relevancy_score >= min_score`, extracts `chunk_content`, joins with `\n\n`

## Entry Points

- Location: `hydradb-memory/__init__.py` → `register(ctx)` function
- Triggers: Hermes Agent plugin loader discovers `plugin.yaml` and calls `register(ctx)`
- Responsibilities: Register the `HydraDBMemoryProvider` instance with the Hermes runtime
- Location: `HydraDBMemoryProvider.initialize()`
- Triggers: Called by Hermes runtime after registration, once per session
- Responsibilities: Load config, resolve tenant/sub_tenant identity, initialize threading primitives and circuit breaker
- `queue_prefetch(query)` — called before each model turn to start background memory fetch
- `prefetch()` — called during prompt assembly to retrieve cached results
- `sync_turn(user, assistant)` — called after each turn to persist the exchange
- `handle_tool_call(name, args)` — called when model invokes a memory tool

## Error Handling

- All I/O operations wrapped in `try/except Exception` — never crash the agent
- `_record_success()` resets failure count and breaker on any successful operation
- `_record_failure()` increments count; at threshold 5, sets `_breaker_open_until = now + 120s`
- Circuit breaker guards: `queue_prefetch`, `sync_turn`, `on_memory_write`, `on_session_end` check `_is_breaker_open()` before attempting I/O
- Tool calls: `handle_tool_call` catches exceptions and returns JSON `{"error": str(e)}` — model sees the error but agent continues
- Thread failures: daemon threads silently terminate on exception (no join required)
- Config errors: `_load_config()` catches `JSONDecodeError` and `OSError`, logs warning, returns defaults

## Cross-Cutting Concerns

- Standard `logging.getLogger(__name__)` — module-level logger
- INFO: initialization details (tenant, sub_tenant, mode)
- WARNING: config load failures, circuit breaker open
- DEBUG: I/O failures (prefetch, sync_turn, on_memory_write, on_session_end)
- `_client` protected by `threading.Lock` with double-checked locking
- `_prefetch_result` protected by `threading.Lock` (atomic get + clear)
- `_failure_count` and `_breaker_open_until` accessed without lock (simple int/float on CPython with GIL — acceptable for non-critical counter)
- Secrets (`HYDRA_DB_API_KEY`): environment variable only
- Non-secret config (`tenant_id`, `sub_tenant_id`, `query_mode`, `query_by`, `max_results`): `~/.hermes/hydradb.json` overrides module-level `DEFAULT_CONFIG`
- `sub_tenant_id` auto-resolves to `agent_identity` (profile name) for per-profile isolation — zero-config default
- Single HydraDB tenant (`tenant_id`) shared across all Hermes profiles
- Per-profile isolation via `sub_tenant_id` — defaults to profile name
- Set `sub_tenant_id: "shared"` for cross-profile memory
- Primary/secondary agent context guard: `sync_turn` and `on_session_end` skip if `_agent_context != "primary"`

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
