# HydraDB Memory Provider for Hermes Agent

HydraDB-backed persistent memory — graph-enriched recall and personalized
context via the HydraDB v2 API. One HydraDB tenant shared across all your
Hermes profiles; optional per-profile isolation via sub-tenants.

## Setup

### 1. Get an API key

Sign up at [app.hydradb.com](https://app.hydradb.com), create a tenant,
and copy your API key.

### 2. Install the plugin

```bash
# Copy into the in-tree plugins directory
cp -r hydradb-memory/ ~/.hermes/hermes-agent/plugins/memory/hydradb/

# Or symlink (for development)
ln -s $(pwd)/hydradb-memory ~/.hermes/hermes-agent/plugins/memory/hydradb
```

### 3. Set your API key

```bash
echo 'HYDRA_DB_API_KEY=sk_live_...' >> ~/.hermes/.env
```

### 4. Activate the provider

In `~/.hermes/config.yaml`:

```yaml
memory:
  provider: hydradb
```

### 5. Configure (optional)

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

See `research/hydradb-provider-design.md` for the full blueprint.

## Requirements

- Python 3.11+
- `hydradb-sdk>=2,<3`
- `HYDRA_DB_API_KEY` environment variable
