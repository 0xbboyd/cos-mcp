# Coding Conventions

**Analysis Date:** 2026-06-20

## Naming Patterns

**Files:**
- `snake_case` for all files. Modules are single-file (`__init__.py`).

**Functions:**
- `snake_case` for all functions and methods (`queue_prefetch`, `_format_chunks`, `_tool_search`).
- Module-level helpers: `snake_case` with underscore prefix when private (`_load_config`).

**Variables:**
- `snake_case` for all variables and instance attributes.
- Private members prefixed with underscore (`_client`, `_client_lock`, `_prefetch_result`).
- `UPPER_SNAKE_CASE` for module-level constants (`SEARCH_SCHEMA`, `DEFAULT_CONFIG`).

**Classes:**
- `PascalCase` for class names (`HydraDBMemoryProvider`).
- Inherits from a single base class (`MemoryProvider`).

## Code Style

**Formatting:**
- 4-space indentation (no tabs).
- No formatter or linter configured. No `pyproject.toml`, `setup.cfg`, or CI config exists.

**Docstrings:**
- Google-style docstrings with triple double-quotes (`"""..."""`).
- Module docstring at top describing purpose, architecture, and references.
- Class docstring with description and credential/config notes.
- Method docstrings on all public and protected methods. Present on `private` helpers as well.

**Module Header:**
- `from __future__ import annotations` at top of every Python file (PEP 563).

**Annotations:**
- Use `typing` module imports (`Any`, `Dict`, `List`, `Optional`) and inline type annotations on all parameters and returns.

## Import Organization

**Order (blank line between groups):**

1. `from __future__ import annotations`
2. Standard library (`json`, `logging`, `os`, `threading`)
3. Typing imports (`from typing import Any, Dict, List, Optional`)
4. Third-party / parent package (`from agent.memory_provider import MemoryProvider`)

**Internal grouping:**
- Alphabetical within each group.
- No path aliases (`@/`) — use relative or absolute imports.

## Error Handling

**Strategy:**
- **Fail-open:** never crash the agent. All exceptions in background threads and tool handlers are caught.
- `try:/except Exception:` wrapping all SDK calls in background threads, logging with `logger.debug(..., exc_info=True)`.
- Circuit breaker: 5 consecutive failures → 120-second cooldown. Tracks via `_failure_count` and `_breaker_open_until`.
- Early returns (guard clauses): `if self._is_breaker_open(): return`.

**Error Reporting:**
- `logger.warning()` for config parse failures and circuit breaker trips.
- `logger.debug(exc_info=True)` for transient SDK failures — suppressed at default log levels.
- Tool dispatch returns JSON error strings: `json.dumps({"error": str(e)})`.

## Logging

**Framework:**
- Standard library `logging` module.
- Module-level logger: `logger = logging.getLogger(__name__)`.

**Levels:**
| Level   | Usage |
|---------|-------|
| `debug` | Background thread exceptions, with `exc_info=True` |
| `info`  | Provider initialization (tenant, sub-tenant, mode) |
| `warning` | Config parse failures, circuit breaker open |

**Format:**
- `printf`-style formatting: `logger.info("text %s", value)` — no f-strings.

## Comments

**Separators:**
- `# --- Section Title ---` with 75-char dashed lines to delimit major blocks (Config, Lifecycle, Client, Read path, Write path, Tools).
- Section headers appear at lines 28, 77, 113, 270, 339, 422, 497, 550.

**When to Comment:**
- Module docstring explains architecture, cross-references design docs.
- Class docstring explains credentials and non-secret config sources.
- Method docstrings explain behavior and parameter requirements.
- No inline comments (docstrings carry the explanation).

**No TODO comments** present in current code.

## Function Design

**Size:**
- Methods range 5–40 lines. Private helpers extracted for reusable logic (`_tool_search`, `_format_chunks`, `_record_success`/`_record_failure`).

**Method Types:**
| Decorator | Usage |
|-----------|-------|
| `@staticmethod` | Functions not needing `self` (`get_config_schema`, `save_config`, `system_prompt_block`, `get_tool_schemas`, `_format_chunks`) |
| `@classmethod` | Used once for `is_available()` — checks credentials and import availability |

**Lazy Init:**
- `_get_client()` double-checks `self._client` inside a `threading.Lock()` for thread-safe on-demand SDK import and instantiation.

**Thread Targets:**
- Nested functions (`_run`, `_sync`, `_write`, `_summary`) passed to `threading.Thread(target=..., daemon=True)`. These are closures over `self` state.

## Module Design

**Pattern:**
- Single-file plugin module: `hydradb-memory/__init__.py` contains everything.

**Structure (top-to-bottom):**
1. Module docstring
2. `from __future__ import annotations`
3. Imports (stdlib → typing → third-party)
4. Module-level logger
5. Module-level constants (tool schemas, default config)
6. Module-level helper function (`_load_config()`)
7. Single exported class (`HydraDBMemoryProvider`)
8. Module-level entry point (`register(ctx)`)

**Plugin Contract:**
- `register(ctx)` function at module level registers the provider via `ctx.register_memory_provider(HydraDBMemoryProvider())`.
- Required hooks declared in `plugin.yaml` (`on_session_end`, `on_memory_write`).
- `pip_dependencies` and `requires_env` declared in `plugin.yaml`.

**Threading:**
- Daemon threads for all fire-and-forget operations (`queue_prefetch`, `sync_turn`, `on_memory_write`, `on_session_end`).
- `threading.Lock()` for singleton client (`_client_lock`) and prefetch result (`_prefetch_lock`).
- `shutdown()` joins active background threads with 5-second timeout.

---

*Convention analysis: 2026-06-20*
*Update when patterns change*
