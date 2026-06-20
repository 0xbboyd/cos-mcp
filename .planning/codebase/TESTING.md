# Testing Patterns

**Analysis Date:** 2026-06-20
**Updated:** 2026-06-20 — documentation pass (test suites now exist)

## Status: Tests Exist

Test suites are present for both context engines and the HydraDB memory provider:

| Test Module | Scope | Tests |
|-------------|-------|-------|
| `tests/plugins/context_engine/test_shared_infra.py` | BaseContextEngine, FakeMemoryBackend, test infrastructure | ~20 |
| `tests/plugins/context_engine/test_context_config.py` | Config loading, defaults, file overrides, env vars | ~20 |
| `tests/plugins/context_engine/test_context_circuit_breaker.py` | Circuit breaker read/write gauges, cooldown, reset | ~20 |
| `tests/plugins/context_engine/test_context_lifecycle.py` | initialize, shutdown, session hooks, thread tracking | ~15 |
| `tests/plugins/context_engine/test_context_compress.py` | Entity extraction (topics, decisions, facts, relationships), compress pipeline | ~20 |
| `tests/plugins/context_engine/test_context_tools.py` | Tool schemas, tool dispatch, search/expand handlers | ~20 |
| `tests/plugins/memory/test_hydradb_provider.py` | Provider lifecycle, config, read/write paths, tools | ~10 |

**Total: 115 tests, zero failures, using fake backends.**

## Test Framework

**Framework:** pytest

**Dependencies (from pyproject.toml):**
- No test dependencies declared; tests use stdlib `unittest.mock` + pytest builtins.

**Run Commands:**
```bash
python3 -m pytest tests/ -v                  # All tests
python3 -m pytest tests/plugins/context_engine/ -v  # Context engine tests
python3 -m pytest tests/plugins/memory/ -v   # Memory provider tests
```

## Fake Backend Pattern

Tests use `FakeMemoryBackend` (defined in `tests/plugins/context_engine/conftest.py`) — an in-memory dict implementing the `MemoryBackend` ABC:

- **query()**: substring match against stored entries, returns list of dicts with `chunk_content`, `relevancy_score`, `ctx_id`, `hop_depth`.
- **ingest()**: stores entry by ID (or auto-generated), returns None.
- **delete()**: removes entry by ID from dict.
- **health_check()**: returns True unless `_fail_health` flag set.
- **provision()**: returns True.
- **shutdown()**: no-op.

Additional test helpers on FakeMemoryBackend:
- `_fail_next_query` / `_fail_next_write`: force next operation to raise for circuit breaker testing.
- `_fail_health`: force health_check to return False.

## Test Fixtures (conftest.py)

```python
@pytest.fixture
def fake_backend():
    """Return a FakeMemoryBackend for testing."""
    return FakeMemoryBackend()

@pytest.fixture
def base_engine(fake_backend, tmp_path):
    """Return a BaseContextEngine subclass with fake backend injected."""
    # Creates a concrete subclass in test scope
    ...
```

## Testing Patterns

**Circuit Breaker:**
- Mock `time.time()` to control cooldown timing.
- Record N failures → assert breaker open.
- Record success → assert breaker closed, counter reset.
- Verify operations skip when breaker open.
- Test independent read/write gauges don't cross-contaminate.

**Config:**
- `tmp_path` fixture for temp config files.
- `monkeypatch.setenv()` for environment variables.
- Test default config fallback when no file/env present.
- Test JSON merge overrides.
- Test `JSONDecodeError` resilience for malformed config files.

**Entity Extraction:**
- Known input text → assert entity type, summary, confidence, count.
- Test conservative/balanced/aggressive modes.
- Test dedup behavior with similar entities.
- Test per-message cap enforcement.
- Empty input → empty entity list.

**Lifecycle:**
- `initialize()` captures identity, creates backend/formatter.
- `shutdown()` joins tracked threads.
- `on_session_reset()` zeros all counters.
- Thread tracking: daemon threads spawned by `_spawn_daemon()`.

**Tools:**
- Verify tool schemas are valid OpenAI function-calling format.
- Unknown tool name → JSON error response.
- Search tool → query backend → format result.
- Expand tool → ctx-id lookup → format with path info.
- Tool calls check circuit breaker, return error JSON if open.

## Error Handling in Tests

- All provider/engine methods tested for resilience — exceptions from backend calls must not propagate.
- Circuit breaker correctly isolates failures.
- Tool handlers return JSON error strings, never raise.

---

*Testing analysis: 2026-06-20*
*Update when test patterns change*
