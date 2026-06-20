# cos-mcp

HydraDB Memory Provider for Hermes Agent — a cloud-backed persistent memory plugin that replaces Hermes' ephemeral file-based memory with graph-enriched semantic retrieval, shared across all profiles.

## What It Does

- **Persistent cross-session memory** — conversations and facts survive restarts
- **Semantic + graph retrieval** — HydraDB's hybrid search (BM25 + vectors + knowledge graph)
- **Auto-fact-extraction** — `infer=true` extracts durable facts from conversations
- **Three agent tools** — `hydradb_search`, `hydradb_profile`, `hydradb_conclude`
- **Per-profile isolation** — one HydraDB tenant, sub-tenants scoped to each Hermes profile
- **Resilience** — independent read/write circuit breakers, fire-and-forget daemon threads

## Files

```
hydradb-memory/          # Provider plugin (735-line Python class)
  __init__.py            # HydraDBMemoryProvider(MemoryProvider)
  plugin.yaml            # Manifest: name, deps, hooks
  README.md              # Setup instructions

research/                # Architecture docs & API reference
  hydradb-provider-design.{md,html}
  hydradb-v2-research.md
  hermes-memory-provider-research.md

tests/                   # 65-unit test suite (fake client, zero live API calls)
  plugins/memory/test_hydradb_provider.py
  plugins/memory/conftest.py
```

## Quick Start

```bash
# Provider is deployed in-tree:
ls ~/.hermes/hermes-agent/plugins/memory/hydradb/

# Activate:
hermes memory setup hydradb

# Verify:
hermes doctor
```

Requires `HYDRA_DB_API_KEY` in `~/.hermes/.env` and `hydradb-sdk>=2,<3` (installed automatically by `hermes memory setup`).
