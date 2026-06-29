# HydraDB Memory Provider — Architecture Design

> **Note (2026-06-29):** This document describes the v0.1 pre-refactor architecture
> where all logic lived in a single monolithic provider class. The code has since
> been refactored into a shared `cos_mcp` package with thin provider plugins.
> See `HERMES.md` and `README.md` for the current v0.2.0 architecture.

> **Goal:** A Hermes Agent memory provider plugin backed by HydraDB v2, shared
> across all profiles. This document specifies the full architecture, file
> layout, method contracts, data flow, and testing strategy. It is the blueprint
> for the build phase.
>
> **Research inputs:**
> - `research/hermes-memory-provider-research.md` — the MemoryProvider ABC contract
> - `research/hydradb-v2-research.md` — the HydraDB v2 API surface
> - Verified SDK signatures from `hydradb-sdk==2.0.1` (installed in Hermes venv)

---

## 1. Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Plugin location | In-tree: `~/.hermes/hermes-agent/plugins/memory/hydradb/` | Cross-profile requirement — in-tree is discovered by every profile. User-installed (`$HERMES_HOME/plugins/`) is per-profile only. |
| Memory topology | One tenant, one `sub_tenant_id` per profile (1:1) | Start isolated — each profile's memories are scoped to its own sub-tenant. No cross-profile noise. When shared concepts emerge (user profile, universal preferences), promote them to a dedicated `"shared"` sub-tenant. Sub-tenants are implicit in HydraDB (just a string), so this evolution needs no schema change. |
| SDK client | `HydraDB` (sync), NOT `AsyncHydraDB` | Hermes memory providers are synchronous (no asyncio). The sync client's methods block on HTTP I/O, which is what a sync provider expects. |
| Write path | Fire-and-forget on daemon threads | `sync_turn()` and `on_memory_write()` must be non-blocking. HydraDB ingestion is async (seconds to index), so blocking would stall the turn. |
| Read path | `queue_prefetch()` → background query, `prefetch()` returns cached | Same pattern as mem0. `prefetch()` must be fast (returns stashed result); the actual HydraDB query runs in `queue_prefetch()` after each turn. |
| Circuit breaker | 5 consecutive failures → 120s cooldown | Cloud provider; same pattern as mem0. Prevents hammering a down API. |
| `infer` mode | `sync_turn` uses `infer: true` (auto-extract); `on_memory_write` uses `infer: false` (verbatim) | `sync_turn` sends raw conversation — HydraDB extracts durable facts. `on_memory_write` mirrors curated built-in entries — store verbatim to preserve the agent's curation. |
| Tools | `hydradb_search`, `hydradb_profile`, `hydradb_conclude` | Same three-tool pattern as mem0. Prefixed to avoid core-tool-name collisions. |
| Tenant provisioning | Auto-create in `initialize()` with readiness polling | First-run: create tenant if it doesn't exist, poll until ready. Subsequent runs: skip. |

---

## 2. File Layout

```
~/.hermes/hermes-agent/plugins/memory/hydradb/
├── __init__.py          # HydraDBMemoryProvider class + register(ctx)
├── plugin.yaml          # Manifest: name, version, pip_dependencies, requires_env, hooks
└── README.md            # Setup instructions, config reference, tool descriptions
```

No `cli.py` in v1. Can add later for `hermes hydradb ...` subcommands (tenant
management, memory browsing, stats).

### plugin.yaml

```yaml
name: hydradb
version: 0.1.0
description: "HydraDB-backed persistent memory — graph-enriched recall and personalized context via the HydraDB v2 API."
pip_dependencies:
  - hydradb-sdk>=2,<3
requires_env:
  - HYDRA_DB_API_KEY
hooks:
  - on_session_end
  - on_memory_write
```

### Config locations

1. `config.yaml` → `memory.provider: "hydradb"` (activation)
2. `~/.hermes/.env` → `HYDRA_DB_API_KEY=<key>` (secret)
3. `~/.hermes/hydradb.json` → `{"tenant_id": "...", "sub_tenant_id": "...", "query_mode": "thinking"}` (non-secret, written by `save_config()`)

---

## 3. Class Architecture

```
HydraDBMemoryProvider(MemoryProvider)
│
├── Config layer
│   ├── _load_config()           # env + hydradb.json merge (static function)
│   ├── get_config_schema()      # field descriptors for `hermes memory setup`
│   └── save_config(values, hh)  # write hydradb.json
│
├── Client layer
│   ├── _get_client()            # lazy, thread-safe, locked
│   ├── _ensure_tenant()         # auto-provision + readiness poll (called from initialize)
│   └── Circuit breaker
│       ├── _is_breaker_open()
│       ├── _record_success()
│       └── _record_failure()
│
├── Lifecycle (ABC requirements)
│   ├── name                     # "hydradb"
│   ├── is_available()           # check HYDRA_DB_API_KEY + import, no network
│   └── initialize(sid, **kw)    # load config, capture kwargs, ensure tenant
│
├── Read path
│   ├── system_prompt_block()    # static: "HydraDB Memory. Active. ..."
│   ├── prefetch(query, sid)     # return cached result (fast)
│   ├── queue_prefetch(query, sid)  # background query → stash result
│   └── _format_chunks(result)   # manual formatting from chunks (NOT build_string)
│
├── Write path
│   ├── sync_turn(user, asst, sid, messages)  # fire-and-forget ingest (infer:true)
│   └── on_memory_write(action, target, content, metadata)  # mirror built-in writes
│
├── Tools
│   ├── get_tool_schemas()       # [SEARCH_SCHEMA, PROFILE_SCHEMA, CONCLUDE_SCHEMA]
│   └── handle_tool_call(name, args)  # dispatch + JSON result
│
├── Session hooks
│   ├── on_session_end(messages)  # ingest full conversation summary
│   └── shutdown()                # drain threads, close client
│
└── register(ctx)                 # module-level entry point
```

---

## 4. Method-by-Method Specification

### 4.1 Config

**`_load_config() -> dict`** (module-level function)

Reads `HYDRA_DB_API_KEY` from env, then merges overrides from
`$HERMES_HOME/hydradb.json`. Defaults:

```python
{
    "api_key": os.environ.get("HYDRA_DB_API_KEY", ""),
    "tenant_id": "hermes",           # default tenant name (one per deployment)
    "sub_tenant_id": None,           # None → auto-set to agent_identity (profile name)
    "query_mode": "thinking",        # "fast" or "thinking"
    "query_by": "hybrid",            # "hybrid" or "text"
    "max_results": 10,               # prefetch top_k
}
```

> **`sub_tenant_id` default is `None`, not a fixed string.** In `initialize()`,
> if `sub_tenant_id` is None or empty, it's set to `agent_identity` — the
> active profile name that Hermes passes in kwargs. This gives automatic
> per-profile isolation with zero config. To override (e.g. point a profile
> at a `"shared"` sub-tenant, or merge two profiles), set it explicitly in
> `hydradb.json`.

**`get_config_schema() -> list[dict]`**

```python
[
    {"key": "api_key", "description": "HydraDB API key", "secret": True,
     "required": True, "env_var": "HYDRA_DB_API_KEY",
     "url": "https://app.hydradb.com"},
    {"key": "tenant_id", "description": "Tenant identifier (one per deployment, shared by all profiles)",
     "default": "hermes"},
    {"key": "sub_tenant_id", "description": "Sub-tenant for memory scoping. Leave empty to auto-use the profile name (per-profile isolation). Set to 'shared' for cross-profile memory.",
     "default": ""},
    {"key": "query_mode", "description": "Query mode: 'thinking' (reranking + graph traversal) or 'fast' (low latency)",
     "default": "thinking", "choices": ["thinking", "fast"]},
]
```

**`save_config(values, hermes_home)`**

Writes non-secret fields (`tenant_id`, `sub_tenant_id`, `query_mode`) to
`$HERMES_HOME/hydradb.json` using `utils.atomic_json_write` (mode 0o600), merging
with any existing file. The `api_key` is NOT written here — the wizard handles
secrets separately to `.env`.

### 4.2 Lifecycle

**`name`** → `"hydradb"`

**`is_available() -> bool`**

No network calls. Returns True iff:
1. `HYDRA_DB_API_KEY` is set (from env or config file), AND
2. `hydradb-sdk` is importable (`from hydra_db import HydraDB` succeeds).

```python
def is_available(self) -> bool:
    cfg = _load_config()
    if not cfg.get("api_key"):
        return False
    try:
        import hydra_db  # noqa: F401
        return True
    except ImportError:
        return False
```

**`initialize(session_id, **kwargs) -> None`**

1. Load config (`_load_config()`).
2. Capture `hermes_home` from kwargs (for any local cache files).
3. **Resolve `sub_tenant_id`:** if config provides a non-empty value, use it.
   Otherwise, default to `agent_identity` (the active profile name from
   kwargs). This gives per-profile isolation with zero config — each profile
   writes to its own sub-tenant automatically.
4. Skip writes if `agent_context != "primary"` (cron/subagent/flush contexts
   would corrupt the profile's memory with system prompts).
5. Call `_ensure_tenant()` — creates the tenant if it doesn't exist, polls
   readiness. This is the one network call in initialize; it's acceptable
   because it runs once at agent startup, not per-turn.

```python
def initialize(self, session_id, **kwargs):
    self._config = _load_config()
    self._api_key = self._config["api_key"]
    self._tenant_id = self._config["tenant_id"]
    self._agent_identity = kwargs.get("agent_identity", "")
    # Resolve sub_tenant_id: explicit config > profile name (agent_identity)
    self._sub_tenant_id = self._config.get("sub_tenant_id") or self._agent_identity or "default"
    self._query_mode = self._config["query_mode"]
    self._query_by = self._config["query_by"]
    self._max_results = self._config["max_results"]
    self._hermes_home = kwargs.get("hermes_home", "")
    self._agent_context = kwargs.get("agent_context", "primary")
    self._user_name = kwargs.get("user_name", "User")
    logger.info("HydraDB provider initialized: tenant=%s sub_tenant=%s (profile=%s)",
                self._tenant_id, self._sub_tenant_id, self._agent_identity)
    # Ensure tenant exists and is ready (first-run only; cached after)
    self._ensure_tenant()
```

**`_ensure_tenant()`**

```python
def _ensure_tenant(self):
    """Create tenant if needed, poll until ready for ingestion."""
    if self._is_breaker_open():
        return
    try:
        client = self._get_client()
        # Check if tenant already exists
        existing = client.tenants.list().data
        tenant_ids = existing.tenant_ids or [] if existing else []
        if self._tenant_id not in tenant_ids:
            client.tenants.create(tenant_id=self._tenant_id)
        # Poll until ready
        for _ in range(60):  # max ~5 min
            status = client.tenants.status(tenant_id=self._tenant_id)
            if status.data.infra.ready_for_ingestion:
                self._record_success()
                return
            time.sleep(5)
        logger.warning("HydraDB tenant not ready after 5min; proceeding anyway")
        self._record_success()
    except Exception as e:
        self._record_failure()
        logger.warning("HydraDB tenant provisioning failed: %s", e)
```

### 4.3 Read Path

**`system_prompt_block() -> str`**

Static text injected into the system prompt at assembly time:

```
# HydraDB Memory
Active. Tenant: {tenant_id}. Sub-tenant: {sub_tenant_id}.
Use hydradb_search to find memories by meaning, hydradb_profile for a
full overview, hydradb_conclude to store a durable fact.
```

**`prefetch(query, *, session_id="") -> str`**

Returns the cached result from the last `queue_prefetch()`. Joins any
in-flight prefetch thread with a 3s timeout (same as mem0). Returns empty
string if nothing cached. Does NOT pre-wrap in `<memory-context>` fences —
`build_memory_context_block` does that.

```python
def prefetch(self, query, *, session_id=""):
    if self._prefetch_thread and self._prefetch_thread.is_alive():
        self._prefetch_thread.join(timeout=3.0)
    with self._prefetch_lock:
        result = self._prefetch_result
        self._prefetch_result = ""
    if not result:
        return ""
    return f"## HydraDB Memory\n{result}"
```

**`queue_prefetch(query, *, session_id="") -> None`**

Background thread: query HydraDB for memories relevant to the upcoming turn.
Uses `type="memory"`, `query_by="hybrid"`, `mode=<config>`. Formats chunks
manually from `result.data.chunks` (`chunk_content` + `relevancy_score`) —
NOT `build_string()` (72-89% framing overhead). Stashes the result for the
next `prefetch()`.

```python
def queue_prefetch(self, query, *, session_id=""):
    if self._is_breaker_open():
        return

    def _run():
        try:
            client = self._get_client()
            result = client.query(
                tenant_id=self._tenant_id,
                sub_tenant_id=self._sub_tenant_id,
                query=query,
                type="memory",
                query_by=self._query_by,
                mode=self._query_mode,
                max_results=self._max_results,
                graph_context=True,
            )
            # Format chunks manually — build_string() has 72-89% framing overhead
            context_str = self._format_chunks(result)
            if context_str and context_str.strip():
                with self._prefetch_lock:
                    self._prefetch_result = context_str
            self._record_success()
        except Exception as e:
            self._record_failure()
            logger.debug("HydraDB prefetch failed: %s", e)

    self._prefetch_thread = threading.Thread(
        target=_run, daemon=True, name="hydradb-prefetch")
    self._prefetch_thread.start()
```

**`_format_chunks(result) -> str`**

Formats query result chunks into clean prose for the system prompt.
Strips all `build_string()` framing overhead (72-89%). Each chunk becomes
a single paragraph with its content only. Chunks below a relevancy threshold
(0.3) are dropped.

```python
@staticmethod
def _format_chunks(result, min_score=0.3) -> str:
    chunks = getattr(result.data, 'chunks', None) or []
    if not chunks:
        return ""
    lines = []
    for c in chunks:
        score = getattr(c, 'relevancy_score', 0) or 0
        if score < min_score:
            continue
        content = getattr(c, 'chunk_content', '') or ''
        if content.strip():
            lines.append(content.strip())
    return "\n\n".join(lines)
```

### 4.4 Write Path

**`sync_turn(user_content, assistant_content, *, session_id="", messages=None) -> None`**

Fire-and-forget on a daemon thread. Ingests the user+assistant pair as a
memory with `infer: true` — HydraDB extracts durable facts server-side.
Skipped for non-primary agent contexts.

```python
def sync_turn(self, user_content, assistant_content, *, session_id="", messages=None):
    if self._agent_context != "primary":
        return
    if self._is_breaker_open():
        return

    def _sync():
        try:
            client = self._get_client()
            # Combine the turn into a single memory text
            text = f"User: {user_content}\nAssistant: {assistant_content}"
            memories = json.dumps([{
                "text": text,
                "infer": True,           # HydraDB extracts durable facts
                "user_name": self._user_name,
            }])
            client.context.ingest(
                type="memory",
                tenant_id=self._tenant_id,
                sub_tenant_id=self._sub_tenant_id,
                memories=memories,
                upsert="true",       # SDK expects string, not bool
            )
            self._record_success()
        except Exception as e:
            self._record_failure()
            logger.warning("HydraDB sync_turn failed: %s", e)

    if self._sync_thread and self._sync_thread.is_alive():
        self._sync_thread.join(timeout=5.0)
    self._sync_thread = threading.Thread(
        target=_sync, daemon=True, name="hydradb-sync")
    self._sync_thread.start()
```

**`on_memory_write(action, target, content, metadata=None) -> None`**

Mirrors built-in MEMORY.md/USER.md writes to HydraDB. Uses `infer: false`
(preserves the agent's curation verbatim). Generates a stable `id` from the
content hash for upsert/delete. Maps `target` to a metadata field for
optional filtering.

```python
def on_memory_write(self, action, target, content, metadata=None):
    if self._agent_context != "primary":
        return
    if self._is_breaker_open():
        return

    def _write():
        try:
            client = self._get_client()
            # Stable ID from content hash (deterministic upsert/delete)
            entry_id = f"hermes_{target}_{hashlib.sha256(content.encode()).hexdigest()[:16]}"

            if action == "remove":
                client.context.delete(
                    type="memory",
                    tenant_id=self._tenant_id,
                    sub_tenant_id=self._sub_tenant_id,
                    ids=[entry_id],
                )
            else:  # "add" or "replace" (upsert handles both)
                # NOTE: metadata must be a JSON STRING for type=memory
                mem_metadata = json.dumps({"target": target, "source": "builtin_mirror"})
                memories = json.dumps([{
                    "id": entry_id,
                    "text": content,
                    "infer": False,          # store verbatim
                    "user_name": self._user_name,
                    "metadata": mem_metadata,  # JSON string, NOT object
                }])
                client.context.ingest(
                    type="memory",
                    tenant_id=self._tenant_id,
                    sub_tenant_id=self._sub_tenant_id,
                    memories=memories,
                    upsert="true",       # SDK expects string, not bool
                )
            self._record_success()
        except Exception as e:
            self._record_failure()
            logger.debug("HydraDB on_memory_write failed: %s", e)

    threading.Thread(target=_write, daemon=True, name="hydradb-mirror").start()
```

> **Gotcha:** for `type=memory`, each memory item's `metadata` must be a
> **JSON-encoded string** (e.g. `"{\"target\":\"memory\"}"`), NOT an object.
> Passing an object returns `400 INVALID_INPUT`. This is wrapped in the helper
> above. See research doc §10.4.

### 4.5 Tools

Three tools, same pattern as mem0:

**`hydradb_search`** — semantic memory search:
```python
{
    "name": "hydradb_search",
    "description": "Search memories by meaning. Returns relevant facts ranked by similarity and graph context.",
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for."},
            "top_k": {"type": "integer", "description": "Max results (default: 10, max: 50)."},
        },
        "required": ["query"],
    },
}
```

**`hydradb_profile`** — full memory overview:
```python
{
    "name": "hydradb_profile",
    "description": "Retrieve all stored memories about the user — preferences, facts, project context. Use at conversation start.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}
```

**`hydradb_conclude`** — store a durable fact verbatim:
```python
{
    "name": "hydradb_conclude",
    "description": "Store a durable fact about the user. Stored verbatim (no extraction). Use for explicit preferences, corrections, or decisions.",
    "parameters": {
        "type": "object",
        "properties": {
            "conclusion": {"type": "string", "description": "The fact to store."},
        },
        "required": ["conclusion"],
    },
}
```

**`handle_tool_call(tool_name, args, **kwargs) -> str`**

Returns JSON strings. Uses `tool_error()` on failure. Circuit-breaker aware.

- `hydradb_search`: `client.query(type="memory", query=..., max_results=top_k)` → format chunks as `[{"memory": chunk.chunk_content, "score": chunk.relevancy_score}]`
- `hydradb_profile`: `client.query(type="memory", query="user profile preferences facts", max_results=50, mode="thinking")` → format all chunks
- `hydradb_conclude`: `client.context.ingest(type="memory", memories=[{text: conclusion, infer: False}], upsert="true")` → `{"result": "Fact stored."}`

### 4.6 Session Hooks

**`on_session_end(messages) -> None`**

Ingests a summary of the full conversation as a memory with `infer: true`.
This is the "episodic experience" — HydraDB extracts durable facts from the
session. Fire-and-forget on a daemon thread.

The implementation concatenates the last few user/assistant messages (not the
entire transcript — that would be too large) and ingests as a single memory.
Skip for non-primary contexts.

**`shutdown() -> None`**

Join prefetch + sync threads with 5s timeout. Clear the client reference
under the lock.

```python
def shutdown(self):
    for t in (self._prefetch_thread, self._sync_thread):
        if t and t.is_alive():
            t.join(timeout=5.0)
    with self._client_lock:
        self._client = None
```

### 4.7 register(ctx)

```python
def register(ctx) -> None:
    ctx.register_memory_provider(HydraDBMemoryProvider())
```

---

## 5. Data Flow

### 5.1 Read flow (per turn, per profile)

```
User sends message (e.g. in the "bridgetrader" profile)
  ↓
turn_context.py calls MemoryManager.prefetch_all(query)
  ↓
MemoryManager calls provider.prefetch(query)  ← returns CACHED result (fast)
  ↓
  (cached result came from a query scoped to sub_tenant_id="bridgetrader")
  ↓
prefetch result wrapped in <memory-context> fences, injected into turn context
  ↓
... LLM processes turn, calls tools, generates response ...
  ↓
turn_context.py calls MemoryManager.queue_prefetch_all(next_query)  ← starts BACKGROUND query
  ↓
MemoryManager calls provider.queue_prefetch(query)  ← daemon thread queries HydraDB
  ↓
  (query: type="memory", tenant_id="hermes", sub_tenant_id="bridgetrader")
  ↓
result stashed in _prefetch_result for next turn's prefetch()
```

> Each profile only sees its own memories. The `bridgetrader` profile queries
> `sub_tenant_id="bridgetrader"`, the `journal` profile queries
> `sub_tenant_id="journal"`, etc. No cross-profile leakage.

### 5.2 Write flow (two paths)

**Path A — sync_turn (automatic, every turn, per profile):**
```
Turn completes (e.g. in the "bridgetrader" profile)
  ↓
MemoryManager.sync_all(user, assistant, messages)  ← dispatched to bg executor
  ↓
provider.sync_turn()  ← daemon thread, fire-and-forget
  ↓
client.context.ingest(type="memory", tenant_id="hermes",
                      sub_tenant_id="bridgetrader",
                      infer=True, text="User: ... Assistant: ...")
  ↓
HydraDB indexes async (seconds)  ← fact available for next turn's query
```

**Path B — on_memory_write (when agent uses the built-in memory tool):**
```
Agent calls memory tool (add/replace/remove)
  ↓
MemoryManager.on_memory_write(action, target, content, metadata)
  ↓
provider.on_memory_write()  ← daemon thread, fire-and-forget
  ↓
client.context.ingest(type="memory", tenant_id="hermes",
                      sub_tenant_id=<current profile>,
                      infer=False, id=stable_hash, text=content)
  OR
client.context.delete(type="memory", tenant_id="hermes",
                      sub_tenant_id=<current profile>, ids=[stable_hash])
  ↓
HydraDB indexes/deletes async  ← change visible on next query
```

### 5.3 Same-turn visibility caveat

A fact written via `sync_turn` or `on_memory_write` this turn may not be
retrievable via `query` until HydraDB finishes indexing (seconds for
memories). This is acceptable for cross-session memory (the point is
persistence across sessions, not within a single turn).

If same-turn visibility becomes important, add an in-memory write-through
cache: keep the last N written entries in a deque, and merge them with
`query` results in `prefetch()` / `handle_tool_call()`, falling back to the
cache if the indexed result doesn't yet include the new fact. This is a
v2 enhancement, not needed for v1.

### 5.4 Evolution: promoting shared concepts to a shared sub-tenant

The 1:1 profile-to-sub-tenant design is the starting point. Over time,
patterns will emerge — facts that are universal across profiles (user
identity, communication preferences, trading approach, terminal setup)
versus facts that are profile-specific (BridgeTrader IBKR port, estate
contact info, dotfiles symlink conventions).

When the shared taxonomy is understood, promote concepts to a `"shared"`
sub-tenant without changing the code:

1. **Identify** which memories are universal by reviewing per-profile
   memories (via `hydradb_profile` tool or `client.context.list`).
2. **Write** the universal facts to `sub_tenant_id="shared"` — either via
   `hydradb_conclude` with a config override, a CLI command (`hermes hydradb
   promote` — future `cli.py`), or a migration script.
3. **Query both** — update `queue_prefetch` and `handle_tool_call` to query
   `type="memory"` against the profile's sub-tenant AND the `"shared"`
   sub-tenant (two queries, merged client-side). Or use a single query
   against `sub_tenant_id="shared"` for profile-agnostic facts and a
   per-profile query for project-specific context.
4. **Tag** memories with a `metadata` field (`{"scope": "profile"}` vs
   `{"scope": "shared"}`) to make filtering deterministic if needed later.

Sub-tenants are implicit in HydraDB (just a string — no pre-creation
needed), so this evolution requires no schema changes, no tenant
reprovisioning, and no code changes to the core provider. Only the query
logic in `queue_prefetch` / `handle_tool_call` would expand to read from
two sub-tenants instead of one.

---

## 6. Circuit Breaker

Same pattern as mem0:

```python
_BREAKER_THRESHOLD = 5       # consecutive failures before tripping
_BREAKER_COOLDOWN_SECS = 120  # pause duration after tripping
```

- `_is_breaker_open()`: returns True if failures >= threshold AND cooldown
  hasn't expired. Resets counter after cooldown.
- `_record_success()`: resets consecutive_failures to 0.
- `_record_failure()`: increments counter; if threshold reached, sets
  `breaker_open_until = time.monotonic() + cooldown` and logs a warning.

All network calls check `_is_breaker_open()` first and short-circuit if open.

---

## 7. Tenant Auto-Provisioning

`_ensure_tenant()` runs in `initialize()` (once per agent startup):

1. List existing tenants: `client.tenants.list()`
2. If `tenant_id` not in the list: `client.tenants.create(tenant_id=...)`
3. Poll `client.tenants.status(tenant_id=...)` until
   `data.infra.ready_for_ingestion` is True (every 5s, max 60 attempts = 5min)
4. On failure: log warning, proceed anyway (the provider will fail-open on
   subsequent calls — the circuit breaker will trip if the API is down)

No `tenant_metadata_schema` in v1. The built-in memory uses `{memory, user}`
targets, which we encode as a `metadata` field on each memory item. If
deterministic filtering by target becomes needed, declare the schema at
tenant creation (immutable — see research doc §10.6). For now, `type="memory"`
queries return all memories regardless of target, which is fine for a
personal memory store.

---

## 8. SDK Method Reference (Verified)

All signatures confirmed against `hydradb-sdk==2.0.1` installed in the Hermes
venv. All parameters are keyword-only (`*` in the signature).

### Constructor
```python
HydraDB(token=str, base_url=None, timeout=None, ...)
```

### Query (the read path)
```python
client.query(
    *, tenant_id, query, sub_tenant_id=None, type="knowledge",
    query_by="hybrid", mode="fast", max_results=None,
    alpha=0.8, recency_bias=0.0, graph_context=True,
    query_forceful_relations=True, query_apps=False,
    metadata_filters=None, additional_context=None, operator="or",
) -> HandlerEnvelopeSearchV2RetrievalResult
```
- `result.data.chunks` → list of `SearchV2Chunk` with fields:
  `chunk_content` (str), `id` (str), `relevancy_score` (float),
  `metadata` (dict), `additional_metadata` (dict), `source_type` (str),
  `source_title` (str)
- `from hydra_db.helpers import build_string` → `build_string(result)` → str
  ⚠️ **NOT USED:** 72-89% framing overhead. Use manual formatting from
  `result.data.chunks` (`chunk_content` + `relevancy_score`) instead.

### Context.ingest (the write path)
```python
client.context.ingest(
    *, tenant_id, type, memories=None, sub_tenant_id=None,
    upsert=None, documents=None, document_metadata=None,
    app_knowledge=None,
) -> HandlerEnvelopeIngestionV2SourceUploadResponse
```
- `memories` = JSON-stringified array of `{id?, text, infer?, user_name?, metadata?}`
- `metadata` for each memory item must be a **JSON string**, not an object
- `result.data.results` → list of `{id, status, error?, error_code?}`

### Context.delete
```python
client.context.delete(
    *, tenant_id, ids, sub_tenant_id=None, type="knowledge",
) -> HandlerEnvelopeSourcesSourceDeleteResponse
```

### Context.status (polling)
```python
client.context.status(
    *, tenant_id, ids, sub_tenant_id=None,
) -> HandlerEnvelopeIngestionV2BatchProcessingStatus
```
- `result.data.statuses` → list of `{indexing_status, error_message?}`
- `indexing_status` ∈ `queued, processing, completed, errored, graph_creation, success`

### Context.list (browsing)
```python
client.context.list(
    *, tenant_id, sub_tenant_id=None, type="knowledge",
    ids=None, page=1, page_size=50, filters=None, include_fields=None,
) -> HandlerEnvelopeListV2SourceListResponse
```

### Tenants
```python
client.tenants.create(*, tenant_id=None, tenant_metadata_schema=None, ...)
client.tenants.list() -> HandlerEnvelopeTenantsTenantIdsResponse
client.tenants.status(*, tenant_id) -> HandlerEnvelopeTenantsInfraStatusResponseV2
client.tenants.sub_tenants(*, tenant_id) -> ...
client.tenants.delete(*, tenant_id) -> ...
```

---

## 9. Testing Strategy

Test file: `~/.hermes/hermes-agent/tests/plugins/memory/test_hydradb_provider.py`

Pattern: fake client (same as `test_mem0_v2.py`), monkeypatch `_get_client`.

### Test classes

**`TestHydraDBConfig`**
- `test_is_available_with_key` — env var set + import succeeds → True
- `test_is_available_no_key` — no env var → False
- `test_is_available_no_sdk` — import fails → False
- `test_load_config_defaults` — defaults applied when no hydradb.json
- `test_load_config_overrides` — hydradb.json values merge correctly
- `test_save_config_writes_json` — non-secret fields written to hydradb.json

**`TestHydraDBQueries`** (fake client)
- `test_prefetch_returns_cached` — queue_prefetch stashes, prefetch returns
- `test_prefetch_empty_when_no_cache` — returns "" if nothing cached
- `test_queue_prefetch_uses_correct_params` — type="memory", tenant, sub_tenant
- `test_search_tool_calls_query` — handle_tool_call("hydradb_search", ...) → query called
- `test_search_tool_formats_results` — chunks → JSON with memory + score
- `test_profile_tool_calls_query` — handle_tool_call("hydradb_profile", ...) → query called
- `test_conclude_tool_calls_ingest` — handle_tool_call("hydradb_conclude", ...) → ingest called
- `test_metadata_is_json_string` — memory metadata encoded as string, not object

**`TestHydraDBWrites`** (fake client)
- `test_sync_turn_ingests_with_infer_true` — sync_turn → ingest(infer=True)
- `test_sync_turn_skips_non_primary` — agent_context="cron" → no ingest
- `test_on_memory_write_add` — action="add" → ingest(infer=False, stable id)
- `test_on_memory_write_replace` — action="replace" → ingest (upsert)
- `test_on_memory_write_remove` — action="remove" → delete(ids=[stable_id])
- `test_on_memory_write_skips_non_primary` — agent_context != "primary"

**`TestHydraDBCircuitBreaker`**
- `test_breaker_opens_after_threshold` — 5 failures → breaker open
- `test_breaker_blocks_calls` — open breaker → sync_turn/prefetch short-circuit
- `test_breaker_resets_on_success` — success after open → resets

**`TestHydraDBShutdown`**
- `test_shutdown_joins_threads` — threads joined with timeout
- `test_shutdown_clears_client` — client set to None

### Fake client

```python
class FakeHydraDBClient:
    """Fake HydraDB client that captures call kwargs and returns canned responses."""
    def __init__(self):
        self.captured_queries = []
        self.captured_ingests = []
        self.captured_deletes = []
        self._query_result = self._make_empty_result()

    def _make_empty_result(self):
        # Return an object with .data.chunks = []
        ...

    @property
    def query(self):
        return self._query
    def _query(self, **kwargs):
        self.captured_queries.append(kwargs)
        return self._query_result

    @property
    def context(self):
        return self._context
    # ContextClient with ingest, delete, status, list, inspect

    @property
    def tenants(self):
        return self._tenants
    # TenantsClient with create, list, status, sub_tenants
```

---

## 10. Open Questions for the Build Phase

1. **RESOLVED — Tenant listing response shape:** `client.tenants.list().data.tenant_ids`
   is `Optional[List[str]]`. The `_ensure_tenant()` code checks against
   `existing.tenant_ids or []`. Confirmed from the installed SDK's
   `TenantsTenantIdsResponse` pydantic model.

2. **REVISITED — `build_string` output format:** Real testing reveals the docs'
   "clean prose" claim is misleading. `build_string()` produces heavy framing
   overhead (72-89%): `=== CONTEXT ===`, `Chunk N`, `Source: ... (id: ...)
   (score: ...)`, `Extra context:`, `Graph Relations:` headers. For the system
   prompt we should format chunks manually from `result.data.chunks` using
   `chunk_content` + `relevancy_score`, stripping all framing. Graph relations
   (from `infer:true` memories) are valuable but should be reformatted tersely.

3. **RESOLVED — Memory indexing latency:** Tested with real API:
   - Ingest: ~500ms (API accept)
   - Query: ~2-2.5s
   - Memory queryable: 1-5s after ingest
   - **Status endpoint caveat:** `GET /context/status` returns `FILE_NOT_FOUND`
     / "ID not found" for the first 1-2s — a race condition, not actual
     failure. Polling should tolerate this and wait for `completed` or a
     real error.
   - The same-turn visibility cache (§5.3) is sufficient to bridge the 2-5s
     gap. Fire-and-forget on a daemon thread (as designed) is correct.

4. **PARTIALLY RESOLVED — Rate limits / free tier:** HydraDB has a **Ship (Free)**
   tier at $0/month claiming "unlimited API calls & tenants." Pricing is
   storage-based, not call-count-based. Paid tiers: Surge ($25/mo, 2GB), Scale
   ($399/mo, 10GB). **Exact rate limit numbers (RPM/RPS) are intentionally not
   published** — gated behind app.hydradb.com dashboard login or
   founders@hydradb.com. For personal Hermes usage (~1 ingest + 1 query per
   turn), the free tier appears more than sufficient based on public claims.
   The circuit breaker pattern (§1) already handles 429s gracefully. Action:
   sign up for free tier when building, monitor for throttling, adjust if
   needed.

5. **RESOLVED — `upsert` parameter type:** the SDK signature shows
   `upsert: Optional[str]` (not `bool`). Pass `"true"` / `"false"` as a string,
   not `True` / `False`. Confirmed from `inspect.signature(Context.ingest)`.

6. **Tenant auto-create race condition:** if two profiles start simultaneously
   and the tenant doesn't exist yet, both may try to create it. HydraDB likely
   returns 409 Conflict — handle gracefully (treat as "already exists").
