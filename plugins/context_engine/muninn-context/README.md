# MuninnDB Context Engine for Hermes Agent

Cognitive-backed context compression and retrieval — neuroscience-inspired
context management using MuninnDB's ACT-R temporal decay, Hebbian
co-activation, and Bayesian confidence.

## Overview

When conversation context exceeds the model's token budget, Hermes compresses
older messages into a summary. The built-in compressor uses LLM summarization,
which is lossy and non-retrievable — once summarized, detail is gone forever.

The MuninnDB Context Engine replaces this with cognitive memory primitives:

1. **Entity extraction** — Pure Python heuristics extract topics, decisions,
   facts, and 16 relationship types from the message window (no LLM calls).
2. **Engram storage** — Entities are written to MuninnDB as cognitive engrams,
   typed and tagged for auto-association. Synchronous (local API, no daemon
   threads).
3. **Retrievable compression** — The model can search or expand any compression
   point using `muninn_context_search` and `muninn_context_expand` tools,
   gated by Bayesian confidence.

## Cognitive Features (Engine-Native)

All cognition happens in MuninnDB's engine — the plugin is a thin adapter:

| Feature | Mechanism | Effect |
|---------|-----------|--------|
| **ACT-R temporal decay** | Frequently accessed context stays strong; stale context fades | Natural forgetting — the engine prunes unused context automatically |
| **Hebbian co-activation** | Context used together auto-associates | Related topics cluster without manual linking |
| **Bayesian confidence** | Contradicted context is discounted | Retrieval quality improves over time as contradictions are resolved |
| **PAS (Predictive Activation)** | Expected-upcoming context is pre-activated | Context relevant to current topic surfaces before explicit query |

## Setup

### 1. Install dependencies

```bash
pip install cos-mcp requests
```

### 2. Install and start MuninnDB

```bash
curl -sSL https://muninndb.com/install.sh | sh
muninn start
# Default: http://127.0.0.1:8475
```

### 3. Set your API key

```bash
echo 'MUNINN_API_KEY=*** >> ~/.hermes/.env
```

### 4. Install the plugin

```bash
cp -r plugins/context_engine/muninn-context/ ~/.hermes/hermes-agent/plugins/context_engine/muninn-context/
```

### 5. Activate

In `~/.hermes/config.yaml`:

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
| `threshold_percent` | `0.75` | Compress when prompt > 75% of context window |

## How It Works

The engine extends `cos_mcp.BaseContextEngine` which handles:
- Circuit breaker (dual read/write gauges, 3 failures → 120s cooldown)
- Token tracking (dual-format: canonical + legacy keys)
- `should_compress()` gate (fires when prompt tokens ≥ threshold)
- Model switch handling
- Session lifecycle (`on_session_start` with health check, `on_session_end`, `on_session_reset`)

The thin plugin (~1007 lines) adds:

**compress() pipeline:**
1. **Guards** — Minimum message count, window boundaries, token threshold
2. **Entity extraction** — Pure Python cognitive heuristics per message:
   - Topics: capitalized phrases + quoted phrases with frequency scoring
   - Decisions: marker-word matching ("decided", "chose", "settled on")
   - Facts: copula detection ("is", "are", "uses", "provides")
   - Relationships: **16 relationship types** from MuninnDB's cognitive
     taxonomy (depends on, built with, uses, runs on, part of, connects to,
     requires, implements, precedes, follows, contradicts, supports,
     replaces, contains, references, extends)
3. **Synchronous engram storage** — Entities written directly to MuninnDB
   via POST /api/engrams (local, no daemon threads). Typed and tagged for
   auto-association. `hermes-context` tag for data segregation.
4. **Summary block assembly** — Formatted entity list inserted as system
   message, replacing the compressed window. Includes `[ctx-id: ...]`
   anchors and confidence annotations for low-confidence items.
5. **Hard guard** — Never returns a list that isn't shorter than the input.

**Key differences from HydraDB context engine:**
- Synchronous storage (local REST API, no daemon threads)
- 16 relationship types for cognitive entity classification
- Bayesian confidence gates on retrieval
- `on_session_start()` performs health check via backend
- Broader relationship detection with confidence modifiers per type

## Tools

Two tools are exposed to the model:

| Tool | Description |
|------|-------------|
| `muninn_context_search` | Search compressed context for topics, decisions, facts, and relationships. Results are Bayesian confidence-gated — low-confidence engrams are excluded. Supports optional `min_confidence` parameter (0.0–1.0). |
| `muninn_context_expand` | Retrieve full context entities for a specific ctx-id anchor or topic. Returns activation chains with hop path information, confidence scores, and memory type labels. Low-confidence engrams (< 0.3) are excluded. |

## Architecture

Shared infrastructure in `cos_mcp/`:
- `cos_mcp/base_context_engine.py` — BaseContextEngine (token tracking, compression gate, lifecycle)
- `cos_mcp/backends/muninn.py` — MuninnDBBackend (REST API wrapper)
- `cos_mcp/formatting/muninn_context.py` — MuninnDBContextFormatter (cognitive annotations)
- `cos_mcp/circuit_breaker.py` — Dual-gauge circuit breaker

Thin plugin at `plugins/context_engine/muninn-context/__init__.py` (~1007 lines):
- Entity extraction with 16 relationship types
- compress() pipeline assembly (synchronous)
- Tool schemas + handlers with Bayesian confidence gating
- Config loading

## Data Segregation

Context engrams are tagged `hermes-context` — separate from memory provider
engrams which use `hermes-memory`. This prevents context compression data
from polluting memory search results.

## Choosing Between Context Engines

**Use HydraDB Context if you want:** zero local infrastructure, cloud-managed
graph search, multi-hop graph traversal with DAG path information.

**Use MuninnDB Context if you want:** cognitive primitives in the storage
engine (temporal decay, auto-association, confidence tracking), offline
capability, no cloud API dependency.

Both implement the same `ContextEngine` ABC — swap by changing
`compression.provider` in `~/.hermes/config.yaml`.

## Requirements

- Python 3.11+
- `cos_mcp` package (shared infrastructure)
- `requests>=2.31`
- Running MuninnDB server at `http://127.0.0.1:8475`
- `MUNINN_API_KEY` environment variable
