# Architecture Research

**Domain:** Hermes Agent Context Engine Plugins (v1.1 Milestone)
**Researched:** 2026-06-20
**Confidence:** HIGH

*Based on: Hermes Agent source (`agent/context_engine.py` ContextEngine ABC), existing v1.0 memory provider implementations (HydraDB 735 lines, MuninnDB 760 lines), shared cos_mcp infrastructure (backends, circuit breaker, formatting, BaseMemoryProvider), PROJECT.md v1.1 milestone spec.*

---

## Standard Architecture

### System Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                       Hermes Agent Runtime (caller)                            │
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌────────────────┐  ┌──────────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │ Token Tracker  │  │ Compression      │  │ Tool Router   │  │ Session     │ │
│  │ (usage/limit)  │  │ Orchestrator     │  │ (dispatch)    │  │ Lifecycle   │ │
│  └───────┬────────┘  └───────┬──────────┘  └──────┬───────┘  └──────┬──────┘ │
│          │                   │                    │                 │        │
├──────────┴───────────────────┴────────────────────┴─────────────────┴────────┤
│                 BaseContextEngine(ContextEngine)  ← cos_mcp shared infra       │
├──────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────────────┐     │
│  │                    [1] Config Layer                                  │     │
│  │  _load_context_config()  │  get_config_schema()  │  save_config()   │     │
│  │  Reads: env vars + $HERMES_HOME/<engine>.json                       │     │
│  └──────────────────────────────┬──────────────────────────────────────┘     │
│                                 │ config dict                                │
│  ┌──────────────────────────────▼──────────────────────────────────────┐     │
│  │                    [2] Lifecycle Layer                               │     │
│  │  name  │  is_available()  │  initialize(session_id, **kwargs)       │     │
│  │  Resolves identity, creates circuit breaker, provisions backend     │     │
│  │  Hooks: on_session_start(), on_session_end(), on_session_reset()    │     │
│  └──────────────────────────────┬──────────────────────────────────────┘     │
│                                 │ identity + threading primitives             │
│  ┌──────────────────────────────▼──────────────────────────────────────┐     │
│  │                    [3] Backend Layer (reuses cos_mcp backends)       │     │
│  │  MemoryBackend (ABC): query, ingest, delete, health_check,          │     │
│  │  provision, shutdown                                                │     │
│  │  Concrete: HydraDBBackend (cloud)  │  MuninnDBBackend (local)       │     │
│  └───────┬───────────────────────────────┬─────────────────────────────┘     │
│          │                               │                                    │
│  ┌───────▼───────────────┐  ┌────────────▼──────────────────────────┐       │
│  │ [4] Compression Path  │  │ [5] Tool Layer                        │       │
│  │                        │  │                                       │       │
│  │ update_from_response() │  │ get_tool_schemas() → [context_search, │       │
│  │ should_compress()      │  │   context_expand, ...]               │       │
│  │ compress(messages)     │  │ handle_tool_call(name, args)         │       │
│  │   ├─ entity_extract()  │  │   → context_search: query backend    │       │
│  │   ├─ store_to_backend()│  │   → context_expand: re-inflate       │       │
│  │   └─ build_summary()   │  │                                       │       │
│  └───────┬───────────────┘  └────────────┬──────────────────────────┘       │
│          │                               │                                    │
│  ┌───────▼───────────────────────────────▼──────────────────────────┐       │
│  │                    [6] Circuit Breaker (cross-cutting)             │       │
│  │  Dual-gauge (read/write independent) — 5 failures → 120s cooldown │       │
│  │  Shared with memory providers via cos_mcp.CircuitBreaker           │       │
│  └───────────────────────────────────────────────────────────────────┘       │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────┐       │
│  │                    [7] Formatting Layer                            │       │
│  │  ContextFormatter (ABC): format_entities(), format_search(),      │       │
│  │  format_summary_block()                                            │       │
│  │  Concrete: HydraDBContextFormatter, MuninnDBContextFormatter      │       │
│  └──────────────────────────────────────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────────────────────┘
          │                               │
          ▼                               ▼
┌──────────────────────┐    ┌───────────────────────────┐
│  HydraDB Cloud       │    │  MuninnDB Local (:8475)    │
│  (api.hydradb.com)   │    │  (REST API, cognitive)     │
│  Tenant: "hermes"    │    │  Vault: per-profile        │
│  Type: "context"     │    │  Memory types: context/*   │
└──────────────────────┘    └───────────────────────────┘
```

**Threading model:** Context engines follow the same pattern as memory providers — synchronous `ContextEngine` ABC methods (no asyncio). Non-blocking I/O is achieved via daemon threads for backend operations (entity storage, tool queries). The `compress()` method itself **must be synchronous** (it returns the compressed message list that `run_agent.py` uses to build the next API call) — but the entity storage fired during compression runs on a daemon thread. Tool calls (context_search, context_expand) are synchronous — the model is actively waiting for a response.

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| **Config Layer** | Read API credentials from env, merge non-secret overrides from `<engine>.json`, expose schema for setup wizard, persist config changes | `_load_context_config()`, `get_config_schema()`, `save_config()` |
| **Lifecycle Layer** | Engine registration, availability check (creds + SDK import), session initialization (identity, threading, breaker, backend provisioning), session hooks (`on_session_start`, `on_session_end`, `on_session_reset`) | `name`, `is_available()`, `initialize(session_id, **kwargs)` |
| **Backend Layer** | Reuses existing cos_mcp backends (`HydraDBBackend`, `MuninnDBBackend`) for entity storage and retrieval. Same tenant/vault as memory providers but with `type="context"` data segregation | `_create_backend()` → `MemoryBackend` |
| **Compression Path** | Token tracking (`update_from_response`), compaction decision (`should_compress`), message compression (`compress`): entity extraction → backend storage → summary block construction | `update_from_response()`, `should_compress()`, `compress()` |
| **Tools Layer** | Expose context retrieval/expansion to the model as function-calling tools. `context_search` queries stored context entities; `context_expand` re-inflates a compressed block into full detail | `get_tool_schemas()`, `handle_tool_call()` |
| **Circuit Breaker** | Same `cos_mcp.CircuitBreaker` used by memory providers — dual read/write gauges. Guards backend calls in entity storage and tool queries | `_breaker.is_read_open()`, `_breaker.record_read_success()` |
| **Formatting Layer** | Backend-specific formatting of compressed blocks, entity search results, and expand responses. Converts backend-native responses to model-consumable prose | `ContextFormatter.format_entities()`, `.format_search()`, `.format_summary_block()` |

## Component Boundaries

### Who Talks to What

```
Hermes Runtime
    │
    ├─► Lifecycle.initialize(session_id, **kwargs)     [session start, once]
    │       ├─► Config._load_context_config()           [env + JSON]
    │       ├─► Backend.provision()                     [network: tenant/vault readiness]
    │       └─► Circuit._breaker = CircuitBreaker()     [fresh breaker per session]
    │
    ├─► Lifecycle.on_session_start(session_id)          [new conversation]
    │       └─► Backend: load persisted state (dag, store) if applicable
    │
    ├─► Compression.update_from_response(usage_dict)    [after each LLM call]
    │       └─► Update: last_prompt_tokens, last_completion_tokens,
    │           last_total_tokens, threshold_tokens
    │
    ├─► Compression.should_compress(prompt_tokens)      [each turn]
    │       └─► Return True when next turn would exceed threshold
    │
    ├─► Compression.compress(messages, tokens, topic)   [when should_compress=True]
    │       ├─► Entity extraction: identify key entities/topics/decisions
    │       │       └─► Pure Python / simple heuristics (no LLM call)
    │       ├─► Backend.ingest(type="context", text=entities)  [fire-and-forget daemon]
    │       ├─► Build summary block from older messages
    │       └─► Return: [system, ..., summary_block, recent_tail]
    │
    ├─► Tools.get_tool_schemas()                        [tool registration]
    │       └─► Returns context_search + context_expand schemas
    │
    ├─► Tools.handle_tool_call("context_search", args)  [model invokes tool]
    │       ├─► Circuit._breaker.is_read_open()? → error
    │       ├─► Backend.query(type="context", query=...)
    │       ├─► Formatter.format_search(result)
    │       └─► Return JSON result
    │
    ├─► Lifecycle.on_session_end(session_id, messages)  [session close]
    │       └─► Backend.ingest(type="context", infer=true)  [session summary]
    │
    └─► Lifecycle.on_session_reset()                    [/reset or /new]
            └─► Reset: compression_count, token tracking
```

**Key boundary rules:**

1. **Config is read-only after `initialize()`** — all layers read from instance attributes, never from disk after startup.
2. **Only Backend Layer touches SDK/HTTP imports** — compression, tools, and hooks go through `self._backend.query()` / `self._backend.ingest()`.
3. **Only Circuit Breaker helpers touch failure state** — `_breaker.is_read_open()` / `_breaker.is_write_open()` / `_breaker.record_read_success()` / `_breaker.record_read_failure()`.
4. **`compress()` is synchronous** — returns the compacted message list that `run_agent.py` uses for the next API call. Entity storage fired during compression runs on daemon threads (fire-and-forget).
5. **Context engines and memory providers share the same backend instance** — same tenant/vault but different `type` field (`"context"` vs `"memory"`) for data segregation. This means co-tenancy but no data type collision.

## Cos-MCP Shared Infrastructure: BaseContextEngine

### What BaseContextEngine Provides

`BaseContextEngine` is the cos_mcp equivalent of `BaseMemoryProvider` — it extends the Hermes `ContextEngine` ABC with shared infrastructure that both HydraDB and MuninnDB context engines inherit:

| Provided By BaseContextEngine | What It Does |
|-------------------------------|-------------|
| `__init__(self)` | Creates `CircuitBreaker()` instance, nulls backend/formatter |
| `initialize(session_id, **kwargs)` | Captures `hermes_home`, `agent_context`, agent identity; calls `_create_backend(kwargs)` and `_create_formatter()`; provisions backend; sets up circuit breaker label |
| `_breaker` (CircuitBreaker) | Dual read/write gauges — 5 consecutive failures → 120s cooldown |
| `_backend` (MemoryBackend) | Subclass-provided backend instance for entity storage/retrieval |
| `_formatter` (ContextFormatter) | Subclass-provided formatter for context-specific output |
| `update_from_response(usage)` | Standard token tracking: `last_prompt_tokens`, `last_completion_tokens`, `last_total_tokens`, recalculates `threshold_tokens` from `threshold_percent * context_length` |
| `should_compress(prompt_tokens)` | Standard check: `prompt_tokens >= threshold_tokens` (can be overridden for engine-specific heuristics) |
| `get_status()` | Returns standard status dict (token counts, usage percent, compression count) |
| `on_session_reset()` | Resets `compression_count` and token tracking |
| `update_model(model, context_length, ...)` | Recalculates `threshold_tokens`; hook for adjusting DAG budgets |
| `_build_summary_block(messages, start, end)` | Shared helper: condense a message slice into a single summary system message |
| `_entity_extract(messages)` | Abstract — subclass provides backend-specific entity extraction |
| `get_config_schema()` | Abstract — subclass provides config wizard fields |
| `save_config(values, hermes_home)` | Abstract — subclass provides config persistence |

### What Each Plugin Implements

| Plugin Must Implement | HydraDB Context Engine | MuninnDB Context Engine |
|----------------------|----------------------|------------------------|
| `name` class attr | `"hydradb-context"` | `"muninn-context"` |
| `is_available()` | Check `HYDRA_DB_API_KEY` env + `hydra_db` SDK import | Check `requests` import + MuninnDB reachable at `base_url` |
| `_create_backend(kwargs)` | `HydraDBBackend(api_key, tenant_id, sub_tenant_id, type="context")` | `MuninnDBBackend(base_url, vault, api_key, type="context")` |
| `_create_formatter()` | `HydraDBContextFormatter()` | `MuninnDBContextFormatter()` |
| `_entity_extract(messages)` | Graph-aware: extract entities with relationship context for DAG enrichment | Cognitive: classify entities by memory type (decision, fact, preference, event) |
| `compress(messages, tokens, topic)` | Full compression pipeline (see Data Flow below) | Full compression pipeline with ACT-R temporal scoring |
| `get_tool_schemas()` | `[context_search, context_expand, hydradb_context_*]` | `[context_search, context_expand, muninn_context_*]` |
| `handle_tool_call(name, args)` | Dispatch to `_tool_context_search()`, `_tool_context_expand()` | Dispatch to `_tool_context_search()`, `_tool_context_expand()` |
| `system_prompt_block()` | Static text: "HydraDB Context Engine. Active." | Static text: "MuninnDB Cognitive Context Engine. Active." |
| `get_config_schema()` | Fields: api_key, tenant_id, sub_tenant_id, compress_threshold | Fields: api_key, base_url, vault, compress_threshold |
| `save_config(values, hermes_home)` | Write hydradb-context.json | Write muninn-context.json |

## Architectural Differences: HydraDB vs MuninnDB Context Engines

### HydraDB Context Engine (Cloud Async)

- **Backend:** `HydraDBBackend` — same tenant as memory provider, different `type="context"` on ingest/query
- **Entity model:** Graph-enriched — entities stored with relationship edges for DAG-style context retrieval. Each compressed block is a node; relationships capture "precedes", "references", "contradicts"
- **compress() path:** Extracts entities → stores in HydraDB with `type="context"` + `infer=false` (verbatim compression, not auto-fact extraction) → builds a single compressed summary system message → returns `[system, ..., summary_block, tail_messages]`
- **Retrieval:** `context_search` uses HydraDB hybrid query (`query_by="hybrid"`) with `graph_context=True` — traverses relationship edges to find connected context blocks
- **Re-expansion:** `context_expand` takes a compressed block reference (chunk ID) and fetches the full original message content for that block
- **Concurrency model:** Cloud API — all backend calls spawn daemon threads. Circuit breaker guards both read (query) and write (ingest) independently
- **Session lifecycle:** `on_session_end()` ingests full session transcript as `type="context"` with `infer=true` to build cross-session context continuity

### MuninnDB Context Engine (Local Sync)

- **Backend:** `MuninnDBBackend` — same vault as memory provider, tagged with `hermes-context` + context-specific tags
- **Entity model:** Cognitive — entities classified by memory type (`context-decision`, `context-topic`, `context-event`, `context-goal`). ACT-R temporal decay naturally deprioritizes stale context; Hebbian co-activation surfaces related context blocks
- **compress() path:** Extracts entities → classifies by type → stores in MuninnDB with appropriate memory_type → builds compressed summary → returns compacted message list
- **Retrieval:** `context_search` uses MuninnDB ACTIVATE pipeline with cognitive scoring (ACT-R recency, Hebbian boosting, PAS relevance injection). Supports `memory_type` filter for focused retrieval
- **Re-expansion:** `context_expand` fetches full engram content for a compressed block by concept label or idempotent_id
- **Concurrency model:** Local REST API — low latency (<10ms typical). Daemon threads still used for fire-and-forget writes but tool calls may be fast enough for synchronous execution
- **Session lifecycle:** `on_session_end()` stores session summary as episodic context engram with `type_label="context-session"`

### Comparison Matrix

| Dimension | HydraDB Context | MuninnDB Context |
|-----------|----------------|-----------------|
| **Latency profile** | 1-3s (cloud round-trip) | <50ms (local) |
| **Compression strategy** | Graph DAG — linked entity nodes | Cognitive decay — ACT-R temporal scoring |
| **Retrieval enrichment** | Graph traversal (related entities) | Hebbian co-activation (associated memories) |
| **Relevance scoring** | BM25 + semantic hybrid | ACT-R (recency + frequency + PAS) |
| **Data isolation** | `type="context"` on same tenant | `tags=["hermes-context"]` on same vault |
| **Circuit breaker** | Critical (cloud unreliability) | Less critical (local, but helps with config errors) |
| **Tool response time** | 1-3s (acceptable for explicit tool call) | <100ms (near-instant) |
| **Cross-session context** | `infer=true` on session summary | Event engram with temporal tags |

## The compress() Pathway: End-to-End

### Step-by-Step Flow

```
compress(messages, current_tokens, focus_topic) → List[Dict] (new message list)

┌──────────────────────────────────────────────────────────────────────┐
│ STEP 1: Pre-compression Guards                                       │
├──────────────────────────────────────────────────────────────────────┤
│ • Check circuit breaker (write gauge): open? → return messages as-is │
│ • Check agent_context != "primary"? → return messages as-is          │
│ • Increment compression_count                                        │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP 2: Determine Compression Window                                 │
├──────────────────────────────────────────────────────────────────────┤
│ • protect_first_n: always preserve first N non-system messages (def 3)│
│ • protect_last_n: always preserve last N messages (def 6)            │
│ • Compression target: messages[first_n : -last_n]                     │
│ • If target is empty (all protected): return messages as-is           │
│                                                                      │
│  Example with 50 messages (protect_first_n=3, protect_last_n=6):    │
│   ┌──────────────────────────────────────────────────────────┐       │
│   │ [system] [0][1][2] │ [3]...[43] │ [44][45][46][47][48][49]│       │
│   │  always    head    │  COMPRESS   │       tail              │       │
│   │  protected protected│   HERE     │     protected            │       │
│   └──────────────────────────────────────────────────────────┘       │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP 3: Entity Extraction (engine-specific)                          │
├──────────────────────────────────────────────────────────────────────┤
│ Extract structured entities from the compression target:             │
│                                                                      │
│ For each message in compression window:                              │
│   • Identify: key topics, decisions made, facts stated, questions    │
│   • Extract: relationships between entities (refers to, contradicts, │
│     builds on, answers, follows up)                                   │
│   • Filter out: filler, greetings, acknowledgments                   │
│                                                                      │
│ HydraDB engine: extracts entities with relationship edges for DAG    │
│ MuninnDB engine: classifies by memory_type + assigns confidence      │
│                                                                      │
│ Output: list of Entity objects with {text, type, relationships,      │
│         source_msg_index, confidence}                                │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP 4: Store Entities to Backend (fire-and-forget)                  │
├──────────────────────────────────────────────────────────────────────┤
│ Spawn daemon thread:                                                 │
│   for entity in extracted_entities:                                  │
│     backend.ingest(                                                  │
│       text=f"{entity.type}: {entity.text}",                          │
│       infer=False,              # verbatim — already extracted       │
│       type="context",           # data type segregation from memory  │
│       metadata={                                                      │
│         "source": "context_compression",                             │
│         "compression_count": self.compression_count,                 │
│         "entity_type": entity.type,                                  │
│         "relationships": entity.relationships,                       │
│         "source_msg_range": f"{first_idx}-{last_idx}"                │
│       },                                                             │
│       tags=["hermes-context", f"compression-{self.compression_count}"]│
│     )                                                                │
│   breaker.record_write_success() or breaker.record_write_failure()  │
│                                                                      │
│ This is fire-and-forget — compress() does NOT wait for completion.  │
│ Context entities will be queryable by next turn (indexing delay).    │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP 5: Build Compressed Summary Block                                │
├──────────────────────────────────────────────────────────────────────┤
│ Generate a single system message summarizing the compressed window:  │
│                                                                      │
│ {                                                                     │
│   "role": "system",                                                  │
│   "content": (                                                       │
│     "[COMPRESSED CONTEXT — compression #{count}]\n"                  │
│     "The following conversation history has been summarized:\n\n"    │
│     "Topics discussed: {summarized_topics}\n"                        │
│     "Key decisions: {summarized_decisions}\n"                        │
│     "Important facts: {summarized_facts}\n"                          │
│     "[ctx-id: {compression_count}_{short_hash}]\n"                    │
│   )                                                                  │
│ }                                                                     │
│                                                                      │
│ The ctx-id is a short reference that context_search/context_expand   │
│ tools can use to retrieve the full original content.                 │
│                                                                      │
│ Focus topic support: when focus_topic is provided (from manual       │
│ /compress <topic>), entity extraction prioritizes that topic and     │
│ the summary block notes: "Focus topic: {focus_topic}"                │
└──────────────┬───────────────────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP 6: Assemble and Return New Message List                          │
├──────────────────────────────────────────────────────────────────────┤
│ Return:                                                              │
│   [                                                                  │
│     system_prompt,          # always preserved                       │
│     messages[1:first_n],    # protected head (non-system)            │
│     compressed_summary_block,  # the single summary system message   │
│     messages[-last_n:],     # protected tail (most recent)           │
│   ]                                                                  │
│                                                                      │
│ Token savings: compressed window of N messages → 1 summary message   │
│ Typical: 40 messages @ ~100 tokens each = 4,000 tokens compressed    │
│          into 1 summary message @ ~500 tokens → 3,500 token savings  │
└──────────────────────────────────────────────────────────────────────┘
```

### Tool-Based Retrieval

After compression, the model can retrieve expanded context via tools:

```
Model calls context_search:
  → backend.query(type="context", query_text="topic X", ...)
  → formatter.format_search(result)
  → returns list of compressed context blocks with ctx-ids

Model calls context_expand:
  → backend.query(type="context", ctx_id="3_a1b2c3")
  → returns full original message content for that compression block
  → re-inflates the compressed summary into detailed conversation
```

## Coexistence: Context Engine + Memory Provider

### Data Segregation Strategy

Context engines and memory providers share the same backend infrastructure (same tenant, same vault) but use different data type markers to prevent cross-contamination:

```
┌───────────────────────────────────────────────────────────────┐
│                   HydraDB Tenant: "hermes"                      │
│                   Sub-Tenant: "profile_name"                    │
├───────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐ │
│  │ type="memory"            │  │ type="context"               │ │
│  │                           │  │                              │ │
│  │ • Durable facts          │  │ • Compressed message blocks  │ │
│  │ • User preferences       │  │ • Extracted entities         │ │
│  │ • Session summaries      │  │ • Topic clusters             │ │
│  │ • Agent decisions        │  │ • Decision timelines         │ │
│  │ • Profile identity       │  │ • Context DAG nodes          │ │
│  │                           │  │                              │ │
│  │ Tools:                    │  │ Tools:                       │ │
│  │  hydradb_search           │  │  context_search              │ │
│  │  hydradb_profile          │  │  context_expand              │ │
│  │  hydradb_conclude         │  │                              │ │
│  │                           │  │                              │ │
│  │ Tags: ["hermes-memory"]   │  │ Tags: ["hermes-context"]     │ │
│  └─────────────────────────┘  │ Tags: ["hermes-context",      │ │
│                                  │   "compression-N"]          │ │
│                                  └─────────────────────────────┘ │
│                                                                │
│  Same tenant, same sub_tenant → same billing, same management   │
│  Different type field → queries never mix memory + context      │
│  Different tag namespace → easy bulk operations per type       │
└───────────────────────────────────────────────────────────────┘
```

```
┌───────────────────────────────────────────────────────────────┐
│                   MuninnDB Vault: "profile_name"                │
├───────────────────────────────────────────────────────────────┤
│                                                                │
│  ┌─────────────────────────┐  ┌─────────────────────────────┐ │
│  │ Tags: ["hermes-memory"] │  │ Tags: ["hermes-context"]     │ │
│  │                           │  │                              │ │
│  │ Memory types:             │  │ Memory types:                │ │
│  │  fact, decision,          │  │  context-topic               │ │
│  │  preference, identity,    │  │  context-decision            │ │
│  │  observation, procedure,  │  │  context-event               │ │
│  │  event, goal, constraint  │  │  context-goal                │ │
│  │                           │  │  context-fact                │ │
│  │ Tools:                    │  │                              │ │
│  │  muninn_search            │  │ Tools:                       │ │
│  │  muninn_profile           │  │  context_search              │ │
│  │  muninn_remember          │  │  context_expand              │ │
│  └─────────────────────────┘  └─────────────────────────────┘ │
│                                                                │
│  Same vault → same cognitive engine instance                   │
│  Different tags → queries filter by tag namespace              │
│  Different memory types → semantic separation at engine level │
└───────────────────────────────────────────────────────────────┘
```

### Interaction Points

| Scenario | Context Engine | Memory Provider | Interaction |
|----------|---------------|-----------------|-------------|
| Normal turn | `update_from_response()` tracks tokens; `should_compress()` checked | `queue_prefetch()` runs background memory query | Independent — both read from own data types |
| Compression triggered | `compress()` extracts entities, builds summary block | Unaffected — memory writes continue normally | None — compress is read-heavy, no conflict |
| Model calls context_search | Queries `type="context"` | Unaffected | None — different query type |
| Model calls hydradb_search | Unaffected | Queries `type="memory"` | None — different query type |
| Model calls context_expand | Retrieves full original messages | Unaffected | Context engine may reference memory provider facts in expanded context (v2 enhancement) |
| Session end | `on_session_end()` ingests context summary | `on_session_end()` ingests memory summary | Both fire on daemon threads — independent writes |

**Key principle:** Context engine and memory provider are independent but co-located. They share infrastructure (circuit breaker, backends) but operate on different data types. The model sees both tool sets (memory tools + context tools) and uses them for different purposes — memory for durable facts, context for compressed conversation retrieval.

## Plugin Directory Structure

```
cos-mcp/                             # Repository root
│
├── cos_mcp/                         # Shared infrastructure (existing, extended)
│   ├── __init__.py
│   ├── base_provider.py             # BaseMemoryProvider (existing)
│   ├── base_context_engine.py       # BaseContextEngine (NEW for v1.1)
│   ├── circuit_breaker.py           # CircuitBreaker (existing, reused)
│   ├── backends/
│   │   ├── __init__.py
│   │   ├── base.py                  # MemoryBackend ABC (existing)
│   │   ├── hydradb.py               # HydraDBBackend (existing, extended with type param)
│   │   └── muninn.py                # MuninnDBBackend (existing, extended with type param)
│   └── formatting/
│       ├── __init__.py
│       ├── base.py                  # MemoryFormatter ABC (existing)
│       ├── context_base.py          # ContextFormatter ABC (NEW for v1.1)
│       ├── hydradb.py               # HydraDBFormatter (existing)
│       ├── hydradb_context.py       # HydraDBContextFormatter (NEW for v1.1)
│       ├── muninn.py                # MuninnDBFormatter (existing)
│       └── muninn_context.py        # MuninnDBContextFormatter (NEW for v1.1)
│
├── plugins/                         # In-tree plugin deployment
│   └── context_engine/              # Hermes discovers plugins/context_engine/*/
│       ├── hydradb-context/
│       │   ├── __init__.py          # HydraDBContextEngine + register(ctx)
│       │   └── plugin.yaml          # Manifest: name, version, pip_deps, env vars
│       └── muninn-context/
│           ├── __init__.py          # MuninnDBContextEngine + register(ctx)
│           └── plugin.yaml          # Manifest: name, version, pip_deps, env vars
│
├── hydradb-memory/                  # Existing v1.0 memory provider (unchanged)
│   ├── __init__.py
│   └── plugin.yaml
│
├── muninn-memory/                   # Existing v1.0 memory provider (unchanged)
│   ├── __init__.py
│   └── plugin.yaml
│
├── tests/                           # Test suites (extended)
│   ├── plugins/
│   │   ├── memory/
│   │   │   ├── test_hydradb_provider.py      # Existing
│   │   │   └── test_muninn_provider.py       # Existing
│   │   └── context_engine/
│   │       ├── test_hydradb_context.py       # NEW for v1.1
│   │       └── test_muninn_context.py        # NEW for v1.1
│   └── cos_mcp/
│       ├── test_circuit_breaker.py           # Existing
│       └── test_base_context_engine.py       # NEW for v1.1
│
└── .planning/                       # Planning documents (this file)
    └── research/
        └── ARCHITECTURE.md          # ← THIS FILE
```

### plugin.yaml Schema

```yaml
# plugins/context_engine/hydradb-context/plugin.yaml
name: hydradb-context
version: 1.0.0
description: HydraDB-backed context engine for Hermes Agent
plugin_type: context_engine
pip_dependencies:
  - hydradb-sdk>=2,<3
requires_env:
  - HYDRA_DB_API_KEY
hooks:
  - on_session_start
  - on_session_end
tools:
  - context_search
  - context_expand
```

```yaml
# plugins/context_engine/muninn-context/plugin.yaml
name: muninn-context
version: 1.0.0
description: MuninnDB cognitive context engine for Hermes Agent
plugin_type: context_engine
pip_dependencies:
  - requests>=2.28
env_optional:
  - MUNINN_API_KEY
hooks:
  - on_session_start
  - on_session_end
tools:
  - context_search
  - context_expand
```

## Registration Mechanism

### Directory Discovery (Primary)

Hermes Agent discovers context engine plugins via directory scanning:

```
Hermes startup:
  1. Scan $HERMES_HOME/plugins/context_engine/*/ for __init__.py files
  2. For each directory found:
     a. Import the module: plugins.context_engine.<name>
     b. Call the module-level register(ctx) function
     c. register(ctx) calls ctx.register_context_engine(EngineInstance())
  3. Config.yaml context.engine value selects the active engine
  4. Only one engine is active at a time (single-select)

Example discovery:
  plugins/context_engine/hydradb-context/__init__.py → hydradb-context
  plugins/context_engine/muninn-context/__init__.py → muninn-context
```

### register() Function Pattern

```python
# plugins/context_engine/hydradb-context/__init__.py

from cos_mcp.base_context_engine import BaseContextEngine
from cos_mcp.backends.hydradb import HydraDBBackend
from cos_mcp.formatting.hydradb_context import HydraDBContextFormatter

class HydraDBContextEngine(BaseContextEngine):
    name = "hydradb-context"
    # ... implementation ...

def register(ctx) -> None:
    """Register this engine with the Hermes Agent plugin system."""
    ctx.register_context_engine(HydraDBContextEngine())
```

```python
# plugins/context_engine/muninn-context/__init__.py

from cos_mcp.base_context_engine import BaseContextEngine
from cos_mcp.backends.muninn import MuninnDBBackend
from cos_mcp.formatting.muninn_context import MuninnDBContextFormatter

class MuninnDBContextEngine(BaseContextEngine):
    name = "muninn-context"
    # ... implementation ...

def register(ctx) -> None:
    """Register this engine with the Hermes Agent plugin system."""
    ctx.register_context_engine(MuninnDBContextEngine())
```

### Registration vs Memory Provider Registration

Both use the same `register(ctx)` pattern but different registration methods:

```python
def register(ctx) -> None:
    # Memory provider (existing v1.0)
    ctx.register_memory_provider(HydraDBMemoryProvider())

    # Context engine (new v1.1)
    ctx.register_context_engine(HydraDBContextEngine())
```

If both memory provider and context engine for the same backend ship in one package, the `register()` function registers both:

```python
# Hypothetical unified plugin
def register(ctx) -> None:
    ctx.register_memory_provider(HydraDBMemoryProvider())
    ctx.register_context_engine(HydraDBContextEngine())
```

However, the v1.1 architecture keeps them as separate plugin directories (`plugins/context_engine/*/` vs `plugins/memory/*/`) for clean separation and independent versioning.

## Architectural Patterns

### Pattern 1: Plugin/Provider with Abstract Base Class (reused from v1.0)

**What:** Hermes Agent defines `ContextEngine` as an ABC with required methods (`name`, `update_from_response`, `should_compress`, `compress`). Context engines subclass it and register via a module-level `register(ctx)` function. The Hermes runtime calls engine methods at well-defined points in the agent lifecycle — engines never call the runtime.

**Trade-offs:**
- ✓ Runtime controls when compression happens; engines are passive
- ✓ ABC guarantees interface consistency — Hermes can swap engines by changing one config value
- ✓ Single registration point (`register(ctx)`) makes plugin discovery deterministic
- ✓ Same pattern as memory providers — operators already understand the model
- ✗ compress() must be synchronous — limits LLM-based compression strategies (would need sub-agent call, adding latency)

### Pattern 2: Fire-and-Forget Entity Storage (adapted from v1.0 write path)

**What:** During `compress()`, entity extraction produces structured entities that are stored to the backend on a daemon thread. `compress()` returns immediately with the summary block — it does NOT wait for the backend ingest to complete. The caller (`run_agent.py`) gets the compressed message list without blocking on I/O.

**When to use:** The entity storage is an eventually-consistent side effect — the summary block is what matters for the immediate next turn. Entity storage enables future context_search/expand but doesn't need to be synchronous.

**Trade-offs:**
- ✓ compress() latency unaffected by backend write speed
- ✓ Summary block is always available for the next turn
- ✗ Context entities not immediately searchable (same 1-5s indexing delay as memory writes)
- ✗ If process crashes mid-compress, entities may be lost (but summary block was already returned — context continuity is preserved)

### Pattern 3: Same-Tenant, Type-Segregated Data

**What:** Context engines and memory providers share the same backend tenant/vault but use a `type` field (HydraDB) or tag namespace (MuninnDB) to segregate context data from memory data. Queries filter by type — `context_search` only sees context data, `hydradb_search` only sees memory data.

**When to use:** When two subsystems (context + memory) share the same infrastructure but must never cross-contaminate each other's data.

**Trade-offs:**
- ✓ Single tenant to manage — one API key, one billing account
- ✓ Simplifies provisioning — no separate tenant/vault setup needed
- ✓ Cross-type queries possible in the future (e.g., "what memories relate to this compressed context block?")
- ✗ Requires discipline in type/tag naming — one bad query without type filter returns mixed results
- ✗ Type field is a string — no schema enforcement at the API level

### Pattern 4: Prefetch/Cache for Context (future v2 enhancement)

**What:** Adapt the memory provider's prefetch/cache pattern for context. Before the model's turn, `queue_prefetch_context()` fires a background query for relevant compressed context blocks from prior compression rounds. `prefetch_context()` returns the cached result instantly during prompt assembly.

**Current v1.1 scope:** Not yet implemented. The model uses explicit `context_search` tool calls for on-demand retrieval. Prefetch would make prior compression context proactively available in the system prompt.

**When to use (v2):** When compression history grows large enough that proactive context retrieval would reduce redundant tool calls.

### Pattern 5: Entity Extraction as Pure Heuristics (No LLM Dependency)

**What:** Entity extraction during `compress()` uses pure Python heuristics (keyword matching, pattern recognition, topic clustering) rather than calling an LLM. This keeps `compress()` synchronous, fast, and cost-free.

**Why not LLM-based extraction:** `compress()` must be synchronous per the ABC contract (it returns the message list for the next API call). An LLM-based extraction would add significant latency and cost to an operation that already fires during a pause in the conversation. The extracted entities are a scaffold for future retrieval — perfect accuracy is less important than speed and zero-cost operation.

**Trade-offs:**
- ✓ Fast (<100ms) — doesn't block the conversation
- ✓ Free — no additional LLM calls
- ✓ Deterministic — same input produces same extraction
- ✗ Less sophisticated than LLM extraction — may miss nuanced entities
- ✗ No cross-turn relationship inference (but the retrieval layer compensates via backend-native features: HydraDB graph traversal, MuninnDB Hebbian learning)

## Data Flow

### Compression Flow (synchronous path)

```
Turn N (after should_compress() returns True):
    Hermes Runtime → compress(messages, current_tokens, focus_topic)
        │
        ├─ Protect head: messages[1 : protect_first_n+1]
        ├─ Protect tail: messages[-protect_last_n : ]
        ├─ Compression window: messages[protect_first_n+1 : -protect_last_n]
        │
        ├─ entity_extract(compression_window)
        │     └─ Pure heuristics: extract topics, decisions, facts, relationships
        │     └─ Returns: List[Entity] with {text, type, relationships, source_idx}
        │
        ├─ Spawn daemon thread: store_entities(entity_list)
        │     └─ For each entity: backend.ingest(type="context", infer=False, ...)
        │     └─ Circuit breaker: record_write_success / record_write_failure
        │
        ├─ build_summary_block(compression_window, entity_list)
        │     └─ Generate system message with ctx-id reference
        │     └─ Format: "[COMPRESSED CONTEXT] Topics: ... Decisions: ... [ctx-id: ...]"
        │
        └─ Return: [system_prompt, head_messages, summary_block, tail_messages]
        
    compression_count += 1
```

### Tool Call Flow (synchronous)

```
Model calls context_search:
    Hermes Runtime → handle_tool_call("context_search", {"query": "topic X"})
        ├─ Circuit breaker: is_read_open()? → return error JSON
        ├─ backend.query(type="context", query_text="topic X", ...)
        ├─ formatter.format_search(result)
        └─ return json.dumps({"results": [...]})

Model calls context_expand:
    Hermes Runtime → handle_tool_call("context_expand", {"ctx_id": "3_a1b2c3"})
        ├─ backend.query(type="context", ctx_id="3_a1b2c3")
        ├─ formatter.format_expand(result)  # returns full original messages
        └─ return json.dumps({"expanded_context": "..."})
```

### Session End Flow

```
Session closes:
    Hermes Runtime → on_session_end(session_id, messages)
        ├─ Guard: agent_context != "primary"? → return
        ├─ Circuit breaker: is_write_open()? → return
        └─ Spawn daemon thread:
            ├─ Extract session summary: last 20 user/assistant messages
            ├─ backend.ingest(
            │       type="context",
            │       text=summary_text,
            │       infer=True,        # auto-extract context entities
            │       metadata={"source": "session_end", "session_id": session_id}
            │   )
            └─ breaker.record_write_success / _failure
```

## State Management

| State | Scope | Mutability | Thread Safety |
|-------|-------|------------|---------------|
| `_config` (dict) | Session | Read-only after `initialize()` | N/A (no writes after init) |
| `_backend` (MemoryBackend) | Session | Read-only after `initialize()` | Backend-internal (HydraDB: `threading.Lock`, MuninnDB: `requests.Session`) |
| `_breaker` (CircuitBreaker) | Session | Read-write | `threading.Lock` (internal to CircuitBreaker) |
| `last_prompt_tokens` (int) | Session | Updated after each response | GIL-safe (atomic int read/write) |
| `last_completion_tokens` (int) | Session | Updated after each response | GIL-safe |
| `compression_count` (int) | Session | Incremented on each compression | GIL-safe |
| `context_length` (int) | Session | Set on init / model switch | GIL-safe |
| `threshold_tokens` (int) | Session | Recalculated on token updates | GIL-safe |
| Context entities in backend | Persistent | Writes via API | Backend server-side |
| Summary block in message list | Per-turn | Created by compress(), consumed immediately | N/A (single-threaded call path) |

**No persistent local state beyond `<engine>.json`.** All context entities live in the backend (HydraDB Cloud or MuninnDB local). The context engine is stateless between sessions — token counters reset, compression count resets, backend is re-provisioned on `initialize()`.

## Suggested Build Order

### Dependency Graph

```
Phase 1 (Foundation):
    Config → Lifecycle → Backend (reuse existing)
                            │
              ┌─────────────┼──────────────┐
              ▼             ▼              ▼
Phase 2:  Compression    Tools          Formatting
          (compress,      (schemas,      (ContextFormatter
           entity_extract, dispatch)      ABC + concrete)
           should_compress)

Phase 3:  Circuit Breaker Integration
          (all backend calls gated by read/write breakers)

Phase 4:  Quality
          (Tests + Live verification + Integration)
```

### Phase 1: Shared Infrastructure + Foundation

**Dependency:** None (bottom layers).

**What to build:**
1. `cos_mcp/base_context_engine.py` — `BaseContextEngine` class extending `ContextEngine` ABC
2. `cos_mcp/formatting/context_base.py` — `ContextFormatter` ABC with `format_entities()`, `format_search()`, `format_summary_block()`
3. Extend `HydraDBBackend` and `MuninnDBBackend` to accept and use `type` field for context vs memory segregation
4. Config layer in `BaseContextEngine`: `_load_context_config()`, `get_config_schema()`, `save_config()`
5. Lifecycle: `is_available()`, `initialize()`, `on_session_start()`, `on_session_end()` (shared in BaseContextEngine)

**Status:** Not started.

### Phase 2: Compression + Tools + Formatting

**Dependency:** Phase 1 complete (BaseContextEngine, backends, formatters).

**What to build:**
1. **HydraDB Context Engine** (`plugins/context_engine/hydradb-context/__init__.py`):
   - `HydraDBContextEngine(BaseContextEngine)` with `name = "hydradb-context"`
   - `_entity_extract(messages)` — graph-aware entity extraction
   - `compress(messages, tokens, focus_topic)` — full compression pipeline
   - `_build_summary_block()` — HydraDB-specific summary formatting
   - `get_tool_schemas()` → `[context_search, context_expand, hydradb_context_status]`
   - `handle_tool_call()` → dispatch to `_tool_context_search`, `_tool_context_expand`
   - `HydraDBContextFormatter` — format HydraDB query results as context blocks
2. **MuninnDB Context Engine** (`plugins/context_engine/muninn-context/__init__.py`):
   - `MuninnDBContextEngine(BaseContextEngine)` with `name = "muninn-context"`
   - `_entity_extract(messages)` — cognitive entity classification
   - `compress(messages, tokens, focus_topic)` — cognitive compression with ACT-R scoring
   - `get_tool_schemas()` → `[context_search, context_expand]`
   - `handle_tool_call()` → dispatch to `_tool_context_search`, `_tool_context_expand`
   - `MuninnDBContextFormatter` — format MuninnDB activations as context blocks
3. **plugin.yaml** for both engines

**Status:** Not started.

### Phase 3: Circuit Breaker Integration

**Dependency:** Phase 1 + 2.

**What to build:**
1. Gate all backend calls (entity storage, tool queries) with circuit breaker
2. Read gauge: context_search, context_expand
3. Write gauge: entity storage during compress(), on_session_end()
4. Error handling: auth errors → ERROR log, no breaker increment; transient errors → WARNING log, breaker increment

**Status:** Not started. Circuit breaker itself already exists in cos_mcp — this phase is about wiring it into context engine code paths.

### Phase 4: Quality (Tests + Verification + Integration)

**Dependency:** All Phase 1-3 layers complete.

**What to build:**
1. **Fake backends for context testing** — extend existing fake HydraDB/MuninnDB clients with `type="context"` support
2. **`test_base_context_engine.py`** — BaseContextEngine lifecycle, token tracking, config loading
3. **`test_hydradb_context.py`** — HydraDB context engine: compress pipeline, entity extraction, tool calls, circuit breaker
4. **`test_muninn_context.py`** — MuninnDB context engine: cognitive compression, ACT-R decay, tool calls
5. **Live API verification** — compress + context_search against real HydraDB Cloud and local MuninnDB
6. **Hermes integration** — in-tree install, `hermes doctor` clean, config.yaml switch between engines

**Status:** Not started.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 1 user, 3-5 profiles (current) | Single-file per engine is fine. Entity extraction via heuristics is fast. Context entities stored on daemon threads. No changes needed. |
| 1 user, 10-20 profiles | Still fine. Per-profile isolation via sub_tenant/vault. Compression history grows linearly but queries filter by type. |
| High-compression-frequency agent | Entity storage may accumulate rapidly. Consider periodic cleanup of old context entities (e.g., keep last N compression rounds). Can be a configurable `max_context_rounds` parameter. |
| Multi-user deployment | Same concerns as memory providers: shared tenant contention. Context data adds to tenant storage load. Consider per-user tenants if storage limits become constraining. |
| Large conversation histories (100+ messages per session) | Entity extraction heuristics scale linearly with message count. For very large windows, consider sampling (extract from every Nth message) or async extraction via sub-agent call (v3 enhancement). |

### Scaling Priorities

1. **First bottleneck:** Entity storage volume — each compression stores all extracted entities. With frequent compression, this grows unbounded. Mitigation: tag entities with `compression-N` and add cleanup for old rounds.
2. **Second bottleneck:** `context_search` query latency — HydraDB cloud queries are 1-3s. Acceptable for explicit tool calls but would be slow for prefetch. MuninnDB local queries are <50ms — no concern.
3. **Not a bottleneck:** Entity extraction (pure Python heuristics), summary block generation (string formatting), configuration (tiny JSON).

## Anti-Patterns

### Anti-Pattern 1: Calling an LLM During compress()

**What people might do:** Use `client.chat.completions.create()` inside `compress()` to generate a "better" summary or extract entities with higher accuracy.

**Why it's wrong:** `compress()` must be synchronous per the ContextEngine ABC — it returns the message list that `run_agent.py` uses for the next API call. An LLM call inside `compress()` would add 1-3s latency to an operation that already fires during a pause in the conversation. It also creates a dependency loop (compression might itself trigger a compression due to token usage in the LLM call).

**Do this instead:** Use pure Python heuristics for entity extraction. If LLM-quality extraction is needed, implement it as a `context_summarize` tool that the model can call explicitly (not inside the compression hot path).

### Anti-Pattern 2: Blocking on Entity Storage During compress()

**What people might do:** Call `backend.ingest()` synchronously inside `compress()` and wait for the response.

**Why it's wrong:** HydraDB Cloud ingest latency is 500ms-2s. Blocking `compress()` for this time makes the agent appear to hang. The summary block is what matters for the immediate next turn — entity storage is an eventually-consistent side effect.

**Do this instead:** Spawn a daemon thread for entity storage. `compress()` returns immediately with the summary block. Entities will be queryable within 1-5s (backend indexing latency).

### Anti-Pattern 3: Mixing Context and Memory Data Types

**What people might do:** Store context entities with `type="memory"` or vice versa, or fail to filter by type in queries.

**Why it's wrong:** Context data and memory data serve different purposes. Mixing them degrades both — context_search returns user preferences, hydradb_search returns compressed message blocks. The model gets confused about what it's looking at.

**Do this instead:** Always use `type="context"` for context entities, `type="memory"` for memory facts. Always include the type filter in query calls. Tag context entities with `hermes-context` and memory entities with `hermes-memory` for easy bulk operations.

### Anti-Pattern 4: Not Guarding Against Non-Primary Agent Contexts

**What people might do:** Let context compression and tool calls run for all agent contexts (primary, secondary, delegated sub-agents).

**Why it's wrong:** Secondary agents and delegated sub-agents would duplicate context data into the same tenant/vault, creating noise and duplicate entries. Context compression should only fire for the primary agent.

**Do this instead:** Guard compression path with `if self._agent_context != "primary": return messages` (return unchanged messages for secondary agents). Guard `on_session_end()` similarly.

### Anti-Pattern 5: Using the Same Config File as Memory Providers

**What people might do:** Store context engine config in `hydradb.json` (same file as memory provider).

**Why it's wrong:** Context engine and memory provider have different config keys (e.g., `compress_threshold`, `max_context_rounds` vs `query_mode`, `max_results`). Sharing a config file creates coupling — changing memory config could accidentally affect context behavior.

**Do this instead:** Use separate config files: `hydradb-context.json` for the context engine, `hydradb.json` for the memory provider. They can share the same API key from env vars.

## Integration Points

### Cos-MCP Internal (Shared Infrastructure)

| Component | Used By Context Engine | Notes |
|-----------|----------------------|-------|
| `CircuitBreaker` | Entity storage, tool queries | Same dual-gauge pattern as memory providers |
| `HydraDBBackend` | Entity storage + retrieval | Extended with `type="context"` parameter |
| `MuninnDBBackend` | Entity storage + retrieval | Extended with context-specific tags |
| `MemoryFormatter` (ABC) | Not directly — context engines use `ContextFormatter` | Separate formatter hierarchy for context-specific output |
| `BaseMemoryProvider` | Not used — context engines use `BaseContextEngine` | Separate base class, shared patterns |

### Hermes Agent Runtime

| Runtime Component | How Context Engine Integrates | Notes |
|------------------|------------------------------|-------|
| `config.yaml` | `context.engine: "hydradb-context"` selects active engine | Single-select — only one engine active |
| Compression orchestrator | Calls `should_compress()` → `compress()` → uses returned message list | `run_agent.py` owns the orchestration |
| Token tracker | Reads `last_prompt_tokens`, `threshold_tokens`, `context_length` for display | Engines MUST maintain these attributes |
| Tool router | Calls `get_tool_schemas()` at registration, `handle_tool_call()` at invocation | Tool names must not collide with core or memory provider tools |
| Session lifecycle | Calls `on_session_start()`, `on_session_end()`, `on_session_reset()` | Engines respond to lifecycle events |

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| **HydraDB Cloud** (`api.hydradb.com`) | Same tenant as memory provider, `type="context"` for data segregation | API key from env `HYDRA_DB_API_KEY` |
| **MuninnDB Local** (port 8475) | Same vault as memory provider, `tags=["hermes-context"]` for data segregation | Default vault or per-profile vault |

## Sources

- `agent/context_engine.py` (Hermes source, 226 lines) — `ContextEngine` ABC with full lifecycle documentation and method signatures. **Authoritative on the plugin contract.**
- `cos_mcp/base_provider.py` (352 lines) — `BaseMemoryProvider` pattern reference. Shows the shared-infrastructure model that `BaseContextEngine` will mirror.
- `cos_mcp/circuit_breaker.py` (87 lines) — `CircuitBreaker` with dual read/write gauges. Reused as-is by context engines.
- `cos_mcp/backends/base.py` (75 lines) — `MemoryBackend` ABC. Extended with type parameter for context vs memory segregation.
- `cos_mcp/backends/hydradb.py` (241 lines) — `HydraDBBackend` concrete implementation. Shows tenant provisioning, ingest with metadata, query patterns.
- `cos_mcp/backends/muninn.py` (210 lines) — `MuninnDBBackend` concrete implementation. Shows tag-based segregation, ACTIVATE pipeline, engram storage.
- `hydradb-memory/__init__.py` (284 lines, refactored from 735) — Completed HydraDB memory provider. Reference for plugin structure, config loading, tool schemas, `register()` pattern.
- `muninn-memory/__init__.py` (384 lines) — Completed MuninnDB memory provider. Reference for cognitive features, memory type enums, tag management.
- `.planning/PROJECT.md` — v1.1 milestone spec: target features, constraints, key decisions, codebase map.
- `.planning/research/ARCHITECTURE.md` (v1.0, 546 lines) — Existing architecture research for memory providers. Documents the patterns (prefetch/cache, fire-and-forget, circuit breaker) that context engines inherit.
- `.planning/research/FEATURES.md` (252 lines) — Feature landscape for v1.0. Informs context engine feature scope (differentiators, anti-features).
- `.planning/research/STACK.md` (136 lines) — Stack decisions (Python 3.12, hydradb-sdk 2.0.1, stdlib only). All applicable to context engines.
- `.planning/codebase/ARCHITECTURE.md` (155 lines) — Existing dual-provider architecture analysis. Documents the pattern that context engines extend.

---

*Architecture research for: Context Engine Plugins for Hermes Agent (cos-mcp v1.1)*
*Researched: 2026-06-20*
*Confidence: HIGH — all design decisions grounded in existing codebase patterns, verified ABC contract, and v1.0 reference implementations.*
