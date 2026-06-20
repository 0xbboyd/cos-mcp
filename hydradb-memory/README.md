# HydraDB Memory Provider for Hermes Agent

HydraDB-backed persistent memory — graph-enriched recall and personalized
context via the HydraDB v2 API. One HydraDB tenant shared across all your
Hermes profiles; optional per-profile isolation via sub-tenants.

Thin provider (~284 lines) extending `cos_mcp.BaseMemoryProvider`. All shared
infrastructure (circuit breaker, threading, config loading) lives in the
`cos_mcp` package.

## Setup

### 1. Install shared package

```bash
pip install -e /path/to/cos-mcp
pip install hydradb-sdk
```

### 2. Get an API key

Sign up at [app.hydradb.com](https://app.hydradb.com), create a tenant,
and copy your API key.

### 3. Install the plugin

```bash
# Copy into the in-tree plugins directory
cp -r hydradb-memory/ ~/.hermes/hermes-agent/plugins/memory/hydradb/

# Or symlink (for development)
ln -s $(pwd)/hydradb-memory ~/.hermes/hermes-agent/plugins/memory/hydradb
```

### 4. Set your API key

```bash
echo 'HYDRA_DB_API_KEY=sk_live_...' >> ~/.hermes/.env
```

### 5. Activate the provider

In `~/.hermes/config.yaml`:

```yaml
memory:
  provider: hydradb
```

### 6. Configure (optional)

Run `hermes memory setup` or create `~/.hermes/hydradb.json`:

```json
{
  "tenant_id": "hermes",
  "sub_tenant_id": "",
  "query_mode": "thinking"
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `tenant_id` | `hermes` | One per deployment, shared by all profiles |
| `sub_tenant_id` | `""` (auto = profile name) | Leave empty for per-profile isolation; set to `"shared"` for cross-profile memory |
| `query_mode` | `thinking` | `thinking` (reranking + graph traversal) or `fast` (lower latency) |

## How it works

The provider extends `cos_mcp.BaseMemoryProvider` which handles:
- Circuit breaker (dual read/write gauges)
- Daemon thread management (fire-and-forget writes)
- Config loading pattern
- Prefetch / cache model for reads
- Session lifecycle hooks

The thin provider (~284 lines) only defines HydraDB-specific config, tool
schemas, tool handlers, and system prompt text.

- **Every turn:** A background query fetches relevant memories before the
  model runs. Results are injected into the system prompt.
- **After every turn:** The user+assistant pair is ingested as a memory
  (`infer: true` — HydraDB extracts durable facts server-side).
- **On memory writes:** Built-in `memory add/replace/remove` operations
  are mirrored into HydraDB.
- **On session end:** A summary of the last few messages is ingested as
  an episodic memory.

## Tools

Three tools are exposed to the model:

| Tool | Description |
|------|-------------|
| `hydradb_search` | Search memory for relevant facts, preferences, and past context |
| `hydradb_profile` | Retrieve the user profile summary from memory |
| `hydradb_conclude` | Store a durable fact or conclusion |

## Pricing

HydraDB offers a **Ship (Free)** tier ($0/month) with unlimited API
calls and tenants. Paid tiers: Surge ($25/mo, 2GB storage) and Scale
($399/mo, 10GB storage). Pricing is storage-based — no per-call limits.

## Architecture

Shared infrastructure in `cos_mcp/`:
- `cos_mcp/backends/hydradb.py` — HydraDBBackend (SDK wrapper, tenant provisioning)
- `cos_mcp/formatting/hydradb.py` — HydraDBFormatter (chunk extraction)
- `cos_mcp/circuit_breaker.py` — Dual-gauge circuit breaker
- `cos_mcp/base_provider.py` — BaseMemoryProvider (threading, lifecycle)

Thin plugin at `hydradb-memory/__init__.py` (~284 lines):
- Config, tool schemas, tool handlers, system prompt block
- Subclass hooks: `_create_backend()`, `_create_formatter()`

Research: `research/hydradb-provider-design.md` (blueprint), `research/hydradb-v2-research.md` (API reference)

## Requirements

- Python 3.12+
- `cos_mcp` package (shared infrastructure)
- `hydradb-sdk>=2,<3`
