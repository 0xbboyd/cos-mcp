# Pitfalls Research — Context Engine Plugins (v1.1)

**Domain:** Hermes Agent Context Engine Plugins (HydraDB + MuninnDB)
**Researched:** 2026-06-20
**Confidence:** HIGH

All pitfalls are grounded against:
- `agent/context_engine.py` (ContextEngine ABC, 226 lines) — authoritative plugin contract
- `cos_mcp/base_provider.py` (352 lines) — existing BaseMemoryProvider pattern (mirrors BaseContextEngine)
- `cos_mcp/circuit_breaker.py` (87 lines) — dual-gauge CircuitBreaker reused by context engines
- `hydradb-memory/__init__.py` (284 lines) — v1.0 pitfalls reference (13 pitfalls, 9 were PRESENT)
- `.planning/research/ARCHITECTURE.md` (936 lines) — architecture blueprint for context engines
- `.planning/codebase/CONCERNS.md` (270 lines) — v1.0 tech debt, known bugs, fragile areas
- `.planning/PROJECT.md` (122 lines) — v1.1 milestone scope and constraints

Pitfalls marked **[v1.0 REPEAT]** are the same class of bug that hit memory providers — context engines must avoid them from the start. Pitfalls marked **[NEW]** are unique to the compress() pathway and the ContextEngine ABC contract.

---

## Critical Pitfalls

### Pitfall 1: compress() returns more or equal messages — no actual compaction [NEW]

**What goes wrong:**
The `compress()` method is called because `should_compress()` returned True — the context window is too large. If `compress()` returns a message list that is NOT shorter than the input, `run_agent.py` will send the oversized list to the API anyway. The result: API `400 context_length_exceeded` or, on permissive hosts, silent truncation of the most recent (most important) messages. The agent effectively loses context instead of compressing it.

**Why it happens:**
- `protect_first_n` + `protect_last_n` consume the entire message list — the compression window is empty, but the code doesn't check this before returning.
- Entity extraction + summary block generation adds messages instead of reducing them (e.g., emitting one summary system message per entity instead of one aggregate block).
- The compressed summary block is longer than the messages it replaced (verbose summaries, excessive entity detail).
- `focus_topic` handling adds a new system message on top without removing anything.

**How to avoid:**
- **Hard guard at the top of compress():** `if len(messages) <= protect_first_n + protect_last_n: return messages` (no-op, there's nothing to compress).
- **Post-compression assertion:** In dev/test, assert `len(returned) < len(input)` or log a warning if not. In production, guard with `if len(returned) >= len(messages): return messages` — return unchanged rather than making things worse.
- **Summary block length budget:** Cap the summary block at ~800 tokens (~2,000 chars). If extracted entities produce more than this, truncate to top-N by relevance. No single system message should exceed the token budget.
- **Test for token reduction, not just message reduction:** One summary message replacing 40 messages could still be a net increase if it's 10,000 tokens long.

**Warning signs:**
- `should_compress()` returns True, `compress()` runs, next turn immediately triggers `should_compress()` again (infinite compression loop).
- `context_length_exceeded` API errors after compression.
- `len(returned) == len(input)` — compression was a no-op but `compression_count` incremented.
- Summary blocks containing full message texts verbatim instead of condensed summaries.

**Phase to address:** Phase 2 (Compression path implementation). Add assertion in `compress()` during development; demote to warning in production.

---

### Pitfall 2: ContextEngine ABC required class attributes not maintained [NEW]

**What goes wrong:**
The ContextEngine ABC declares six class-level attributes that `run_agent.py` reads directly:
```python
last_prompt_tokens: int = 0
last_completion_tokens: int = 0
last_total_tokens: int = 0
threshold_tokens: int = 0
context_length: int = 0
compression_count: int = 0
```
If a context engine subclass fails to update these correctly (or at all), `run_agent.py`'s preflight compression check, token display in the CLI, and `/status` output show stale/zero values. Specifically:
- `last_prompt_tokens` stuck at 0 means `should_compress()` is never called by the built-in preflight (depends on the engine's own `should_compress_preflight()` override though).
- `threshold_tokens` stuck at 0 means `should_compress()` default implementation (`prompt_tokens >= threshold_tokens`) always returns True — compressing every turn.
- `compression_count` not incremented means `get_status()` reports 0 compressions even after dozens.

**Why it happens:**
These are class attributes (not instance attributes) in the ABC. Subclasses that override `__init__()` without calling `super().__init__()` lose the defaults. Subclasses that store their own `_token_count` without writing back to `last_prompt_tokens` leave the public attribute stale. The ABC doesn't enforce that they're updated — it trusts the engine implementation.

**How to avoid:**
- `BaseContextEngine.update_from_response()` MUST write to ALL token tracking attributes: `last_prompt_tokens`, `last_completion_tokens`, `last_total_tokens`, recalculate `threshold_tokens = int(context_length * threshold_percent)`.
- `BaseContextEngine.compress()` wrapper MUST increment `compression_count` before delegating to the subclass's `_compress_impl()`.
- Add a test that calls `update_from_response()` with realistic usage data, then asserts all six attributes are non-zero and consistent (e.g., `last_total_tokens == last_prompt_tokens + last_completion_tokens`).
- Never shadow these attribute names with instance variables — use property decorators if custom logic is needed, but write back to the class-attribute slot.

**Warning signs:**
- `hermes doctor` reports compression count as 0 after hours of use.
- CLI token display shows `0/0 tokens` when `context_length` is 128k.
- `should_compress()` returns True on every turn (threshold_tokens=0) or never (last_prompt_tokens=0).
- Engine passes ABC `isinstance` check but `run_agent.py` logs `AttributeError` for missing attributes.

**Phase to address:** Phase 1 (BaseContextEngine foundation). Wire `update_from_response()` to update all six attributes. Test in `test_base_context_engine.py`.

---

### Pitfall 3: compress() modifies the input message list in place (shallow-copy trap) [NEW]

**What goes wrong:**
`compress()` receives `messages: List[Dict[str, Any]]` — a mutable list of mutable dicts. If the engine modifies this list in place (e.g., `messages.pop()`, `messages[i]['content'] = '...'`), the caller's message list is corrupted. After compression, `run_agent.py` may still hold references to the original list and use it for the next turn's prompt assembly. The result: messages mysteriously disappear, summaries overwrite original content, or the system prompt gets duplicated.

**Why it happens:**
Python's default behavior for list operations is mutation. New developers naturally write `head = messages[:protect_first_n]` without realizing `head` is a shallow copy — nested dicts are still shared. If the summary block is constructed by reusing a dict from the compressed window (e.g., `summary = compressed_messages[0]; summary['role'] = 'system'`), the original message is corrupted.

**How to avoid:**
- **Always construct a NEW list for the return value.** Never call `messages.pop()`, `messages.remove()`, `del messages[i]`, or `messages.append()`.
- **Deep-copy header/tail messages if they're going to be modified.** But better: don't modify them at all — just slice and include.
- Pattern:
  ```python
  def compress(self, messages, current_tokens=None, focus_topic=None):
      # Reference only — never mutate
      system_msg = messages[0]  # safe: not modified
      head = messages[1:1 + self.protect_first_n]  # shallow copy of refs (fine, dicts unchanged)
      tail = messages[-self.protect_last_n:]        # shallow copy of refs (fine)
      middle = messages[1 + self.protect_first_n : -self.protect_last_n]

      summary = self._build_summary_block(middle)  # brand new dict
      return [system_msg] + head + [summary] + tail
  ```
- Add a test that calls `compress()` then asserts the original `messages` list is identical (length, content, object identity of elements after protect_first_n).

**Warning signs:**
- After compression, the agent repeats summaries from a previous compression round verbatim.
- System prompt appears twice in the message list.
- `IndexError` in `run_agent.py` because `messages[-protect_last_n:]` is no longer the correct slice after in-place removals.
- Hard to debug — only surfaces when `run_agent.py` reuses the message list reference across turns (implementation detail you can't control).

**Phase to address:** Phase 2 (compress() implementation). Add mutation guard test in `test_hydradb_context.py` and `test_muninn_context.py`.

---

### Pitfall 4: Entity extraction quality — over-extraction vs under-extraction [NEW]

**What goes wrong:**
Entity extraction during `compress()` uses pure Python heuristics (keyword matching, pattern recognition, topic clustering). The quality of extraction determines whether future `context_search` calls find useful information. Two failure modes:

- **Over-extraction:** Every message produces 5-10 entities. A 40-message compression window produces 200-400 entities, bloating the backend, increasing query latency, and returning noise on `context_search`. The summary block becomes unwieldy.
- **Under-extraction:** Only 2-3 entities extracted from 40 messages. Important decisions, facts, and topic shifts are lost. Future `context_search` returns "no context found" for queries that should hit.

**Why it happens:**
Entity extraction is the hardest part to tune. Heuristics that are too permissive (e.g., every noun phrase is an entity) cause over-extraction. Heuristics that are too conservative (e.g., only capitalized proper nouns) cause under-extraction. The correct balance depends on conversation density, domain, and user style — which the engine can't know ahead of time.

**How to avoid:**
- **Per-message entity cap:** Extract at most 3 entities per message. If a message is dense, score entities by importance (proper nouns > decisions > facts > topics) and keep top 3.
- **Global dedup across the compression window:** Two messages about the same topic should produce one entity, not two. Use simple string similarity (e.g., trigram Jaccard > 0.7) to merge near-duplicate entities.
- **Minimum entity "weight":** Filter out entities with text shorter than 10 characters or only containing stopwords ("ok", "yes", "thanks").
- **Entity type distribution:** Track counts per type (topic, decision, fact, question, event). If one type dominates (>80%), adjust extraction thresholds for that type downward and others upward.
- **Configurable extraction aggressiveness:** Expose `entity_extraction_mode` config value: `"conservative"` (1-2 entities per message, strict dedup), `"balanced"` (default, 2-3 per message), `"aggressive"` (3-5 per message, loose dedup).
- **HydraDB vs MuninnDB differences:** HydraDB can handle larger entity volumes (graph traversal scales). MuninnDB's ACT-R decay means stale entities naturally fade, so over-extraction is less harmful — but still wastes local storage.

**Warning signs:**
- `context_search` returns 50+ results for a narrow query (over-extraction).
- `context_search` returns empty for queries about topics discussed 5 turns ago (under-extraction).
- Entity storage ingest calls approach HydraDB API rate limits.
- Summary blocks grow to 3,000+ tokens because too many entities are listed.

**Phase to address:** Phase 2 (entity extraction implementation). Add extraction quality tests with realistic conversation samples. Tune heuristics during Phase 4 live testing.

---

### Pitfall 5: Thread safety with shared backends — memory provider and context engine hitting same tenant/vault [v1.0 REPEAT, AMPLIFIED]

**What goes wrong:**
Both the memory provider AND the context engine share the same `HydraDBBackend` or `MuninnDBBackend` instance (or each creates their own pointing at the same tenant/vault). Both spawn daemon threads for writes (`sync_turn`, `on_memory_write`, `on_session_end` from the memory provider; entity storage during `compress()`, tool calls from the context engine). Without coordination:

- **Two `CircuitBreaker` instances for the same backend:** The memory provider's circuit breaker and the context engine's circuit breaker track failures independently. The memory provider's breaker opens from write failures, but the context engine's breaker is still closed — so context engine writes continue hammering a degraded backend while memory provider writes are correctly paused. Or worse: the context engine's breaker opens but memory provider reads (which should still work if only writes are degraded) get blocked because the memory provider's breaker is also open from unrelated failures.
- **Race on tenant/vault provisioning:** Both `initialize()` calls run `backend.provision()`. If HydraDB tenant is missing, both try to create it simultaneously — one succeeds, the other gets 409 Conflict. If one engine handles 409 and the other doesn't, the second `initialize()` crashes.
- **Daemon thread explosion:** Under active use, per-turn the memory provider spawns `_prefetch_thread` + `_sync_thread`, and the context engine spawns entity-storage thread + potential tool query threads. That's 3-4 threads per turn, unbounded. Under high-frequency use, thread count grows without bound.

**Why it happens:**
v1.0 memory providers implemented per-instance circuit breakers. The architecture research for v1.1 specifies each engine gets its own `CircuitBreaker()` instance — "shared infrastructure" but NOT shared breaker state. This is the correct design (independent failure tracking), but it means write failures from the memory provider don't inform the context engine's breaker and vice versa. This is acceptable for read/write independence (the whole point of the dual-gauge design), but dangerous if both engines share a single backend that's actually degraded.

**How to avoid:**
- **Shared CircuitBreaker? No.** The dual-gauge design means each engine's breaker should operate independently — read failures in one shouldn't block writes in the other. The breaker's job is to protect the caller from wasting time on a degraded dependency, not to coordinate across callers.
- **BUT: track backend health at the Backend level.** Add a lightweight health check (`backend.health_check()`) that both engines call before spawning threads. If the backend itself is down, both breakers will open from their own thread failures — this is the correct emergent behavior.
- **Provisioning guard:** Make `backend.provision()` idempotent — if called twice, the second call is a no-op (check readiness flag). Handle 409 Conflict in both engines' `initialize()`.
- **Thread pool instead of per-call threads:** Replace ad-hoc `threading.Thread` spawning with a shared `ThreadPoolExecutor(max_workers=4)` in `BaseContextEngine` (mirror the same fix needed in `BaseMemoryProvider`). This bounds total thread count across both engines.
- **`type` field segregation ensures no data cross-contamination:** Even if both engines are writing simultaneously, context data has `type="context"` and memory data has `type="memory"` — they don't collide. Queries filter by type.

**Warning signs:**
- After a burst of HydraDB write failures, context engine tool calls still work but memory prefetch is broken (or vice versa) — breaker opens in one engine but not the other.
- Two `initialize()` calls for the same profile — second one crashes with 409 Conflict.
- `threading` errors: `RuntimeError: can't start new thread` under load.
- `context_search` returns memory entities (type filter missing) or `hydradb_search` returns context entities.

**Phase to address:** Phase 1 (backends extended with `type` param, provisioning idempotency). Phase 3 (shared thread pool consideration). Test: concurrent initialize + concurrent write stress test.

---

### Pitfall 6: Circuit breaker — independent vs shared for context engine [v1.0 REPEAT]

**What goes wrong:**
The context engine gets its own `CircuitBreaker` instance in `BaseContextEngine.__init__()`. If the implementation mirrors `BaseMemoryProvider` faithfully, it will have dual read/write gauges. But context engines have a different I/O profile than memory providers:

| Operation | Memory Provider | Context Engine |
|-----------|----------------|----------------|
| Read path | 1 call/turn (prefetch) | 0 calls/turn (no prefetch in v1.0); tool calls on demand |
| Write path | 2-4 calls/turn (sync_turn, on_memory_write, on_session_end) | 1 call per compression (entity storage); 1 call per session end |
| Blocking? | Prefetch is background; tools are sync | compress() entity storage is fire-and-forget; tools are sync |

The context engine does far fewer writes (only on compression events, not every turn). A 5-failure write breaker may never trip because 5 compressions without a success could span hours of conversation. Meanwhile, the read breaker protects tool calls (`context_search`, `context_expand`) which are called maybe once every 10 turns. The failure thresholds that make sense for per-turn memory provider I/O are too conservative for context engine I/O.

**Why it happens:**
Same `CircuitBreaker(failure_threshold=5, cooldown=120)` used for both memory provider (per-turn calls) and context engine (per-compression calls). Copy-paste without adjusting thresholds.

**How to avoid:**
- **Use the same CircuitBreaker class but different thresholds per engine.** Allow `failure_threshold` and `cooldown_seconds` to be constructor parameters:
  ```python
  # Memory provider: trips fast, recovers fast
  CircuitBreaker(failure_threshold=5, cooldown_seconds=120)

  # Context engine: trips slower (fewer calls), recovers same
  CircuitBreaker(failure_threshold=3, cooldown_seconds=120)
  ```
- **Make thresholds configurable:** Expose `breaker_read_threshold`, `breaker_write_threshold`, `breaker_cooldown_seconds` in the context engine's config schema.
- **Write breaker guards `compress()` entity storage — NOT `compress()` itself.** If the write breaker is open, `compress()` still runs and returns the summary block; it just skips storing entities in the backend. The immediate value (shorter message list for the next turn) is preserved.
- **Read breaker guards ONLY tools (`context_search`, `context_expand`).** The read breaker never gates `compress()` or `should_compress()` — those are local logic, not backend calls.

**Warning signs:**
- Write breaker never opens after 10+ compressions with backend errors (threshold too high).
- Read breaker opens after 3 failed `context_search` calls and stays open for 2 minutes, blocking all context retrieval.
- `compress()` skips building the summary block because the write breaker is open (wrong — only entity storage should be skipped).

**Phase to address:** Phase 1 (breaker thresholds in config). Phase 3 (correct breaker gating — entity storage only, not compress() itself).

---

### Pitfall 7: Fire-and-forget data loss on shutdown — entity storage threads not joined [v1.0 REPEAT]

**What goes wrong:**
When `compress()` fires entity storage on a daemon thread and returns immediately, the thread is still running. If the session ends (user types `/exit`, `/reset`, or the gateway expires) before the thread completes, `shutdown()` must join it. If `shutdown()` doesn't track the entity storage thread (exact same bug as v1.0's untracked `on_memory_write` and `on_session_end` threads), the thread is abandoned. Entities are lost. The compressed summary block exists (it was returned synchronously), but the backend has no record of the compression — future `context_search` won't find it.

Additionally: `on_session_end()` also spawns a daemon thread for ingesting the session summary as context. Same bug.

**Why it happens:**
Exact same oversight as v1.0 Pitfall 6: bare `threading.Thread(target=..., daemon=True).start()` without storing a reference. The pattern is trivially easy to miss in code review because `.start()` looks complete on its own.

**How to avoid:**
- **Track ALL daemon threads in `initialize()`:**
  ```python
  self._entity_thread: Optional[threading.Thread] = None
  self._session_end_thread: Optional[threading.Thread] = None
  self._tool_threads: List[threading.Thread] = []  # if tools spawn threads
  ```
- **Store reference at spawn time.** Use a helper:
  ```python
  def _spawn_daemon(self, target, name):
      t = threading.Thread(target=target, daemon=True, name=name)
      t.start()
      return t
  ```
- **`shutdown()` joins ALL tracked threads:**
  ```python
  def shutdown(self):
      for thread in (self._entity_thread, self._session_end_thread):
          if thread and thread.is_alive():
              thread.join(timeout=30.0)
      self._backend.shutdown()
  ```
- **Add shutdown test:** Call `compress()`, immediately call `shutdown()`, assert the entity storage thread was joined (use `threading.Event` synchronization in tests).

**Warning signs:**
- `context_search` returns nothing for a compression that definitely happened.
- `shutdown()` returns immediately even after `compress()` fired entity storage.
- Intermittent missing context entities — sometimes queryable, sometimes not, depending on thread timing.
- Python runtime warning: "daemon thread still running" on process exit.

**Phase to address:** Phase 1 (BaseContextEngine foundation). Fix before any context engine code is written. This is the exact same bug that was PRESENT in v1.0 memory providers for 3 phases before being caught.

---

### Pitfall 8: Plugin registration conflicts — only one context engine allowed [NEW]

**What goes wrong:**
Hermes Agent supports only ONE active context engine at a time (`context.engine` in config.yaml is a single value). If both `hydradb-context` and `muninn-context` plugins are installed in-tree, Hermes discovers both via directory scanning. But ONLY the one named in `config.yaml` should activate. If the plugin system (or the engine's own `register()` function) doesn't respect the single-select constraint:

- Both engines could be instantiated and initialized, both spawning backend connections, both tracking tokens — but only one's `compress()` is called. The other is a zombie consuming resources.
- If `config.yaml` says `context.engine: "hydradb-context"` but the plugin's `name` attribute is `"hydradb_context"` (underscore vs hyphen), the config lookup fails silently and Hermes falls back to the built-in `ContextCompressor` — the user thinks they're using graph-backed compression but they're not.
- If a future Hermes version supports multiple context engines (e.g., chain-of-compression), the v1.1 code that assumes single-select breaks.

**Why it happens:**
The plugin `register(ctx)` function calls `ctx.register_context_engine(EngineInstance())` — same pattern as memory providers. But memory providers support multiple simultaneous providers (Hermes can use hydradb + muninn in parallel). Context engines are single-select. The `register()` function doesn't itself enforce single-select — the Hermes runtime's plugin loader does. But if the engine's `is_available()` returns True even when it's not the active engine, it might still be partially initialized by the plugin loader.

**How to avoid:**
- **`is_available()` should be lightweight** — check SDK imports and env vars only. Do NOT make network calls. This way, both plugins can be "available" without consuming resources — only `initialize()` creates backends and starts threads.
- **`name` must match the config.yaml value exactly.** Documented as `"hydradb-context"` (hyphen) in plugin.yaml and config docs. Test that `engine.name == config['context']['engine']` during integration.
- **Add a test:** Install both context engine plugins in-tree, set `config.yaml` to `hydradb-context`, verify `muninn-context`'s `initialize()` is never called.
- **If the plugin loader calls `initialize()` on ALL registered context engines (hypothetical future behavior):** Guard with an `is_active` check:
  ```python
  def initialize(self, session_id, **kwargs):
      active_engine = kwargs.get('active_context_engine', '')
      if active_engine and active_engine != self.name:
          logger.debug("Skipping %s — not the active context engine", self.name)
          return
  ```
  This is defensive coding; current Hermes runtime only initializes the active engine.

**Warning signs:**
- `hermes doctor` shows two context engines registered but only one configured.
- Config says `hydradb-context` but logs show `ContextCompressor` (built-in) active — config name mismatch.
- Memory usage doubled because both engines' backends are provisioned.
- `context_search` returns results even though `context.engine: "compressor"` (the built-in doesn't have this tool — another engine is active).

**Phase to address:** Phase 4 (Hermes integration). Verify single-select behavior with both plugins installed.

---

### Pitfall 9: Config drift between `context.engine` value and actual plugin `name` [NEW]

**What goes wrong:**
The user sets `context.engine: "hydradb-context"` in `config.yaml`. The `HydraDBContextEngine` class sets `name = "hydradb_context"` (underscore, not hyphen). The Hermes plugin loader discovers the engine by directory name (`plugins/context_engine/hydradb-context/`) but matches against `engine.name` for activation. If `name` doesn't match the config value, the engine is registered but never activated — the built-in `ContextCompressor` runs instead with zero indication to the user.

Even subtler: the engine's config file is named `hydradb-context.json` (matching the directory), but the `_load_context_config()` function hardcodes a different filename or reads from the same `hydradb.json` as the memory provider. The context engine reads memory provider config or writes its config to the wrong file.

**Why it happens:**
The plugin system has three names for the same thing:
1. Directory name: `plugins/context_engine/hydradb-context/`
2. Plugin `name` attribute: `"hydradb-context"` (must match config.yaml value)
3. Config file name: `$HERMES_HOME/hydradb-context.json` (must match identifier used in `_load_context_config()`)

Any mismatch between these three breaks silently — no error, just wrong behavior.

**How to avoid:**
- **Use a single source of truth:** Define `ENGINE_NAME = "hydradb-context"` as a module-level constant. Use it for `name` class attribute, config file path, and plugin.yaml. Never hardcode the string in multiple places.
- **Config file separation:** Context engine config MUST use `hydradb-context.json`, NOT `hydradb.json` (same as memory provider). See Anti-Pattern 5 in architecture research.
- **`_load_context_config()` pattern:**
  ```python
  def _load_context_config(self) -> dict:
      cfg = dict(DEFAULT_CONTEXT_CONFIG)
      cfg["api_key"] = os.environ.get("HYDRA_DB_API_KEY", "")
      config_path = os.path.join(self._hermes_home, f"{self.name}.json")
      if os.path.isfile(config_path):
          with open(config_path) as f:
              cfg.update(json.load(f))
      return cfg
  ```
  This automatically uses the correct config file name for any engine name.
- **Add an integration test:** Set `config.yaml` to `context.engine: "hydradb-context"`, initialize the engine, verify `engine.name == "hydradb-context"` AND the active engine's `compress()` is called (not the built-in's).

**Warning signs:**
- Config says `hydradb-context` but `hermes doctor` reports active engine as `compressor`.
- `hydradb-context.json` is written but the engine reads from `hydradb.json` (or vice versa).
- Changing `context.engine` in config.yaml has no effect on which engine is active.

**Phase to address:** Phase 1 (config layer in BaseContextEngine). Add config name test.

---

### Pitfall 10: Token tracking accuracy in `update_from_response()` [NEW]

**What goes wrong:**
The ContextEngine ABC declares `update_from_response(usage: Dict[str, Any])` as abstract. The engine MUST track token usage from EVERY API response. If token tracking is inaccurate:

- `should_compress()` default implementation (`prompt_tokens >= threshold_tokens`) makes the wrong decision. Under-counting means compression never fires (context window overflows, API errors). Over-counting means compression fires too often (unnecessary context loss, extra latency).
- The new canonical bucket fields (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens`) are optional — engines must handle both old-style (`prompt_tokens`, `completion_tokens`) and new-style responses from different API hosts.

**Why it happens:**
- Engine only updates `last_prompt_tokens` from `usage.get("prompt_tokens", 0)` but ignores `input_tokens` when the host sends the newer format.
- Engine adds `input_tokens` AND `prompt_tokens` together (double-counting) instead of preferring `input_tokens` and falling back to `prompt_tokens`.
- `cache_read_tokens` represent tokens NOT charged to the context window (prompt caching). If the engine counts them as consumed tokens, it overestimates usage and compresses prematurely.
- `reasoning_tokens` are internal to the model (not context window). Counting them inflates usage.

**How to avoid:**
- `BaseContextEngine.update_from_response()` MUST handle both formats correctly:
  ```python
  def update_from_response(self, usage: Dict[str, Any]) -> None:
      # Prefer canonical field names (newer hosts), fall back to legacy
      self.last_prompt_tokens = usage.get("input_tokens") or usage.get("prompt_tokens", 0)
      self.last_completion_tokens = usage.get("output_tokens") or usage.get("completion_tokens", 0)

      # Cache tokens are NOT context window tokens — track separately for info
      self.cache_read_tokens = usage.get("cache_read_tokens", 0)
      self.cache_write_tokens = usage.get("cache_write_tokens", 0)

      # Total: prompt + completion only (not reasoning, not cache)
      self.last_total_tokens = self.last_prompt_tokens + self.last_completion_tokens

      # Recalculate threshold if context_length changed
      self.threshold_tokens = int(self.context_length * self.threshold_percent)
  ```
- **Test with both response formats:**
  ```python
  # Legacy host
  engine.update_from_response({"prompt_tokens": 5000, "completion_tokens": 500, "total_tokens": 5500})
  assert engine.last_prompt_tokens == 5000

  # New host with canonical fields + caching
  engine.update_from_response({
      "input_tokens": 4000, "output_tokens": 500,
      "cache_read_tokens": 1500, "cache_write_tokens": 200,
      "prompt_tokens": 5500,  # includes cache reads
  })
  assert engine.last_prompt_tokens == 4000  # input_tokens preferred
  assert engine.cache_read_tokens == 1500    # tracked separately
  ```
- **Never count `cache_read_tokens` or `reasoning_tokens` toward `last_prompt_tokens`.** They don't consume context window budget.

**Warning signs:**
- `should_compress()` fires when `context_window_used` is actually at 40% (over-counting cache hits).
- `should_compress()` never fires when at 90% because `input_tokens` field is ignored.
- Token display in CLI shows impossible values (e.g., `25000/128000` when actual usage is lower).

**Phase to address:** Phase 1 (BaseContextEngine.update_from_response() implementation). Test with both formats.

---

### Pitfall 11: `should_compress()` false negatives (never compresses) and false positives (compresses too aggressively) [NEW]

**What goes wrong:**
`should_compress()` is the gatekeeper. It decides whether to call `compress()` or let the message list go to the API as-is. Two failure modes:

- **False negative (never compresses):** `should_compress()` returns False even as context window approaches 100%. The next API call gets `400 context_length_exceeded` or the host silently truncates the most recent messages. The user sees "context window full" errors or the agent forgets recent conversation.
- **False positive (compresses too aggressively):** `should_compress()` returns True when usage is at 40% of context window. `compress()` runs, summarises important recent messages, and the agent loses access to detail it needed. The user sees the agent "forgetting" things it just discussed.

**Why it happens:**
- `threshold_tokens` calculated incorrectly (e.g., `context_length * 0.75` but context_length is the model's max, not the host's limit which may be lower).
- `last_prompt_tokens` is stale (stuck at 0 or an old value because `update_from_response()` isn't called reliably).
- `should_compress()` doesn't account for the number of messages protected by `protect_first_n` and `protect_last_n`. If 9 messages are protected (3 head + 6 tail) and the message list is only 12 messages, compression only has 3 messages to work with — not worth the overhead.
- Engine doesn't implement `should_compress_preflight()` (rough estimate before the API call), so the only check happens after the response — when it's already too late for the turn that just completed.
- Override of `should_compress()` with custom logic that uses a different token counting method than `update_from_response()` — two sources of truth diverge.

**How to avoid:**
- **Default `should_compress()` in BaseContextEngine:**
  ```python
  def should_compress(self, prompt_tokens: int = None) -> bool:
      tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
      return tokens >= self.threshold_tokens
  ```
  Simple, predictable, uses the same token count that `update_from_response()` maintains.

- **Add a minimum compressible message count guard:**
  ```python
  def should_compress(self, prompt_tokens: int = None, message_count: int = None) -> bool:
      # Don't compress if there's nothing to compress
      if message_count is not None and message_count <= self.protect_first_n + self.protect_last_n + 2:
          return False
      tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
      return tokens >= self.threshold_tokens
  ```
  This prevents compression when the message list is too small for meaningful compaction.

- **Implement `should_compress_preflight()` for engines that can estimate:**
  ```python
  def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
      # Quick char-count estimate: ~4 chars per token
      total_chars = sum(len(str(m.get("content", ""))) for m in messages)
      estimated_tokens = total_chars // 4
      return estimated_tokens >= self.threshold_tokens
  ```
  This catches the need to compress BEFORE the API call, not after.

- **Add `should_defer_preflight_to_real_usage()` override** that returns True after a successful compression (avoiding re-compression based on noisy estimates).

- **Test both extremes:**
  ```python
  # False negative test
  engine.last_prompt_tokens = engine.threshold_tokens + 1
  assert engine.should_compress() is True

  # False positive test
  engine.last_prompt_tokens = engine.threshold_tokens - 1
  assert engine.should_compress() is False

  # Edge: exactly at threshold
  engine.last_prompt_tokens = engine.threshold_tokens
  assert engine.should_compress() is True  # or False? Decide and document
  ```

**Warning signs:**
- User sees `context_length_exceeded` errors despite having a context engine active.
- Agent "forgets" conversation details from 2 turns ago (compression fired too early, summarized recent messages).
- Compression fires on every turn after the first 3 turns (threshold too low).
- `should_compress_preflight()` returns False but the API call fails with context length error (preflight estimate is inaccurate).

**Phase to address:** Phase 2 (should_compress implementation). Add tests for both edge cases.

---

### Pitfall 12: Tool result JSON format non-compliance [v1.0 REPEAT]

**What goes wrong:**
`handle_tool_call()` must return a JSON string. Hermes Agent parses this and passes the `content` field to the model as the tool result. If the returned JSON is malformed (not valid JSON, wrong structure, extra fields), the model receives an error or garbled output. The ContextEngine ABC's default implementation returns:
```python
json.dumps({"error": f"Unknown context engine tool: {name}"})
```
This establishes the expected format: `{"error": "..."}` for errors, `{"result": "..."}` or `{"results": [...]}` for success. If the context engine returns a flat string (not JSON-wrapped), or nests results incorrectly, the model can't parse the output.

**Why it happens:**
- Returning raw text instead of `json.dumps({"result": text})`.
- Returning a Python dict instead of a JSON string (the ABC signature says `-> str`, but it's easy to forget `json.dumps()`).
- Tool error handling catches `except Exception` but returns `json.dumps({"error": str(e)})` — the error message is a Python exception string, not a user-friendly explanation.
- Circuit breaker open returns error JSON, but the model sees "HydraDB read circuit breaker is open" — which is meaningless to the model and confuses it. Better: "Context search is temporarily unavailable. Please try again in a moment."

**How to avoid:**
- **Consistent result format:**
  ```python
  # Success
  json.dumps({"result": "Formatted search results..."})

  # Multi-item success
  json.dumps({"results": [{"ctx_id": "...", "summary": "..."}, ...]})

  # Error
  json.dumps({"error": "Human-readable error message"})
  ```
- **Add a helper that validates and wraps:**
  ```python
  def _json_result(self, result: str = "", error: str = "", results: list = None) -> str:
      if error:
          return json.dumps({"error": error})
      if results is not None:
          return json.dumps({"results": results})
      return json.dumps({"result": result})
  ```
- **Model-friendly circuit breaker messages:**
  ```python
  if self._breaker.is_read_open():
      return self._json_result(
          error="Context search is temporarily unavailable. Please try again shortly."
      )
  ```
  NOT:
  ```python
  return json.dumps({"error": "HydraDB read circuit breaker is open"})
  ```
- **Test tool return format:**
  ```python
  result = engine.handle_tool_call("context_search", {"query": "test"})
  parsed = json.loads(result)
  assert "error" in parsed or "result" in parsed or "results" in parsed
  assert isinstance(parsed, dict)
  ```

**Warning signs:**
- Model says "I received an error but I don't understand it" after calling `context_search`.
- `json.JSONDecodeError` in Hermes runtime logs when parsing tool results.
- Model sees raw Python dict `{'result': '...'}` instead of a clean text response.

**Phase to address:** Phase 2 (tool implementation). Add format compliance tests.

---

### Pitfall 13: Fake backend fidelity in tests — must match real backend behavior [v1.0 REPEAT]

**What goes wrong:**
Tests use a `FakeHydraDB` or `FakeMuninnDB` client that returns idealized, perfectly-formed responses. The fake client doesn't simulate:
- **Async indexing delay:** Real HydraDB ingests return 201 immediately, but the data isn't queryable for 1-5s. The fake client makes data instantly queryable. Tests pass — but real usage shows `context_search` returning empty for newly compressed entities.
- **Parameter type strictness:** Real HydraDB requires `metadata` as a JSON string, not a dict. Real MuninnDB expects `tags` as a list of strings. A fake client that accepts both silently masks API contract violations.
- **Response shape edge cases:** Real HydraDB returns `result.data.chunks` (object attribute). A fake that returns a dict instead of an object will make `_format_chunks()` crash — but the test never exercises real attribute access.
- **Network error simulation:** Real backends throw `ConnectionError`, `Timeout`, or SDK-specific exceptions. A fake that never throws means error handling code paths are never tested.

**Why it happens:**
v1.0 Pitfall 12 documented exactly this: the fake client was built to return clean, success-case responses — no failure modes, no edge cases, no API contract validations. All 65 tests passed but 2 live integration requirements still couldn't be verified because the fake hid real-world behavior.

**How to avoid:**
- **Fake client MUST validate parameter types:**
  ```python
  class FakeHydraDB:
      def ingest(self, text, metadata=None, **kwargs):
          if metadata is not None and not isinstance(metadata, str):
              raise TypeError("metadata must be a JSON string, not a dict")
          # ... store ...
  ```
- **Fake client MUST simulate indexing delay (configurable):**
  ```python
  class FakeHydraDB:
      def __init__(self, indexing_delay_seconds=0):
          self._indexing_delay = indexing_delay_seconds
          self._pending = {}  # ingested but not yet queryable

      def ingest(self, ...):
          # Store in pending first
          # After indexing_delay_seconds, move to queryable
  ```
  Tests can set `indexing_delay_seconds=0` for fast unit tests and `indexing_delay_seconds=2` for integration-like tests.
- **Fake client MUST support error injection:**
  ```python
  fake = FakeHydraDB()
  fake.inject_fault("query", ConnectionError("timeout"))
  # Next query() call raises ConnectionError
  ```
  This enables circuit breaker testing without real network calls.
- **Separate "unit tests" (fake, no delay) from "integration-like tests" (fake with realistic simulation).** Both run in CI without API keys.
- **Live API tests are a separate, opt-in test file** that requires `HYDRA_DB_API_KEY` env var — skipped in CI.

**Warning signs:**
- All tests pass against fake client; live API integration test fails on first run.
- `metadata` passed as dict in tests, but real API requires JSON string — test doesn't catch it.
- Circuit breaker tests pass but only because the fake client never throws.
- 100% test coverage but 0% error path coverage.

**Phase to address:** Phase 4 (testing). Build fake clients with validation + error injection + indexing delay simulation. Create separate live API integration test file.

---

### Pitfall 14: In-tree deployment paths vs discoverability [v1.0 REPEAT]

**What goes wrong:**
Context engine plugins must be deployed to `~/.hermes/hermes-agent/plugins/context_engine/<name>/` for Hermes to discover them. Common deployment mistakes from v1.0:

- **Wrong directory name:** Plugin deployed to `plugins/context_engine/hydradb_context/` (underscore) but config.yaml expects `"hydradb-context"` (hyphen). Hermes scans directory names, finds the plugin, but `engine.name` doesn't match config.
- **Missing `plugin.yaml`:** Without `plugin_type: context_engine`, Hermes doesn't know this is a context engine plugin and skips it.
- **Missing `__init__.py`:** Hermes imports the module — without `__init__.py`, the directory isn't a Python package.
- **Missing `register(ctx)` function:** The plugin loader calls `register(ctx)` — if the function is named `register_engine(ctx)` or doesn't exist, the plugin is silently skipped.
- **Install vs development path confusion:** Code works in `cos-mcp/plugins/context_engine/hydradb-context/` (development) but not when symlinked/synced to `~/.hermes/hermes-agent/plugins/context_engine/hydradb-context/` (production) because import paths differ.

**Why it happens:**
v1.0 memory providers hit every one of these. The plugin discovery mechanism is directory-scanning with loose conventions — no registry, no manifest validation, no error reporting for misconfigured plugins.

**How to avoid:**
- **Follow the exact directory layout from architecture research:**
  ```
  plugins/context_engine/hydradb-context/
  ├── __init__.py        # Contains class + register(ctx)
  └── plugin.yaml        # name: hydradb-context, plugin_type: context_engine
  ```
- **`register(ctx)` must be the exact function name.** No variations.
- **`plugin.yaml` must include `plugin_type: context_engine`.** This is the only way Hermes distinguishes context engine plugins from memory provider plugins in the same directory tree (though they live in separate `plugins/context_engine/` vs `plugins/memory/` directories).
- **Test deployment with `hermes doctor`** — it should show the context engine as "available" and the active engine as the correct one.
- **Add a smoke test:** After in-tree install, call `hermes context setup hydradb-context` and verify it exits 0.

**Warning signs:**
- Plugin directory exists but `hermes doctor` doesn't list the engine.
- `hermes doctor` lists the engine but `context.engine` config value doesn't match.
- Engine works in development (direct Python path) but not after `pip install` or symlink to `~/.hermes/`.
- `ModuleNotFoundError` for `cos_mcp` when Hermes tries to import the plugin — the plugin's Python environment doesn't have cos_mcp installed.

**Phase to address:** Phase 4 (Hermes integration). Verify in-tree deployment with `hermes doctor`.

---

### Pitfall 15: Agent context gating — skipping non-primary (same as v1.0 memory providers) [v1.0 REPEAT]

**What goes wrong:**
Context compression and `on_session_end()` context ingestion should only run for the primary agent context. If they run for secondary agents (cron jobs, sub-agents, flush agents), context data is duplicated, noise increases, and the tenant/vault accumulates duplicate entities from non-primary sessions. The same `_agent_context != "primary"` guard used in v1.0 memory providers (`sync_turn`, `on_session_end`) must be applied to context engine paths.

**Why it happens:**
The `_agent_context` kwarg is passed to `initialize()` from the Hermes runtime. The context engine must capture it and check it in:
- `compress()` — should return messages unchanged (no compression for secondary agents)
- `on_session_end()` — should skip context summary ingestion for secondary agents

If these guards are omitted, context data pollution is invisible — no errors, just degraded query quality over time.

**How to avoid:**
- **Guard `compress()` at the top:**
  ```python
  def compress(self, messages, current_tokens=None, focus_topic=None):
      if self._agent_context != "primary":
          return messages  # No compression for sub-agents
      # ... normal compression ...
  ```
- **Guard `on_session_end()` at the top:**
  ```python
  def on_session_end(self, session_id, messages):
      if self._agent_context != "primary":
          return  # No context summary for sub-agents
      # ... normal session end ...
  ```
- **Add logging** (DEBUG level) when skipping non-primary contexts so operators can see that the engine is correctly gating.
- **BaseContextEngine should enforce this** — provide the guard in the base class so subclasses can't forget it.

**Warning signs:**
- Context entities tied to cron agent sessions appear in `context_search` results.
- Duplicate compression summaries for the same conversation because both primary and secondary agents compressed.
- Context data volume grows faster than expected — each sub-agent session adds its own entities.

**Phase to address:** Phase 1 (BaseContextEngine foundation). Implement guard in base class.

---

### Pitfall 16: Summary block with ctx-id that can't be resolved later [NEW]

**What goes wrong:**
The compressed summary block includes a `[ctx-id: 3_a1b2c3]` reference. The `context_expand` tool is supposed to take this ctx-id and return the full original messages from that compression round. But if the ctx-id is:

- **Not stored with the entities:** The entity storage fires on a daemon thread with `metadata={"ctx_id": "3_a1b2c3"}`, but the thread fails silently (circuit breaker open, network error). The summary block references an ID that doesn't exist in the backend.
- **Stored but not queryable:** The ctx-id is stored as metadata, but the `context_expand` tool queries by `tags=["compression-3"]` while the metadata has `"compression_id": "3"` — field name mismatch. Query returns nothing.
- **Too generic:** The ctx-id is just `compression_count` with no hash — two sessions both have compression #3. `context_expand` returns the wrong session's data.
- **Non-unique across engine restarts:** The engine restarts, `compression_count` resets to 0, ctx-ids start reusing. Old ctx-ids now point to the wrong compression round.

**Why it happens:**
The ctx-id scheme is a design choice with no enforced correctness. `compress()` creates the ID, stores entities with it (fire-and-forget), and embeds it in the summary block — all without confirmation that the store succeeded. The retrieval path (`context_expand`) assumes the ID exists and is unique.

**How to avoid:**
- **Include a session-scoped component in the ctx-id:**
  ```python
  ctx_id = f"{self._session_id[:8]}_{self.compression_count}_{short_hash}"
  ```
  This ensures ctx-ids are unique across sessions even if `compression_count` resets.

- **Store ctx-id in the summary block AND in entity metadata.** Use the same field name in both places:
  ```python
  entity_metadata = {
      "ctx_id": ctx_id,
      "source": "context_compression",
      "compression_count": self.compression_count,
  }
  ```
  The `context_expand` tool queries by `ctx_id` metadata field.

- **Verify entity storage before embedding ctx-id? No.** This would block `compress()` (anti-pattern 2 in architecture research). Instead: accept that the ctx-id may point to nothing (eventual consistency). The `context_expand` tool should handle "no results" gracefully:
  ```python
  return json.dumps({
      "result": "This compressed context block is no longer available. "
                "It may not have finished indexing yet."
  })
  ```

- **Add a `context_search` path for fallback:** If `context_expand` by ctx-id returns nothing, search by `tags=["compression-N"]` to find related entities.

**Warning signs:**
- `context_expand` returns "no results" for a ctx-id that was just generated.
- Manual inspection of entity metadata shows ctx-id as `None` or missing.
- Two different sessions' `context_expand` return the same original messages (ctx-id collision).

**Phase to address:** Phase 2 (compress() + context_expand tool). Add ctx-id uniqueness test.

---

### Pitfall 17: Tool name collisions between context engine and memory provider [v1.0 REPEAT, AMPLIFIED]

**What goes wrong:**
v1.0 memory providers use prefixed tool names: `hydradb_search`, `muninn_search`. Context engines introduce `context_search` and `context_expand` — these are generic names. If:

- Two context engines are installed (hydradb-context, muninn-context) and BOTH register `context_search` and `context_expand` tools — even though only one is active, the plugin loader might register tools from all installed plugins. The LLM sees duplicate function definitions.
- A future memory provider adds a `context_search` tool for cross-type queries — collision with the context engine's tool.
- The built-in ContextCompressor adds a `context_search` tool (future Hermes enhancement) — collision with the plugin's tool.

**Why it happens:**
The tool namespace is flat. Any plugin can register any tool name. There's no registry that rejects duplicate names — the LLM sees whatever tools are registered, and behavior on duplicates is runtime-dependent (some reject, some silently pick one).

**How to avoid:**
- **Prefix context engine tools with the engine name:**
  - `hydradb_context_search`, `hydradb_context_expand` (HydraDB context engine)
  - `muninn_context_search`, `muninn_context_expand` (MuninnDB context engine)
  - OR keep `context_search` / `context_expand` as the generic names, but only the active engine registers them.

- **Check with the architecture research:** The current design (ARCHITECTURE.md) uses `context_search` and `context_expand` as generic tool names. This is the cleaner UX (model doesn't need to know which backend is active), but it requires that ONLY the active engine's `get_tool_schemas()` is called. Verify this with Hermes runtime.

- **If Hermes runtime calls `get_tool_schemas()` on all registered engines (not just active):**
  ```python
  def get_tool_schemas(self):
      if not self._is_active:
          return []  # Only the active engine registers tools
      return [CONTEXT_SEARCH_SCHEMA, CONTEXT_EXPAND_SCHEMA]
  ```

- **Add a test:** Install both context engines, verify only the active engine's tools are registered (no duplicate tool names).

**Warning signs:**
- LLM receives duplicate `context_search` function definitions and errors.
- `context_expand` returns HydraDB results when MuninnDB is the active engine (or vice versa).
- `hydradb_search` (memory) and `context_search` (context) return overlapping results because type filter is missing.

**Phase to address:** Phase 2 (tool schemas) + Phase 4 (Hermes integration). Verify tool registration with both engines installed.

---

### Pitfall 18: Backend provisioning double-runs — context engine re-provisions the same tenant/vault [NEW]

**What goes wrong:**
Both the memory provider and the context engine call `backend.provision()` during `initialize()`. If the memory provider has already provisioned the tenant/vault (created tenant, verified health, set readiness flag), the context engine's provisioning call should be a no-op. But if `provision()` is not idempotent:

- HydraDB: `_ensure_tenant()` tries to create the `"hermes"` tenant again → 409 Conflict. If not caught, crashes `initialize()`.
- MuninnDB: Provisioning might create a vault or set initial schema. Second call may error or create duplicate resources.
- The provisioning check might make network calls that add latency to session startup (both engines calling health check sequentially).

**Why it happens:**
`provision()` implementations in the backends are not guaranteed idempotent. The memory provider implementation handles 409 Conflict for tenant creation, but the context engine's `initialize()` calls `backend.provision()` independently. If the backend's implementation doesn't have its own "already provisioned" guard, the second call triggers the conflict.

**How to avoid:**
- **Make `provision()` idempotent at the backend level:**
  ```python
  class HydraDBBackend:
      def provision(self):
          if self._provisioned:
              return True
          # ... tenant creation with 409 handling ...
          self._provisioned = True
          return True
  ```
- **OR have BaseContextEngine skip provisioning if the backend is already provisioned:**
  ```python
  def initialize(self, session_id, **kwargs):
      # ...
      if not self._backend.is_provisioned():
          self._backend.provision()
      # ...
  ```
- **Don't make health checks in both engines' initialize()** — the health check adds latency. Delegate to the first engine that initializes, or make the backend track its own health state.

**Warning signs:**
- Second `initialize()` call (for context engine, after memory provider) crashes with 409 Conflict.
- Session startup takes 2x longer when both memory provider and context engine are active.
- HydraDB dashboard shows "tenant hermes" created twice (or creation attempted twice).

**Phase to address:** Phase 1 (extend backends with idempotent provision). Test: call `provision()` twice, verify second call is no-op.

---

## Technical Debt Patterns (Context Engine Edition)

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Single-file engine per plugin (`__init__.py` monolith) | Fast scaffolding, matches v1.0 memory provider pattern | Hard to test entity extraction, compress pipeline, and tool dispatch in isolation | Prototype/MVP only. Must split before production as v1.0 memory provider did not. |
| Single circuit breaker instance per engine | Simplest state model — matches v1.0 pattern | Thresholds tuned for per-turn memory I/O miss context engine's per-compression frequency | Acceptable if thresholds are configurable per engine. |
| Bare `except Exception` in entity storage threads | Never crashes compress(), easy to write | Entity storage failures invisible — ctx-ids become dangling references with no indication | Never acceptable. Add specific exception handling with WARNING log level. |
| Fire-and-forget daemon threads for entity storage | compress() returns fast, non-blocking | Untracked threads can't be joined on shutdown — context entities lost | Acceptable ONLY if threads are tracked and joined in shutdown(). |
| Heuristic entity extraction (no LLM) | Fast (<100ms), free, deterministic | Extraction quality varies by conversation domain — over/under extraction possible | Best approach for v1.0. LLM extraction adds latency and cost to the hot-path. |
| Single ctx-id namespace (compression_count only) | Simple, deterministic ID generation | ctx-id collisions across sessions; non-unique after engine restart | Not acceptable. Must include session-scoped component in ctx-id. |
| `type="context"` as a string constant | Simple data segregation from memory `type="memory"` | No schema enforcement — one bad query without type filter returns mixed data | Acceptable with discipline. Add test asserting all query calls include type filter. |
| Hybrid search for context retrieval (same as memory) | Reuses backend query infrastructure | Context queries may return memory entities if type filter is missing | Acceptable with type filter enforcement. |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Hermes Runtime: `context.engine` config | Config value doesn't match `engine.name` | Use module constant for name; test exact match |
| Hermes Runtime: `register(ctx)` | Using `register_context_provider` instead of `register_context_engine` | `ctx.register_context_engine(instance)` |
| Hermes Runtime: `should_compress()` call pattern | Runtime passes `prompt_tokens` kwarg, engine ignores it | Use `prompt_tokens` if provided, fall back to `last_prompt_tokens` |
| Hermes Runtime: `compress()` message list | Runtime expects a new list — mutating input list corrupts caller state | Always construct new list; never mutate input |
| Hermes Runtime: Tool dispatch | `handle_tool_call()` called for ANY tool name, not just context engine tools | Filter: only handle tools returned by `get_tool_schemas()`; return error JSON for unknown tools |
| HydraDB Backend: `type` field | Forgetting `type="context"` on query/ingest | Always explicitly pass `type="context"` — never rely on defaults |
| HydraDB Backend: `metadata` | Passing dict instead of JSON string | `json.dumps(metadata_dict)` before sending |
| HydraDB Backend: `upsert` | Passing bool `True` instead of string `"true"` | Always `upsert="true"` (lowercase string) |
| MuninnDB Backend: Tags | Using `["hermes-memory"]` tag on context entities | Use `["hermes-context"]` tag namespace for context entities |
| Config: Shared API key | Context engine creates its own config file but borrows API key from env | Use `HYDRA_DB_API_KEY` / `MUNINN_API_KEY` from env; write only non-secret fields to `<engine>.json` |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| LLM call inside `compress()` | 1-3s pause during compaction; possible recursive compression | Use pure Python heuristics for entity extraction; never call `client.chat.completions.create()` in the compress() hot-path | Every compression (immediate) |
| Blocking entity storage in `compress()` | 500ms-2s pause per compression | Fire-and-forget on daemon thread; return summary block immediately | Every compression (immediate) |
| Over-extraction bloating backend | 200+ entities per compression; query latency increases | Per-message entity cap (max 3); global dedup; configurable extraction aggressiveness | After 10+ compressions |
| Verbose summary blocks exceeding token budget | Summary block is 3,000+ tokens — larger than the messages it replaced | Cap summary at 800 tokens; truncate entity list to top-N | First compression with dense conversation |
| Unbounded daemon threads | Thread count grows with compression frequency | Use `ThreadPoolExecutor(max_workers=4)`; bounded queue | ~50+ rapid compressions |
| `shutdown()` timeout too short (5s) | Entity storage threads abandoned mid-Ingest | 30s join timeout; graceful drain of tracked threads | Under degraded backend conditions |
| Both memory provider and context engine provisioning | 2x network calls during `initialize()` | Idempotent `provision()`; skip if already provisioned | Every session start |
| `context_expand` querying the entire backend for full messages | 2-5s latency for large message bodies | Store original messages with ctx-id as a lookup key; query by metadata filter, not full-text search | First `context_expand` call |

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| compress() returns non-shorter message list | LOW | Add length guard at top of compress(): `if len(returned) >= len(input): return input`. ~5 lines. |
| ABC class attributes not maintained | LOW | BaseContextEngine.update_from_response() writes all 6 attributes. ~15 lines. |
| compress() mutates input message list | LOW | Always construct new list; no in-place mutations. Pattern enforcement in code review. |
| Entity extraction quality (over/under) | MEDIUM | Configurable extraction aggressiveness; per-msg cap; global dedup. ~50 lines. |
| Shared backend thread safety | MEDIUM | Independent circuit breakers (correctly), backend-level health check, thread pool. ~40 lines + tests. |
| Circuit breaker thresholds wrong for context engine | LOW | Make thresholds configurable per engine; constructor params. ~15 lines. |
| Fire-and-forget thread tracking (shutdown) | LOW | Store thread refs; join in shutdown(). ~20 lines (same fix as v1.0 Pitfall 6). |
| Plugin registration conflicts (single-select) | LOW | Defensive `is_active` check; test with both plugins installed. ~10 lines. |
| Config drift (name mismatch) | LOW | Module constant for name; config file uses `self.name`. ~5 lines. |
| Token tracking inaccuracy (update_from_response) | LOW | Handle both legacy and canonical fields; never count cache/reasoning tokens. ~20 lines. |
| should_compress() false negatives/positives | LOW | Default implementation + minimum message count guard. ~10 lines. |
| Tool result JSON non-compliance | LOW | `_json_result()` helper; format tests. ~20 lines. |
| Fake backend fidelity | HIGH | Build realistic fake with validation, error injection, indexing delay. ~150 lines per fake. |
| In-tree deployment path mistakes | MEDIUM | `hermes doctor` verification; deployment smoke test. ~30 lines test only. |
| Agent context gating (non-primary) | LOW | Guard in base class; 2 methods × 3 lines each. ~6 lines. |
| ctx-id resolution failures | MEDIUM | Session-scoped ctx-id; graceful "not available" response on expand tool. ~30 lines. |
| Tool name collisions | MEDIUM | Prefix tools or gate registration to active engine only. Depends on Hermes runtime behavior. |
| Backend provisioning double-run | LOW | Idempotent `provision()`; `_provisioned` flag. ~10 lines per backend. |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|-----------------|--------------|
| compress() returns non-shorter list | Phase 2 | Unit test: assert len(returned) < len(input) when compression window exists |
| ABC class attributes not maintained | Phase 1 | Unit test: update_from_response → assert all 6 attributes updated |
| compress() mutates input | Phase 2 | Unit test: original messages list unchanged after compress() |
| Entity extraction quality | Phase 2 | Integration test with realistic conversation; measure extraction precision/recall |
| Shared backend thread safety | Phase 1 + 3 | Concurrent stress test: memory provider + context engine both active |
| Circuit breaker thresholds | Phase 1 | Config test: custom thresholds respected; breaker opens at configured count |
| Fire-and-forget thread tracking | Phase 1 | Shutdown test: thread joined after compress() → shutdown() |
| Plugin registration conflicts | Phase 4 | Integration test: install both plugins, verify single active engine |
| Config drift (name mismatch) | Phase 1 | Integration test: config.yaml engine name matches engine.name |
| Token tracking accuracy | Phase 1 | Unit test: both legacy and canonical usage dicts handled correctly |
| should_compress() false negatives/positives | Phase 2 | Unit test: at threshold → compresses; below threshold → doesn't; tiny message list → doesn't |
| Tool result JSON format | Phase 2 | Unit test: all tool results valid JSON with expected keys |
| Fake backend fidelity | Phase 4 | Fake client test: validates parameter types, simulates errors, reproduces indexing delay |
| In-tree deployment | Phase 4 | `hermes doctor` smoke test; config.yaml switch test |
| Agent context gating | Phase 1 | Unit test: non-primary context → compress() and on_session_end() skip |
| ctx-id resolution | Phase 2 | Integration test: compress → context_expand by ctx-id returns data |
| Tool name collisions | Phase 2 + 4 | Integration test: both engines installed, no duplicate tool names |
| Backend provisioning double-run | Phase 1 | Unit test: provision() called twice, second call is no-op |

---

## "Looks Done But Isn't" Checklist

- [ ] **Token tracking:** `update_from_response()` writes ALL 6 ABC attributes — test with both legacy and canonical usage dicts
- [ ] **compress() length guard:** `len(returned) < len(input)` when compression window is non-empty — test with actual message lists
- [ ] **compress() non-mutation guard:** Original `messages` list is unchanged after `compress()` — test with object identity checks
- [ ] **Entity extraction:** Per-message cap enforced; global dedup; configurable aggressiveness — test extraction count on realistic conversation
- [ ] **Thread tracking:** ALL daemon threads (entity storage, session end, tool queries) stored as instance attributes — test shutdown joins them all
- [ ] **Circuit breaker:** Write breaker gates entity storage ONLY (not compress() itself); read breaker gates tools ONLY — test with mocked breaker states
- [ ] **Config separation:** Context engine uses `hydradb-context.json`, not `hydradb.json` — test config file isolation
- [ ] **Plugin name match:** `engine.name` matches directory name and config.yaml value — test with `hermes doctor`
- [ ] **Agent context gating:** `compress()` and `on_session_end()` skip for non-primary — test with `agent_context="secondary"`
- [ ] **Tool JSON format:** All tool results are valid JSON with `{"result": ...}` or `{"error": ...}` — test every tool handler
- [ ] **Tool name uniqueness:** No duplicate tool names between active context engine and memory providers — test with both active
- [ ] **ctx-id uniqueness:** ctx-ids include session-scoped component and don't collide across sessions — test parallel sessions
- [ ] **Fake backend realism:** Fake validates parameter types, supports error injection, simulates indexing delay — test fake validation catches real API contract violations
- [ ] **Idempotent provisioning:** `backend.provision()` called twice → second call no-op — test double initialize

---

## Sources

- `agent/context_engine.py` (Hermes source, 226 lines) — `ContextEngine` ABC with full lifecycle documentation. **Authoritative on the plugin contract.**
- `cos_mcp/base_provider.py` (352 lines) — `BaseMemoryProvider` pattern reference for `BaseContextEngine`.
- `cos_mcp/circuit_breaker.py` (87 lines) — `CircuitBreaker` with dual read/write gauges. Reused by context engines.
- `cos_mcp/backends/base.py` (75 lines) — `MemoryBackend` ABC. Extended with type parameter for context vs memory data.
- `hydradb-memory/__init__.py` (284 lines) — v1.0 reference: all 13 pitfalls documented, 9 were PRESENT. Direct source for v1.0 REPEAT pitfalls.
- `.planning/research/ARCHITECTURE.md` (936 lines) — Architecture blueprint for v1.1 context engines. Data flows, component responsibilities, anti-patterns.
- `.planning/codebase/CONCERNS.md` (270 lines) — v1.0 tech debt patterns (monolithic files, coarse breaker, bare except, untracked threads) that context engines must not repeat.
- `.planning/codebase/ARCHITECTURE.md` (155 lines) — Existing dual-provider architecture. Threading, error handling, config patterns.
- `.planning/PROJECT.md` (122 lines) — v1.1 milestone scope, constraints (Python 3.12, sync-only, plugin contract, secrets in env).
- `.planning/STATE.md` (92 lines) — v1.0 learnings: 48/50 requirements verified, 65 tests, zero failures. Carry-forward concerns.

---

*Pitfalls research for: Context Engine Plugins for Hermes Agent (cos-mcp v1.1)*
*Researched: 2026-06-20*
*Confidence: HIGH — all pitfalls grounded in existing codebase patterns, verified ABC contract, v1.0 reference implementations, and architecture design research.*
*Next: Use this document as a design checklist during Phase 1-4 implementation. Each pitfall must be addressed or explicitly deferred with rationale.*
