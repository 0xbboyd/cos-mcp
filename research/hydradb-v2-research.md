# HydraDB v2 — Comprehensive Build-Ready Research Document

> **Purpose:** Authoritative reference for building a HydraDB-backed memory provider plugin for Hermes Agent.
> This document is derived from the official HydraDB v2 docs (essentials, get-started, API reference),
> the v2 OpenAPI JSON spec (`https://docs.hydradb.com/api-reference/v2/openapi.json`, OpenAPI 3.1.0,
> 47 paths / 53 operations), and the Agent Integration Guide (`/AGENTS`).
> It is intended to let a developer build against HydraDB without re-reading the docs.

**Sources cross-checked:** OpenAPI JSON (authoritative API surface) + every essentials page + every endpoint
reference page + error-responses + webhooks + context-graphs + semantic-search + app-sources + cookbooks index.

---

## 1. Executive Summary

**What HydraDB is:** HydraDB is a **unified context substrate for AI** — "the brain behind your AI." It provides
one brain for all working context: user memories, semantic knowledge, and episodic experiences. You ingest and
connect your most important tools; HydraDB automatically builds a context graph and returns useful context,
personalized for each user.

The pitch: *"VectorDBs find what's similar. But your agents want what's useful."* Pure vector search is
reasoning-blind, meaning-blind, serves identical results to everyone, and has no answer for relational questions
like "who owns this customer escalation?" HydraDB combines dense-vector similarity, BM25 keyword matching,
context-graph traversal, metadata scoping, and recency/personalization signals behind a single `POST /query`
endpoint.

### Deployment Model — CLOUD-ONLY (DEFINITIVE)

**HydraDB is a managed cloud SaaS. There is no self-hosted, on-prem, Docker, or local-binary deployment option.**

Evidence (verified across the docs):
- **Base URL:** `https://api.hydradb.com` (the OpenAPI `servers` field; every example hits this host).
- **API keys:** Issued at `https://app.hydradb.com` — the managed dashboard. *"Sign up at app.hydradb.com for your API key."*
- **Architecture page:** *"From the outside, you call a small set of HTTP APIs. Inside, HydraDB orchestrates… You interact only with the API; everything else stays behind the service boundary."* There is no documented way to run the orchestration layer yourself.
- **Webhooks:** *"Your webhook URL must be reachable from the public internet. Localhost and private network addresses are blocked."* — confirms a hosted, multi-tenant cloud model.
- **Enterprise:** Only path is email `founders@hydradb.com` for onboarding. No self-hosted enterprise tier is documented.
- **Scaling claim:** "Scales from 10K to 10M documents" — a managed-service capability statement.
- **No** Docker image, no binary download, no Helm chart, no Kubernetes manifest, no self-host guide, no open-source
  repo reference appears anywhere in the docs, llms.txt, cookbooks, or the OpenAPI spec. The internal codebase name
  in the OpenAPI `info.title` is "Cortex SDK API" (HydraDB is the product brand).

> **CRITICAL FOR THE BUILD PHASE:** The Hermes user prefers self-hosted (Linux/Docker/binary, no managed cloud).
> HydraDB **cannot** satisfy that preference. Any Hermes memory provider built on HydraDB will be a client of a
> managed cloud service. This is the single biggest constraint to surface before building. See §10.

### The Five Primitives

| Primitive | What it is | Deep dive |
| --- | --- | --- |
| **Tenants** | Isolated workspaces with a sub-tenant hierarchy, used to enforce RBAC. Hard isolation boundary. | §2, §4 |
| **Knowledge** | Complete working context of an org — documents, markdown, knowledge from apps (Slack, Notion, Gmail, Jira…). Shared, tenant-wide. | §2, §3 |
| **Memories** | User-scoped preferences, conversation history, inferred behavioral traits. Makes retrieval natively personalized. | §2, §7 |
| **Context Graphs** | Relationships between stored context, modeled as triplets (`source → relation → target`). Augments retrieval. | §2 |
| **Querying** | How agents read context — one `POST /query` endpoint over knowledge, memories, or both, combining semantic + BM25 + graph + metadata. | §6 |
| *(Metadata)* | Filters to enrich the context graph; also useful for deterministic retrieval. Often counted as a 5th primitive alongside Query. | §2 |

The docs' "Core Concepts" table lists **five primitives**: Knowledge, Memories, Querying, Tenants, Metadata.
(Context Graphs are described separately as the relational layer that augments Query.) For the Hermes use case
the relevant ones are **Memories**, **Tenants/sub-tenants**, and **Query**.

### Performance & Scale (vendor claims)
- Long-context accuracy: 90%+ on LongMemEvals.
- Retrieval latency: **sub-200ms**.
- Strict tenant isolation: no cross-tenant aggregation, ever; RBAC respected at all times.
- Scale: 10K → 10M documents.
- Benchmarks: `benchmarks.hydradb.com/hydradb.pdf`.

---

## 2. Core Concepts

### 2.1 Architecture — Three Logical Planes

You interact only with the API; everything else stays behind the service boundary.

| Plane | What it handles | Endpoints |
| --- | --- | --- |
| **Control** | API auth, tenant lifecycle, provisioning, status | `/tenants` family |
| **Ingestion** | File & app-source uploads, memory writes, parsing, chunking, embedding, graph construction | `POST /context/ingest`, `GET /context/status` |
| **Retrieval** | Hybrid query, metadata filtering, graph context, BM25, response shaping | `POST /query`, `GET /context/relations` |

Key architectural facts:
- **Two vector stores, one ingest endpoint.** `POST /context/ingest` routes to the **Knowledge** store when
  `type=knowledge` and to the **Memories** store when `type=memory`. Same endpoint, different bucket.
- **One query endpoint, every retrieval method.** `POST /query` is the only retrieval entry point. `type` and
  `query_by` decide what gets queried and how.

### 2.2 Ingestion Lifecycle (asynchronous)

Ingestion is **asynchronous**. A successful upload means HydraDB accepted the work and queued it; it does **not**
mean the content is immediately queryable. Each source moves through a status pipeline:

`queued` → `processing` → `graph_creation` → `completed`

| Status | Searchable? | Meaning |
| --- | --- | --- |
| `queued` | No | Accepted by the server, not yet picked up by a worker. |
| `processing` | No | Being parsed, chunked, and embedded. |
| `graph_creation` | **Yes** | Indexed and retrievable; the knowledge graph is still being built. Already searchable via `/query`, but graph context may be incomplete. |
| `completed` | Yes | Fully indexed and graphed. Ready for all retrieval modes. |
| `errored` | No | Processing failed. Inspect `error_code` and `message`. **Terminal.** |

> **`graph_creation` is already queryable.** Chunks become retrievable as soon as embedding finishes; you only
> need to wait for `completed` when you specifically need full graph context (`graph_context: true` on query).
>
> Unknown IDs returned to `/context/status` come back as `errored` (not silently dropped), so typos surface.

Typical processing times: memories = seconds; small docs (<50 pages) = 1–5 min; large docs (50+ pages) = 5–15 min.

Poll `GET /context/status` with the returned `id`s to follow items through the pipeline, OR register an
`indexing.status_changed` webhook to be notified at terminal states.

### 2.3 End-to-End Flow (the canonical loop)

1. **Create a tenant** — `POST /tenants` (isolated workspace, optionally with a metadata schema declared up front).
2. **Wait for provisioning** — poll `GET /tenants/status` until `infra.ready_for_ingestion` is `true` (i.e.
   `vectorstore_status.knowledge`, `vectorstore_status.memories`, and `graph_status` all `true`).
3. **Ingest content** — `POST /context/ingest`, `type=knowledge` (shared docs) or `type=memory` (per-user context).
4. **Watch indexing finish** — poll `GET /context/status` until each `id` reaches `completed`
   (or `graph_creation` if you don't need graph traversal).
5. **Query** — `POST /query` with `type: "knowledge"|"memory"|"all"`, paired with `query_by: "hybrid"` (default)
   or `"text"` (BM25, with `operator`).

### 2.4 Tenants & Sub-tenants (verbatim from docs)

> A `tenant_id` is the **top-level isolation boundary** — no tenant can read another tenant's data. Within a
> tenant, `sub_tenant_id` carves out logical partitions: users, teams, workspaces, or projects.
>
> Omitting `sub_tenant_id` on any call resolves to the tenant's **default sub-tenant**, which is auto-created
> with the tenant itself.

**Key behaviors:**
- Write and query operations are scoped by `tenant_id`.
- When `sub_tenant_id` is provided, it further narrows the scope.
- If `sub_tenant_id` is omitted, HydraDB uses the tenant's default sub-tenant.
- **Use the same scoping values consistently across writes and reads.** Writing with one `sub_tenant_id` and
  querying with a different one may cause data to not appear in results.
- **Sub-tenants are implicitly created** — auto-created when ingestion writes data under a new `sub_tenant_id`.
- **Do not** use `sub_tenant_id` as a substitute for separate prod/staging tenants — environments should be
  separated at the `tenant_id` level.
- **Don't use `metadata_filters` as a substitute for `sub_tenant_id`** (AGENTS guide, explicit rule).

**Mapping patterns:**

| Product shape | Tenant pattern | Sub-tenant pattern |
| --- | --- | --- |
| B2B SaaS | One tenant per customer org | One sub-tenant per team/workspace/end user |
| B2C app | One tenant for the application | One sub-tenant per end user |
| Internal tools | One tenant per company/environment | One sub-tenant per department/project |

### 2.5 Knowledge (verbatim)

> **Knowledge** is the shared, tenant-wide context that every user and agent in a tenant can query — product
> documents, internal wikis, policy PDFs, Slack threads, Notion pages, CSVs, emails, and other reusable
> workspace content.

Knowledge vs. Memories:

| | Knowledge | Memories |
| --- | --- | --- |
| Content | Documents, files, app-generated content | User preferences, conversations, inferred traits |
| Scope | Shared across all users in a tenant | Per-user, scoped by `sub_tenant_id` |
| Mutability | Versioned — replaced or deleted explicitly | Dynamic — evolves with every interaction |
| Query via | `POST /query` `type: "knowledge"` | `POST /query` `type: "memory"` |
| `vectorstore_status` | `vectorstore_status.knowledge` | `vectorstore_status.memories` |

> **One endpoint, two stores.** Knowledge and memories are stored separately, but a single `POST /query` call
> can hit both via `type: "all"` — results are merged and re-ranked together.

**Decision rule:** "Would a different user benefit from seeing this?" → Yes: Knowledge; No: Memory.

**Ingestion pipeline (knowledge):** parse (PDF/DOCX/Markdown/CSV/plain text/app-source JSON) → chunk into
semantically coherent segments → extract entities & relationships (build graph nodes/edges) → embed every chunk
into dense + sparse vector stores → index so it becomes queryable.

### 2.6 Memories (verbatim)

> Most applications start every conversation from scratch. **Memories let your agent carry useful user-specific
> context from one session to the next**, so responses become more personal over time.
>
> A **memory** is one unit of context: a stated preference, an inferred trait, a past conversation, a decision,
> feedback, or a fact your agent should be able to retrieve later. HydraDB stores every memory in a structural
> context graph, so related preferences surface together at retrieval time.

> Before you ingest anything, ask one question: **"Should every user be able to retrieve this?"**
> About a specific user or session → Memory. Shared across all users in your tenant → Knowledge.

> Memories are not set once at onboarding. They compound with every interaction, so your agent becomes more
> accurate and feels more familiar to the end user over time.

**Ingestion modes (the `infer` flag):**
- **`infer: true`** — HydraDB extracts the underlying preference/trait/fact. You can ship raw behavioral logs
  (interaction events, UI actions, dialogue) and HydraDB derives the structured insight.
- **`infer: false`** (the default) — HydraDB stores exactly what you send. Useful for deterministic facts you've
  already captured.

**What happens when you ingest a memory** (`POST /context/ingest` `type=memory`):
1. Parse the input — raw text, markdown, or user–assistant pairs.
2. Optionally infer meaning — if `infer: true`, extract the underlying preference/trait/fact.
3. Process and index the result.
4. Make it queryable — later `POST /query` retrieves it with `type: "memory"` or `type: "all"`.

(Full memory field reference in §3 and §7.)

### 2.7 Context Graphs

A **context graph** is a structured map of relationships between stored pieces of context, represented as
**triplets**: `source → relation → target`. Each triplet is a directional connection; `source`, `relation`, and
`target` are returned as structured objects (not plain strings).

> **Context graphs augment retrieval. They do not replace it.**

**Hybrid:** relationships are extracted at ingestion time and traversed at query time.
- At ingestion: HydraDB extracts relationships and stores them in the graph; sources can declare explicit
  relationships via a `relations` payload (forceful relations).
- At query (`graph_context: true`, the default): run hybrid retrieval → traverse the graph → return
  `query_paths` (multi-hop paths from the query), `chunk_relations` (paths between retrieved chunks), and
  `chunk_id_to_group_ids` (chunk→path-group mapping).

**Key concepts:**
- **Triplets** — the unit of the graph, `source → relation → target`. Example: `billing_policy → governs → failed_payment_handling`.
- **`query_paths`** — multi-hop chains of triplets connecting the query to retrieved chunks. Each path carries a
  `relevancy_score` and the chunk IDs whose traversal produced it.
- **`chunk_relations`** — paths describing how returned chunks relate to one another (same shape as query_paths;
  the difference is the anchor — query-driven vs chunk-to-chunk).
- **`chunk_id_to_group_ids`** — maps each chunk ID to path-group identifiers (e.g. `p_0`, `p_1`); use to group
  chunks by which graph path produced them.
- **`additional_context`** — map keyed by `chunk_uuid`; when a chunk has `extra_context_ids`, look up related
  chunks here (populated from forceful relations, only in `mode: "thinking"`).

**Use context graphs when** answers require synthesizing across chunks, relational context matters (cause/effect,
ownership, sequence, dependency), or multi-hop reasoning. **Skip** for direct factual lookups (adds response size
+ latency).

### 2.8 Metadata — Two Tiers

Metadata is structured data attached to Knowledge and Memories so you can scope queries to a known set **before**
semantic retrieval runs. It bridges vector similarity ("things like X") with exact scoping ("must be
environment=production, must be customer=acme").

HydraDB exposes **two metadata layers**:
- **Declared up front (fast)** — `metadata` (schema-aligned, defined at tenant creation, immutable).
- **Free-form (slower)** — `additional_metadata` (no schema, ~3× over-fetch, post-retrieval pass).

**Schema declaration (`tenant_metadata_schema`, set at tenant creation — IMMUTABLE):**

| Field | Type/values | Purpose |
| --- | --- | --- |
| `name`* | string | Field key. Must start with a letter or `_`. Reserved system names rejected. |
| `data_type` | `"string"`\|`"integer"`\|`"float"`\|`"boolean"`\|`"array"`\|`"object"`\|`VARCHAR`\|`INT*`\|`JSON`… | Field type. Default `VARCHAR`. Friendly names coerced server-side. (Full enum: BOOL, INT8/16/32/64, FLOAT, DOUBLE, VARCHAR, JSON, ARRAY, FLOAT_VECTOR, SPARSE_FLOAT_VECTOR.) |
| `enable_match` | boolean | `true` → use as scope key in `metadata_filters`. Default `false`. |
| `enable_dense_embedding` | boolean | Index this VARCHAR field for semantic similarity. Default `false`. |
| `enable_sparse_embedding` | boolean | Index this VARCHAR field for BM25 keyword search. Default `false`. |
| `max_length` | integer | Max length for string fields. Default `1024`. |
| `nullable` | boolean | Whether field can be null. Default `true`. |
| `searchable` (shorthand) | boolean | = `enable_dense_embedding: true` + `enable_sparse_embedding: true`. |
| `filterable` (shorthand) | boolean | = `enable_match: true`. |

> ⚠️ **Schema is immutable.** Tenant-level field names cannot be mutated after creation. Undeclared scope keys
> are **silently ignored** at query time. Plan scoping fields before first ingest. Fields with embeddings enabled
> **must be VARCHAR type**.

**Ingest-time metadata (per source):**

| Field | Type | Purpose |
| --- | --- | --- |
| `metadata` | object (knowledge); **JSON-encoded string** (memory) | Tenant-schema fields. Keys must match `tenant_metadata_schema`. Fast scoping path. |
| `additional_metadata` | object | Free-form per-document fields. No schema. Flexible, slower path. |

> ⚠️ **Memory metadata encoding gotcha:** For `type=memory`, each memory item's `metadata` must be a
> **JSON-encoded string**. Passing an object returns `400 INVALID_INPUT`. Keep `additional_metadata` as an object.

**Query-time scoping (`metadata_filters` on `POST /query`):**

| Where the key lives | What it scopes | Performance |
| --- | --- | --- |
| Top level of `metadata_filters` | `metadata` fields | Applied in the vector store **before** ranking. Fast. |
| Nested under `additional_metadata` (canonical) or `document_metadata` (deprecated alias) | `additional_metadata` fields | Post-retrieval, with engine over-fetch ~3×. Slower. |

> `metadata_filters` are **equality constraints only**. Range/contains/fuzzy belong in the query, mode, or
> downstream reranking.

### 2.9 Query Pipeline (conceptual)

`POST /query` is the single retrieval endpoint. Two parameters describe the request: `type` (picks the
collection) and `query_by` (picks the method). Every call goes through the same pipeline:

1. **Authenticate and scope.** Validate `tenant_id`, resolve the tenant, apply the requested `sub_tenant_id`.
2. **Filter before ranking.** Apply `metadata_filters` to narrow the candidate set.
3. **Retrieve.** Run hybrid retrieval over the semantic vector store and the BM25 index, or BM25-only when
   `query_by: "text"`.
4. **Blend.** Use `alpha` to weight semantic vs. BM25 contributions (`1.0` = pure semantic, `0.0` = pure BM25).
5. **Enrich.** When `graph_context: true`, traverse the context graph and attach related paths. When
   `mode: "thinking"`, expand the query, rerank, and pull in author-declared forceful relations.
6. **Shape the response.** Return ranked `chunks`, deduplicated `sources`, optional `graph_context`, and
   optional `additional_context` from forceful relations.

---

## 3. Data Model & Schema

### 3.1 Entities

HydraDB manages these entity types (from the OpenAPI component schemas):

- **Tenant** — isolated workspace. Identified by `tenant_id` (string, ≤25 chars, stable, case-sensitive; prefer
  lowercase + numbers + underscores). Carries an immutable `tenant_metadata_schema`. Has an `org_id`.
- **Sub-tenant** — logical partition within a tenant, identified by `sub_tenant_id`. Implicitly created on first
  write. A default sub-tenant is auto-provisioned at tenant creation.
- **Knowledge source** — a document or app source in the Knowledge store. Has `id`, `title`, `type`, `description`,
  `note`, `url`, `timestamp`, `content`, `tenant_metadata`, `document_metadata`, `meta`, relations, attachments.
- **Memory** — a user-scoped context unit in the Memories store. Has `id`/`source_id`, `title`, `text` or
  `user_assistant_pairs`, `is_markdown`, `infer`, `custom_instructions`, `user_name`, `expiry_time`, `metadata`
  (JSON string), `additional_metadata` (object), `tenant_metadata`, relations.
- **Chunk** — a semantically coherent segment of a source. Has `chunk_uuid`, `id` (source id), `chunk_content`,
  `source_type`, `source_title`, `source_upload_time`, `source_last_updated_time`, `layout`, `relevancy_score`,
  `metadata`, `additional_metadata`, `extra_context_ids`.
- **Triplet** — `source (Entity) → relation (RelationEvidence) → target (Entity)`. The unit of the context graph.
- **Webhook** — an HTTPS endpoint registered to receive `indexing.status_changed` events.

### 3.2 Memory item shape (the key entity for the Hermes use case)

**`MemoryItem`** (OpenAPI schema; sent as items in the `memories` JSON array on `POST /context/ingest`
`type=memory`):

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `source_id` | string\|null | No | auto-generated | Optional unique identifier. Auto-generated if not provided. (Docs use `id` as the field name; OpenAPI field is `source_id`.) Acts as upsert key and relation target. |
| `title` | string\|null | No | — | Display title for this memory item. |
| `text` | string\|null | No* | — | Raw text or markdown content to be indexed. *Required unless `user_assistant_pairs` is provided. |
| `user_assistant_pairs` | `[{user, assistant}]`\|null | No* | — | Array of user/assistant conversation pairs to store as memory. *Required unless `text` is provided. |
| `is_markdown` | boolean | No | `false` | Whether the text is markdown formatted. |
| `infer` | boolean | No | `false` | If true, process and extract additional insights/inferences from the content before indexing. Useful for extracting implicit information from conversations. |
| `custom_instructions` | string\|null | No | — | Custom instructions to guide inference processing. (Ignored when `infer: false`.) |
| `user_name` | string | No | `"User"` | User's name for personalization in conversation pairs. |
| `expiry_time` | integer\|null | No | — | Optional TTL in seconds for this memory. Memory stops surfacing after expiry. |
| `metadata` | object (MetadataMap) | No | — | Key-value metadata attached to this memory. **For `type=memory` this must be sent as a JSON-encoded string** (not an object) or the API returns 400. Keys must match `tenant_metadata_schema`. |
| `additional_metadata` | object (MetadataMap) | No | — | Additional free-form key-value metadata for this memory. **Sent as an object.** |
| `tenant_metadata` | string\|null | No | — | JSON string containing tenant-level document metadata. |

> **`id` vs `source_id`:** The docs' "Memory Fields" table uses `id` as the field name ("Optional stable ID.
> Acts as upsert key and relation target"). The OpenAPI `MemoryItem` schema uses `source_id`. In practice the
> multipart form field `memories` accepts the documented shape; the SDK normalizes. When you supply an `id` and
> `upsert: true`, re-ingesting the same `id` replaces the existing memory. **Use a stable `id` for upsert/delete.**

**Request-level fields** (on the `POST /context/ingest` form, not per-memory-item):

| Field | Default | Description |
| --- | --- | --- |
| `type` * | — | Selects the Memories store. Use singular `memory`. |
| `tenant_id` * | — | Target tenant. |
| `sub_tenant_id` ● | default sub-tenant | User, workspace, or session scope. |
| `upsert` | `true` | Replace existing memories with the same `id`. |
| `memories` * | — | JSON-stringified array of memory items. |

\* = required, ● = recommended

**Two input shapes (provide either `text` OR `user_assistant_pairs`, not both):**
- `text` — prose input: plain observations, captured facts, preference statements, meeting notes, semi-structured records. Set `is_markdown: true` when content uses markdown.
- `user_assistant_pairs` — when the useful signal comes from dialogue, especially when the preference is implied rather than explicitly stated.

### 3.3 Knowledge source shape

**`SourceModel`** (OpenAPI): `id`, `tenant_id`, `sub_tenant_id`, `title`, `type`, `description`, `note`, `url`,
`timestamp`, `content` (ContentModel), `tenant_metadata` (object), `document_metadata` (object), `meta`, relations.

**App source** (`app_knowledge`): pre-parsed records from business apps. Identity from `id`, `kind`, `provider`,
`external_id`. Searchable content lives in `fields` (typed by `kind`: email/message/ticket/knowledge_base/comment/custom).

| `kind` | Main searchable text field |
| --- | --- |
| `email` | `fields.subject`, `fields.body` |
| `message` | `fields.body` |
| `ticket` | `fields.title`, `fields.description` |
| `knowledge_base` | `fields.title`, `fields.body` |
| `comment` | `fields.body` |
| `custom` | `fields.data` |

> ⚠️ Do **NOT** put primary app text in a top-level `content` object for app-native ingestion. Top-level
> `content` is the older generic knowledge payload.

**Forceful relations** (`relations`/`ForcefulRelationsPayload`): `{ "source_ids": [...], "properties": {...} }`
— forcefully connect a source to others. `source_ids` accepts the deprecated alias `cortex_source_ids`.

**App relations** (`AppRelationInput`): explicit typed relations between app sources —
`{ "source_id", "predicate" (e.g. reply_to/parent_of/linked_to, default linked_to), "target" (AppRelationTargetRef), "properties" }`.
Target is either `source_id` (existing Cortex source) or `external_id`+`provider` (resolved later).

### 3.4 What gets chunked/embedded automatically

- **Knowledge:** HydraDB parses the input (PDF/DOCX/Markdown/CSV/TXT/app-source JSON), chunks into semantically
  coherent segments, extracts entities & relationships (graph), embeds every chunk into dense + sparse vector
  stores, and indexes. The application never chunks or embeds — all server-side.
- **Memories:** parsed (raw text/markdown/user-assistant pairs), optionally inferred, processed, indexed. Each
  memory becomes queryable; related preferences surface together via the context graph.
- **Metadata VARCHAR fields** with `enable_dense_embedding`/`enable_sparse_embedding` are also indexed for
  semantic/keyword search.

### 3.5 Key response schemas

**`V2RetrievalResult`** (the `data` payload of `POST /query`):
```json
{
  "chunks": [ V2Chunk ],
  "sources": [ SourceInfo ],
  "graph_context": GraphContext,
  "additional_context": { "<chunk_uuid>": V2Chunk }
}
```

**`V2Chunk`:** `chunk_uuid`, `id` (source id), `chunk_content`, `source_type`, `source_title`,
`source_upload_time`, `source_last_updated_time`, `layout` (stringified dict with `offsets`/`page`),
`relevancy_score` (number), `metadata` (object), `additional_metadata` (object), `extra_context_ids` (string[]).

**`SourceInfo`** (deduplicated sources): `id`, `title`, `type`, `description`, `url`, `timestamp`, `metadata`,
`additional_metadata`, `app_kind`, `app_provider`, `app_external_id`.

**`GraphContext`:** `query_paths` (ScoredPathResponse[]), `chunk_relations` (ScoredPathResponse[]),
`chunk_id_to_group_ids` ({chunk_uuid: [group_id]}), `synthesis_context` (string, present only for multi-step
queries with `requires_synthesis=True`).

**`TripletWithEvidence`:** `source` (Entity), `target` (Entity), `relations` (RelationEvidence[]),
`chunk_id`. **`RelationEvidence`:** `canonical_predicate` (e.g. "works for"), `raw_predicate`, `context`
(rich description), `confidence` (0.0–1.0, default 0.8), `temporal_details`, `timestamp`, `relationship_id`,
`chunk_id`, `source_entity_id`, `target_entity_id`.

**`UserMemory`** (from `POST /context/list` `type=memory`): `memory_id`, `memory_content` (the stored text),
`inferred_content` (the inference-stage output, when available).

---

## 4. Full REST API Reference

**Base URL:** `https://api.hydradb.com`
**Auth (every endpoint):** `Authorization: Bearer <your_api_key>`
**Version header:** `API-Version: 2` (SDKs set this automatically; raw HTTP must set it manually)
**Get an API key:** `https://app.hydradb.com`
**Env var convention:** `HYDRA_DB_API_KEY`

### 4.0 Response Envelope

Core endpoints (`/tenants`, `/context/*`, `/query`) use the same top-level envelope for success and failure:

```json
{
  "success": true,
  "data": {},
  "error": null,
  "meta": { "request_id": "...", "latency_ms": 12.3 }
}
```

- Parse payloads from `data`. SDKs return the full envelope; read from `.data` (e.g. `response.data`).
- On failure, SDKs raise **typed exceptions**. Log `meta.request_id` for failed requests.
- **Webhook management endpoints** (`/webhooks/indexing*`) return their documented response object **directly,
  without the envelope**.

Error response:
```json
{ "success": false, "data": null,
  "error": { "code": "VALIDATION_ERROR", "message": "Request validation failed" },
  "meta": { "request_id": "...", "latency_ms": 4.8 } }
```

### 4.1 The v2 endpoint surface (authoritative, from OpenAPI)

The OpenAPI spec contains **47 paths / 53 operations**, but most are **legacy v1 routes** (e.g.
`/memories/add_memory`, `/recall/full_recall`, `/ingestion/upload_knowledge`, `/list/data`, `/tenants/create`,
`/tenants/delete`, `/tenants/infra/status`, `/tenants/monitor`, `/embeddings/*`, `/dashboard/webhooks/*`).
The **v2 canonical surface** (what the SDKs wrap and the docs recommend) is the set below. Build against these
only — the legacy routes are retained for backward compatibility.

#### Tenants group

| Endpoint | Method | SDK method (Python) | Purpose |
| --- | --- | --- | --- |
| `/tenants` | POST | `client.tenants.create()` | Create a tenant (+ optional metadata schema) |
| `/tenants` | GET | `client.tenants.list()` | List tenant IDs available to the API key |
| `/tenants` | DELETE | `client.tenants.delete()` | Permanently delete a tenant + all its data (irreversible) |
| `/tenants/status` | GET | `client.tenants.status()` | Check provisioning readiness |
| `/tenants/sub-tenants` | GET | `client.tenants.sub_tenants()` | List active sub-tenant IDs in a tenant |
| `/tenants/stats` | GET | `client.tenants.stats()` | Usage statistics (row counts, vector dimensions) |

#### Context group

| Endpoint | Method | SDK method (Python) | Purpose |
| --- | --- | --- | --- |
| `/context/ingest` | POST | `client.context.ingest()` | Ingest knowledge (documents/app sources) and memories |
| `/context/status` | GET | `client.context.status()` | Check processing status (poll indexing) |
| `/context/inspect` | GET | `client.context.inspect()` | Fetch parsed content / presigned URL for a source |
| `/context/list` | POST | `client.context.list()` | List knowledge sources or memories (paginated, filterable) |
| `/context` | DELETE | `client.context.delete()` | Delete knowledge sources or memories by IDs |
| `/context/relations` | GET | `client.context.relations()` | Inspect graph relations for a source or sub-tenant |

#### Query group

| Endpoint | Method | SDK method (Python) | Purpose |
| --- | --- | --- | --- |
| `/query` | POST | `client.query()` | Unified retrieval over knowledge, memories, or both |

#### Webhooks group (envelope-less)

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/webhooks/indexing` | POST | Register/replace an indexing webhook |
| `/webhooks/indexing` | GET | Get the current webhook registration |
| `/webhooks/indexing` | DELETE | Delete the webhook registration |
| `/webhooks/indexing/test` | POST | Send a test delivery |
| `/webhooks/indexing/deliveries` | GET | List recent delivery attempts |
| `/webhooks/indexing/deliveries/{delivery_id}` | GET | Get a single delivery |
| `/webhooks/indexing/deliveries/{delivery_id}/retry` | POST | Retry a failed delivery |

### 4.2 POST /tenants — Create Tenant

**Auth:** Bearer token. **Body:** `application/json` → `TenantCreateRequest`.

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `tenant_id` | string (minLength 1) | ✅ | — | Unique tenant identifier. Stable, case-sensitive, ≤25 chars; prefer lowercase letters, numbers, underscores. |
| `tenant_metadata_schema` | array<CustomPropertyDefinition>\|null | No | `null` | Defines tenant-level metadata fields. **Immutable after creation.** |
| `is_embeddings_tenant` | boolean | No | `false` | `true` to create an embeddings tenant. |
| `embeddings_dimension` | integer\|null | No | `1536` | Embedding dimensions for an embeddings tenant. Not required when `is_embeddings_tenant=false`. |

`CustomPropertyDefinition`: `name`* (string), `data_type` (MilvusDataType, default VARCHAR),
`max_length` (int, default 1024), `enable_analyzer` (bool, default false), `enable_match` (bool, default false),
`enable_dense_embedding` (bool, default false), `enable_sparse_embedding` (bool, default false),
`nullable` (bool, default true).

**Request example:**
```json
{
  "tenant_id": "acme_corp",
  "tenant_metadata_schema": [
    { "name": "category", "data_type": "VARCHAR", "max_length": 256, "enable_match": true },
    { "name": "product_description", "data_type": "VARCHAR", "max_length": 4096,
      "enable_dense_embedding": true, "enable_sparse_embedding": true }
  ]
}
```

**Response (200 → `TenantCreateApiResponse`):** envelope with `data: TenantCreateAcceptedResponse`
(`status: "accepted"`, `tenant_id`, `message`). Tenant creation is **asynchronous** — poll `GET /tenants/status`.

```json
{ "success": true,
  "data": { "status": "accepted", "tenant_id": "acme_corp",
    "message": "Tenant creation started in the background. Use GET /tenants/status?tenant_id=... to check progress." },
  "error": null, "meta": { "request_id": "...", "latency_ms": 12.3 } }
```

**Errors:** 400 Invalid input; 401 Unauthorized; 403 Forbidden; 409 `TENANT_ALREADY_EXISTS`; 422 Validation; 500/503.

### 4.3 GET /tenants — List Tenants

**Auth:** Bearer. **No parameters.**

**Response (200 → `TenantListApiResponse`):** `data: TenantIdsResponse`
(`tenant_ids: string[]` — active/provisioning tenants only; `failed_tenant_ids: [{tenant_id, error}, …]\|null`;
`message`). Retry failed tenants by re-creating with `POST /tenants`.

```json
{ "success": true,
  "data": { "tenant_ids": ["acme_corp", "my_first_tenant"], "failed_tenant_ids": null,
    "message": "Successfully retrieved tenant IDs" },
  "error": null, "meta": { "request_id": "...", "latency_ms": 12.3 } }
```

### 4.4 DELETE /tenants — Delete Tenant

**Auth:** Bearer. **Query params:** `tenant_id` (string, required).

**Response (200 → `TenantDeleteApiResponse`):** `data: TenantDeleteResponse`
(`tenant_id`, `status: "deletion_scheduled"`, `message`). **Irreversible** — removes content, memories,
metadata schema, vector indices, graphs. Async cleanup; treat deletion as complete when the tenant no longer
appears in `GET /tenants` or `GET /tenants/status` returns `TENANT_NOT_FOUND`. The same `tenant_id` can be
reused after cleanup completes.

**Errors:** 404 `TENANT_NOT_FOUND`; 401; 422.

### 4.5 GET /tenants/status — Tenant Status (provisioning readiness)

**Auth:** Bearer. **Query params:** `tenant_id` (string, required).

**Response (200 → `TenantInfraStatusApiResponse`):** `data: InfraStatusResponseV2`
(`tenant_id`, `org_id`, `infra: InfraV2`, `message`).

`InfraV2`: `scheduler_status` (bool), `graph_status` (bool),
`vectorstore_status: { knowledge: bool, memories: bool }`,
`ready_for_ingestion` (bool, **derived** — true once both vectorstores are provisioned).

> Use `infra.ready_for_ingestion` for a single check. Poll until `true` before ingesting.
> Stale/nonexistent `tenant_id` → 404 `TENANT_NOT_FOUND`.

```json
{ "success": true,
  "data": { "tenant_id": "my_first_tenant", "org_id": "org_abc123",
    "infra": { "scheduler_status": true, "graph_status": true,
      "vectorstore_status": { "knowledge": true, "memories": true },
      "ready_for_ingestion": true },
    "message": "Deployed infrastructure status" },
  "error": null, "meta": { "request_id": "...", "latency_ms": 12.3 } }
```

### 4.6 GET /tenants/sub-tenants — List Sub-Tenants

**Auth:** Bearer. **Query params:** `tenant_id` (string, required).

**Response (200):** `data: SubTenantIdsResponse` (`sub_tenant_ids: string[]`, `message`). Sub-tenants are
auto-created on first write under a new `sub_tenant_id`; the list grows organically. The default sub-tenant
appears as e.g. `subtenant_default_abc123`.

### 4.7 GET /tenants/stats — Tenant Stats

**Auth:** Bearer. **Query params:** `tenant_id` (string, required).

**Response (200 → `TenantStatsResponse`):** `tenant_id`,
`knowledge_collection: { row_count, dimensions }`,
`memory_collection: { row_count, dimensions }`, `message`.

> ⚠️ **`row_count` is chunks, not sources.** One document → many chunks. For distinct source/memory counts, use
> `POST /context/list` with `page_size=1` and read `pagination.total`. Counts are eventually consistent.

```json
{ "success": true,
  "data": { "tenant_id": "my_first_tenant",
    "knowledge_collection": { "row_count": 42318, "dimensions": 1536 },
    "memory_collection": { "row_count": 287, "dimensions": 1536 },
    "message": "Successfully retrieved tenant collection statistics" },
  "error": null, "meta": { "request_id": "...", "latency_ms": 12.3 } }
```

### 4.8 POST /context/ingest — Ingest Context (THE key write endpoint)

**Auth:** Bearer. **Content-Type:** `multipart/form-data`. **Body schema:** `Body_ingest_context_ingest_post`.

**Form fields:**

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `type` | enum `knowledge`\|`memory` | No | `knowledge` | Type of content. Use singular `memory` for memories. |
| `tenant_id` | string | ✅ | — | Target tenant. |
| `sub_tenant_id` | string | No | `""` (default sub-tenant) | Logical partition inside the tenant. |
| `upsert` | boolean | No | `true` | If true, update existing sources with the same `id`. Set `false` to error on conflict. |
| `documents` | file[] (binary) | No | `[]` | Binary uploads, **knowledge only**. Omit when ingesting memories. |
| `document_metadata` | string (JSON array) | No | — | One entry per file in `documents`, same order. Each object: `id` (optional), `metadata`, `additional_metadata`, `infer`, `relations`. |
| `app_knowledge` | string (JSON object or array) | No | — | Pre-extracted app source objects (Slack/Notion/etc.), **knowledge only**. |
| `memories` | string (JSON array) | No | — | Memory items, **memory only**. Required and non-empty when `type=memory`. Use plural `memories` for the form field even though `type` is singular `memory`. |

**Returns** `202 Accepted`. Response → `SourceIngestApiResponse` with `data` being either
`V2SourceUploadResponse` (knowledge) or `V2AddMemoryResponse` (memory). Each has `success`, `message`,
`results: [{ id, filename?, status: "queued", error, error_code }]`, `success_count`, `failed_count`.

**Memory ingestion example (the Hermes-relevant path):**
```bash
curl -X POST 'https://api.hydradb.com/context/ingest' \
  -H "Authorization: Bearer $HYDRA_DB_API_KEY" -H "API-Version: 2" \
  -F "type=memory" -F "tenant_id=acme_corp" -F "sub_tenant_id=user_123" -F "upsert=true" \
  -F 'memories=[{"id":"pref_dark_mode","text":"User prefers dark mode and concise answers.","infer":true,"user_name":"John","metadata":"{\"department\":\"support\",\"workspace\":\"docs\"}","additional_metadata":{"source":"onboarding"}}]'
```

**Memory ingestion response:**
```json
{ "success": true,
  "data": { "success": true, "message": "Memories queued for ingestion successfully",
    "results": [ { "id": "pref_dark_mode", "title": null, "status": "queued", "infer": true, "error": null, "error_code": null } ],
    "success_count": 1, "failed_count": 0 },
  "error": null, "meta": { "request_id": "...", "latency_ms": 8.4 } }
```

**Knowledge — documents + app sources in one call (Python SDK):**
```python
import json
with open("/path/to/policy.pdf","rb") as f:
    client.context.ingest(type="knowledge", tenant_id="acme_corp", sub_tenant_id="team_docs", upsert=True,
        documents=[("policy.pdf", f, "application/pdf")],
        document_metadata=json.dumps([{"id":"policy_main","metadata":{"department":"legal"},
                                       "additional_metadata":{"source":"policy"}}]),
        app_knowledge=json.dumps([{"id":"slack_thread_001","tenant_id":"acme_corp","sub_tenant_id":"team_docs",
            "title":"Pricing discussion","type":"slack","kind":"message","provider":"slack",
            "external_id":"1716213600.000100",
            "fields":{"kind":"message","body":"We agreed on three tiers...","author":"alice",
                      "thread_id":"1716213600.000100","created_at":"2026-05-20T10:00:00Z"},
            "metadata":{"department":"product"},"additional_metadata":{"channel":"pricing"}}]))
```

**Upload in-memory text as a `.txt` file:**
```python
import io, json
txt = io.BytesIO("Q4 planning notes\n- Launch checklist owned by Priya.".encode())
client.context.ingest(type="knowledge", tenant_id="acme_corp", sub_tenant_id="team_docs",
    documents=[("meeting-notes.txt", txt, "text/plain")],
    document_metadata=json.dumps([{"id":"meeting_notes_q4","metadata":{"department":"product"}}]))
```

### 4.9 GET /context/status — Ingestion Status

**Auth:** Bearer. **Query params:**

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `ids` | string[] | ✅ | — | One or more `id` values returned at ingestion. Accepts document/app/memory IDs. |
| `tenant_id` | string | ✅ | — | Tenant the items belong to. |
| `sub_tenant_id` | string\|null | No | `null` | Sub-tenant scope. If omitted, default sub-tenant. |

**Response (200 → `V2BatchProcessingStatus`):** `statuses: [V2ProcessingStatus]`. Each item:
`id`, `indexing_status` (enum: `queued`, `processing`, `completed`, `errored`, `graph_creation`, `success`),
`error_code`, `error_message`, `success`, `message`.

> **`graph_creation` is searchable.** Items at `graph_creation` are already retrievable via `/query`; wait for
> `completed` only when you need full graph context. **Treat `errored` as terminal.**
> (The OpenAPI status enum also includes `success` as a legacy alias for `completed`.)

```json
{ "success": true,
  "data": { "statuses": [
    { "id": "policy_main", "indexing_status": "completed", "error_code": "", "success": true,
      "error_message": "", "message": "Processing status retrieved successfully" },
    { "id": "runbook_deploy", "indexing_status": "graph_creation", "error_code": "", "success": true,
      "error_message": "", "message": "Processing status retrieved successfully" } ] },
  "error": null, "meta": { "request_id": "...", "latency_ms": 12.3 } }
```

### 4.10 GET /context/inspect — Inspect Context

**Auth:** Bearer. **Query params:**

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `id` | string | ✅ | — | Source ID to fetch. |
| `tenant_id` | string | ✅ | — | Owning tenant. |
| `sub_tenant_id` | string\|null | No | `null` | Sub-tenant scope. |
| `mode` | enum `content`\|`url`\|`both` | No | `both` | What to return. |
| `expiry_seconds` | integer | No | `3600` | Presigned URL TTL (when mode includes `url`). Range 60–604800 (7 days). |

**Response (200 → `V2SourceFetchResponse`):** `success`, `id`, `content` (parsed text, for text-parseable files),
`content_base64` (for binary files), `inferred_content` (model-derived text — for memories, the inferred memory
statement; for knowledge, derived/normalized content; `null` when none), `presigned_url`, `content_type`,
`size_bytes`, `message`, `error`.

**Modes:** `content` (parsed text only), `url` (presigned URL only), `both` (default). For memories,
`inferred_content` is populated when `infer: true` was used at ingestion.

**Memory fetch example:**
```json
{ "success": true,
  "data": { "success": true, "id": "mem_user_alex_tone",
    "content": "Prefers concise answers and dark mode.", "content_base64": null,
    "inferred_content": "User prefers concise answers and dark mode.",
    "presigned_url": null, "content_type": "text/plain", "size_bytes": null,
    "message": "Memory fetched successfully" },
  "error": null, "meta": { "request_id": "...", "latency_ms": 12.3 } }
```

### 4.11 POST /context/list — List Context

**Auth:** Bearer. **Body:** `application/json` → `V2ListContentRequest`.

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `tenant_id` | string | ✅ | — | Owning tenant. |
| `sub_tenant_id` | string\|null | No | `null` | Sub-tenant scope. If omitted, default sub-tenant. |
| `type` | enum `knowledge`\|`memory` | No | `knowledge` | Bucket to list. |
| `ids` | string[] (max 100)\|null | No | `null` | When non-empty, only these IDs are returned (pagination + filters still apply). |
| `page` | integer (min 1) | No | `1` | Page number (1-indexed). |
| `page_size` | integer (1–100) | No | `50` | Items per page. |
| `filters` | ContentFilter\|null | No | `null` | Structured exact-match filters. |
| `include_fields` | string[]\|null | No | `null` | Field projection (**knowledge only**). Allowed: `description`, `metadata`, `additional_metadata`, `note`, `relations`, `timestamp`, `title`, `type`. (`content`/`url`/`attachments` are NOT projectable — fetch via `/context/inspect`.) |

**`ContentFilter`:** `metadata` (schema-aligned, AND logic, keys must be declared with `enable_match`),
`additional_metadata` (free-form, AND logic), `source_fields` (built-in: `description`/`timestamp`/`title`/`type`/`url`, AND logic).
> ⚠️ Undeclared `filters.metadata` keys are **silently ignored**. No range/contains/OR — run multiple calls and union client-side for OR.

**Response (200):** `data` is `V2SourceListResponse` (knowledge: `sources[]`, `total`, `pagination`) or
`ListUserMemoriesResponse` (memory: `success`, `user_memories: [UserMemory]`, `total`, `pagination`).
`PaginationMeta`: `page`, `page_size`, `total`, `total_pages`, `has_next`, `has_previous`.

```python
# List memories for a user
mems = client.context.list(tenant_id="acme_corp", sub_tenant_id="user_123", type="memory", page=1, page_size=50)
# mems.data.user_memories[i].memory_id / .memory_content / .inferred_content
```

### 4.12 DELETE /context — Delete Context

**Auth:** Bearer. **Body:** `application/json` → `V2SourceDeleteRequest` (flat, top-level — replaces the legacy
nested `{"request": {...}, "type": ...}` body).

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `tenant_id` | string | ✅ | — | Tenant identifier. |
| `ids` | string[] (min 1) | ✅ | — | List of source/memory IDs to delete. |
| `sub_tenant_id` | string\|null | No | `null` | Sub-tenant identifier. |
| `type` | enum `knowledge`\|`memory` | No | `knowledge` | Type of content to delete. |

**Response (200):** `data` is `SourceDeleteResponse` (knowledge: `success`, `message`, `results: [{id, deleted, error}]`, `deleted_count`) or `DeleteUserMemoryResponse` (memory: `success`, `user_memory_deleted`).

> **Partial-success semantics:** knowledge deletes report each ID independently in `results[]`; a failure on one
> doesn't stop the rest. Memory deletes report an aggregate `user_memory_deleted`. Deleted IDs disappear from
> `/query` and `/context/list` immediately, even before background cleanup. **Mixed deletes need two calls.**

```python
client.context.delete(type="memory", tenant_id="acme_corp", sub_tenant_id="user_123", ids=["pref_dark_mode"])
```

### 4.13 GET /context/relations — Graph Relations

**Auth:** Bearer. **Query params:**

| Name | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `tenant_id` | string | ✅ | — | Tenant. |
| `id` | string | No | — | Source ID. If omitted, returns relations across the entire sub-tenant. |
| `type` | enum `knowledge`\|`memory` | No | `knowledge` | Type of content. |
| `sub_tenant_id` | string | No | — | Sub-tenant scope. If omitted, default sub-tenant. |
| `limit` | integer | No | `5000` | Max relation groups to return. |
| `cursor` | string | No | — | Pagination cursor from a previous response. |

**Response (200 → `SourceGraphRelationsResponse`):** `relations: [TripletWithEvidence|null]`,
`is_truncated` (bool), `next_cursor` (number|null, opaque — pass as `cursor` to continue), `success`, `message`.

### 4.14 POST /query — Query (THE key read endpoint)

**Auth:** Bearer. **Body:** `application/json` → `QueryRequest`. (Full parameter deep-dive in §6.)

| Field | Type | Required | Default | Description |
| --- | --- | --- | --- | --- |
| `tenant_id` | string | ✅ | — | Owning tenant. |
| `query` | string | ✅ | — | Query terms / natural-language question. Cannot be empty. |
| `sub_tenant_id` | string\|null | No | default sub-tenant | Sub-tenant scope. **Required for per-user memory queries.** |
| `type` | enum `knowledge`\|`memory`\|`all` | No | `knowledge` | What collection to query. `all` runs both in parallel and merges by `relevancy_score`. |
| `query_by` | enum `hybrid`\|`text` | No | `hybrid` | Retrieval method. `hybrid` = semantic + BM25; `text` = BM25 only. |
| `mode` | enum `fast`\|`thinking` | No | `fast` | `fast` = single-pass, low-latency; `thinking` = query expansion, reranking, forceful-relation context, richer multi-hop graph traversal. |
| `max_results` | integer\|null | No | — | Maximum number of results. Start at 10. |
| `alpha` | number (0.0–1.0) or `"auto"` | No | `0.8` | Semantic vs. BM25 blend (hybrid only). 1.0 = pure semantic, 0.0 = pure BM25. `"auto"` lets the engine decide. Ignored for `text`. |
| `recency_bias` | number (0.0–1.0) | No | `0.0` | Boost for newer content. 0.0 = no bias. |
| `graph_context` | boolean | No | `true` | Include entity/relation graph slice. Set `false` for chunk-only responses. |
| `query_forceful_relations` | boolean | No | `true` | Fetch author-declared related sources into `additional_context`. **Only takes effect in `mode: "thinking"`.** |
| `query_apps` | boolean | No | `false` | Adds an app-aware retrieval lane (reconstructed threads, parent/child traversal, exact ID/actor lookups). Does NOT restrict query to only app sources. Memory searches ignore this flag. |
| `metadata_filters` | object\|null | No | — | Equality filters. Top-level keys → `metadata` (fast, pre-ranking). Nested under `additional_metadata` (canonical) or `document_metadata` (deprecated alias) → free-form (slower, ~3× over-fetch). |
| `additional_context` | string\|null | No | — | Short factual hint to guide retrieval (not a hard filter). |
| `operator` | enum `or`\|`and`\|`phrase` | No | `or` | Text search operator (only when `query_by: "text"`). |
| `num_related_chunks` | integer | No | `10` | (Hidden in docs, `x-hydradb-docs-hide: true`.) Number of related content chunks to include. |

**Response (200 → `QueryApiResponse`):** envelope with `data: V2RetrievalResult` (`chunks[]`, `sources[]`,
`graph_context`, `additional_context`). Chunks are already ranked — preserve order when building prompts.
**Also:** 429 Too Many Requests (rate limit) is a documented response code specifically for `/query`.

```json
{
  "success": true,
  "data": {
    "chunks": [
      { "chunk_uuid": "doc-001_chunk_0", "id": "policy_main",
        "chunk_content": "Section 1. Authentication policies...",
        "source_type": "pdf", "source_title": "policy.pdf",
        "relevancy_score": 1.09, "extra_context_ids": ["ctx-id-1"],
        "additional_metadata": {"author": "Support Team"}, "metadata": {"department": "legal"} }
    ],
    "sources": [ { "id": "policy_main", "title": "Compliance Policy", "type": "pdf", "url": "",
                   "timestamp": "2026-05-12T08:14:00Z", "metadata": {"department": "legal"},
                   "additional_metadata": {"author": "Compliance Team"} } ],
    "graph_context": {
      "query_paths": [ { "triplets": [ { "source": {"name":"billing_policy","type":"POLICY"},
          "relation": {"canonical_predicate":"governs","context":"policy covering retry logic"},
          "target": {"name":"failed_payment_handling","type":"PROCESS"} } ],
        "relevancy_score": 0.84, "group_id": "p_0", "source_chunk_ids": ["chunk_abc","chunk_def"] } ],
      "chunk_relations": [],
      "chunk_id_to_group_ids": { "chunk_abc": ["p_0"] }
    },
    "additional_context": { "ctx-id-1": { "chunk_uuid":"ctx-id-1", "id":"related_doc",
        "chunk_content":"Extra related content...", "source_title":"related_doc.pdf", "relevancy_score": 0.7 } }
  },
  "error": null, "meta": { "request_id": "...", "latency_ms": 187.4 }
}
```

### 4.15 Webhooks (envelope-less endpoints)

`POST /webhooks/indexing` — register/replace. Body: `{ "url", "event_types": ["indexing.status_changed"], "signing_secret" (optional, ≥16 chars) }`. Response: `{ "registered": true, "url", "event_types", "signing_secret_configured": true, "message": "Webhook registered." }`.

`GET /webhooks/indexing` → `{ registered, url, event_types, signing_secret_configured }`.
`DELETE /webhooks/indexing` → `{ "deleted": true, "message": "Webhook unregistered." }`.
`POST /webhooks/indexing/test` → `{ "delivered": true, "status_code": 204, "message": "Test delivery succeeded." }`.

**Delivery payload (terminal states only: `completed`, `errored`, `success`):**
Headers: `Content-Type: application/json`, `X-HydraDB-Delivery-ID`, `X-HydraDB-Event: indexing.status_changed`, `X-HydraDB-Signature` (when secret set).
Body: `{ "event", "delivery_id", "id", "tenant_id", "sub_tenant_id", "status", "timestamp", "error_code"?, "error_message"? }`.

> `success` is a legacy alias for `completed`. Webhook URLs must be public-internet-reachable (localhost/private
> networks blocked).

### 4.16 Error Responses (full)

**HTTP status codes:**

| Code | Meaning | Retry? |
| --- | --- | --- |
| 400 | Invalid parameters / malformed request | No |
| 401 | Missing/expired/invalid API key | No |
| 403 | Authenticated but not permitted / plan limit | No |
| 404 | Tenant/source/memory not found | No |
| 409 | Conflict (existing tenant or context item ID) | Usually no |
| 422 | Well-formed request that failed validation | No |
| 429 | Rate limit exceeded | **Yes, with backoff** |
| 500 | Internal server error | **Yes, with backoff** |
| 503 | Temporary service unavailability | **Yes, with backoff** |

**Common error codes:**

| Code | Typical status | Meaning |
| --- | --- | --- |
| `INVALID_PARAMETERS` | 400 | Required param missing/malformed/mutually incompatible. |
| `UNAUTHORIZED` | 401 | `Authorization: Bearer` header missing/invalid. |
| `FORBIDDEN` | 403 | API key valid but lacks access, or account/plan limit prevents operation. |
| `TENANT_ALREADY_EXISTS` | 409 | `POST /tenants` received an in-use `tenant_id`. |
| `TENANT_NOT_FOUND` | 404 | Tenant doesn't exist or isn't visible to the API key. |
| `SOURCE_NOT_FOUND` | 404 | Source/memory ID doesn't exist in the selected tenant/sub-tenant. |
| `VALIDATION_ERROR` | 422 | Valid JSON/form but a field failed semantic validation. |
| `RATE_LIMITED` | 429 | API key exceeded its rate limit. |
| `INTERNAL_ERROR` | 500 | Unexpected server-side error. |
| `SERVICE_UNAVAILABLE` | 503 | Dependency temporarily unavailable / under load. |

**Retry pattern:** Only retry transient failures (429, 500, 503). Exponential backoff with jitter:
`baseDelayMs = 2**attempt * 1000` + random jitter (0–250ms). Keep retries bounded.

---

## 5. Python SDK

### 5.1 Install & client init

```bash
pip install "hydradb-sdk>=2,<3"
```

```python
import os
from hydra_db import HydraDB, AsyncHydraDB        # sync + async share identical surface

client       = HydraDB(token=os.environ["HYDRA_DB_API_KEY"])
async_client = AsyncHydraDB(token=os.environ["HYDRA_DB_API_KEY"])
```

- The SDKs **automatically set `API-Version: 2`** on every request — no manual header needed.
- The server echoes `X-API-Version: 2`; verify your client version by inspecting response headers.
- Response includes the full envelope; read payloads from `.data` (e.g. `response.data`).
- On failure, the SDK raises **typed exceptions** (e.g. `HydraDBError` in TS; equivalent typed exceptions in
  Python). Inspect `error.code` for branching and log `meta.request_id`.

### 5.2 Naming conventions

The REST API uses **snake_case** for all request/response fields; both SDKs preserve those field names for
request/response objects. TypeScript only camelCases multi-word **method names**.

| Format | Method naming | Parameter field naming | Example |
| --- | --- | --- | --- |
| Raw HTTP / cURL | — | snake_case | `tenant_id`, `sub_tenant_id` |
| Python SDK | snake_case | snake_case | `query()`, `tenant_id`, `query_by` |
| TypeScript SDK | camelCase (multi-word methods) | snake_case | `query()`, `tenant_id`, `query_by` |

> Use snake_case request fields in TypeScript too (e.g. `client.context.list({ tenant_id: "...", page_size: 50 })`).

### 5.3 Method structure (three namespaces)

| URL prefix | SDK group | Purpose |
| --- | --- | --- |
| `/context/*` and `/context` | `client.context` | Ingest, status, fetch, list, delete, graph relations |
| `/query` | `client.query` | Unified retrieval |
| `/tenants/*` and `/tenants` | `client.tenants` | Create, list, delete, status, sub-tenants, stats |

### 5.4 Full method reference (Python)

**`client.tenants.*`**

| Method | Endpoint | Signature |
| --- | --- | --- |
| `create()` | `POST /tenants` | `tenants.create(tenant_id, tenant_metadata_schema=None, is_embeddings_tenant=False, embeddings_dimension=1536)` |
| `list()` | `GET /tenants` | `tenants.list()` |
| `delete()` | `DELETE /tenants` | `tenants.delete(tenant_id)` |
| `status()` | `GET /tenants/status` | `tenants.status(tenant_id)` |
| `sub_tenants()` | `GET /tenants/sub-tenants` | `tenants.sub_tenants(tenant_id)` |
| `stats()` | `GET /tenants/stats` | `tenants.stats(tenant_id)` |

**`client.context.*`**

| Method | Endpoint | Signature (key kwargs) |
| --- | --- | --- |
| `ingest()` | `POST /context/ingest` | `context.ingest(type, tenant_id, sub_tenant_id="", upsert=True, documents=[], document_metadata=None, app_knowledge=None, memories=None)` |
| `status()` | `GET /context/status` | `context.status(tenant_id, ids, sub_tenant_id=None)` |
| `inspect()` | `GET /context/inspect` | `context.inspect(id, tenant_id, sub_tenant_id=None, mode="both", expiry_seconds=3600)` |
| `list()` | `POST /context/list` | `context.list(tenant_id, sub_tenant_id=None, type="knowledge", ids=None, page=1, page_size=50, filters=None, include_fields=None)` |
| `delete()` | `DELETE /context` | `context.delete(tenant_id, ids, sub_tenant_id=None, type="knowledge")` |
| `relations()` | `GET /context/relations` | `context.relations(tenant_id, id=None, type="knowledge", sub_tenant_id=None, limit=5000, cursor=None)` |

**`client.query`**

| Method | Endpoint | Signature (key kwargs) |
| --- | --- | --- |
| `query()` | `POST /query` | `client.query(tenant_id, query, sub_tenant_id=None, type="knowledge", query_by="hybrid", mode="fast", max_results=None, alpha=0.8, recency_bias=0.0, graph_context=True, query_forceful_relations=True, query_apps=False, metadata_filters=None, additional_context=None, operator="or")` |

### 5.5 v1 → v2 migration (context for any v1 code encountered)

| v1 | v2 |
| --- | --- |
| `client.upload.knowledge(...)` | `client.context.ingest(type="knowledge", ...)` |
| `client.upload.addMemory(...)` | `client.context.ingest(type="memory", ...)` |
| `client.upload.verifyProcessing(...)` | `client.context.status(...)` |
| `client.search.fullRecall(...)` | `client.query(type="knowledge", ...)` |
| `client.search.recallPreferences(...)` | `client.query(type="memory", ...)` |
| `client.search.booleanRecall(...)` | `client.query(query_by="text", ...)` |
| `client.fetch.listData(...)` | `client.context.list(...)` |
| `client.fetch.graphRelationsBySourceId(...)` | `client.context.relations(...)` |
| `client.fetch.content(...)` | `client.context.inspect(...)` |
| `client.data.delete(...)` | `client.context.delete(type="knowledge", ...)` |
| `client.upload.deleteMemory(...)` | `client.context.delete(type="memory", ...)` |
| `client.tenant.create(...)` | `client.tenants.create(...)` |

### 5.6 Error handling & polling patterns

**Polling pattern — stop when content is searchable (normal RAG):**
```python
import time
ids = ["policy_main", "runbook_deploy"]
while True:
    response = client.context.status(tenant_id="acme_corp", sub_tenant_id="team_docs", ids=ids)
    statuses = [s.indexing_status for s in response.data.statuses]
    if all(s in ("graph_creation", "completed") for s in statuses):
        break
    if any(s == "errored" for s in statuses):
        raise RuntimeError("Context processing failed")
    time.sleep(5)
```

**Polling pattern — stop when graph processing is complete (for graph-heavy ops / `/context/relations`):**
```python
while True:
    response = client.context.status(tenant_id="acme_corp", sub_tenant_id="team_docs", ids=ids)
    statuses = [s.indexing_status for s in response.data.statuses]
    if all(s == "completed" for s in statuses):
        break
    if any(s == "errored" for s in statuses):
        raise RuntimeError("Graph processing failed")
    time.sleep(5)
```

**Retry transient failures (429/500/503) with exponential backoff + jitter** (same formula as the TS example).
Python typed-exception handling mirrors `HydraDBError`: catch the SDK exception, read `error.code`, branch on
`TENANT_NOT_FOUND` vs `429` etc., log `meta.request_id`.

### 5.7 Turning a query response into an LLM context string

```python
from hydra_db import HydraDB
from hydra_db.helpers import build_string        # canonical helper

client = HydraDB(token="YOUR_API_KEY")
result = client.query(tenant_id="your-tenant", sub_tenant_id="your-sub-tenant",
                      query="How does authentication work?", type="knowledge",
                      query_by="hybrid", max_results=5, mode="fast", graph_context=True)
context = build_string(result)        # formatted plain string for the LLM prompt
```

`build_string` handles `chunks`, `query_paths`, `chunk_relations`, and `chunk_id_to_group_ids` automatically,
stripping IDs/timestamps/metadata noise. It accepts either the full envelope or just the `data` payload.

### 5.8 Full end-to-end example: create → ingest → poll → query

```python
import os, json, time
from hydra_db import HydraDB

client = HydraDB(token=os.environ["HYDRA_DB_API_KEY"])
tenant_id = "my_first_tenant"

# 1. Create a tenant.
client.tenants.create(tenant_id=tenant_id)

# 2. Wait until the tenant can accept data.
while True:
    infra = client.tenants.status(tenant_id=tenant_id).data.infra
    if infra.ready_for_ingestion:
        break
    time.sleep(5)

# 3. Ingest one memory into the default sub-tenant.
ingest = client.context.ingest(
    type="memory", tenant_id=tenant_id,
    memories=json.dumps([{"text": "User prefers detailed technical explanations and dark mode"}]),
)
mid = ingest.data.results[0].id

# 4. Wait until the memory is indexed.
while True:
    st = client.context.status(tenant_id=tenant_id, ids=[mid]).data.statuses[0]
    if st.indexing_status == "completed":
        break
    if st.indexing_status == "errored":
        raise RuntimeError(st.error_message)
    time.sleep(2)

# 5. Search memories.
results = client.query(tenant_id=tenant_id, type="memory", query="What does the user prefer?")
print(results.data.chunks)
```

Poll intervals used in the docs: tenant readiness every 5s; indexing status every 2s.

### 5.9 Sync vs async — the Hermes tension (CRITICAL)

Hermes memory providers are **synchronous** — plain methods, daemon threads for non-blocking sync, **no
`asyncio`**. HydraDB ships both `HydraDB` (sync) and `AsyncHydraDB` (async), with an identical method surface.

- **Use `HydraDB` (sync) directly in a Hermes memory provider.** Its methods block on HTTP I/O, which is exactly
  what a sync provider expects.
- `AsyncHydraDB` **cannot be used directly** from a sync provider — its methods return coroutines that require a
  running event loop. If for some reason async were desired, you'd have to run a private event loop in a thread
  and bridge back to sync — not worth it.
- For non-blocking ingestion (writes that don't need to block the prompt path), dispatch the sync `ingest()` call
  to a daemon thread and ignore its result (or capture errors via a callback). Retrieval (`query`) must be
  synchronous on the prompt path because the provider needs the result before returning.

See §9 and §10 for how this maps onto the Hermes provider contract.

---

## 6. Query Deep Dive

### 6.1 The three core decisions

| Parameter | Values | Use it for |
| --- | --- | --- |
| `type` | `knowledge`, `memory`, `all` | Choose the collection. `knowledge` = shared docs/app sources; `memory` = user context; `all` = both, merged and re-ranked together. |
| `query_by` | `hybrid`, `text` | Choose the matching method. `hybrid` (default) = semantic + BM25; `text` = BM25 only, pair with `operator`. |
| `mode` | `fast`, `thinking` | Choose latency vs quality. `fast` = single-pass, low-latency; `thinking` = multi-query retrieval, reranking, forceful-relation context. |

### 6.2 All query parameters (full reference)

| Parameter | Type / values | Default | What it does | When to use |
| --- | --- | --- | --- | --- |
| `tenant_id` | string | (required) | Owning tenant. | Every call. |
| `query` | string | (required) | Query terms / natural-language question. Non-empty. | Every call. |
| `sub_tenant_id` | string\|null | default sub-tenant | Sub-tenant scope. | **Required for per-user memory queries.** Use the same value used at ingestion. |
| `type` | `knowledge`\|`memory`\|`all` | `knowledge` | Picks collection(s). `all` runs both in parallel, merges by `relevancy_score`. | `memory` for user prefs; `all` for personalized+grounded answers. |
| `query_by` | `hybrid`\|`text` | `hybrid` | Retrieval method. `hybrid` = semantic + BM25; `text` = BM25 only. | `text` for exact terms/phrases (legal clauses, SKUs, error codes, IDs). |
| `mode` | `fast`\|`thinking` | `fast` | Retrieval depth. `thinking` expands the query, reranks, pulls forceful relations, richer multi-hop graph traversal. | `thinking` for highest-quality RAG / personalized answers; `fast` for low-latency paths. |
| `max_results` | integer\|null | — | Max results returned. | Start at 10. Drop to 5 for tight context windows; raise to 20 if you rerank downstream. |
| `alpha` | 0.0–1.0 or `"auto"` | `0.8` | Semantic vs. BM25 blend (hybrid only). 1.0 = pure semantic, 0.0 = pure BM25. `"auto"` = engine decides. Ignored for `text`. | Start 0.8. Lower to 0.3–0.5 for literal tokens (error codes, SKUs, product names). Raise to 0.9 for conceptual questions. |
| `recency_bias` | 0.0–1.0 | `0.0` | Boost for newer content. 0.0 = no boost. | 0.2–0.4 for recent operational updates; combine with a document-type filter. |
| `graph_context` | boolean | `true` | Include entity/relation graph slice with chunks. Set `false` for chunk-only responses. | `true` for entity-relationship / multi-hop questions; pair with `mode: "thinking"`. `false` for direct factual lookups (saves size/latency). |
| `query_forceful_relations` | boolean | `true` | Fetch author-declared related sources into `additional_context`. **Only takes effect in `mode: "thinking"`.** | Keep `true` (default) when you want forceful-relation enrichment; only disable to trim context. |
| `query_apps` | boolean | `false` | Adds an app-aware retrieval lane (reconstructed threads, parent/child traversal, exact ID/actor lookups). Does NOT restrict query to app sources only. **Memory searches ignore this flag.** | `true` for app data (Slack/Gmail/Confluence/Jira/Salesforce). Pair with `mode: "thinking"`. |
| `metadata_filters` | object\|null | — | Equality filters. Top-level keys → `metadata` (fast, pre-ranking). Nested `additional_metadata` (canonical) or `document_metadata` (deprecated alias) → free-form (slower, ~3× over-fetch). | Scope to a known slice before ranking. Top-level keys must be declared in `tenant_metadata_schema` with `enable_match: true`. |
| `additional_context` | string\|null | — | Short factual hint to guide retrieval (not a hard filter). | When the query alone is ambiguous. Keep short and factual. |
| `operator` | `or`\|`and`\|`phrase` | `or` | Text search operator. **Only applies when `query_by: "text"`.** | `phrase` for exact phrase lookup (e.g. "GDPR Article 17"). |
| `num_related_chunks` | integer | `10` | (Hidden in docs.) Number of related content chunks to include. | Rarely set manually. |

### 6.3 Use-case recipes

| I want to… | `type` | `query_by` | `mode` | Notes |
| --- | --- | --- | --- | --- |
| Document Q&A / RAG | `knowledge` | `hybrid` | `thinking` | `graph_context: true` for richer context-graph. |
| Exact keyword match | `knowledge` | `text` | — | Optionally `operator: "and"`/`"phrase"` for metadata filtering. |
| Personalized response | `memory` | `hybrid` | `thinking` | Include `sub_tenant_id`. |
| Personalized + grounded | `all` | `hybrid` | `thinking` | One call merges both stores. Include `sub_tenant_id`. |
| Query over apps | `knowledge` | `hybrid` | `thinking` | `query_apps: true` for app-aware retrieval lane. |

**Recommended configurations:**

| User intent | Recommended config |
| --- | --- |
| Fast document RAG | `type="knowledge"`, `query_by="hybrid"`, `mode="fast"`, `max_results=5-10`, `graph_context=false` |
| Highest-quality document RAG | `type="knowledge"`, `query_by="hybrid"`, `mode="thinking"`, `graph_context=true`, `alpha="auto"` |
| Personalized answer | `type="all"`, include `sub_tenant_id`, `query_by="hybrid"`, `mode="thinking"` |
| User preferences only | `type="memory"`, include `sub_tenant_id`, `query_by="hybrid"` |
| Exact keyword or phrase | `type="knowledge"`, `query_by="text"`, `operator="phrase"` |
| Recent operational updates | `query_by="hybrid"`, `recency_bias=0.2-0.4`, filter to the right document type |

### 6.4 How graph traversal + reranking work

- **`mode: "fast"`** — single-pass hybrid retrieval; the graph slice is **shallow**. Lowest latency.
- **`mode: "thinking"`** — the engine: (1) **expands the query** (multi-query retrieval), (2) **reranks** the
  candidate chunks, (3) **pulls in author-declared forceful relations** into `additional_context`
  (when `query_forceful_relations: true`), and (4) performs **richer multi-hop graph traversal**
  (deeper `query_paths`). Higher quality, higher latency.
- **Graph enrichment** (when `graph_context: true`): after hybrid retrieval finds relevant chunks, HydraDB
  traverses the context graph and returns `query_paths` (multi-hop paths from the query), `chunk_relations`
  (paths between retrieved chunks), and `chunk_id_to_group_ids`. When no relevant relationships are found, graph
  fields may be empty.
- **Forceful relations** are author-declared explicit links (set at ingestion via `relations`/`source_ids`).
  In `thinking` mode they're fetched into `additional_context`, keyed by `chunk_uuid`; a chunk's
  `extra_context_ids` point into that map.

### 6.5 Three relevance signals (and why each matters)

- **Dense-vector similarity** — meaning-based retrieval; best for natural-language questions, paraphrases, conceptual lookup.
- **BM25 keyword matching** — token-based retrieval; best for error codes, identifiers, names, SKUs, exact phrases.
- **Context-graph traversal** — relationship-based retrieval; best for multi-hop questions, dependencies, ownership, project context.

`alpha` blends the first two (`hybrid` only). `graph_context` toggles the third. `mode` controls how much
reranking/expansion work the engine does. `metadata_filters` and `sub_tenant_id` scope candidates **before**
ranking, keeping retrieval predictable.

---

## 7. Memories Deep Dive

### 7.1 What a memory is

A **memory** is one unit of user-specific context: a stated preference, an inferred trait, a past conversation,
a decision, feedback, or a fact. Stored in the Memories store (separate from Knowledge), scoped by
`sub_tenant_id`, retrievable via `POST /query` `type: "memory"` or `type: "all"`. Every memory lives in a
structural context graph, so related preferences surface together at retrieval time.

> Memories compound with every interaction — the agent becomes more accurate and familiar over time.

### 7.2 Storing a memory (add/upsert)

**Endpoint:** `POST /context/ingest` with `type=memory`. Multipart form fields: `type=memory`, `tenant_id`,
`sub_tenant_id` (optional, defaults to default sub-tenant), `upsert` (default `true`), and `memories` — a
**JSON-stringified array** of memory items.

**Memory item fields** (recap from §3.2): `id`/`source_id` (stable upsert key + relation target), `title`,
`text` *or* `user_assistant_pairs`, `is_markdown`, `infer`, `custom_instructions`, `user_name`, `expiry_time`
(TTL seconds), `metadata` (**JSON-encoded string** for memories), `additional_metadata` (object), `tenant_metadata`.

**Provide either `text` or `user_assistant_pairs`, not both.**

```python
# Text memory with inference (Hydra extracts the durable preference)
client.context.ingest(
    type="memory", tenant_id="acme_corp", sub_tenant_id="user_alex", upsert=True,
    memories=json.dumps([
        {"id": "pref_dark_mode", "text": "Prefers concise answers and dark mode.",
         "infer": True, "user_name": "Alex",
         "metadata": "{\"department\":\"support\",\"workspace\":\"docs\"}",
         "additional_metadata": {"source": "onboarding"}}
    ]),
)

# Conversation memory (preference implied by dialogue) — store verbatim
client.context.ingest(
    type="memory", tenant_id="acme_corp", sub_tenant_id="user_alex",
    memories=json.dumps([
        {"title": "Support conversation about refunds", "infer": False,
         "user_assistant_pairs": [
            {"user": "Can I get a refund?", "assistant": "Refunds are available within 30 days."}
         ]}
    ]),
)
```

**`upsert: true`** (default) replaces an existing memory with the same `id`. Use a stable `id` to make updates
and deletes deterministic. Returns `202 Accepted` with `results: [{ id, status: "queued", infer, error, error_code }]`,
`success_count`, `failed_count`.

### 7.3 `infer: true` vs `infer: false`

- **`infer: true`** — HydraDB extracts the underlying preference/trait/fact from raw signal. You can ship raw
  behavioral logs, interaction events, UI actions, or dialogue, and HydraDB derives the structured insight. The
  inferred text is returned later via `/context/inspect` as `inferred_content` and via `/context/list` (memory)
  as `UserMemory.inferred_content`. Use `custom_instructions` to guide extraction. `user_name` is used during inference.
- **`infer: false`** (default) — HydraDB stores exactly what you send. Use for deterministic facts you've already
  captured and don't want re-interpreted.

For the Hermes use case (§9): `infer: true` is attractive for auto-extracting durable facts from conversation
history; `infer: false` is the safe choice for explicit user_profile fields you already have structured.

### 7.4 Retrieving memories (query time)

```python
result = client.query(
    tenant_id="acme_corp", sub_tenant_id="user_alex",
    query="Does the user have any specific preferences for tone or response length?",
    type="memory", query_by="hybrid", mode="thinking",
)
# result.data.chunks[i].chunk_content  — ranked memory chunks
```

**`type: "memory"`** queries only the user's memories (scoped by `sub_tenant_id`). **`type: "all"`** runs
knowledge + memory in parallel and merges by `relevancy_score` — the usual choice for personalized answers
grounded in shared docs. Always pass the **same `sub_tenant_id`** used at ingestion; data won't cross sub-tenants.

### 7.5 Listing & inspecting memories

```python
# List all memories for a user (paginated)
mems = client.context.list(tenant_id="acme_corp", sub_tenant_id="user_alex",
                           type="memory", page=1, page_size=50)
# mems.data.user_memories[i] = UserMemory(memory_id, memory_content, inferred_content)
# mems.data.pagination.{page,page_size,total,total_pages,has_next,has_previous}

# Inspect a single memory (parsed text + inferred content)
fetched = client.context.inspect(id="pref_dark_mode", tenant_id="acme_corp",
                                 sub_tenant_id="user_alex", mode="content")
# fetched.data.content         — the stored text
# fetched.data.inferred_content — the inference-stage output (when infer:true), else null
```

### 7.6 Updating a memory

There is no dedicated update endpoint. **Re-ingest with the same `id` and `upsert: true`** (the default) to
replace the existing memory. Then poll `/context/status` until the new version is `completed`/`graph_creation`.

```python
client.context.ingest(
    type="memory", tenant_id="acme_corp", sub_tenant_id="user_alex",
    memories=json.dumps([{"id": "pref_dark_mode", "text": "Prefers dark mode, concise answers, and code examples.",
                          "infer": True}]),
)   # upsert=True (default) → replaces the existing pref_dark_mode memory
```

### 7.7 Deleting a memory

```python
client.context.delete(type="memory", tenant_id="acme_corp",
                      sub_tenant_id="user_alex", ids=["pref_dark_mode"])
# data: DeleteUserMemoryResponse { success, user_memory_deleted }
```

Deleted IDs disappear from `/query` and `/context/list` immediately (even before background cleanup). To delete
both knowledge and memory items, send two calls.

### 7.8 How personalization works at query time

1. The user's `sub_tenant_id` scopes retrieval to that user's memories (+ any shared knowledge if `type: "all"`).
2. Hybrid retrieval + (in `thinking` mode) query expansion/reranking surface the most relevant memory chunks.
3. The context graph connects related preferences so they surface together.
4. `recency_bias` can boost newer memories; `metadata_filters` can scope to a category.
5. The merged, ranked chunks become the personalized context injected into the LLM prompt (via `build_string`).

### 7.9 Scoping by `sub_tenant_id` (the isolation primitive)

- Write memories with `sub_tenant_id = <user id>`; query with the **same** `sub_tenant_id`.
- Omit `sub_tenant_id` to use the tenant's default sub-tenant (for broadly shared context).
- Data written under one sub-tenant should not be expected to appear when querying from another.
- Sub-tenants are implicitly created on first write; no explicit "create sub-tenant" call.
- **Don't use `metadata_filters` as a substitute for `sub_tenant_id`** — it's an explicit anti-pattern in the AGENTS guide.

---

## 8. Deployment & Auth

### 8.1 Getting an API key

1. Sign up at **`https://app.hydradb.com`** — the managed dashboard.
2. Generate an API key in the dashboard.
3. Set it as an env var: `export HYDRA_DB_API_KEY="<key>"`.
4. Use it on every request: `Authorization: Bearer $HYDRA_DB_API_KEY` + `API-Version: 2` (SDK sets the latter automatically).
5. **Enterprise onboarding:** email `founders@hydradb.com`.

### 8.2 Tenant provisioning

- `POST /tenants` (async) → poll `GET /tenants/status` until `infra.ready_for_ingestion` is `true`
  (both vectorstores + graph provisioned). A default sub-tenant is auto-provisioned.
- Declare `tenant_metadata_schema` up front if you need fast scoping fields — **immutable after creation**.
- Retry failed tenants (those in `data.failed_tenant_ids` from `GET /tenants`) by re-creating with `POST /tenants`.

### 8.3 Self-hosting — NOT available

As established in §1: HydraDB is **cloud-only**. There is no Docker image, binary, Helm chart, on-prem tier, or
self-host guide. Every integration is a client of `https://api.hydradb.com`. The only "enterprise" path is
contacting the founders. This is a hard constraint for any deployment that requires data locality, air-gap, or
no managed-cloud dependency.

### 8.4 Tenant isolation guarantees

- `tenant_id` = hard isolation boundary. **No tenant can read another tenant's data. No cross-tenant aggregation, ever. RBACs are safe and respected at all times.** (Vendor claim, emphasized across docs.)
- `sub_tenant_id` = logical partition within a tenant (user/workspace/team). Data doesn't cross sub-tenants.
- Isolation is enforced at the storage layer (two vector stores per tenant: knowledge + memories).

### 8.5 Pricing / scaling notes

- Scale: **10K → 10M documents** (managed-service capability).
- Retrieval latency: **sub-200ms** (vendor claim).
- Embedding dimension: default `1536` (OpenAI-text-embedding-sized); configurable via `embeddings_dimension`
  for embeddings tenants (`is_embeddings_tenant: true`).
- Specific pricing tiers, rate limits, and plan quotas are **not published in the docs** — they live in the
  `app.hydradb.com` dashboard. The `FORBIDDEN` error code explicitly mentions "the account/plan limit prevents
  the operation," so plan limits are enforced server-side. Rate limits surface as `429 RATE_LIMITED`.

### 8.6 Rate limits & retries

- `/query` documents a `429` response (rate limit). Other endpoints share the standard envelope's 429 handling.
- Retry **only** transient failures: 429, 500, 503. Exponential backoff: `2**attempt * 1000` ms + jitter (0–250ms). Bounded retries.

---

## 9. Map to the Hermes Memory Provider Use Case

This section bridges HydraDB concepts to the Hermes memory-provider contract (the companion doc
`hermes-memory-provider-research.md` covers the provider contract). The goal: a single HydraDB-backed memory
provider that stores/retrieves the agent's persistent cross-session memory (user profile facts + memory notes)
and injects them into the system prompt each turn, shared across all Hermes profiles.

### 9.1 Concept mapping

| Hermes concept | HydraDB concept | Notes |
| --- | --- | --- |
| User profile (durable facts about the user) | **Memories** with `infer: false` (verbatim, structured) | Stable facts you already have. Use a stable `id` per fact for upsert/delete. |
| Memory notes (episode/observation logs) | **Memories** with `infer: true` (or `false`) | `infer: true` to auto-extract durable facts from raw conversation signal; `false` to store verbatim notes. |
| `add` memory operation | `client.context.ingest(type="memory", …)` | Multipart form; `memories` = JSON-stringified array. Returns `id`s for polling. |
| `replace` memory operation | `client.context.ingest(type="memory", upsert=True, memories=[{id, …}])` | Same `id` + `upsert:true` (default) replaces. |
| `remove` memory operation | `client.context.delete(type="memory", ids=[…])` | Immediate removal from `/query` + `/context/list`. |
| `prefetch()` / retrieval | `client.query(type="memory" or "all", …)` + `build_string(result)` | Inject the formatted string into the system prompt. |
| Per-profile isolation | `sub_tenant_id` per Hermes profile | One tenant, one `sub_tenant_id` per profile. OR one shared `sub_tenant_id` if cross-profile memory is desired. |
| Frozen-snapshot injection (current Hermes behavior) | Replaced by **live retrieval** via `client.query()` | No more static snapshot — each turn queries the current memory state. |
| Whole-tenant isolation | `tenant_id` | One tenant for the whole HydraDB-backed Hermes deployment. |

### 9.2 Recommended topology for the user's setup

The user runs Hermes with multiple profiles under `~/.hermes/profiles/<name>/` and wants **one shared
HydraDB-backed memory across all profiles**. Two viable patterns:

**Pattern A — one tenant, one shared `sub_tenant_id` (shared memory across profiles):**
- `tenant_id = "hermes_<owner>"` (one tenant for the whole deployment).
- `sub_tenant_id = "shared"` (or the default sub-tenant) — all profiles read/write the same memory pool.
- Every profile's provider uses the same `tenant_id` + `sub_tenant_id`. Memory is globally shared.

**Pattern B — one tenant, one `sub_tenant_id` per profile (per-profile isolation):**
- `tenant_id = "hermes_<owner>"`.
- `sub_tenant_id = "<profile_name>"` (e.g. `default`, `work`, `research`).
- Each profile sees only its own memories. To share a memory, write it under each profile's `sub_tenant_id`
  (or pick a shared sub-tenant for cross-profile knowledge and per-profile sub-tenants for personal memory).

> The user explicitly wants ONE shared memory across all profiles → **Pattern A** is the default. If per-profile
> isolation is later wanted, switch to Pattern B with no schema change (sub-tenants are implicit).

### 9.3 Mapping the provider operations to HydraDB calls

Assume the provider is configured with `tenant_id`, `sub_tenant_id`, and a `HydraDB` sync client.

**`add(memory)`** — store a new memory note / user-profile fact:
```python
client.context.ingest(
    type="memory", tenant_id=TENANT, sub_tenant_id=SUB, upsert=True,
    memories=json.dumps([{
        "id": memory.id,                 # stable id for later replace/remove
        "text": memory.content,
        "infer": memory.infer or False,   # True to auto-extract durable facts
        "user_name": memory.user_name or "User",
        "metadata": json.dumps(memory.tenant_metadata) if memory.tenant_metadata else None,
        # NOTE: memory.metadata must be a JSON STRING for type=memory
        "additional_metadata": memory.extra or None,   # object, not string
    }]),
)
# Fire-and-forget on a daemon thread; capture the returned id for status polling if needed.
```

**`replace(memory)`** — update an existing memory by id:
```python
client.context.ingest(
    type="memory", tenant_id=TENANT, sub_tenant_id=SUB, upsert=True,
    memories=json.dumps([{"id": memory.id, "text": memory.content, "infer": False}]),
)   # upsert replaces the existing memory with the same id
```

**`remove(memory_id)`** — delete a memory:
```python
client.context.delete(type="memory", tenant_id=TENANT, sub_tenant_id=SUB, ids=[memory_id])
```

**`prefetch()` / retrieval** — query memories and inject into the system prompt:
```python
from hydra_db.helpers import build_string
result = client.query(
    tenant_id=TENANT, sub_tenant_id=SUB,
    query=current_user_turn_or_summary,     # or a fixed "user profile and preferences" query
    type="memory",                          # "all" if you also ingest shared Knowledge
    query_by="hybrid", mode="thinking",     # thinking for best personalization
    max_results=10, graph_context=True,
)
memory_context = build_string(result)       # plain string for the system prompt
```

> **Why `type: "memory"` (or `"all"`) replaces frozen-snapshot injection:** Instead of reading a static JSON
> snapshot of the user profile from disk each turn, the provider queries HydraDB for the current, ranked,
> personalized memory state. New facts added mid-session are visible on the next `prefetch()` (once indexing
> completes — see §9.5 on the async caveat).

### 9.4 `infer: true` for auto-extracting durable facts

When the agent observes a user preference implied by conversation (e.g. "the user keeps asking for code
examples"), ingest the raw signal as a memory with `infer: true`. HydraDB extracts the durable preference
("User prefers code examples in responses") and returns it via `inferred_content` on `inspect`/`list` and as
ranked chunks on `query`. This lets the provider auto-distill conversation into a growing user profile without
hand-written extraction logic. Use `infer: false` for facts you already have structured and don't want re-interpreted.

### 9.5 The async caveat for the Hermes provider (IMPORTANT)

Ingestion is **asynchronous**: a memory is not queryable until it reaches at least `graph_creation` (seconds
for memories). Implications for the provider:

- **Writes (add/replace/remove) should be fire-and-forget on a daemon thread.** Don't block the prompt path on
  ingestion + polling. Remove is effectively immediate (deleted IDs disappear from `/query` right away), but
  add/replace need background indexing before they're retrievable.
- **A fact added this turn may not be retrievable until the next turn** (a few seconds of indexing). This is
  acceptable for cross-session memory (the point is persistence across sessions, not within a single turn), but
  the provider should not assume an `add` followed immediately by a `query` will return the just-added fact.
- **For same-turn visibility**, the provider can keep an in-memory write-through cache of recent additions and
  merge it with the `query` results client-side, falling back to the cache if the indexed result doesn't yet
  include the new fact.
- **Polling is optional for the provider.** If you need to confirm a write succeeded, poll
  `client.context.status(tenant_id, ids=[id])` until `completed`/`graph_creation` (or `errored`). For
  fire-and-forget, skip polling and rely on eventual consistency — but do log `error_code`/`error_message` if a
  later status check reveals an `errored` item.

### 9.6 Sync client is mandatory

Hermes memory providers are synchronous (no `asyncio`). Use `HydraDB` (sync), not `AsyncHydraDB`. See §5.9 and §10.

### 9.7 Suggested tenant metadata schema for the Hermes use case

If you want fast scoping on memory category (e.g. "profile" vs "note" vs "feedback"), declare it at tenant
creation (immutable):

```python
client.tenants.create(
    tenant_id="hermes_owner",
    tenant_metadata_schema=[
        {"name": "category", "data_type": "VARCHAR", "max_length": 64, "enable_match": True},
        # e.g. category in {"profile", "note", "feedback", "preference"}
    ],
)
```
Then at ingest, set each memory's `metadata` (as a JSON string) to `{"category": "profile"}`, and at query,
filter with `metadata_filters={"category": "profile"}` to retrieve only profile facts. (You can skip this and
rely on `additional_metadata` if you don't need the fast path — but then scoping is slower.)

---

## 10. Gotchas & Open Questions

### 10.1 Cloud-only constraint (the big one)
HydraDB is a **managed cloud SaaS** — no self-hosting, Docker, binary, or on-prem option exists (§1, §8.3).
The Hermes user prefers self-hosted Linux/Docker/binary with no managed cloud. **HydraDB cannot satisfy that
preference.** Any provider built on it is a client of `https://api.hydradb.com`. This must be surfaced and
accepted before the build phase. Alternatives if self-hosting is a hard requirement: a local vector DB
(Qdrant/Milvus/Chroma) + your own retrieval logic, or a different memory substrate entirely.

### 10.2 Async polling requirements
- **Tenant creation** is async — poll `GET /tenants/status` until `infra.ready_for_ingestion` (§4.5).
- **Ingestion** is async — poll `GET /context/status` until `graph_creation` (searchable) or `completed`
  (graph-complete). `errored` is terminal (§4.9). Memories index in seconds; docs in minutes.
- For the Hermes provider, writes should be fire-and-forget on a daemon thread; retrieval assumes eventual
  consistency across turns (§9.5).

### 10.3 SDK sync vs async — Hermes tension
- Hermes memory providers are **synchronous**, no `asyncio`. Use `HydraDB` (sync). `AsyncHydraDB` returns
  coroutines and cannot be called from a sync provider without a private event loop in a thread (not worth it).
- For non-blocking writes, dispatch sync `ingest()` to a daemon thread. Retrieval must be synchronous on the
  prompt path (the provider needs the result before returning).

### 10.4 Memory metadata encoding gotcha
For `type=memory`, each memory item's `metadata` must be a **JSON-encoded string** (e.g.
`"{\"department\":\"support\"}"`), NOT an object. Passing an object returns `400 INVALID_INPUT`. Keep
`additional_metadata` as an object. (For `type=knowledge`, `metadata` is an object.) This is a sharp edge that
will bite during the build — wrap it in a helper.

### 10.5 `id` vs `source_id` field naming
The docs' "Memory Fields" table uses `id`; the OpenAPI `MemoryItem` schema uses `source_id`. The SDK normalizes.
Use a stable `id` for upsert/delete. The ingest response returns `id` in `results[].id`. For knowledge uploads,
the result item uses `id` in v2 (`source_id` in the legacy v1 `SourceUploadResultItem`).

### 10.6 Schema immutability
`tenant_metadata_schema` field names cannot be changed after tenant creation. Undeclared `metadata_filters` keys
are **silently ignored** at query time (no error — just no filtering). Plan scoping fields before first ingest.
Renaming a key or toggling `enable_match` requires re-ingesting affected sources.

### 10.7 `row_count` is chunks, not sources
`GET /tenants/stats` `row_count` counts individual chunks, not documents/memories. For distinct counts, use
`POST /context/list` with `page_size=1` and read `pagination.total`.

### 10.8 Sub-tenant isolation is real but not a security boundary
`sub_tenant_id` partitions data within a tenant but the docs frame `tenant_id` as the hard isolation boundary
("no cross-tenant aggregation, ever"). Sub-tenant isolation is a logical/data-scoping partition, not a
separate-authz boundary. For the Hermes use case (one owner, one tenant, profiles as sub-tenants) this is fine.

### 10.9 `metadata_filters` are equality-only
No range, contains, or fuzzy matching in `metadata_filters`. OR semantics require multiple query calls +
client-side union. `additional_metadata` filters are slower (~3× over-fetch, post-retrieval).

### 10.10 Webhooks require public HTTPS
Webhook URLs must be public-internet-reachable; localhost/private networks are blocked. For a local Hermes
deployment, polling `/context/status` is the only option — you can't receive webhooks locally without a tunnel.

### 10.11 Rate limits not documented
Specific rate limits/quotas aren't in the docs — they're in the `app.hydradb.com` dashboard. `/query` documents
`429`; all endpoints share 429 handling. Plan for exponential backoff on 429/500/503.

### 10.12 `graph_context` default is `true` on v2
`graph_context` defaults to `true` (v2). Set it `false` explicitly for chunk-only responses (saves size/latency
for direct factual lookups). In `mode: "fast"` the graph slice is shallow; pair `graph_context: true` with
`mode: "thinking"` for richer multi-hop traversal.

### 10.13 `query_forceful_relations` only in `thinking`
`query_forceful_relations` (default `true`) only takes effect in `mode: "thinking"`. In `fast` mode it's ignored.

### 10.14 `query_apps` ignored for memory searches
`query_apps` adds an app-aware retrieval lane but is ignored for `type: "memory"` queries. It also does NOT
restrict the query to app sources — it augments the standard knowledge lane.

### 10.15 Open questions (unresolved by the docs)
- **Pricing / plan limits / exact rate limits:** not in the docs; dashboard-only. Unknown whether a free tier
  exists sufficient for a personal Hermes deployment.
- **Data residency / region selection:** not documented. Unknown whether you can pin a tenant to a specific region.
- **Data export / backup:** no documented export endpoint beyond `/context/inspect` (per-source) and
  `/context/list` (paginated). No bulk export. Self-backup would require iterating `/context/list` + `/context/inspect`.
- **SLA / uptime guarantees:** not in the docs.
- **Concurrency limits on ingestion:** not documented. Unknown max concurrent in-flight ingests per tenant.
- **`num_related_chunks`:** present in the OpenAPI `QueryRequest` (default 10) but hidden in the docs
  (`x-hydradb-docs-hide: true`). Effect of changing it is undocumented.
- **Whether `success` status (legacy alias for `completed`) still appears in `/context/status` responses in
  practice:** the OpenAPI enum lists it, the docs say `success` is a legacy alias for `completed`. Treat both as
  terminal-success.

---

## Appendix A — Quick reference: the canonical v2 loop

```
POST /tenants                              → client.tenants.create()
  ↓ (async)
GET /tenants/status (poll)                 → client.tenants.status()  until infra.ready_for_ingestion
  ↓
POST /context/ingest (type=knowledge|memory) → client.context.ingest()  → returns ids, 202 Accepted
  ↓ (async)
GET /context/status (poll, ids)            → client.context.status()  until graph_creation|completed (errored=terminal)
  ↓
POST /query (type=knowledge|memory|all)    → client.query()           → V2RetrievalResult{chunks,sources,graph_context,additional_context}
  ↓
build_string(result)                        → plain string for the LLM system prompt
```

## Appendix B — Key enums

- `IngestType`: `knowledge`, `memory`
- `SourceType` (query `type`): `knowledge`, `memory`, `all`
- `QueryBy`: `hybrid`, `text`
- `RetrieveMode`: `fast`, `thinking`
- `BM25OperatorType`: `or`, `and`, `phrase`
- `indexing_status` (`V2ProcessingStatus`): `queued`, `processing`, `completed`, `errored`, `graph_creation`, `success`
- `SourceStatus`: `queued`, `processing`, `completed`, `failed`
- `MilvusDataType`: `BOOL`, `INT8`, `INT16`, `INT32`, `INT64`, `FLOAT`, `DOUBLE`, `VARCHAR`, `JSON`, `ARRAY`, `FLOAT_VECTOR`, `SPARSE_FLOAT_VECTOR`

## Appendix C — Source URLs verified

- Essentials: architecture, knowledge, memories, query, metadata, multi-tenant, api-results, context-graphs, semantic-search, app-sources, webhooks
- Get-started: introduction, core-concepts, quickstart
- API reference: v2 overview, sdks, error-responses, and every endpoint page (create-tenant, ingest-context, query/query-overview, source-status, list-documents, fetch-content, delete-tenant, delete-source, tenant-status, tenant-stats, list-sub-tenants, list-tenants)
- Agent guide: `/AGENTS`, `/llms.txt`
- OpenAPI JSON: `https://docs.hydradb.com/api-reference/v2/openapi.json` (OpenAPI 3.1.0, 359,343 bytes, 47 paths / 53 operations)
- Cookbooks index: `/cookbooks/v2/index`
