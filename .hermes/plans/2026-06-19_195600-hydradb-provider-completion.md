# HydraDB Memory Provider — Completion & Deployment

> **For Hermes:** Execute task-by-task. TDD where applicable.

**Goal:** Test, verify, deploy, and activate the HydraDB memory provider plugin for Hermes Agent (cos-mcp profile).

**Architecture:** In-tree Hermes plugin at `~/.hermes/hermes-agent/plugins/memory/hydradb/`. Python 3.12, hydradb-sdk v2.0.1, sync-only. One shared tenant "hermes" with per-profile sub_tenant_id isolation. Fire-and-forget writes on daemon threads, circuit breaker (5 failures / 120s).

**Tech Stack:** Python 3.12, hydradb-sdk==2.0.1, pytest, unittest.mock

**Current State:**
- 558-line provider class at `hydradb-memory/__init__.py` — all methods scaffolded
- SDK installed in `.venv`, API key configured and verified
- No tests exist
- Not deployed to Hermes plugins dir
- Venv at `/home/bboyd/src/cos-mcp/.venv`

---

### Task 1: Verify SDK import and API key connectivity

**Objective:** Confirm the HydraDB SDK and API key work against the live API.

**Files:**
- None (read-only verification)

**Step 1: Smoke-test client init and tenant list**

```bash
cd /home/bboyd/src/cos-mcp && .venv/bin/python -c "
from hydra_db import HydraDB
import os
client = HydraDB(token=os.environ['HYDRA_DB_API_KEY'])
result = client.tenants.list()
print('Tenants:', result.data.tenant_ids)
print('OK — SDK + API key working')
"
```

Expected: Lists tenant IDs including "hermes" or similar.

**Step 2: Smoke-test a query against an existing tenant (if any data exists)**

```bash
cd /home/bboyd/src/cos-mcp && .venv/bin/python -c "
from hydra_db import HydraDB
import os
client = HydraDB(token=os.environ['HYDRA_DB_API_KEY'])
result = client.query(
    tenant_id='hermes',
    sub_tenant_id='cos-mcp',
    query='test',
    type='memory',
    query_by='hybrid',
    mode='fast',
    max_results=1,
    graph_context=True,
)
print('Query returned data:', hasattr(result.data, 'chunks'))
print('OK — queries work')
"
```

Expected: Returns data (may be empty chunks).

---

### Task 2: Write test suite — fixtures and fake client

**Objective:** Create test infrastructure with a fake HydraDB client that records calls.

**Files:**
- Create: `hydradb-memory/tests/__init__.py`
- Create: `hydradb-memory/tests/test_hydradb_provider.py`

**Step 1: Create tests directory and init**

```bash
mkdir -p /home/bboyd/src/cos-mcp/hydradb-memory/tests
touch /home/bboyd/src/cos-mcp/hydradb-memory/tests/__init__.py
```

**Step 2: Write test file with FakeHydraClient fixture**

Create `hydradb-memory/tests/test_hydradb_provider.py`:

```python
"""Tests for HydraDBMemoryProvider."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

# ---------------------------------------------------------------------------
# Fake HydraDB client — records calls, returns configurable results
# ---------------------------------------------------------------------------

class FakeChunk:
    def __init__(self, content="", score=0.5, chunk_id="c1", metadata=None,
                 source_type="memory"):
        self.chunk_content = content
        self.relevancy_score = score
        self.id = chunk_id
        self.metadata = metadata or {}
        self.source_type = source_type


class FakeQueryResult:
    def __init__(self, chunks=None):
        self.data = MagicMock()
        self.data.chunks = chunks or []


class FakeContext:
    def __init__(self):
        self.ingest_calls = []

    def ingest(self, **kwargs):
        self.ingest_calls.append(kwargs)


class FakeTenants:
    def __init__(self, tenant_ids=None):
        self._tenant_ids = tenant_ids or ["hermes"]

    def list(self):
        result = MagicMock()
        result.data.tenant_ids = self._tenant_ids
        return result


class FakeHydraClient:
    def __init__(self, token=None, query_result=None, tenant_ids=None):
        self.token = token
        self.query_calls = []
        self._query_result = query_result or FakeQueryResult()
        self.context = FakeContext()
        self.tenants = FakeTenants(tenant_ids)

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return self._query_result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_client():
    return FakeHydraClient(token="sk_test")


@pytest.fixture
def provider(fake_client, monkeypatch):
    """Return an initialized HydraDBMemoryProvider with a fake client."""
    monkeypatch.setenv("HYDRA_DB_API_KEY", "sk_test")
    monkeypatch.setenv("HERMES_HOME", "/tmp/hermes-test")

    from __init__ import HydraDBMemoryProvider

    p = HydraDBMemoryProvider()
    # Bypass real client creation
    p._client = fake_client
    p._api_key = "sk_test"
    p._tenant_id = "hermes"
    p._sub_tenant_id = "test-profile"
    p._query_mode = "thinking"
    p._query_by = "hybrid"
    p._max_results = 10
    p._user_name = "TestUser"
    p._agent_context = "primary"
    p._hermes_home = "/tmp/hermes-test"
    p._failure_count = 0
    p._breaker_open_until = 0.0
    p._prefetch_result = ""
    p._prefetch_lock = threading.Lock()
    p._client_lock = threading.Lock()
    return p
```

**Step 3: Verify tests can be discovered**

```bash
cd /home/bboyd/src/cos-mcp && .venv/bin/pip install pytest 2>&1 | tail -3
cd /home/bboyd/src/cos-mcp/hydradb-memory && ../../.venv/bin/python -m pytest tests/ --collect-only
```

Expected: Collects 0 tests (no test functions yet, but import works).

---

### Task 3: Test config layer

**Objective:** Test `_load_config()`, `get_config_schema()`, `save_config()`, `is_available()`.

**Files:**
- Modify: `hydradb-memory/tests/test_hydradb_provider.py`

**Step 1: Add config tests**

Append to test file:

```python
class TestConfig:
    def test_load_config_defaults(self, monkeypatch):
        monkeypatch.delenv("HYDRA_DB_API_KEY", raising=False)
        monkeypatch.setenv("HERMES_HOME", "/tmp/nonexistent")
        from __init__ import _load_config
        cfg = _load_config()
        assert cfg["tenant_id"] == "hermes"
        assert cfg["api_key"] == ""
        assert cfg["query_mode"] == "thinking"

    def test_load_config_reads_api_key(self, monkeypatch):
        monkeypatch.setenv("HYDRA_DB_API_KEY", "sk_abc123")
        from __init__ import _load_config
        cfg = _load_config()
        assert cfg["api_key"] == "sk_abc123"

    def test_get_config_schema_has_required_fields(self):
        from __init__ import HydraDBMemoryProvider
        schema = HydraDBMemoryProvider.get_config_schema()
        keys = {f["key"] for f in schema}
        assert "api_key" in keys
        assert "tenant_id" in keys
        assert "sub_tenant_id" in keys
        assert "query_mode" in keys

    def test_save_config_writes_json(self, tmp_path):
        from __init__ import HydraDBMemoryProvider
        hermes_home = str(tmp_path)
        HydraDBMemoryProvider.save_config(
            {"api_key": "secret", "tenant_id": "test-tenant"}, hermes_home
        )
        saved = json.load(open(f"{hermes_home}/hydradb.json"))
        assert "api_key" not in saved  # secret stripped
        assert saved["tenant_id"] == "test-tenant"

    def test_is_available_with_key(self, monkeypatch):
        monkeypatch.setenv("HYDRA_DB_API_KEY", "sk_abc")
        from __init__ import HydraDBMemoryProvider
        # Note: import will succeed since hydra_db is installed
        assert HydraDBMemoryProvider.is_available() is True

    def test_is_available_without_key(self, monkeypatch):
        monkeypatch.delenv("HYDRA_DB_API_KEY", raising=False)
        from __init__ import HydraDBMemoryProvider
        assert HydraDBMemoryProvider.is_available() is False
```

**Step 2: Run config tests**

```bash
cd /home/bboyd/src/cos-mcp/hydradb-memory && ../../.venv/bin/python -m pytest tests/test_hydradb_provider.py::TestConfig -v
```

Expected: 6 passed.

---

### Task 4: Test _format_chunks

**Objective:** Verify chunk formatting strips build_string() overhead and filters by score.

**Files:**
- Modify: `hydradb-memory/tests/test_hydradb_provider.py`

**Step 1: Add format_chunks tests**

```python
class TestFormatChunks:
    def test_empty_chunks(self, provider):
        result = provider._format_chunks(FakeQueryResult())
        assert result == ""

    def test_single_chunk(self, provider):
        chunks = [FakeChunk(content="Hello world", score=0.8)]
        result = provider._format_chunks(FakeQueryResult(chunks))
        assert result == "Hello world"

    def test_filters_below_min_score(self, provider):
        chunks = [
            FakeChunk(content="Good", score=0.8),
            FakeChunk(content="Bad", score=0.1),
        ]
        result = provider._format_chunks(FakeQueryResult(chunks))
        assert "Good" in result
        assert "Bad" not in result

    def test_multiple_chunks_joined(self, provider):
        chunks = [
            FakeChunk(content="First fact", score=0.7),
            FakeChunk(content="Second fact", score=0.5),
        ]
        result = provider._format_chunks(FakeQueryResult(chunks))
        assert "First fact" in result
        assert "Second fact" in result
        assert "\n\n" in result

    def test_empty_content_stripped(self, provider):
        chunks = [
            FakeChunk(content="  ", score=0.8),
            FakeChunk(content="Valid", score=0.8),
        ]
        result = provider._format_chunks(FakeQueryResult(chunks))
        assert result == "Valid"
```

**Step 2: Run tests**

```bash
cd /home/bboyd/src/cos-mcp/hydradb-memory && ../../.venv/bin/python -m pytest tests/test_hydradb_provider.py::TestFormatChunks -v
```

Expected: 5 passed.

---

### Task 5: Test read path — prefetch and queue_prefetch

**Objective:** Verify prefetch returns cached result, queue_prefetch fires background query.

**Files:**
- Modify: `hydradb-memory/tests/test_hydradb_provider.py`

**Step 1: Add read path tests**

```python
class TestReadPath:
    def test_prefetch_returns_cached(self, provider):
        provider._prefetch_result = "Cached memory"
        result = provider.prefetch(query="anything", session_id="s1")
        assert "HydraDB Memory" in result
        assert "Cached memory" in result
        # Should consume the cached result
        assert provider._prefetch_result == ""

    def test_prefetch_empty_when_no_cache(self, provider):
        result = provider.prefetch(query="anything", session_id="s1")
        assert result == ""

    def test_queue_prefetch_starts_thread(self, provider, fake_client):
        fake_client._query_result = FakeQueryResult([
            FakeChunk(content="Relevant memory", score=0.9),
        ])
        provider.queue_prefetch(query="test query", session_id="s1")
        if provider._prefetch_thread:
            provider._prefetch_thread.join(timeout=5)
        assert "Relevant memory" in provider._prefetch_result

    def test_queue_prefetch_noop_when_breaker_open(self, provider):
        provider._breaker_open_until = time.time() + 999
        provider.queue_prefetch(query="test", session_id="s1")
        assert provider._prefetch_thread is None
        assert provider._prefetch_result == ""
```

**Step 2: Run tests**

```bash
cd /home/bboyd/src/cos-mcp/hydradb-memory && ../../.venv/bin/python -m pytest tests/test_hydradb_provider.py::TestReadPath -v
```

Expected: 4 passed.

---

### Task 6: Test write path — sync_turn and on_memory_write

**Objective:** Verify fire-and-forget writes call ingest with correct args.

**Files:**
- Modify: `hydradb-memory/tests/test_hydradb_provider.py`

**Step 1: Add write path tests**

```python
class TestWritePath:
    def test_sync_turn_ingests_turn_pair(self, provider, fake_client):
        provider.sync_turn(
            user_content="Hello", assistant_content="Hi there",
            session_id="s1",
        )
        # Wait for daemon thread
        time.sleep(0.5)
        assert len(fake_client.context.ingest_calls) == 1
        call = fake_client.context.ingest_calls[0]
        assert call["type"] == "memory"
        assert call["tenant_id"] == "hermes"
        assert call["upsert"] == "true"
        # memories JSON should contain user+assistant text with infer=true
        mems = json.loads(call["memories"])
        assert len(mems) == 1
        assert "Hello" in mems[0]["text"]
        assert mems[0]["infer"] is True

    def test_sync_turn_skips_non_primary(self, provider, fake_client):
        provider._agent_context = "secondary"
        provider.sync_turn(user_content="x", assistant_content="y")
        time.sleep(0.3)
        assert len(fake_client.context.ingest_calls) == 0

    def test_sync_turn_skips_when_breaker_open(self, provider, fake_client):
        provider._breaker_open_until = time.time() + 999
        provider.sync_turn(user_content="x", assistant_content="y")
        time.sleep(0.3)
        assert len(fake_client.context.ingest_calls) == 0

    def test_on_memory_write_ingests_verbatim(self, provider, fake_client):
        provider.on_memory_write(
            action="add", target="memory",
            content="User prefers dark mode",
        )
        time.sleep(0.5)
        assert len(fake_client.context.ingest_calls) == 1
        call = fake_client.context.ingest_calls[0]
        mems = json.loads(call["memories"])
        assert mems[0]["infer"] is False
        assert "dark mode" in mems[0]["text"]
```

**Step 2: Run tests**

```bash
cd /home/bboyd/src/cos-mcp/hydradb-memory && ../../.venv/bin/python -m pytest tests/test_hydradb_provider.py::TestWritePath -v
```

Expected: 4 passed.

---

### Task 7: Test circuit breaker

**Objective:** Verify breaker opens after 5 failures, closes after cooldown.

**Files:**
- Modify: `hydradb-memory/tests/test_hydradb_provider.py`

**Step 1: Add circuit breaker tests**

```python
class TestCircuitBreaker:
    def test_opens_after_5_failures(self, provider):
        for _ in range(5):
            provider._record_failure()
        assert provider._is_breaker_open() is True
        assert provider._failure_count == 5

    def test_closed_before_5(self, provider):
        for _ in range(4):
            provider._record_failure()
        assert provider._is_breaker_open() is False

    def test_resets_on_success(self, provider):
        for _ in range(3):
            provider._record_failure()
        provider._record_success()
        assert provider._failure_count == 0
        assert provider._is_breaker_open() is False

    def test_cooldown_expires(self, provider):
        provider._breaker_open_until = time.time() - 1  # already past
        assert provider._is_breaker_open() is False
```

**Step 2: Run tests**

```bash
cd /home/bboyd/src/cos-mcp/hydradb-memory && ../../.venv/bin/python -m pytest tests/test_hydradb_provider.py::TestCircuitBreaker -v
```

Expected: 4 passed.

---

### Task 8: Test tools — hydradb_search, hydradb_profile, hydradb_conclude

**Objective:** Verify tool dispatch and argument handling.

**Files:**
- Modify: `hydradb-memory/tests/test_hydradb_provider.py`

**Step 1: Add tool tests**

```python
class TestTools:
    def test_hydradb_search(self, provider, fake_client):
        fake_client._query_result = FakeQueryResult([
            FakeChunk(content="Found memory", score=0.9),
        ])
        result = provider.handle_tool_call("hydradb_search", {"query": "test"})
        data = json.loads(result)
        assert "Found memory" in data["result"]

    def test_hydradb_search_no_results(self, provider, fake_client):
        fake_client._query_result = FakeQueryResult([])
        result = provider.handle_tool_call("hydradb_search", {"query": "nothing"})
        data = json.loads(result)
        assert "No relevant" in data["result"]

    def test_hydradb_profile(self, provider, fake_client):
        fake_client._query_result = FakeQueryResult([
            FakeChunk(content="User likes Python", score=0.7),
        ])
        result = provider.handle_tool_call("hydradb_profile", {})
        data = json.loads(result)
        assert "User likes Python" in data["result"]

    def test_hydradb_conclude_stores_fact(self, provider, fake_client):
        result = provider.handle_tool_call("hydradb_conclude", {"fact": "Important fact"})
        time.sleep(0.3)
        data = json.loads(result)
        assert "Fact stored" in data["result"]
        assert len(fake_client.context.ingest_calls) == 1

    def test_unknown_tool_returns_error(self, provider):
        result = provider.handle_tool_call("unknown_tool", {})
        data = json.loads(result)
        assert "error" in data

    def test_tool_exception_returns_error_json(self, provider, fake_client):
        # Make query raise
        fake_client.query = MagicMock(side_effect=RuntimeError("boom"))
        result = provider.handle_tool_call("hydradb_search", {"query": "test"})
        data = json.loads(result)
        assert "error" in data
```

**Step 2: Run tests**

```bash
cd /home/bboyd/src/cos-mcp/hydradb-memory && ../../.venv/bin/python -m pytest tests/test_hydradb_provider.py::TestTools -v
```

Expected: 6 passed.

---

### Task 9: Test shutdown and session hooks

**Objective:** Verify shutdown joins threads, on_session_end ingests summary.

**Files:**
- Modify: `hydradb-memory/tests/test_hydradb_provider.py`

**Step 1: Add lifecycle tests**

```python
class TestLifecycle:
    def test_shutdown_clears_client(self, provider):
        provider.shutdown()
        assert provider._client is None

    def test_on_session_end_ingests_summary(self, provider, fake_client):
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        provider.on_session_end(messages)
        time.sleep(0.5)
        assert len(fake_client.context.ingest_calls) == 1
        mems = json.loads(fake_client.context.ingest_calls[0]["memories"])
        assert mems[0]["infer"] is True

    def test_system_prompt_block(self):
        from __init__ import HydraDBMemoryProvider
        block = HydraDBMemoryProvider.system_prompt_block()
        assert "HydraDB" in block

    def test_get_tool_schemas(self):
        from __init__ import HydraDBMemoryProvider
        schemas = HydraDBMemoryProvider.get_tool_schemas()
        assert len(schemas) == 3
        names = [s["function"]["name"] for s in schemas]
        assert "hydradb_search" in names
        assert "hydradb_profile" in names
        assert "hydradb_conclude" in names
```

**Step 2: Run all tests**

```bash
cd /home/bboyd/src/cos-mcp/hydradb-memory && ../../.venv/bin/python -m pytest tests/test_hydradb_provider.py -v
```

Expected: All tests pass (~29 tests).

---

### Task 10: Live API verification

**Objective:** Run a real query and ingest against HydraDB to confirm provider works end-to-end.

**Files:**
- None (read-only verification)

**Step 1: Run a live end-to-end test script**

```bash
cd /home/bboyd/src/cos-mcp && .venv/bin/python -c "
import os, sys, json, time, threading
sys.path.insert(0, 'hydradb-memory')
os.environ['HERMES_HOME'] = os.path.expanduser('~/.hermes')

from __init__ import HydraDBMemoryProvider

p = HydraDBMemoryProvider()
p.initialize(
    session_id='live-test',
    hermes_home=os.environ['HERMES_HOME'],
    agent_identity='cos-mcp',
    agent_context='primary',
)

# Test queue_prefetch + prefetch
p.queue_prefetch(query='test memory provider', session_id='t1')
time.sleep(3)

result = p.prefetch(query='test', session_id='t1')
print('PREFETCH:', result[:200] if result else '(empty — expected for fresh tenant)')

# Test sync_turn
p.sync_turn(user_content='Hello from live test', assistant_content='Hi!', session_id='t1')
time.sleep(2)

# Test tool
tool_result = p.handle_tool_call('hydradb_search', {'query': 'Hello'})
print('SEARCH:', tool_result[:200])

print('LIVE VERIFICATION COMPLETE')
"
```

Expected: Runs without exceptions, prints results.

---

### Task 11: Deploy to Hermes plugins directory

**Objective:** Copy/symlink the provider into the Hermes Agent in-tree plugins dir.

**Files:**
- Target: `~/.hermes/hermes-agent/plugins/memory/hydradb/`

**Step 1: Create target directory and symlink**

```bash
mkdir -p ~/.hermes/hermes-agent/plugins/memory/
ln -sfn /home/bboyd/src/cos-mcp/hydradb-memory ~/.hermes/hermes-agent/plugins/memory/hydradb
```

**Step 2: Verify symlink**

```bash
ls -la ~/.hermes/hermes-agent/plugins/memory/hydradb/__init__.py
```

Expected: Shows the file.

---

### Task 12: Activate provider and verify

**Objective:** Set `memory.provider: hydradb` in config and verify with `hermes memory status`.

**Files:**
- Modify: `~/.hermes/config.yaml` (add `memory.provider: hydradb`)

**Step 1: Update config.yaml**

```bash
# Check if memory block exists, add if not
cd ~/.hermes && python3 -c "
import yaml, sys
try:
    with open('config.yaml') as f:
        cfg = yaml.safe_load(f) or {}
except FileNotFoundError:
    cfg = {}
cfg.setdefault('memory', {})['provider'] = 'hydradb'
with open('config.yaml', 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False)
print('Config updated')
"
```

**Step 2: Verify with hermes doctor**

```bash
hermes doctor 2>&1 | grep -i -A5 'memory\|hydradb'
```

Expected: Shows HydraDB provider detected and available.

**Step 3: Run hermes memory status**

```bash
hermes memory status 2>&1
```

Expected: Shows hydradb as the active memory provider, connected.

---

## Risks & Open Questions

1. **FILE_NOT_FOUND race**: Context status returns 404 for ~1-2s after ingest. The `queue_prefetch` already queries on a background thread so timing should be fine, but live verification may see empty results on first query.
2. **Tenant auto-provisioning**: The `initialize()` method doesn't explicitly create a tenant — it relies on `sub_tenant_id` resolution. If the "hermes" tenant doesn't exist, first writes may fail. Consider adding tenant creation in initialize().
3. **Config merge**: `_load_config()` merges `hydradb.json` overrides but the `api_key` comes from env only. This is correct but worth verifying in live test.
4. **Thread safety**: `_prefetch_result` uses a lock but `prefetch()` reads and clears it. If `queue_prefetch` hasn't finished by the time `prefetch()` is called, it returns empty. This is by design (fail-open) but a race window exists.
