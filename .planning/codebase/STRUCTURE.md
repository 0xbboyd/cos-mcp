# Codebase Structure

**Analysis Date:** 2026-06-20
**Updated:** 2026-06-20 — documentation pass (shared infrastructure, context engines, tests)

## Directory Layout

```
cos-mcp/
├── cos_mcp/                           # Shared infrastructure package
│   ├── __init__.py                    # Package exports (44 lines)
│   ├── circuit_breaker.py             # Dual-gauge circuit breaker (103 lines)
│   ├── base_provider.py               # BaseMemoryProvider ABC (352 lines)
│   ├── base_context_engine.py         # BaseContextEngine ABC (341 lines)
│   ├── backends/
│   │   ├── __init__.py                # (empty)
│   │   ├── base.py                    # MemoryBackend ABC (75 lines)
│   │   ├── hydradb.py                 # HydraDBBackend (245 lines)
│   │   └── muninn.py                  # MuninnDBBackend (217 lines)
│   └── formatting/
│       ├── __init__.py                # (empty)
│       ├── base.py                    # MemoryFormatter ABC (31 lines)
│       ├── context_base.py            # ContextFormatter ABC (63 lines)
│       ├── hydradb.py                 # HydraDBFormatter (44 lines)
│       ├── hydradb_context.py         # HydraDBContextFormatter (151 lines)
│       ├── muninn.py                  # MuninnDBFormatter (61 lines)
│       └── muninn_context.py          # MuninnDBContextFormatter (192 lines)
├── hydradb-memory/                    # HydraDB memory provider plugin
│   ├── __init__.py                    # HydraDBMemoryProvider (284 lines)
│   ├── plugin.yaml                    # Hermes plugin manifest
│   ├── README.md                      # User-facing setup guide
│   └── SPEC.md                        # Specification and roadmap
├── muninn-memory/                     # MuninnDB memory provider plugin
│   ├── __init__.py                    # MuninnDBMemoryProvider (384 lines)
│   └── plugin.yaml                    # Hermes plugin manifest
├── plugins/                           # In-tree plugin directory
│   └── context_engine/
│       ├── __init__.py                # (empty)
│       ├── hydradb-context/
│       │   ├── __init__.py            # HydraDBContextEngine (973 lines)
│       │   └── plugin.yaml            # Hermes plugin manifest
│       └── muninn-context/
│           ├── __init__.py            # MuninnDBContextEngine (1007 lines)
│           └── plugin.yaml            # Hermes plugin manifest
├── tests/                             # Test suites
│   ├── __init__.py
│   ├── plugins/
│   │   ├── __init__.py
│   │   ├── memory/
│   │   │   ├── __init__.py
│   │   │   ├── conftest.py
│   │   │   └── test_hydradb_provider.py
│   │   └── context_engine/
│   │       ├── __init__.py
│   │       ├── conftest.py            # FakeMemoryBackend fixture
│   │       ├── test_shared_infra.py
│   │       ├── test_context_config.py
│   │       ├── test_context_circuit_breaker.py
│   │       ├── test_context_lifecycle.py
│   │       ├── test_context_compress.py
│   │       └── test_context_tools.py
├── research/                          # Research and design documents
│   ├── hydradb-provider-design.md
│   ├── hydradb-v2-research.md
│   └── hermes-memory-provider-research.md
├── .planning/                         # Codebase analysis artifacts (GSD)
│   ├── PROJECT.md                     # Project overview and requirements
│   ├── ROADMAP.md                     # Development roadmap
│   ├── STATE.md                       # Current development state
│   ├── REQUIREMENTS.md                # Detailed requirements
│   ├── v1.0-COMPLETION.md             # Milestone completion summary
│   ├── v1.0-MILESTONE-AUDIT.md        # Milestone audit
│   ├── milestones/                    # Per-milestone planning
│   ├── phases/                        # Per-phase planning + summaries
│   ├── research/                      # GSD research outputs
│   └── codebase/                      # Codebase analysis (this directory)
│       ├── ARCHITECTURE.md
│       ├── CONVENTIONS.md
│       ├── CONCERNS.md
│       ├── INTEGRATIONS.md
│       ├── STACK.md
│       ├── STRUCTURE.md               # (this file)
│       └── TESTING.md
├── pyproject.toml                     # Build config, deps, package layout
├── README.md                          # Project overview
├── HERMES.md                          # Compiled project context (from .planning/ sources)
└── .venv/                             # Python virtual environment (gitignored)
```

## Directory Purposes

**cos_mcp/:** Shared infrastructure package — provides base classes, backends, formatters, and circuit breaker used by all plugins. Installed via `pip install -e .` or copied in-tree.

**hydradb-memory/:** Thin HydraDB memory provider — extends BaseMemoryProvider with HydraDB-specific config, tool schemas, and handlers. ~284 lines.

**muninn-memory/:** Thin MuninnDB memory provider — extends BaseMemoryProvider with MuninnDB-specific config, tool schemas (12 memory types), and handlers. ~384 lines.

**plugins/context_engine/:** Thin context engine plugins — extend BaseContextEngine with full compress() pipeline, entity extraction, and tool schemas/handlers.

**tests/:** Pytest test suites — 115 tests across 7 modules, zero failures. Uses `FakeMemoryBackend` for all backend operations.

**research/:** Reference materials and design documents that informed implementation. v1.0-era docs; may not reflect current multi-provider architecture.

**.planning/:** GSD codebase analysis artifacts — source of truth for PROJECT.md, STACK.md, ARCHITECTURE.md, etc. These feed into HERMES.md.

**.venv/:** Python virtual environment with installed dependencies (gitignored).

## Key File Locations

**Entry Points:**
- `cos_mcp/__init__.py` → Package exports: `CircuitBreaker`, `MemoryBackend`, `HydraDBBackend`, `MuninnDBBackend`, formatters, `BaseContextEngine`
- `hydradb-memory/__init__.py` → `register(ctx)`: Registers `HydraDBMemoryProvider`
- `muninn-memory/__init__.py` → `register(ctx)`: Registers `MuninnDBMemoryProvider`
- `plugins/context_engine/hydradb-context/__init__.py` → `register(ctx)`: Registers `HydraDBContextEngine`
- `plugins/context_engine/muninn-context/__init__.py` → `register(ctx)`: Registers `MuninnDBContextEngine`
- `*.plugin.yaml`: Hermes plugin manifests

**Build:**
- `pyproject.toml`: setuptools build config, project metadata, optional dependency groups

**Configuration (external, at runtime):**
- `~/.hermes/.env`: API keys (`HYDRA_DB_API_KEY`, `MUNINN_API_KEY`)
- `~/.hermes/hydradb.json`: HydraDB non-secret config (provider)
- `~/.hermes/muninn.json`: MuninnDB non-secret config (provider)
- `~/.hermes/hydradb-context.json`: HydraDB context engine non-secret config
- `~/.hermes/muninn-context.json`: MuninnDB context engine non-secret config

**Documentation:**
- `README.md` — Project-level overview and quick start
- `hydradb-memory/README.md` — User-facing setup and usage guide for HydraDB provider
- `hydradb-memory/SPEC.md` — Specification, roadmap, constraints (may be stale)
- `HERMES.md` — Compiled project context for AI agents (auto-generated from .planning/ sources)

## Naming Conventions

**Files:**
- `snake_case` for all Python files.
- `base_*.py` for abstract base classes.
- `__init__.py`: Package entry points; plugins contain full implementation.
- `plugin.yaml`: Hermes plugin manifest.
- `*.md`: Documentation (kebab-case or UPPERCASE for important docs).

**Directories:**
- `cos_mcp/`: snake_case with underscore separator.
- `hydradb-memory/`, `muninn-memory/`: kebab-case — plugin package names.
- `plugins/context_engine/<name>-context/`: kebab-case with hyphen suffix.
- `research/`, `tests/`: singular noun.
- `.planning/`, `.venv/`: dot-prefixed for hidden/infrastructure.

**Classes:**
- `HydraDBMemoryProvider`, `MuninnDBContextEngine`: PascalCase, provider/engine suffix.
- `BaseMemoryProvider`, `BaseContextEngine`: PascalCase, Base prefix.
- `HydraDBBackend`, `MuninnDBFormatter`: PascalCase, Backend/Formatter suffix.

**Tool Prefixes:**
- HydraDB memory: `hydradb_search`, `hydradb_profile`, `hydradb_conclude`
- HydraDB context: `hydradb_context_search`, `hydradb_context_expand`
- MuninnDB memory: `muninn_search`, `muninn_profile`, `muninn_remember`
- MuninnDB context: `muninn_context_search`, `muninn_context_expand`

## Where to Add New Code

**New Backend:**
- Add `cos_mcp/backends/<name>.py` implementing `MemoryBackend` ABC.
- Add `cos_mcp/formatting/<name>.py` implementing `MemoryFormatter` ABC.
- Add `cos_mcp/formatting/<name>_context.py` implementing `ContextFormatter` ABC.
- Export from `cos_mcp/__init__.py`.

**New Memory Provider:**
- Create `<name>-memory/__init__.py` extending `BaseMemoryProvider`.
- Create `<name>-memory/plugin.yaml` with deps and hooks.
- Only define: `name`, config, `_create_backend()`, `_create_formatter()`, `is_available()`, tool schemas, tool handlers, `system_prompt_block()`, `get_config_schema()`, `save_config()`.

**New Context Engine:**
- Create `plugins/context_engine/<name>-context/__init__.py` extending `BaseContextEngine`.
- Create `plugins/context_engine/<name>-context/plugin.yaml`.
- Only define: `name`, config, `_create_backend()`, `_create_formatter()`, `is_available()`, `compress()`, entity extraction, tool schemas, tool handlers.

**Tests:**
- Add `tests/plugins/<category>/test_<name>.py` with `FakeMemoryBackend`.
- Follow patterns in `tests/plugins/context_engine/conftest.py`.

---

*Structure analysis: 2026-06-20*
*Update when directory structure changes*
