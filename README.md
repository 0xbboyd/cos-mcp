# cos-mcp

Shared infrastructure for Hermes Agent plugins — memory providers and context
engines backed by HydraDB Cloud and MuninnDB (local cognitive DB).

## Architecture (v0.2.0)

```
cos_mcp/                               # Shared infrastructure package
├── circuit_breaker.py                 # Dual-gauge circuit breaker
├── base_provider.py                   # BaseMemoryProvider (MemoryProvider ABC)
├── base_context_engine.py             # BaseContextEngine (ContextEngine ABC)
├── backends/
│   ├── base.py                        # MemoryBackend ABC
│   ├── hydradb.py                     # HydraDBBackend
│   └── muninn.py                      # MuninnDBBackend
└── formatting/
    ├── base.py                        # MemoryFormatter ABC
    ├── context_base.py                # ContextFormatter ABC
    ├── hydradb.py                     # HydraDBFormatter
    ├── hydradb_context.py             # HydraDBContextFormatter
    ├── muninn.py                      # MuninnDBFormatter
    └── muninn_context.py              # MuninnDBContextFormatter

hydradb-memory/                        # Thin HydraDB memory provider (~297 lines)
muninn-memory/                         # Thin MuninnDB memory provider (~384 lines)
plugins/context_engine/
├── hydradb-context/                   # Thin HydraDB context engine (~973 lines)
└── muninn-context/                    # Thin MuninnDB context engine (~1007 lines)
```

Four thin plugins share one infrastructure package. Plugins define only
backend-specific config, tool schemas, tool handlers, and system prompt text.
All shared code (circuit breaker, threading, config loading, read/write paths,
token tracking, compression gating) lives in `cos_mcp`.

## Plugins at a Glance

| Plugin | Type | Backend | Key Features |
|--------|------|---------|-------------|
| **hydradb** | Memory Provider | HydraDB Cloud | Graph-enriched hybrid search, auto-fact-extraction, free tier |
| **muninn** | Memory Provider | MuninnDB Local | ACT-R decay, Hebbian learning, Bayesian confidence, 12 memory types |
| **hydradb-context** | Context Engine | HydraDB Cloud | Graph-backed compression, multi-hop traversal, retrievable summaries |
| **muninn-context** | Context Engine | MuninnDB Local | Cognitive compression, 16 relationship types, confidence-gated retrieval |

All plugins implement the same Hermes Agent ABCs — swap by changing one config
value.

---

## Install — HydraDB Memory Provider

Requires a HydraDB Cloud account. Sign up at [app.hydradb.com](https://app.hydradb.com)
for a free API key.

### 1. Install shared package and SDK

```bash
cd /path/to/cos-mcp
pip install -e .
pip install hydradb-sdk
```

### 2. Set your API key

```bash
echo 'HYDRA_DB_API_KEY=sk_live_YOUR_KEY' >> ~/.hermes/.env
```

### 3. Install the plugin in-tree

```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory
cp -r hydradb-memory/ ~/.hermes/hermes-agent/plugins/memory/hydradb/
```

### 4. Activate the provider

```bash
hermes config set memory.provider hydradb
```

Or edit `~/.hermes/config.yaml`:

```yaml
memory:
  provider: hydradb
```

### 5. Configure (optional)

Create `~/.hermes/hydradb.json` to override defaults:

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
| `max_results` | `10` | Max results per prefetch query |

### 6. Verify

```bash
# Check provider is available
hermes doctor

# Start a session and test memory tools
hermes chat -q "Search my memory for any preferences I've mentioned"
```

The model should have access to `hydradb_search`, `hydradb_profile`, and
`hydradb_conclude` tools. On first run, the provider provisions a HydraDB
tenant (up to 5 minutes). Subsequent sessions connect instantly.

---

## Install — MuninnDB Memory Provider

Requires a running MuninnDB instance. No cloud account needed.

### 1. Install shared package and dependencies

```bash
cd /path/to/cos-mcp
pip install -e .
pip install requests
```

### 2. Install and start MuninnDB

```bash
# Install MuninnDB
curl -sSL https://muninndb.com/install.sh | sh

# Create a vault and start the server
muninn init --vault default
muninn start
# Listening on http://127.0.0.1:8475
```

### 3. Set API key (if your vault requires auth)

```bash
echo 'MUNINN_API_KEY=your_key' >> ~/.hermes/.env
```

Skip this step if using the default vault without authentication.

### 4. Install the plugin in-tree

```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory
cp -r muninn-memory/ ~/.hermes/hermes-agent/plugins/memory/muninn/
```

### 5. Activate the provider

```bash
hermes config set memory.provider muninn
```

Or edit `~/.hermes/config.yaml`:

```yaml
memory:
  provider: muninn
```

### 6. Configure (optional)

Create `~/.hermes/muninn.json`:

```json
{
  "base_url": "http://127.0.0.1:8475",
  "vault": "default"
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `base_url` | `http://127.0.0.1:8475` | MuninnDB server address |
| `vault` | `default` | Per-profile vault for isolation |

### 7. Verify

```bash
# Confirm MuninnDB is reachable
curl http://127.0.0.1:8475/api/health
# → {"status":"ok"}

# Check provider is available
hermes doctor

# Test memory tools
hermes chat -q "Search my memory for any facts I've stored"
```

The model should have access to `muninn_search`, `muninn_profile`, and
`muninn_remember` tools.

---

## Install — HydraDB Context Engine

Replaces the built-in lossy context compressor with graph-backed persistent
context. Requires the same HydraDB Cloud account as the memory provider.

### 1. Install shared package and SDK

```bash
cd /path/to/cos-mcp
pip install -e .
pip install hydradb-sdk
```

Skip if already installed for the memory provider.

### 2. Set your API key

```bash
echo 'HYDRA_DB_API_KEY=sk_live_YOUR_KEY' >> ~/.hermes/.env
```

Skip if already set for the memory provider.

### 3. Install the plugin in-tree

```bash
mkdir -p ~/.hermes/hermes-agent/plugins/context_engine
cp -r plugins/context_engine/hydradb-context/ ~/.hermes/hermes-agent/plugins/context_engine/hydradb-context/
```

### 4. Activate the engine

```bash
hermes config set compression.provider hydradb-context
```

Or edit `~/.hermes/config.yaml`:

```yaml
compression:
  provider: hydradb-context
```

### 5. Configure (optional)

Create `~/.hermes/hydradb-context.json`:

```json
{
  "tenant_id": "hermes",
  "sub_tenant_id": "",
  "query_mode": "thinking",
  "entity_extraction_mode": "balanced",
  "entity_per_message_cap": 3,
  "threshold_percent": 0.75
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `tenant_id` | `hermes` | HydraDB tenant, shared with memory provider |
| `sub_tenant_id` | `""` (auto = profile) | Per-profile isolation |
| `query_mode` | `thinking` | Query mode for context search/expand |
| `entity_extraction_mode` | `balanced` | `conservative` (fewer, higher confidence), `balanced`, or `aggressive` (more entities, lower confidence) |
| `entity_per_message_cap` | `3` | Max entities extracted per message |
| `threshold_percent` | `0.75` | Compress when prompt tokens > 75% of context window |
| `protect_first_n` | `3` | Messages at start of conversation never compressed |
| `protect_last_n` | `6` | Messages at end of conversation never compressed |

### 6. Verify

```bash
# Check engine is available
hermes doctor

# Start a long conversation — compression should fire when threshold is hit
hermes chat -q "Tell me about yourself, then let's have a long conversation about AI"
```

The model should have access to `hydradb_context_search` and
`hydradb_context_expand` tools. Compression fires automatically when prompt
tokens exceed the threshold (default 75% of context window). Use
`hydradb_context_search` to retrieve compressed context; use
`hydradb_context_expand` with a `[ctx-id: ...]` anchor to expand a specific
compression point.

---

## Install — MuninnDB Context Engine

Cognitive context compression using MuninnDB's neuroscience-inspired engine.
Requires a running MuninnDB instance.

### 1. Install shared package and dependencies

```bash
cd /path/to/cos-mcp
pip install -e .
pip install requests
```

Skip if already installed for the MuninnDB memory provider.

### 2. Ensure MuninnDB is running

```bash
# If not already running:
muninn start

# Verify
curl http://127.0.0.1:8475/api/health
# → {"status":"ok"}
```

### 3. Set API key

```bash
echo 'MUNINN_API_KEY=your_key' >> ~/.hermes/.env
```

Required for the context engine (unlike the memory provider where it's optional).

### 4. Install the plugin in-tree

```bash
mkdir -p ~/.hermes/hermes-agent/plugins/context_engine
cp -r plugins/context_engine/muninn-context/ ~/.hermes/hermes-agent/plugins/context_engine/muninn-context/
```

### 5. Activate the engine

```bash
hermes config set compression.provider muninn-context
```

Or edit `~/.hermes/config.yaml`:

```yaml
compression:
  provider: muninn-context
```

### 6. Configure (optional)

Create `~/.hermes/muninn-context.json`:

```json
{
  "base_url": "http://127.0.0.1:8475",
  "vault": "default",
  "entity_extraction_mode": "balanced",
  "entity_per_message_cap": 3,
  "threshold_percent": 0.75
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `base_url` | `http://127.0.0.1:8475` | MuninnDB server address |
| `vault` | `default` | Per-profile vault for isolation |
| `entity_extraction_mode` | `balanced` | `conservative`, `balanced`, or `aggressive` |
| `entity_per_message_cap` | `3` | Max entities extracted per message |
| `threshold_percent` | `0.75` | Compress when prompt tokens > 75% of context window |
| `protect_first_n` | `3` | Messages at start never compressed |
| `protect_last_n` | `6` | Messages at end never compressed |

### 7. Verify

```bash
# Confirm MuninnDB is reachable
curl http://127.0.0.1:8475/api/health

# Check engine is available
hermes doctor

# Test compression
hermes chat -q "Let's discuss several complex topics and see how context is managed"
```

The model should have access to `muninn_context_search` and
`muninn_context_expand` tools. Compression fires automatically. Use
`muninn_context_search` with optional `min_confidence` parameter to gate
results by Bayesian confidence; use `muninn_context_expand` to retrieve
activation chains for a specific ctx-id.

---

## Using Both Memory + Context Together

The memory provider and context engine are independent. You can mix and match
backends:

```yaml
# HydraDB memory + MuninnDB context
memory:
  provider: hydradb
compression:
  provider: muninn-context
```

Or same backend for both:

```yaml
# All HydraDB
memory:
  provider: hydradb
compression:
  provider: hydradb-context
```

Data is segregated — memory entries use `type=memory` / `hermes-memory` tag;
context entries use `type=context` / `hermes-context` tag. They never pollute
each other's results.

## Development

```bash
# Install in dev mode with all optional deps
pip install -e ".[hydradb,muninn]"

# Install test deps
pip install pytest pytest-cov

# Run tests
python3 -m pytest tests/ -v

# Context engine tests only
python3 -m pytest tests/plugins/context_engine/ -v

# Memory provider tests only
python3 -m pytest tests/plugins/memory/ -v
```

115 tests across 7 modules. Uses `FakeMemoryBackend` — no live API calls.
Context engine tests: 115 passed, 0 failures.

## Choosing a Provider / Engine

| Consideration | HydraDB | MuninnDB |
|---------------|---------|----------|
| Infrastructure | Cloud-managed, zero ops | Local binary, single command |
| Cost | Free tier ($0/mo, unlimited API) | Free (local) |
| Latency | ~2s query, ~500ms ingest | Sub-millisecond (local) |
| Search | BM25 + vectors + knowledge graph | ACT-R temporal + Hebbian + Bayesian |
| Offline | No (cloud API required) | Yes (fully local) |
| Cognitive features | Graph traversal, DAG paths | Temporal decay, auto-association, confidence, PAS |
| Entity extraction | 4 types (topic, decision, fact, relationship) | 4 types + 16 relationship subtypes |
| Best for | Zero-maintenance cloud memory | Neuroscience-inspired local cognition |

All plugins implement the same ABCs — change `memory.provider` or
`compression.provider` in `~/.hermes/config.yaml` to swap. Restart Hermes
after changing providers.
