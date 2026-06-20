# MuninnDB Memory Provider for Hermes Agent

Local cognitive memory — neuroscience-inspired persistent memory using
MuninnDB's ACT-R temporal scoring, Hebbian co-activation, Bayesian
confidence, and 16 typed relationship types.

## Overview

A Hermes Agent memory provider plugin backed by MuninnDB — a local
cognitive database. All cognitive features are engine-native — the plugin
is a thin adapter to the REST API.

Thin provider (~384 lines) extending `cos_mcp.BaseMemoryProvider`. All shared
infrastructure (circuit breaker, threading, config loading) lives in the
`cos_mcp` package.

## Cognitive Features (Engine-Native)

| Feature | Mechanism | Effect |
|---------|-----------|--------|
| **ACT-R temporal decay** | Frequently accessed memories stay strong; stale memories fade | Natural forgetting — the engine prunes unused memories automatically |
| **Hebbian co-activation** | Memories used together auto-associate | Related facts cluster without manual linking |
| **Bayesian confidence** | Contradicted memories are discounted | Recall quality improves as contradictions are resolved |
| **PAS (Predictive Activation)** | Expected-upcoming memories are pre-activated | Relevant context surfaces before explicit query |
| **16 relationship types** | depends on, built with, uses, runs on, part of, connects to, requires, implements, precedes, follows, contradicts, supports, replaces, contains, references, extends | Rich semantic typing for all stored memories |

## Setup

### 1. Install dependencies

```bash
pip install cos-mcp requests
```

### 2. Install and start MuninnDB

```bash
curl -sSL https://muninndb.com/install.sh | sh
muninn init --vault default
muninn start
# Default: http://127.0.0.1:8475
```

### 3. Set API key (optional)

```bash
# Only needed if your vault requires authentication
echo 'MUNINN_API_KEY=*** >> ~/.hermes/.env
```

### 4. Install the plugin

```bash
cp -r muninn-memory/ ~/.hermes/hermes-agent/plugins/memory/muninn/
```

### 5. Activate

In `~/.hermes/config.yaml`:

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

## How It Works

The provider extends `cos_mcp.BaseMemoryProvider` which handles:
- Circuit breaker (dual read/write gauges)
- Daemon thread management (fire-and-forget writes)
- Config loading pattern
- Prefetch / cache model for reads
- Session lifecycle hooks

The thin provider (~384 lines) only defines MuninnDB-specific config, tool
schemas, tool handlers, and system prompt text.

- **Every turn:** A background ACTIVATE query fetches relevant memories.
  MuninnDB scores by ACT-R temporal decay + Hebbian co-activation +
  Bayesian confidence. Results injected into system prompt.
- **After every turn:** The user+assistant pair is stored as an event engram
  with concept + content split.
- **On memory writes:** Built-in `memory add/replace/remove` operations
  are mirrored into MuninnDB as fact/preference engrams.
- **On session end:** A summary of the last few messages is stored as
  episodic memory.

## Tools

Three tools are exposed to the model:

| Tool | Description |
|------|-------------|
| `muninn_search` | Search memory with optional `memory_type` filter (12 types) and `min_confidence` threshold. Type filter narrows by category; confidence gates out uncertain/contradicted results. |
| `muninn_profile` | Retrieve user profile via dual query: preferences (`memory_type=preference`) and identity (`memory_type=identity`). Returns combined results. |
| `muninn_remember` | Store a structured memory with concept + content + type + tags. Tags enable auto-association via Hebbian co-activation. |

**12 memory types available:** preference, identity, fact, decision, event,
relationship, procedure, constraint, goal, question, insight, note.

## Architecture

Shared infrastructure in `cos_mcp/`:
- `cos_mcp/base_provider.py` — BaseMemoryProvider (threading, read/write paths, lifecycle)
- `cos_mcp/backends/muninn.py` — MuninnDBBackend (REST API wrapper, engram CRUD)
- `cos_mcp/formatting/muninn.py` — MuninnDBFormatter (activation formatting with confidence annotations)
- `cos_mcp/circuit_breaker.py` — Dual-gauge circuit breaker

Thin plugin at `muninn-memory/__init__.py` (~384 lines):
- Config (MUNINN_API_KEY + muninn.json)
- Tool schemas + handlers (12 memory types, confidence thresholds)
- System prompt block
- Subclass hooks: `_create_backend()`, `_create_formatter()`

## Data Segregation

Memory engrams are tagged `hermes-memory` — separate from context engine
engrams which use `hermes-context`. This prevents memory data from
polluting context search results.

## Choosing Between Providers

**Use MuninnDB if you want:** cognitive primitives in the storage engine
(temporal decay based on actual access patterns, auto-association through
co-activation, confidence tracking), offline capability, no API dependency.
MuninnDB is local — no cloud, no latency, no API costs.

**Use HydraDB if you want:** zero local infrastructure, cloud-managed graph
search, free tier ($0/mo with unlimited API calls).

Both implement the same `MemoryProvider` ABC — swap by changing
`memory.provider` in `~/.hermes/config.yaml`.

## Requirements

- Python 3.12+
- `cos_mcp` package (shared infrastructure)
- `requests>=2.31`
- Running MuninnDB server at `http://127.0.0.1:8475`
- `MUNINN_API_KEY` environment variable (optional for default vault)
