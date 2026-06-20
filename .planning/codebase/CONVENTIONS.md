# Coding Conventions

**Analysis Date:** 2026-06-20
**Updated:** 2026-06-20 — documentation pass (multi-module architecture, test conventions)

## Naming Patterns

**Files:**
- `snake_case` for all files. Modules are single-file or packages (`__init__.py` + submodules).
- `base_*.py` for abstract base classes (`base_provider.py`, `base_context_engine.py`, `backends/base.py`, `formatting/base.py`, `formatting/context_base.py`).

**Functions:**
- `snake_case` for all functions and methods (`queue_prefetch`, `_format_chunks`, `_tool_search`).
- Module-level helpers: `snake_case` with underscore prefix when private (`_load_config`).

**Variables:**
- `snake_case` for all variables and instance attributes.
- Private members prefixed with underscore (`_client`, `_client_lock`, `_prefetch_result`).
- `UPPER_SNAKE_CASE` for module-level constants (`SEARCH_SCHEMA`, `DEFAULT_CONFIG`).

**Classes:**
- `PascalCase` for class names (`HydraDBContextEngine`, `MuninnDBFormatter`).
- Inherits from a single base class (`MemoryProvider`, `ContextEngine`, `MemoryBackend`).
- Base classes prefixed with `Base` (`BaseMemoryProvider`, `BaseContextEngine`).

## Code Style

**Formatting:**
- 4-space indentation (no tabs).
- Line length: no hard limit, but methods stay focused (5-40 lines typical).
- `pyproject.toml` exists with build config; no linter configured.

**Docstrings:**
- Google-style docstrings with triple double-quotes (`"""..."""`).
- Module docstring at top describing purpose, architecture, and references.
- Class docstring with description, credential/config notes, and subclass contract.
- Method docstrings on all public, protected, and private methods.
- `Args:` and `Returns:` sections for non-trivial methods.

**Module Header:**
- `from __future__ import annotations` at top of every Python file (PEP 563).

**Annotations:**
- Use `typing` module imports (`Any`, `Dict`, `List`, `Optional`) and inline type annotations on all parameters and returns.

## Import Organization

**Order (blank line between groups):**

1. `from __future__ import annotations`
2. Standard library (`json`, `logging`, `os`, `threading`, `hashlib`, `re`, `time`, `collections`)
3. Third-party (`import requests`, `from hydra_db import HydraDB`)
4. Hermes Agent runtime (`from agent.memory_provider import MemoryProvider`, `from agent.context_engine import ContextEngine`)
5. cos_mcp shared infrastructure (`from cos_mcp.base_provider import BaseMemoryProvider`, `from cos_mcp.backends.hydradb import HydraDBBackend`)

**Internal grouping:**
- Alphabetical within each group.
- No path aliases (`@/`) — use absolute imports from `cos_mcp`.

## Error Handling

**Strategy:**
- **Fail-open:** never crash the agent. All exceptions in background threads and tool handlers are caught.
- `try:/except Exception:` wrapping all backend calls in background threads, logging with `logger.debug(..., exc_info=True)`.
- Circuit breaker: configurable failure threshold (5 for providers, 3 for context engines) → 120s cooldown. Independent read/write gauges.
- Early returns (guard clauses): `if self._breaker.is_read_open(): return`.

**Error Reporting:**
- `logger.warning()` for config parse failures and circuit breaker trips.
- `logger.debug(exc_info=True)` for transient backend failures — suppressed at default log levels.
- Tool dispatch returns JSON error strings: `json.dumps({"error": str(e)})`.

## Logging

**Framework:**
- Standard library `logging` module.
- Module-level logger: `logger = logging.getLogger(__name__)`.

**Levels:**
| Level   | Usage |
|---------|-------|
| `debug` | Background thread exceptions, with `exc_info=True` |
| `info`  | Provider/engine initialization (tenant, sub_tenant, vault, agent context) |
| `warning` | Config parse failures, circuit breaker open, provisioning timeouts |

**Format:**
- `printf`-style formatting: `logger.info("text %s", value)` — no f-strings.

## Comments

**Separators:**
- `# --- Section Title ---` with 70-char dashed lines to delimit major blocks. Used consistently for: Config, Lifecycle, Client, Read path, Write path, Tools, Session hooks, Entity Extraction.
- Section header comments appear throughout base classes and plugins.

**When to Comment:**
- Module docstring explains architecture, cross-references design docs.
- Class docstring explains credentials, non-secret config sources, and subclass contract.
- Method docstrings explain behavior and parameter requirements.
- No inline comments (docstrings carry the explanation).

## Function Design

**Size:**
- Methods range 5-40 lines. Private helpers extracted for reusable logic.

**Method Types:**
| Decorator | Usage |
|-----------|-------|
| `@staticmethod` | Functions not needing `self` (`_load_config_file`, `_spawn_daemon`, `system_prompt_block`, formatter methods, entity extraction helpers, `make_mirror_id`) |
| `@classmethod` | `is_available()` — checks credentials and import availability |

**Lazy Init:**
- HydraDB client: `_get_client()` double-checks `self._client` inside a `threading.Lock()` for thread-safe on-demand SDK import and instantiation (double-checked locking).
- Backend provisioning: `_backend.provision()` called once in `initialize()`, backend tracks `_provisioned` flag.

**Thread Targets:**
- Nested functions (`_run`, `_sync`, `_write`, `_summary`) passed to `threading.Thread(target=..., daemon=True)`. These are closures over `self` state.
- BaseMemoryProvider: daemon threads for all fire-and-forget operations.
- BaseContextEngine: daemon threads only for HydraDB context engine (entity ingest); MuninnDB context engine is synchronous (local).

**Abstract Methods:**
- `raise NotImplementedError` for methods subclasses MUST override.
- Default no-op or stub implementations for optional overrides.

## Module Design

**Pattern:**
- **Shared infrastructure** in `cos_mcp/` — base classes, backends, formatters, circuit breaker.
- **Thin plugins** in `hydradb-memory/`, `muninn-memory/`, `plugins/context_engine/hydradb-context/`, `plugins/context_engine/muninn-context/`.

**Plugin Structure (each provider/engine):**
1. Module docstring
2. `from __future__ import annotations`
3. Imports (stdlib → typing → third-party → agent → cos_mcp)
4. Module-level logger
5. Compiled regex patterns (context engines only, for entity extraction)
6. Module-level constants (tool schemas, default config)
7. Module-level helper function (`_load_config()`)
8. Single exported class (extends base from cos_mcp)
9. Module-level entry point (`register(ctx)`)

**Plugin Contract:**
- `register(ctx)` function at module level registers via `ctx.register_memory_provider(...)` or `ctx.register_context_engine(...)`.
- Required hooks declared in `plugin.yaml` (per plugin: `on_session_end`, `on_memory_write`, etc.).
- `pip_dependencies` and `requires_env` declared in `plugin.yaml`.

**Threading:**
- Daemon threads for fire-and-forget operations (HydraDB: all writes; MuninnDB memory: prefetch + writes).
- `threading.Lock()` for singleton client (`_client_lock`) and prefetch result (`_prefetch_lock`).
- `shutdown()` joins tracked background threads with timeout (5s for memory providers, 30s for context engines).

## Test Conventions

**Framework:** pytest with `conftest.py` fixtures.

**Location:**
```
tests/
├── conftest.py          # Shared fixtures (if needed)
├── plugins/
│   ├── memory/
│   │   ├── conftest.py
│   │   └── test_hydradb_provider.py
│   └── context_engine/
│       ├── conftest.py          # FakeMemoryBackend fixture
│       ├── test_context_config.py
│       ├── test_context_circuit_breaker.py
│       ├── test_context_lifecycle.py
│       ├── test_context_compress.py
│       ├── test_context_tools.py
│       └── test_shared_infra.py
```

**Fake Backends:**
- `FakeMemoryBackend` implements `MemoryBackend` ABC with in-memory dict storage.
- No live API calls in tests — all backend operations stubbed.
- Query uses substring matching; ingest stores entries by ID.

**Test Patterns:**
- `tmp_path` fixture for temp config files.
- `monkeypatch` for environment variables.
- `time.time()` mocking for circuit breaker cooldown tests.
- Thread tracking: `time.sleep(0.1)` + `thread.join(timeout=1.0)` for daemon thread tests.
- Entity extraction: known input text → assert entity type, summary, confidence, count.

---

*Convention analysis: 2026-06-20*
*Update when patterns change*
