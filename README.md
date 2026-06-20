# cos-mcp

Memory provider plugins for Hermes Agent — persistent, cross-session memory with cognitive retrieval.

## Architecture (v0.2.0)

Shared infrastructure extracted into the `cos_mcp` package:

```
cos_mcp/                    # Shared package
  circuit_breaker.py        # Dual-gauge circuit breaker (read/write)
  base_provider.py          # BaseMemoryProvider — threading, config, lifecycle
  backends/
    base.py                 # MemoryBackend ABC
    hydradb.py              # HydraDBBackend (SDK wrapper)
    muninn.py               # MuninnDBBackend (REST API wrapper)
  formatting/
    base.py                 # MemoryFormatter ABC
    hydradb.py              # HydraDB chunk extractor
    muninn.py               # MuninnDB activation formatter

hydradb-memory/             # Thin HydraDB provider (~280 lines)
  __init__.py
  plugin.yaml

muninn-memory/              # Thin MuninnDB provider (~400 lines)
  __init__.py
  plugin.yaml
```

Each provider is now a thin adapter — backend-specific tool schemas, handlers,
config, and system prompt block. All shared infrastructure (circuit breaker,
threading, config loading pattern, read/write path) lives in `cos_mcp`.

## Providers

| Provider | Backend | Cognitive Features | Deployment |
|---|---|---|---|
| **hydradb** | HydraDB Cloud (managed graph DB) | Hybrid search (BM25 + vectors + knowledge graph), `infer` auto-extraction | Cloud API, zero ops |
| **muninn** | MuninnDB (local cognitive DB) | ACT-R temporal scoring, Hebbian learning, Bayesian confidence, 16 typed relationships, PAS — all engine-native | Local binary, single command |

## Quick Start

### HydraDB (Cloud)

```bash
# Install shared package
pip install cos-mcp hydradb-sdk

# Copy plugin in-tree
cp -r hydradb-memory/ ~/.hermes/hermes-agent/plugins/memory/hydradb/

# Set API key
echo 'HYDRA_DB_API_KEY=sk_live_...' >> ~/.hermes/.env

# Activate
hermes memory setup hydradb
```

### MuninnDB (Local)

```bash
# Install shared package
pip install cos-mcp requests

# Install and start MuninnDB
curl -sSL https://muninndb.com/install.sh | sh
muninn start

# Copy plugin in-tree
cp -r muninn-memory/ ~/.hermes/hermes-agent/plugins/memory/muninn/

# Activate (no API key needed for default vault)
hermes memory setup muninn
```

## Development

```bash
# Install in dev mode
pip install -e .

# Run tests (future)
python -m pytest
```

## Choosing a Provider

**Use HydraDB if you want:** zero local infrastructure, cloud-managed graph search, free tier ($0/mo with unlimited API calls).

**Use MuninnDB if you want:** cognitive primitives in the storage engine (temporal decay based on actual access patterns, auto-association through co-activation, confidence tracking, contradiction detection), offline capability, no API dependency.

Both implement the same `MemoryProvider` ABC — swap by changing `memory.provider` in `~/.hermes/config.yaml`.
