# External Integrations

**Analysis Date:** 2026-06-20

## APIs & External Services

**Memory Storage & Retrieval:**
- HydraDB Cloud — Managed cloud graph database for AI memory (unified context substrate)
  - SDK/Client: `hydradb-sdk` v2.0.1, imported as `from hydra_db import HydraDB` (sync client; no asyncio)
  - Auth: Bearer token via `HYDRA_DB_API_KEY` environment variable (stored in `~/.hermes/.env`)
  - API Base URL: `https://api.hydradb.com`
  - API Version: v2 (sent via `API-Version: 2` request header)
  - SDK Operations Used:
    - `client.query()` — Hybrid search (semantic + BM25 + graph traversal + recency scoring); params: tenant_id, sub_tenant_id, query, type, query_by, mode, max_results, graph_context
    - `client.context.ingest()` — Memory ingestion; params: type, tenant_id, sub_tenant_id, memories (JSON string), upsert
  - Query Modes: `thinking` (reranking + graph traversal, ~2-2.5s) or `fast` (low latency)
  - Ingest Latency: ~500ms; queryable in 1-5s after ingest
  - Rate Limits: None documented (free tier: unlimited API calls, storage-based pricing)
  - Pricing Tiers: Free ($0/mo, unlimited calls), Surge ($25/mo, 2GB), Scale ($399/mo, 10GB)

## Data Storage

**Memory Database:**
- HydraDB Cloud — Stores user memories, inferred facts, episodic session summaries
  - Tenant topology: One shared `tenant_id` ("hermes") per deployment; one `sub_tenant_id` per Hermes profile (auto-resolved to profile name for per-profile isolation, or "shared" for cross-profile memory)
  - Client: `HydraDB` sync SDK (instantiated with `token=self._api_key` in `_get_client()`)
  - Write pattern: Fire-and-forget on daemon threads (non-blocking); `sync_turn` uses `infer: true` (auto-extract facts), `on_memory_write` uses `infer: false` (verbatim mirroring)
  - Read pattern: `queue_prefetch()` fires background query → `prefetch()` returns cached result (fast path); tools use on-demand queries
  - Circuit breaker: 5 consecutive failures → 120s cooldown

**File Storage:**
- None (all data lives in HydraDB cloud; no local file storage beyond config)

**Caching:**
- None (query results cached per-turn in memory via `_prefetch_result`; no Redis or external cache)

## Authentication & Identity

**API Auth:**
- HydraDB API Key — Bearer token authentication
  - Credential: `HYDRA_DB_API_KEY` environment variable
  - Obtained from: `https://app.hydradb.com` (user signup → create tenant → copy key)
  - Storage: `~/.hermes/.env` file (gitignored, not committed)

**Provider Identity:**
- No external identity provider; tenant/sub-tenant isolation handled entirely by HydraDB's multi-tenancy model
- Per-profile identity: `sub_tenant_id` auto-resolves to Hermes `agent_identity` (profile name) passed via `initialize()` kwargs

## Monitoring & Observability

**Error Tracking:**
- None (no Sentry, no external error service)

**Analytics:**
- None

**Logs:**
- Python `logging` module — stdout/stderr only; no external log aggregation
- Log level: Debug for failures (`logger.debug("HydraDB prefetch failed", exc_info=True)`), Warning for circuit breaker open, Info for initialization
- Logger name: `__name__` (resolves to the module path)

## CI/CD & Deployment

**Hosting:**
- None (plugin runs embedded in Hermes Agent process on user's machine)

**CI Pipeline:**
- None (no GitHub Actions, no CI configuration found in project)

## Environment Configuration

**Development:**
- Required env vars: `HYDRA_DB_API_KEY`
- Optional env vars: `HERMES_HOME` (defaults to `~/.hermes`)
- Config file: `~/.hermes/hydradb.json` (non-secret: tenant_id, sub_tenant_id, query_mode)
- Activation: `~/.hermes/config.yaml` → `memory.provider: "hydradb"`
- Secrets location: `~/.hermes/.env` (gitignored)
- Local HydraDB SDK: installed in .venv at `/home/bboyd/src/cos-mcp/.venv/`

**Staging:**
- Not applicable (no staging environment)

**Production:**
- Secrets management: User-managed `~/.hermes/.env` file
- HydraDB tenant: Production tenant at `https://api.hydradb.com`
- Failover/redundancy: Circuit breaker (5 failures → 120s cooldown) provides resilience; no external failover

## Hermes Agent Runtime Integration

**Provider Registration:**
- Entry point: `register(ctx)` — calls `ctx.register_memory_provider(HydraDBMemoryProvider())`
- Plugin discovery: In-tree at `~/.hermes/hermes-agent/plugins/memory/hydradb/`

**Runtime Hooks:**
- `initialize(session_id, **kwargs)` — Called at session start; receives `hermes_home`, `platform`, `agent_identity`, `agent_context` kwargs
- `system_prompt_block()` — Returns static block injected into system prompt: "HydraDB Memory. Active. Memories are retrieved each turn."
- `queue_prefetch(query)` / `prefetch(query)` — Background query + cached result retrieval per turn
- `sync_turn(user_content, assistant_content)` — Fire-and-forget ingest after each turn (only on primary agent_context)
- `on_memory_write(action, target, content)` — Mirrors built-in memory writes into HydraDB
- `on_session_end(messages)` — Ingests session summary as episodic memory
- `shutdown()` — Joins background threads, clears client

**Tool Integration (OpenAI function-calling):**
- `hydradb_search` — On-demand memory search via client.query()
- `hydradb_profile` — Retrieves user profile summary
- `hydradb_conclude` — Stores a durable fact (client.context.ingest, infer=false)

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

---

*Integration audit: 2026-06-20*
*Update when adding/removing external services*
