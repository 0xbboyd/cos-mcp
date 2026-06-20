# Testing Patterns

**Analysis Date:** 2026-06-20

## Status: No Tests Exist

There are **no test files** in this project. The following were checked and found absent:

| Artifact | Status |
|----------|--------|
| `test_*.py` files | None found |
| `*_test.py` files | None found |
| `pytest.ini` / `pyproject.toml` (pytest config) | None found |
| `setup.cfg` (test configuration) | None found |
| `.github/` (CI config) | None found |
| `Makefile` (test targets) | None found |
| `requirements-dev.txt` / `requirements-test.txt` | None found |

## Planned Test Framework (from SPEC.md)

The project specification (Phase 2: Integration Testing) outlines:

- **Test file:** `test_hydradb_provider.py` — unit tests with a fake/mock HydraDB client.
- **Scope:** config handling, queries, writes, circuit breaker behavior, shutdown logic.
- **Specific test targets:**
  - Per-profile `sub_tenant_id` resolution
  - Metadata JSON string encoding (the `upsert="true"` string-vs-bool gotcha)
  - Live API verification with real HydraDB API key (integration)

## Recommended Setup

When tests are introduced, follow these conventions:

**Framework:**
- **pytest** — standard Python test framework, no additional runner needed.
- No configuration file exists yet; add `pytest.ini` or `[tool.pytest.ini_options]` in a new `pyproject.toml`.

**Test File Location:**
- `tests/` directory at project root, mirroring the source layout:
  ```
  tests/
    test_hydradb_provider.py
  ```

**Mocking:**
- Use `unittest.mock` (stdlib, no additional dependency) to fake the HydraDB SDK client.
- Mock `hydra_db.HydraDB` to return canned responses for query/ingest calls.
- Mock `threading.Thread` or use short timeouts to test circuit breaker without real delays.

**Coverage:**
- No coverage target currently defined.
- When configured, use `pytest-cov` (`pip install pytest-cov`).

**Run Commands (proposed):**
```bash
python3 -m pytest tests/                  # Run all tests
python3 -m pytest tests/ -v               # Verbose output
python3 -m pytest tests/ --cov=hydradb_memory  # With coverage
```

## Testing Patterns to Match

Based on the existing code patterns, tests should follow:

**Async/Fire-and-Forget:**
- Tests must handle daemon threads spawned by `queue_prefetch`, `sync_turn`, `on_memory_write`, and `on_session_end`.
- Use `thread.join(timeout=...)` in test teardown or mock `threading.Thread` to run targets synchronously.

**Circuit Breaker:**
- Tests should verify: 5 consecutive failures open the breaker (120s cooldown), success resets the counter, breaker-open skips operations.

**Config:**
- Tests for `_load_config()` with environment variables (`HYDRA_DB_API_KEY`) and `hydradb.json` merge.
- Tests for `DEFAULT_CONFIG` fallback when no env/file present.

**Error Handling:**
- All provider methods must be tested for resilience — exceptions from SDK calls must not propagate.

---

*Testing analysis: 2026-06-20*
*Update when tests are introduced*
