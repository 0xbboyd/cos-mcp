# Codebase Concerns

**Analysis Date:** 2026-06-20

## Tech Debt

**Monolithic single-file provider (`hydradb-memory/__init__.py` — 558 lines):**
- Issue: All provider logic — config, lifecycle, client management, read path, write path, tools, circuit breaker, session hooks — lives in one file with one class. No modular decomposition.
- Why: Rapid scaffolding during Phase 1 prototype.
- Impact: Hard to test individual components in isolation. Changes to any subsystem risk regressions across the class. No separation of concerns between I/O, state management, and business logic.
- Fix approach: Split into `provider.py` (class skeleton), `client.py` (SDK wrapper with circuit breaker), `query.py` (_format_chunks, read path), `ingest.py` (write path helpers), `tools.py` (tool schemas + handlers), `config.py` (config layer). Keep `__init__.py` as thin re-export + register().

**Coarse circuit breaker (lines 253–268):**
- Issue: One global circuit breaker gates all operations (prefetch, sync_turn, on_memory_write, on_session_end). A failure in any operation increments the single counter.
- Why: Simplest implementation during scaffolding.
- Impact: If `sync_turn` (fire-and-forget writes) fails 5 times, the breaker opens and blocks all reads too — `prefetch` stops returning memories even though the query endpoint may be healthy. Write failures shouldn't gate reads.
- Fix approach: Either split into read-breaker and write-breaker, or use per-operation circuit breakers. At minimum, exclude reads from the write-failure count.

**Bare `except Exception` swallowing specific failures (5 locations: lines 309, 374, 415, 442, 535):**
- Issue: All I/O operations catch `except Exception` with no differentiation between network errors, auth errors, SDK errors, and data errors.
- Why: Fail-open constraint: "never crash the agent, catch all exceptions."
- Impact: Transient network errors and permanent auth errors are treated identically (both increment the same failure counter). A bad API key will trip the circuit breaker after 5 turns and silently block all memory for 120s instead of surfacing the auth problem.
- Fix approach: Catch specific exceptions (`ConnectionError`, `Timeout`, SDK-specific `AuthenticationError`) with differentiated handling. Auth errors should bubble up or log at ERROR, not increment the circuit breaker.

**No thread tracking for `on_memory_write` and `on_session_end` daemon threads:**
- Issue: `on_memory_write` (line 419) and `on_session_end` (line 539) spawn bare `threading.Thread(target=..., daemon=True)` without storing a reference. `shutdown()` (line 544) only joins `_prefetch_thread` and `_sync_thread` — the write and session-end threads are untracked.
- Why: Oversight during scaffolding; these paths were added after `sync_turn` which does store `_sync_thread`.
- Impact: `shutdown()` cannot join these threads. They will be forcibly terminated when the process exits, potentially mid-Ingest, causing partial writes or orphaned HydraDB operations. Memory writes may be silently lost.
- Fix approach: Store references as `self._write_thread` and `self._summary_thread` in `initialize()`, update `shutdown()` to join all four threads.

**Circuit breaker state unprotected (lines 259–268):**
- Issue: `_failure_count` and `_breaker_open_until` are read and written without any lock. While CPython's GIL makes individual int/float assignments atomic, the read-modify-write cycle (`_failure_count += 1`, `_is_breaker_open()` check-then-act) is not.
- Why: Acceptable for non-critical counter under GIL (per `ARCHITECTURE.md` line 204).
- Impact: Low risk in practice (single-session, low thread count), but two threads could simultaneously read `_failure_count` as 4, both increment to 5, and the circuit breaker may open on one failure earlier than expected or miss the threshold window entirely.
- Fix approach: Either add a `threading.Lock` guard for breaker state mutations, or use `threading.RLock` shared with the client lock, or switch to `itertools.count` + atomic operations if Python ≥ 3.10.

## Known Bugs

**Orphaned daemon threads on `on_memory_write` and `on_session_end`:**
- Symptoms: `shutdown()` completes but background ingest threads are still running. If process exits during HydraDB ingest, memory data may be lost. No error surfaced to caller.
- Trigger: Call `on_memory_write()` or `on_session_end()`, then immediately call `shutdown()`.
- Workaround: None — the threads are unreachable from the provider instance.
- Root cause: Bare `threading.Thread(target=..., daemon=True).start()` at lines 419 and 539 without assigning to an instance attribute.
- Fix: Assign to `self._write_thread` / `self._summary_thread` in `initialize()`, join both in `shutdown()`.

**Race condition on `_prefetch_result` cache:**
- Symptoms: A second `queue_prefetch()` call could overwrite the cached result before the first `prefetch()` call retrieves it. Only one slot exists.
- Trigger: Two rapid turns where `queue_prefetch()` is called for turn N+1 before `prefetch()` is called for turn N.
- Workaround: The agent runtime's turn loop serializes these calls in practice, so this is unlikely but architecturally present.
- Root cause: Single-slot cache model (`_prefetch_result`) with no queuing or versioning (lines 279–316).
- Fix: Add a `_prefetch_generation` counter incremented in `queue_prefetch()` and checked in `prefetch()`, or use a `queue.Queue` with maxsize=1.

**`_format_chunks` assumes SDK internals without guard:**
- Symptoms: `AttributeError` if the HydraDB SDK response shape changes (`result.data.chunks` missing or `chunk_content` renamed).
- Trigger: HydraDB SDK update that changes response object attributes.
- Workaround: Pin `hydradb-sdk==2.0.1` (current behavior).
- Root cause: Direct attribute access via `getattr(c, "chunk_content", "")` (line 334) is defensive, but `getattr(result.data, "chunks", None)` (line 326) assumes `result` has a `.data` attribute. No `hasattr` guard.
- Fix: Add `hasattr(result, "data")` check before accessing `result.data.chunks`, return empty string if shape doesn't match.

**`_format_chunks` no defense for `None` result:**
- Symptoms: If `client.query()` returns `None` (e.g., network timeout with no response), `getattr(result.data, "chunks", None)` will raise `AttributeError` because `None` has no `.data`.
- Trigger: Network partition or HydraDB API outage that returns `None` instead of a response object.
- Workaround: The `try/except Exception` in `queue_prefetch` catches this and increments circuit breaker — result is silently lost.
- Root cause: No `None` guard on `result` parameter in `_format_chunks()` (lines 319–337).
- Fix: Add `if result is None: return ""` at top of `_format_chunks()`.

**`on_session_end` creates thread without honoring `agent_context` gate at call time:**
- Symptoms: If `_agent_context` is changed after `on_session_end` is called but before the background thread executes, the thread may ingest when it shouldn't (or skip when it should ingest).
- Trigger: Race between `on_session_end` thread start and a context change (unlikely in practice since this is called once at session end).
- Workaround: Captured in closure at spawn time (line 501 vs 507) — the guard is evaluated synchronously, so the thread is only spawned when context permits. This is actually correct.
- Root cause: N/A — the guard at line 501 is evaluated before thread spawn, making this a false alarm. However, the pattern is fragile: if someone refactors to move the guard check inside the thread function, it would break.

## Security Considerations

**API key validation gap:**
- Risk: No validation of `HYDRA_DB_API_KEY` format before passing to SDK. A malformed or expired key causes 5 failures and trips the circuit breaker — silently blocking all memory for 120s with no user-visible auth error.
- Current mitigation: `is_available()` (line 191) checks that the env var is non-empty and the SDK is importable, but doesn't validate key format or make a test API call.
- Recommendations: Add key format validation (prefixed `sk_live_` or `sk_test_` per HydraDB convention). Optionally make a lightweight API call (e.g., `client.tenants.list()`) during `initialize()` with a short timeout to validate the key early and log a clear ERROR if auth fails.

**Secret isolation in `save_config`:**
- Risk: If `save_config()` (lines 178–184) inadvertently writes the API key to `hydradb.json`, it would be on disk in a non-`.env` file, potentially committed or leaked.
- Current mitigation: `save_config()` explicitly filters `api_key` from the written dict via a `secrets` set (line 180–181). API key only loaded from environment, never written to disk. This is correct.
- Recommendations: Add a regression test that asserts `hydradb.json` never contains `"api_key"` after any `save_config()` call.

**No config write safeguards:**
- Risk: `save_config()` writes with `open(path, "w")` — no atomic write pattern (write to temp file + rename). If the process crashes mid-write, `hydradb.json` is corrupted.
- Current mitigation: None.
- Recommendations: Use `tempfile.NamedTemporaryFile` + `os.rename` for atomic config writes.

## Performance Bottlenecks

**Unbounded daemon thread creation:**
- Problem: Every `queue_prefetch`, `sync_turn`, `on_memory_write`, and `on_session_end` spawns a new `threading.Thread`. No thread pool, no thread reuse.
- File: `hydradb-memory/__init__.py` lines 313–316, 378–381, 419–420, 539–540.
- Measurement: Thread creation overhead ~0.5ms per thread (negligible for one-at-a-time), but under high-frequency memory writes (rapid `on_memory_write` calls), dozens of threads could accumulate before any complete.
- Cause: Simplest non-blocking pattern — fire-and-forget via daemon threads.
- Improvement path: Replace ad-hoc daemon threads with a `concurrent.futures.ThreadPoolExecutor` (max_workers=4). This limits concurrent I/O, enables `Future` tracking, and allows `shutdown()` to wait for all pending operations. Add a bounded queue to reject excess operations instead of spawning unbounded threads.

**Fire-and-forget writes with no backpressure:**
- Problem: `sync_turn`, `on_memory_write`, and `on_session_end` spawn threads and return immediately. If HydraDB ingestion is slow (API latency ~500ms), calls can queue up with no bound.
- File: `hydradb-memory/__init__.py` lines 341–420, 499–540.
- Measurement: Under load, thread count grows linearly with call rate — no throttling.
- Cause: Fire-and-forget design constraint ("sync_turn must be non-blocking").
- Improvement path: Use `ThreadPoolExecutor` with a bounded work queue (e.g., `max_workers=2`). When queue is full, log a warning and drop the oldest or newest entry. For `sync_turn`, consider batching multiple turns into a single ingest call.

**No connection pooling or reuse:**
- Problem: The HydraDB SDK client (`self._client`) is created once as a singleton (line 248), but it's unclear whether the SDK internally uses connection pooling or creates a new HTTP connection per request. If it doesn't pool, each `client.query()` or `client.context.ingest()` pays TCP+TLS handshake overhead.
- Measurement: Unknown — depends on SDK internals. `requests.Session` reuses connections if the SDK uses it; `httpx` does too. If the SDK uses bare `urllib`, each call is a fresh connection.
- Cause: Opaque SDK behavior — no control from provider code.
- Improvement path: Verify SDK uses a session-based HTTP client with connection reuse. If not, file an issue with HydraDB SDK or wrap calls with a `requests.Session` adapter.

**`shutdown()` thread.join(timeout=5.0) leaves background work incomplete:**
- Problem: If a HydraDB ingest is in flight when `shutdown()` is called, `thread.join(timeout=5.0)` gives it 5 seconds to finish. If the API is slow (>5s), the thread is abandoned and the write may be lost.
- File: `hydradb-memory/__init__.py` lines 544–546.
- Measurement: HydraDB ingest latency ~500ms typical; query latency 2–2.5s. Under normal conditions, 5s is enough. Under degraded conditions, writes will be dropped.
- Cause: Fixed 5s timeout with no retry or graceful degradation.
- Improvement path: Increase timeout to 30s for shutdown joins, or implement a graceful shutdown pattern: stop accepting new work, wait for in-flight operations with a generous timeout (30s), then force-terminate remaining threads.

## Fragile Areas

**Threading patterns (`hydradb-memory/__init__.py` lines 220–226, 291–316, 355–381, 394–420, 506–540):**
- Why fragile: Mix of tracked threads (`_prefetch_thread`, `_sync_thread`) and bare anonymous threads (`on_memory_write`, `on_session_end`). Daemon threads can be killed mid-operation by the Python runtime on exit. No thread lifecycle monitoring. Circuit breaker state shared without lock.
- Common failures: Orphaned threads not joined on shutdown; partial HydraDB writes on process exit; circuit breaker miscount under concurrent access.
- Safe modification: Any change to threading code must (a) store thread references, (b) join them in `shutdown()`, (c) protect breaker state with a lock, (d) add `threading.main_thread().is_alive()` guards if needed.
- Test coverage: None. Threading behavior is untested.

**Exception swallowing with DEBUG-only logging (`hydradb-memory/__init__.py` lines 309–311, 374–376, 415–417, 535–537):**
- Why fragile: All I/O failures are caught, logged at DEBUG, and silently discarded. The circuit breaker increments, but no ERROR or WARNING is emitted for individual failures — only the "circuit breaker OPEN" message at threshold 5 is at WARNING. This means 4 consecutive failures produce zero visible log output at default log levels.
- Common failures: Memory silently stops working with no indication to the user. An operator checking logs at INFO level sees nothing until circuit breaker opens (and even then, only the breaker message, not the root cause).
- Safe modification: Upgrade individual failure logs from `logger.debug` to `logger.warning` or add `exc_info=True` to expose the traceback at higher verbosity. Add a `logger.info("HydraDB operation failed (X/5)")` message on each failure.
- Test coverage: None.

**`_format_chunks` tight coupling to SDK response shape (`hydradb-memory/__init__.py` lines 319–337):**
- Why fragile: Accesses `result.data.chunks`, `c.relevancy_score`, `c.chunk_content` via `getattr` with defaults — defensive but assumes the SDK response is an object with these attributes. A HydraDB SDK v3 that changes the response model will silently return empty strings.
- Common failures: SDK upgrade breaks all memory retrieval with no error — `_format_chunks` returns `""`, `prefetch()` returns `""`, and the agent sees no memories.
- Safe modification: Add a version-check or response-shape validation at startup. Wrap `_format_chunks` with a fallback that logs the actual response shape on mismatch.
- Test coverage: None. Response parsing is untested.

**Tool call dispatch with bare `except Exception` (`hydradb-memory/__init__.py` lines 442–444):**
- Why fragile: `handle_tool_call` catches all exceptions and returns JSON `{"error": str(e)}`. The model sees the error, but the provider has no awareness that a tool failed. Unlike the write path, tool failures do NOT increment the circuit breaker — they are silently swallowed.
- Common failures: If `_tool_search` raises (e.g., missing `query` key in args), the model gets an error JSON but the provider continues as if nothing happened. No telemetry, no circuit breaker increment for query failures.
- Safe modification: Add `_record_failure()` call inside the except block for tool failures. Differentiate between user errors (missing args → return error to model) and system errors (API failure → increment breaker).
- Test coverage: None.

**`on_memory_write` with `infer=False` but no dedup/conflict handling:**
- Why fragile: Built-in memory operations (`memory add/replace/remove`) are mirrored verbatim (`infer=False`). If the user does `memory add` then `memory replace` for the same entry, both are ingested as separate memories with no linking or deduplication. HydraDB may store contradictory information.
- Common failures: Stale or superseded memory entries persist alongside updated ones, producing conflicting search results.
- Safe modification: Tag mirrored writes with a `source="builtin"` metadata field and an operation timestamp. Add a periodic dedup job (future feature).
- Test coverage: None.

**`sync_turn` and `on_session_end` skip non-primary agent contexts (lines 350–351, 501–502):**
- Why fragile: If `_agent_context` is not "primary", these methods silently return without ingesting. This is by design (avoid memory pollution from sub-agents), but there's no logging or telemetry to indicate that a turn was skipped.
- Common failures: Operator debugging why memory isn't being populated for a profile — no indication that context is non-primary.
- Safe modification: Add `logger.debug("sync_turn skipped: agent_context=%s", self._agent_context)` when skipping.
- Test coverage: None.

## Scaling Limits

**Thread count under high-frequency writes:**
- Current capacity: ~10 concurrent daemon threads under normal usage (1 prefetch + 1 sync + occasional writes).
- Limit: No bound on thread creation. Under pathological conditions (rapid `on_memory_write` calls from a script), dozens of threads could be created. Python's default thread stack is ~8MB — 100 threads = 800MB virtual memory.
- Symptoms at limit: Memory pressure, thread creation slowdown, eventual `RuntimeError: can't start new thread`.
- Scaling path: Implement `ThreadPoolExecutor` with `max_workers=4`. Add bounded queue with rejection policy for excess operations.

**Single memory cache slot:**
- Current capacity: 1 cached prefetch result per session.
- Limit: If `queue_prefetch()` is called twice before `prefetch()` retrieves the first result, the first result is lost.
- Symptoms at limit: Some turns get empty memory injection despite successful background queries.
- Scaling path: Replace single `_prefetch_result` string with a `collections.deque(maxlen=2)` or a versioned cache that `prefetch()` can validate.

**HydraDB free tier storage:**
- Current capacity: Free tier ($0/mo) with unlimited API calls, storage-based pricing only.
- Limit: Storage limit not publicly documented for free tier. Paid tiers: Surge ($25/mo, 2GB), Scale ($399/mo, 10GB).
- Symptoms at limit: Ingest calls rejected with 4xx/5xx errors, circuit breaker trips.
- Scaling path: Upgrade to paid HydraDB tier. Add local storage monitoring/warning before hitting limits.

**No rate limiting on API calls:**
- Current capacity: Unlimited API calls per HydraDB free tier, but no client-side rate limiting.
- Limit: If HydraDB enforces rate limits (unclear from docs), the provider has no backoff or throttling beyond the circuit breaker's 120s cooldown.
- Symptoms at limit: 429 responses, circuit breaker opens, all memory blocked for 120s.
- Scaling path: Add exponential backoff between retries. Add client-side rate limiter (token bucket) to smooth burst traffic.

## Dependencies at Risk

**`hydradb-sdk==2.0.1` (installed in `.venv`, declared in `plugin.yaml` as `>=2,<3`):**
- Risk: hydradb-sdk major version 2 is the current release. A 3.0 release could break the response shape (`result.data.chunks`, `chunk_content`, `relevancy_score` attributes) that `_format_chunks()` depends on. The `>=2,<3` pin prevents automatic upgrade to 3.x, but manual upgrade would silently break memory retrieval.
- Impact: If SDK 3.0 changes response attributes, `_format_chunks()` returns empty strings. All memory reads break. Circuit breaker may not catch this (successful HTTP call with unrecognized response shape).
- Migration plan: Add integration tests that validate response shape against known SDK version. When upgrading to SDK 3.x, run tests first and update `_format_chunks()` for new shape. Consider adding a runtime SDK version check in `is_available()`.

**`agent.memory_provider.MemoryProvider` ABC (imported from Hermes Agent runtime):**
- Risk: The Hermes Agent `MemoryProvider` ABC may change its contract in future versions — methods added, removed, or signatures altered. The provider implements the current contract but has no version guard.
- Impact: Hermes Agent upgrade could fail to load the provider if `is_available()` raises `AttributeError`, or methods could be called with unexpected kwargs.
- Migration plan: Pin Hermes Agent version in deployment docs. Add ABC method signature validation in `initialize()`. Monitor Hermes Agent changelog for breaking changes to the memory provider contract.

**Python 3.12 (system Python, PEP 668 enforced):**
- Risk: The provider uses standard library features available in Python 3.11+ (per README). Python 3.12 is the development environment. Future Python versions could deprecate `threading` patterns or change GIL behavior.
- Impact: Python 3.13+ free-threaded mode (PEP 703) would break assumptions about GIL-protected `_failure_count` access. Thread safety bugs currently masked by the GIL could surface.
- Migration plan: Add explicit locks for all shared mutable state. Test under Python 3.13 free-threaded builds when available.

## Missing Critical Features

**Tenant auto-provisioning with 409 conflict handling:**
- Problem: SPEC.md Phase 1 requires "Handle tenant auto-provisioning with 409 conflict handling." The current `initialize()` method (lines 199–237) loads config and captures identity but does NOT create or verify the tenant exists. No call to `client.tenants.create()` or `client.tenants.list()`.
- File: `hydradb-memory/__init__.py` lines 199–237 (`initialize()`).
- Current workaround: User must manually create the tenant in HydraDB dashboard before using the provider.
- Blocks: Zero-config setup. Operator must know to create a tenant named "hermes" before activating.
- Implementation complexity: Low (~20 lines). Add tenant existence check via `client.tenants.list()` in `initialize()`. If tenant is missing, call `client.tenants.create(name="hermes")` with 409 handling. Cache tenant verification result for session.

**FILE_NOT_FOUND race on `context.status` after ingest:**
- Problem: SPEC.md Phase 1 requires "Handle FILE_NOT_FOUND race on context.status for first 1-2s after ingest." HydraDB ingestion returns a `context_id`, but querying its status immediately may return FILE_NOT_FOUND because indexing takes 1-5s. The current implementation is fire-and-forget — no status check at all.
- File: `hydradb-memory/__init__.py` — no context status polling exists.
- Current workaround: Provider relies on eventual consistency — memories become available 1-5s after ingest. No feedback to caller.
- Blocks: Synchronous verify-after-write patterns. `_tool_conclude()` returns "Fact stored." immediately but the fact may not be queryable for several seconds.
- Implementation complexity: Medium (~40 lines). Add optional status polling with exponential backoff (100ms, 200ms, 400ms, 800ms, max 5s). Return confirmation only after status transitions from FILE_NOT_FOUND to COMPLETED. Use a timeout to avoid blocking indefinitely.

**No memory deduplication or conflict resolution:**
- Problem: Repeated memory writes (e.g., same fact stored multiple times, or contradictory facts from different sessions) accumulate without dedup. HydraDB's `upsert="true"` handles exact-duplicate IDs but not semantically duplicate content.
- Current workaround: User must manually review and clean up memories.
- Blocks: Long-term memory quality. Over months, duplicate and contradictory memories degrade search relevance.
- Implementation complexity: High (~100+ lines). Requires semantic similarity comparison on ingest, or periodic dedup jobs.

**No batch query or multi-turn context windowing:**
- Problem: `queue_prefetch` accepts a single query string and returns a single result set. Complex turns that need multiple memory angles require multiple sequential tool calls (`hydradb_search`).
- Current workaround: Model can call `hydradb_search` tool multiple times with different queries.
- Blocks: Efficient multi-faceted memory retrieval in one round-trip.
- Implementation complexity: Medium (~50 lines). Add `queries: List[str]` support to `queue_prefetch`, merge and deduplicate results.

**No memory expiry, archival, or retention policy:**
- Problem: All memories are stored indefinitely. No mechanism to age out old or irrelevant memories. Over months/years, the memory store grows unbounded.
- Current workaround: Manual cleanup via HydraDB dashboard.
- Blocks: Long-running deployments with high turn volume.
- Implementation complexity: Medium (~60 lines). Add `retention_days` config option. Periodically (or on session start) call HydraDB API to delete memories older than retention threshold. Requires HydraDB SDK support for time-range deletes (to be verified).

## Test Coverage Gaps

**No test suite exists:**
- What's not tested: Entire provider — config loading, client initialization, circuit breaker behavior, `queue_prefetch`/`prefetch` cycle, `sync_turn`, `on_memory_write`, `on_session_end`, tool dispatch (`hydradb_search`, `hydradb_profile`, `hydradb_conclude`), `_format_chunks` response parsing, tenant auto-provisioning, error handling paths.
- Files: No test files anywhere in the project. SPEC.md Phase 2 plans `test_hydradb_provider.py` with fake client, but it hasn't been created.
- Risk: Any code change could silently break functionality. Regression risk is extremely high — the provider has zero safety net. The 558-line monolith with threading and I/O has no validation that it works.
- Priority: Critical — tests must exist before any further feature work.
- Difficulty to test: Moderate. Requires a fake/mock HydraDB client class (`FakeHydraDB`) that implements `.query()`, `.context.ingest()`, and `.tenants.list()` with in-memory state. Threading tests require `threading.Event` synchronization to verify ordering. Live API tests require a valid `HYDRA_DB_API_KEY` and network access.

**Untested threading safety:**
- What's not tested: Double-checked locking in `_get_client()`, lock-protected `_prefetch_result` access, circuit breaker concurrent mutation, daemon thread spawning and joining, `shutdown()` with in-flight operations.
- Risk: Race conditions and deadlocks could exist undetected. The GIL masks many threading bugs on CPython but they would surface on free-threaded Python or under high concurrency.
- Priority: High.
- Difficulty to test: High — requires `threading.Barrier`, `threading.Event`, and careful test orchestration to provoke concurrent access patterns.

**Untested SDK response parsing (`_format_chunks`):**
- What's not tested: `_format_chunks` with valid chunks, empty chunks, None result, chunks below `min_score`, chunks with missing attributes, mixed valid/invalid chunks.
- Risk: A HydraDB SDK update that changes response attributes would silently break memory retrieval. No test would catch it.
- Priority: High.
- Difficulty to test: Low — pure function, easy to unit test with mock chunk objects. Create `FakeChunk(chunk_content, relevancy_score)` dataclass and test all edge cases.

**Untested circuit breaker transitions:**
- What's not tested: Breaker opening at threshold (5 failures), breaker closing on success, cooldown timing (120s), breaker state not affecting tool calls (currently bypasses breaker), `_record_success()` resetting counter.
- Risk: Circuit breaker could fail to open (denial-of-service from retry storms) or fail to close (permanent memory outage).
- Priority: High.
- Difficulty to test: Medium — manipulate `_failure_count` and `_breaker_open_until` directly, call guarded methods, assert behavior. Mock `time.time()` to fast-forward through cooldown.

**Untested config layer edge cases:**
- What's not tested: Missing `hydradb.json`, malformed JSON, missing env var, empty API key, `sub_tenant_id` auto-resolution from `agent_identity`, `save_config` filtering secrets, config schema field completeness.
- Risk: Config errors during provider initialization could crash the agent or silently misconfigure the provider.
- Priority: Medium.
- Difficulty to test: Low — all config functions are pure or I/O-bound with predictable file paths. Use `tempfile.TemporaryDirectory` for hermetic config file tests.

---

*Concerns audit: 2026-06-20*
*Update as issues are fixed or new ones discovered*
