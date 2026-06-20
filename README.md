# cos-mcp

Memory provider plugins for Hermes Agent — persistent, cross-session memory with cognitive retrieval.

Two providers:

| Provider | Backend | Cognitive Features | Deployment |
|---|---|---|---|
| **hydradb** | HydraDB Cloud (managed graph DB) | Hybrid search (BM25 + vectors + knowledge graph), `infer` auto-extraction | Cloud API, zero ops |
| **muninn** | MuninnDB (local cognitive DB) | ACT-R temporal scoring, Hebbian learning, Bayesian confidence, 16 typed relationships, PAS — all engine-native | Local binary, single command |

## Quick Start

### HydraDB (Cloud)

```bash
# Copy plugin in-tree
cp -r hydradb-memory/ ~/.hermes/hermes-agent/plugins/memory/hydradb/

# Set API key
echo 'HYDRA_DB_API_KEY=sk_live_...' >> ~/.hermes/.env

# Activate
hermes memory setup hydradb
```

### MuninnDB (Local)

```bash
# Install and start MuninnDB
curl -sSL https://muninndb.com/install.sh | sh
muninn start

# Copy plugin in-tree
cp -r muninn-memory/ ~/.hermes/hermes-agent/plugins/memory/muninn/

# Activate (no API key needed for default vault)
hermes memory setup muninn
```

## Files

```
hydradb-memory/          # HydraDB provider (735 lines)
  __init__.py            # HydraDBMemoryProvider(MemoryProvider)
  plugin.yaml            # Manifest: hydradb-sdk>=2,<3

muninn-memory/           # MuninnDB provider (760 lines)
  __init__.py            # MuninnDBMemoryProvider(MemoryProvider)
  plugin.yaml            # Manifest: requests>=2.31

research/                # Architecture docs & API reference
  hydradb-provider-design.{md,html}
  hydradb-v2-research.md
  hermes-memory-provider-research.md
```

## Choosing a Provider

**Use HydraDB if you want:** zero local infrastructure, cloud-managed graph search, free tier ($0/mo with unlimited API calls).

**Use MuninnDB if you want:** cognitive primitives in the storage engine (temporal decay based on actual access patterns, auto-association through co-activation, confidence tracking, contradiction detection), offline capability, no API dependency.

Both implement the same `MemoryProvider` ABC — swap by changing `memory.provider` in `~/.hermes/config.yaml`.
