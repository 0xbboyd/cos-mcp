# HydraDB Context Engine for Hermes Agent

Graph-backed context compression and retrieval — replaces the built-in lossy
LLM context summarizer with persistent, retrievable graph memory.

## Overview

When conversation context exceeds the model's token budget, Hermes compresses
older messages into a summary. The built-in compressor uses LLM summarization,
which is lossy and non-retrievable — once summarized, detail is gone forever.

The HydraDB Context Engine replaces this with:

1. **Entity extraction** — Pure Python heuristics extract topics, decisions,
   facts, and relationships from the message window (no LLM calls).
2. **Graph storage** — Entities are written to HydraDB's knowledge graph with
   stable ctx-id anchors, creating persistent DAG-compressed memory.
3. **Retrievable compression** — The model can later search or expand any
   compression point using `hydradb_context_search` and
   `hydradb_context_expand` tools.

## Setup

### 1. Install dependencies

```bash
pip install cos-mcp hydradb-sdk
```

### 2. Set your API key

```bash
echo 'HYDRA_DB_API_KEY=sk_live_...' >> ~/.hermes/.env
```

### 3. Install the plugin

```bash
cp -r plugins/context_engine/hydradb-context/ ~/.hermes/hermes-agent/plugins/context_engine/hydradb-context/
```

### 4. Activate

In `~/.hermes/config.yaml`:

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
| `query_mode` | `thinking` | `thinking` (reranking) or `fast` |
| `entity_extraction_mode` | `balanced` | `conservative`, `balanced`, or `aggressive` |
| `entity_per_message_cap` | `3` | Max entities extracted per message |
| `threshold_percent` | `0.75` | Compress when prompt > 75% of context window |

## How It Works

The engine extends `cos_mcp.BaseContextEngine` which handles:
- Circuit breaker (dual read/write gauges, 3 failures → 120s cooldown)
- Token tracking (dual-format: canonical + legacy keys)
- `should_compress()` gate (fires when prompt tokens ≥ threshold)
- Model switch handling
- Session lifecycle (`on_session_start`, `on_session_end`, `on_session_reset`)

The thin plugin (~973 lines) adds:

**compress() pipeline:**
1. **Guards** — Minimum message count, window boundaries, token threshold
2. **Entity extraction** — Pure Python heuristics per message:
   - Topics: capitalized phrases + quoted phrases with frequency scoring
   - Decisions: marker-word matching ("decided", "chose", "settled on")
   - Facts: copula detection ("is", "are", "uses", "provides")
   - Relationships: verb pattern matching ("depends on", "requires")
3. **Fire-and-forget graph ingest** — Entities written to HydraDB on daemon
   thread, tagged `type=context` for data segregation. Stable ctx-id anchors
   generated from content hash.
4. **Summary block assembly** — Formatted entity list inserted as system
   message, replacing the compressed window. Includes `[ctx-id: ...]`
   anchors for later retrieval.
5. **Hard guard** — Never returns a list that isn't shorter than the input.

**Per-message cap** and **global trigram Jaccard dedup** prevent entity
explosion on long conversations.

## Tools

Two tools are exposed to the model:

| Tool | Description |
|------|-------------|
| `hydradb_context_search` | Search compressed context for topics, decisions, facts, and relationships. Returns graph-annotated prose with ctx-id anchors, hop depth, and relationship edges. |
| `hydradb_context_expand` | Retrieve full context entities for a specific ctx-id anchor. Supports multi-hop graph traversal with hierarchical indentation and DAG path information. |

## Architecture

Shared infrastructure in `cos_mcp/`:
- `cos_mcp/base_context_engine.py` — BaseContextEngine (token tracking, compression gate, lifecycle)
- `cos_mcp/backends/hydradb.py` — HydraDBBackend (SDK wrapper, tenant provisioning)
- `cos_mcp/formatting/hydradb_context.py` — HydraDBContextFormatter (graph-aware formatting)
- `cos_mcp/circuit_breaker.py` — Dual-gauge circuit breaker

Thin plugin at `plugins/context_engine/hydradb-context/__init__.py` (~973 lines):
- Entity extraction (pure Python heuristics)
- compress() pipeline assembly
- Tool schemas + handlers
- Config loading

## Data Segregation

Context entities use `type=context` in HydraDB — separate from memory
provider entries which use `type=memory`. This prevents context compression
data from polluting memory search results and vice versa.

## Requirements

- Python 3.12+
- `cos_mcp` package (shared infrastructure)
- `hydradb-sdk>=2,<3`
- `HYDRA_DB_API_KEY` environment variable
