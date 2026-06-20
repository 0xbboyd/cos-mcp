"""
Comprehensive test suite for HydraDB Memory Provider.

Tests all provider functionality using FakeHydraDBClient with zero live API calls.

Requires: conftest.py in the same directory (sets up fake modules).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest

from hydradb_memory import HydraDBMemoryProvider, DEFAULT_CONFIG, _load_config


# ============================================================================
# Fake data classes (matching conftest.py definitions)
# ============================================================================


class FakeData:
    """Generic attribute bag that mimics HydraDB API response objects."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeEnvelope:
    """Wraps a FakeData object like a HydraDB API response envelope."""

    def __init__(self, data=None):
        self.data = data


class FakeTenants:
    """Fake client.tenants — list / create / status with configurable behaviour."""

    def __init__(self, *, existing_tenants=None, ready_after_attempts=1,
                 raise_on_create=None, raise_on_status=None):
        self._existing = existing_tenants or []
        self._ready_after = ready_after_attempts
        self._raise_on_create = raise_on_create
        self._raise_on_status = raise_on_status
        self.status_calls = 0
        self.create_calls = []
        self.list_calls = 0

    def list(self):
        self.list_calls += 1
        return FakeEnvelope(FakeData(tenant_ids=list(self._existing)))

    def create(self, tenant_id=None):
        self.create_calls.append(tenant_id)
        if self._raise_on_create:
            raise self._raise_on_create

    def status(self, tenant_id=None):
        self.status_calls += 1
        if self._raise_on_status:
            raise self._raise_on_status
        ready = self.status_calls >= self._ready_after
        return FakeEnvelope(FakeData(infra=FakeData(ready_for_ingestion=ready)))


class FakeContext:
    """Fake client.context — tracks ingest / delete calls."""

    def __init__(self):
        self.ingest_calls = []
        self.delete_calls = []

    def ingest(self, **kwargs):
        self.ingest_calls.append(kwargs)
        return FakeEnvelope(FakeData())

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return FakeEnvelope(FakeData())


class FakeHydraDBClient:
    """Complete fake HydraDB client — used in all tests via monkeypatch."""

    def __init__(self, token=None):
        self.token = token
        self.tenants = FakeTenants()
        self.context = FakeContext()
        self.query_results = []  # list of FakeEnvelope to return in order
        self.query_calls = []
        self._query_index = 0

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if self._query_index < len(self.query_results):
            result = self.query_results[self._query_index]
            self._query_index += 1
            return result
        return FakeEnvelope(FakeData(chunks=[]))


# ============================================================================
# 1. TestHydraDBConfig
# ============================================================================


class TestHydraDBConfig:
    """Tests for provider configuration: is_available, config loading, save_config."""

    # -- is_available ---------------------------------------------------------

    def test_is_available_with_key(self, monkeypatch):
        """is_available returns True when API key is set and SDK is importable."""
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        assert HydraDBMemoryProvider.is_available() is True

    def test_is_available_without_key(self, monkeypatch):
        """is_available returns False when HYDRA_DB_API_KEY is not set."""
        monkeypatch.delenv("HYDRA_DB_API_KEY", raising=False)
        assert HydraDBMemoryProvider.is_available() is False

    def test_is_available_no_sdk(self, monkeypatch):
        """is_available returns False when hydra_db SDK cannot be imported."""
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        # Remove the fake hydra_db from sys.modules to simulate missing SDK
        saved = sys.modules.pop("hydra_db", None)
        try:
            keys_to_pop = [k for k in sys.modules if k.startswith("hydra_db")]
            saved_modules = {k: sys.modules.pop(k) for k in keys_to_pop}
            try:
                assert HydraDBMemoryProvider.is_available() is False
            finally:
                for k, v in saved_modules.items():
                    sys.modules[k] = v
        finally:
            if saved is not None:
                sys.modules["hydra_db"] = saved

    # -- Config defaults -----------------------------------------------------

    def test_config_defaults(self):
        """DEFAULT_CONFIG has expected keys and default values."""
        assert DEFAULT_CONFIG["tenant_id"] == "hermes"
        assert DEFAULT_CONFIG["query_mode"] == "thinking"
        assert DEFAULT_CONFIG["query_by"] == "hybrid"
        assert DEFAULT_CONFIG["max_results"] == 10
        assert DEFAULT_CONFIG["api_key"] == ""

    def test_config_overrides_from_file(self, tmp_path, monkeypatch):
        """_load_config() merges overrides from hydradb.json."""
        hydradb_json = tmp_path / "hydradb.json"
        hydradb_json.write_text(json.dumps({
            "tenant_id": "custom-tenant",
            "query_mode": "fast",
            "max_results": 20,
        }))

        monkeypatch.delenv("HYDRA_DB_API_KEY", raising=False)
        cfg = _load_config(hermes_home=str(tmp_path))
        assert cfg["tenant_id"] == "custom-tenant"
        assert cfg["query_mode"] == "fast"
        assert cfg["max_results"] == 20
        assert cfg["api_key"] == ""

    def test_config_env_api_key(self, monkeypatch):
        """_load_config() reads HYDRA_DB_API_KEY from environment."""
        monkeypatch.setenv("HYDRA_DB_API_KEY", "env-key-123")
        cfg = _load_config(hermes_home="")
        assert cfg["api_key"] == "env-key-123"

    def test_config_broken_json(self, tmp_path, monkeypatch):
        """_load_config() handles corrupt hydradb.json gracefully (falls back to defaults)."""
        hydradb_json = tmp_path / "hydradb.json"
        hydradb_json.write_text("{broken json!!")

        monkeypatch.delenv("HYDRA_DB_API_KEY", raising=False)
        cfg = _load_config(hermes_home=str(tmp_path))
        assert cfg["tenant_id"] == "hermes"
        assert cfg["query_mode"] == "thinking"

    # -- save_config ---------------------------------------------------------

    def test_save_config_filters_api_key(self, tmp_path):
        """save_config() writes non-secret values, never api_key."""
        values = {
            "api_key": "secret-should-not-appear",
            "tenant_id": "my-tenant",
            "sub_tenant_id": "my-profile",
            "query_mode": "fast",
        }
        HydraDBMemoryProvider.save_config(values, hermes_home=str(tmp_path))

        written_path = tmp_path / "hydradb.json"
        assert written_path.exists()
        written = json.loads(written_path.read_text())
        assert "api_key" not in written
        assert written["tenant_id"] == "my-tenant"
        assert written["sub_tenant_id"] == "my-profile"
        assert written["query_mode"] == "fast"

    # -- Config via hermes_home kwarg ----------------------------------------

    def test_config_via_hermes_home_kwarg(self, tmp_path, monkeypatch):
        """initialize() loads config from the hermes_home kwarg, not ~/.hermes."""
        hydradb_json = tmp_path / "hydradb.json"
        hydradb_json.write_text(json.dumps({"tenant_id": "kwarg-tenant"}))

        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        provider = HydraDBMemoryProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: FakeHydraDBClient())
        monkeypatch.setattr(provider, "_ensure_tenant", lambda: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        provider.initialize("sess", hermes_home=str(tmp_path),
                            agent_identity="test-profile",
                            agent_context="primary")
        assert provider._tenant_id == "kwarg-tenant"
        assert provider._hermes_home == str(tmp_path)

    def test_initialize_sub_tenant_fallback(self, monkeypatch):
        """sub_tenant_id falls back to agent_identity, then 'default'."""
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        provider = HydraDBMemoryProvider()
        monkeypatch.setattr(provider, "_get_client", lambda: FakeHydraDBClient())
        monkeypatch.setattr(provider, "_ensure_tenant", lambda: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        provider.initialize("sess", hermes_home="/tmp/fake",
                            agent_identity="my-profile",
                            agent_context="primary")
        assert provider._sub_tenant_id == "my-profile"

        provider2 = HydraDBMemoryProvider()
        monkeypatch.setattr(provider2, "_get_client", lambda: FakeHydraDBClient())
        monkeypatch.setattr(provider2, "_ensure_tenant", lambda: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        provider2.initialize("sess", hermes_home="/tmp/fake",
                             agent_identity="", agent_context="primary")
        assert provider2._sub_tenant_id == "default"


# ============================================================================
# 2. TestHydraDBQueries
# ============================================================================


class TestHydraDBQueries:
    """Tests for read path: prefetch, queue_prefetch, _format_chunks."""

    def test_prefetch_returns_cached_result(self, provider):
        """prefetch() returns the cached _prefetch_result with HydraDB heading."""
        provider._prefetch_result = "Memory chunk 1\n\nMemory chunk 2"
        result = provider.prefetch("some query", session_id="s1")
        assert result.startswith("## HydraDB Memory\n")
        assert "Memory chunk 1" in result
        assert provider._prefetch_result == ""

    def test_prefetch_returns_empty(self, provider):
        """prefetch() returns empty string when no cached result."""
        provider._prefetch_result = ""
        result = provider.prefetch("some query")
        assert result == ""

    def test_queue_prefetch_stores_result(self, provider, fake_client):
        """queue_prefetch fires a background query and stores formatted chunks."""
        fake_client.query_results = [
            FakeEnvelope(FakeData(chunks=[
                FakeData(chunk_content="Prefetched memory A", relevancy_score=0.9),
                FakeData(chunk_content="Prefetched memory B", relevancy_score=0.7),
            ]))
        ]

        provider.queue_prefetch("test query", session_id="s1")

        if provider._prefetch_thread:
            provider._prefetch_thread.join(timeout=2)

        result = provider.prefetch("test query")
        assert "Prefetched memory A" in result
        assert "Prefetched memory B" in result

    def test_queue_prefetch_empty_result(self, provider, fake_client):
        """queue_prefetch with empty chunks sets empty prefetch result."""
        fake_client.query_results = [
            FakeEnvelope(FakeData(chunks=[]))
        ]

        provider.queue_prefetch("empty query", session_id="s1")
        if provider._prefetch_thread:
            provider._prefetch_thread.join(timeout=2)

        result = provider.prefetch("empty query")
        assert result == ""

    def test_queue_prefetch_params_passed(self, provider, fake_client):
        """queue_prefetch passes correct query parameters to the client."""
        fake_client.query_results = [
            FakeEnvelope(FakeData(chunks=[
                FakeData(chunk_content="X", relevancy_score=0.8),
            ]))
        ]

        provider._query_by = "hybrid"
        provider._query_mode = "thinking"
        provider._max_results = 10

        provider.queue_prefetch("param-test query")
        if provider._prefetch_thread:
            provider._prefetch_thread.join(timeout=2)

        assert len(fake_client.query_calls) == 1
        call = fake_client.query_calls[0]
        assert call["tenant_id"] == "hermes"
        assert call["sub_tenant_id"] == "test-profile"
        assert call["query"] == "param-test query"
        assert call["type"] == "memory"
        assert call["query_by"] == "hybrid"
        assert call["mode"] == "thinking"
        assert call["max_results"] == 10
        assert call["graph_context"] is True

    def test_queue_prefetch_skips_when_breaker_open(self, provider, fake_client):
        """queue_prefetch returns early when read circuit breaker is open."""
        provider._read_breaker_open_until = time.time() + 9999
        provider.queue_prefetch("blocked query")
        assert len(fake_client.query_calls) == 0

    def test_format_chunks_filters_by_min_score(self, provider):
        """_format_chunks drops chunks with relevancy_score below min_score."""
        result = FakeEnvelope(FakeData(chunks=[
            FakeData(chunk_content="High score", relevancy_score=0.9),
            FakeData(chunk_content="Low score", relevancy_score=0.1),
            FakeData(chunk_content="Medium score", relevancy_score=0.5),
            FakeData(chunk_content="Borderline", relevancy_score=0.3),
        ]))
        formatted = HydraDBMemoryProvider._format_chunks(result, min_score=0.3)
        assert "High score" in formatted
        assert "Medium score" in formatted
        assert "Borderline" in formatted
        assert "Low score" not in formatted

    def test_format_chunks_empty(self, provider):
        """_format_chunks returns empty string for empty chunks list."""
        result = FakeEnvelope(FakeData(chunks=[]))
        formatted = HydraDBMemoryProvider._format_chunks(result)
        assert formatted == ""

    def test_format_chunks_strips_whitespace(self, provider):
        """_format_chunks skips chunks with only whitespace content."""
        result = FakeEnvelope(FakeData(chunks=[
            FakeData(chunk_content="   ", relevancy_score=0.8),
            FakeData(chunk_content="Valid content", relevancy_score=0.8),
        ]))
        formatted = HydraDBMemoryProvider._format_chunks(result)
        assert "Valid content" in formatted
        assert formatted.strip() == "Valid content"


# ============================================================================
# 3. TestHydraDBWrites
# ============================================================================


class TestHydraDBWrites:
    """Tests for write path: sync_turn, on_memory_write."""

    def test_sync_turn_infer_true(self, provider, fake_client):
        """sync_turn ingests with infer=True for auto fact extraction."""
        provider.sync_turn("user msg", "assistant msg", session_id="s1")

        if provider._sync_thread:
            provider._sync_thread.join(timeout=2)

        assert len(fake_client.context.ingest_calls) == 1
        call = fake_client.context.ingest_calls[0]
        assert call["type"] == "memory"
        assert call["tenant_id"] == "hermes"
        assert call["upsert"] == "true"

        memories = json.loads(call["memories"])
        assert len(memories) == 1
        assert memories[0]["infer"] is True
        assert "User: user msg" in memories[0]["text"]
        assert "Assistant: assistant msg" in memories[0]["text"]

    def test_sync_turn_skips_non_primary(self, monkeypatch):
        """sync_turn skips ingestion when agent_context != 'primary'."""
        provider = HydraDBMemoryProvider()
        monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
        fake_client = FakeHydraDBClient()
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        monkeypatch.setattr(provider, "_ensure_tenant", lambda: None)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        provider.initialize("sess", hermes_home="/tmp/t", agent_identity="p",
                            agent_context="subagent")
        provider._get_client = lambda: fake_client  # type: ignore[method-assign]

        provider.sync_turn("user", "assistant")
        assert provider._sync_thread is None
        assert len(fake_client.context.ingest_calls) == 0

    def test_sync_turn_skips_when_breaker_open(self, provider, fake_client):
        """sync_turn returns early when write circuit breaker is open."""
        provider._write_breaker_open_until = time.time() + 9999
        provider.sync_turn("user", "assistant")
        assert len(fake_client.context.ingest_calls) == 0

    def test_on_memory_write_add(self, provider, fake_client):
        """on_memory_write 'add' ingests with infer=False and stable content-hash ID."""
        provider.on_memory_write("add", "session_notes", "Important context here")

        if provider._mirror_thread:
            provider._mirror_thread.join(timeout=2)

        assert len(fake_client.context.ingest_calls) == 1
        call = fake_client.context.ingest_calls[0]
        assert call["type"] == "memory"
        assert call["upsert"] == "true"

        memories = json.loads(call["memories"])
        assert len(memories) == 1
        mem = memories[0]
        assert mem["infer"] is False

        expected_hash = hashlib.sha256(b"Important context here").hexdigest()[:16]
        expected_id = f"hermes_session_notes_{expected_hash}"
        assert mem["id"] == expected_id

        assert isinstance(mem["metadata"], str)
        metadata = json.loads(mem["metadata"])
        assert metadata["target"] == "session_notes"
        assert metadata["source"] == "builtin_mirror"

    def test_on_memory_write_replace(self, provider, fake_client):
        """on_memory_write 'replace' behaves like 'add' (upsert handles both)."""
        provider.on_memory_write("replace", "preferences", "Likes dark mode")

        if provider._mirror_thread:
            provider._mirror_thread.join(timeout=2)

        assert len(fake_client.context.ingest_calls) == 1
        memories = json.loads(fake_client.context.ingest_calls[0]["memories"])
        assert memories[0]["infer"] is False
        assert "Likes dark mode" in memories[0]["text"]

    def test_on_memory_write_delete(self, provider, fake_client):
        """on_memory_write 'remove' calls client.context.delete with stable ID."""
        content = "Delete this memory"
        provider.on_memory_write("remove", "temp", content)

        if provider._mirror_thread:
            provider._mirror_thread.join(timeout=2)

        assert len(fake_client.context.delete_calls) == 1
        call = fake_client.context.delete_calls[0]
        assert call["type"] == "memory"
        assert call["tenant_id"] == "hermes"

        expected_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        expected_id = f"hermes_temp_{expected_hash}"
        assert call["ids"] == [expected_id]

        assert len(fake_client.context.ingest_calls) == 0

    def test_on_memory_write_skips_when_breaker_open(self, provider, fake_client):
        """on_memory_write returns early when write circuit breaker is open."""
        provider._write_breaker_open_until = time.time() + 9999
        provider.on_memory_write("add", "target", "content")
        assert len(fake_client.context.ingest_calls) == 0


# ============================================================================
# 4. TestHydraDBCircuitBreaker
# ============================================================================


class TestHydraDBCircuitBreaker:
    """Tests for circuit breaker: open/close, read/write independence."""

    def test_opens_after_5_consecutive_read_failures(self, provider):
        """Read circuit breaker opens after 5 consecutive _record_read_failure calls."""
        now = time.time()
        for _ in range(4):
            provider._record_read_failure()
        assert provider._read_breaker_open_until == 0.0
        assert not provider._is_read_breaker_open()

        provider._record_read_failure()
        assert provider._read_breaker_open_until > now
        assert provider._is_read_breaker_open()

    def test_opens_after_5_consecutive_write_failures(self, provider):
        """Write circuit breaker opens after 5 consecutive _record_write_failure calls."""
        now = time.time()
        for _ in range(4):
            provider._record_write_failure()
        assert provider._write_breaker_open_until == 0.0
        assert not provider._is_write_breaker_open()

        provider._record_write_failure()
        assert provider._write_breaker_open_until > now
        assert provider._is_write_breaker_open()

    def test_blocks_reads_when_open(self, provider):
        """When read breaker is open, _is_read_breaker_open returns True."""
        provider._read_breaker_open_until = time.time() + 9999
        assert provider._is_read_breaker_open() is True

    def test_blocks_writes_when_open(self, provider):
        """When write breaker is open, _is_write_breaker_open returns True."""
        provider._write_breaker_open_until = time.time() + 9999
        assert provider._is_write_breaker_open() is True

    def test_breaker_not_open_when_expired(self, provider):
        """Breaker returns False when cooldown period has elapsed."""
        provider._read_breaker_open_until = time.time() - 1
        assert not provider._is_read_breaker_open()

    def test_resets_on_read_success(self, provider):
        """_record_read_success resets failure count and breaker state."""
        provider._read_failures = 4
        provider._read_breaker_open_until = time.time() + 9999

        provider._record_read_success()

        assert provider._read_failures == 0
        assert provider._read_breaker_open_until == 0.0
        assert not provider._is_read_breaker_open()

    def test_resets_on_write_success(self, provider):
        """_record_write_success resets failure count and breaker state."""
        provider._write_failures = 4
        provider._write_breaker_open_until = time.time() + 9999

        provider._record_write_success()

        assert provider._write_failures == 0
        assert provider._write_breaker_open_until == 0.0
        assert not provider._is_write_breaker_open()

    def test_write_failures_dont_block_reads(self, provider):
        """Write circuit breaker failures do not affect read breaker."""
        for _ in range(5):
            provider._record_write_failure()
        assert provider._is_write_breaker_open() is True
        assert provider._is_read_breaker_open() is False

    def test_read_failures_dont_block_writes(self, provider):
        """Read circuit breaker failures do not affect write breaker."""
        for _ in range(5):
            provider._record_read_failure()
        assert provider._is_read_breaker_open() is True
        assert provider._is_write_breaker_open() is False

    def test_breaker_thread_safety_lock(self, provider):
        """Breaker operations are protected by _breaker_lock."""
        assert hasattr(provider, "_breaker_lock")
        assert isinstance(provider._breaker_lock, type(threading.Lock()))


# ============================================================================
# 5. TestHydraDBShutdown
# ============================================================================


class TestHydraDBShutdown:
    """Tests for shutdown: thread joining, client cleanup."""

    def test_joins_prefetch_thread(self, provider):
        """shutdown joins _prefetch_thread if alive."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        provider._prefetch_thread = mock_thread
        provider._sync_thread = None
        provider._mirror_thread = None

        provider.shutdown()

        mock_thread.join.assert_called_once_with(timeout=5.0)

    def test_joins_sync_thread(self, provider):
        """shutdown joins _sync_thread if alive."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        provider._prefetch_thread = None
        provider._sync_thread = mock_thread
        provider._mirror_thread = None

        provider.shutdown()

        mock_thread.join.assert_called_once_with(timeout=5.0)

    def test_joins_mirror_thread(self, provider):
        """shutdown joins _mirror_thread if alive."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        provider._prefetch_thread = None
        provider._sync_thread = None
        provider._mirror_thread = mock_thread

        provider.shutdown()

        mock_thread.join.assert_called_once_with(timeout=5.0)

    def test_skips_dead_threads(self, provider):
        """shutdown skips join on threads that are not alive."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = False
        provider._prefetch_thread = mock_thread
        provider._sync_thread = None
        provider._mirror_thread = None

        provider.shutdown()

        mock_thread.join.assert_not_called()

    def test_handles_none_threads(self, provider):
        """shutdown handles None thread references gracefully."""
        provider._prefetch_thread = None
        provider._sync_thread = None
        provider._mirror_thread = None

        provider.shutdown()

    def test_clears_client_on_shutdown(self, provider, fake_client):
        """shutdown clears the client reference."""
        provider._client = fake_client
        provider._prefetch_thread = None
        provider._sync_thread = None
        provider._mirror_thread = None

        provider.shutdown()

        assert provider._client is None

    def test_handles_timeout_gracefully(self, provider):
        """shutdown propagates join errors (join itself handles timeout)."""
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        mock_thread.join.side_effect = RuntimeError("join failed")

        provider._prefetch_thread = mock_thread
        provider._sync_thread = None
        provider._mirror_thread = None

        with pytest.raises(RuntimeError, match="join failed"):
            provider.shutdown()

    def test_joins_multiple_threads(self, provider):
        """shutdown joins all three threads in sequence."""
        t1 = MagicMock()
        t1.is_alive.return_value = True
        t2 = MagicMock()
        t2.is_alive.return_value = True
        t3 = MagicMock()
        t3.is_alive.return_value = True

        provider._prefetch_thread = t1
        provider._sync_thread = t2
        provider._mirror_thread = t3

        provider.shutdown()

        t1.join.assert_called_once_with(timeout=5.0)
        t2.join.assert_called_once_with(timeout=5.0)
        t3.join.assert_called_once_with(timeout=5.0)


# ============================================================================
# 6. TestHydraDBTenant
# ============================================================================


class TestHydraDBTenant:
    """Tests for _ensure_tenant: create, skip, 409 conflict, polling."""

    def test_creates_tenant_when_missing(self, monkeypatch):
        """_ensure_tenant creates the tenant when it doesn't exist in the list."""
        provider = HydraDBMemoryProvider()
        fake_client = FakeHydraDBClient()
        fake_client.tenants = FakeTenants(existing_tenants=["other-tenant"])

        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        monkeypatch.setattr(time, "sleep", lambda s: None)

        provider._api_key = "test-key"
        provider._tenant_id = "hermes"
        provider._tenant_ready = False
        provider._breaker_lock = threading.Lock()
        provider._read_failures = 0
        provider._write_failures = 0
        provider._read_breaker_open_until = 0.0
        provider._write_breaker_open_until = 0.0

        provider._ensure_tenant()

        assert fake_client.tenants.create_calls == ["hermes"]
        assert provider._tenant_ready is True

    def test_skips_when_tenant_exists(self, monkeypatch):
        """_ensure_tenant skips creation when tenant already exists."""
        provider = HydraDBMemoryProvider()
        fake_client = FakeHydraDBClient()
        fake_client.tenants = FakeTenants(existing_tenants=["hermes"])

        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        provider._api_key = "test-key"
        provider._tenant_id = "hermes"
        provider._tenant_ready = False
        provider._breaker_lock = threading.Lock()
        provider._read_failures = 0
        provider._write_failures = 0
        provider._read_breaker_open_until = 0.0
        provider._write_breaker_open_until = 0.0

        provider._ensure_tenant()

        assert len(fake_client.tenants.create_calls) == 0
        assert provider._tenant_ready is True

    def test_skips_when_already_ready(self, monkeypatch):
        """_ensure_tenant returns immediately when _tenant_ready is already True."""
        provider = HydraDBMemoryProvider()
        fake_client = FakeHydraDBClient()
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        provider._tenant_ready = True

        provider._ensure_tenant()

        assert fake_client.tenants.list_calls == 0

    def test_handles_409_conflict_status_code(self, monkeypatch):
        """_ensure_tenant handles 409 with e.status_code gracefully."""
        provider = HydraDBMemoryProvider()
        fake_client = FakeHydraDBClient()

        exc = Exception("Conflict")
        exc.status_code = 409  # type: ignore[attr-defined]
        fake_client.tenants = FakeTenants(
            existing_tenants=[],
            raise_on_create=exc,
        )

        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        provider._api_key = "test-key"
        provider._tenant_id = "hermes"
        provider._tenant_ready = False
        provider._breaker_lock = threading.Lock()
        provider._read_failures = 0
        provider._write_failures = 0
        provider._read_breaker_open_until = 0.0
        provider._write_breaker_open_until = 0.0

        provider._ensure_tenant()
        assert provider._tenant_ready is True

    def test_handles_409_conflict_response_attr(self, monkeypatch):
        """_ensure_tenant handles 409 via e.response.status_code."""
        provider = HydraDBMemoryProvider()
        fake_client = FakeHydraDBClient()

        class Response409:
            status_code = 409
        exc = Exception("Conflict")
        exc.response = Response409()  # type: ignore[attr-defined]

        fake_client.tenants = FakeTenants(
            existing_tenants=[],
            raise_on_create=exc,
        )

        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        provider._api_key = "test-key"
        provider._tenant_id = "hermes"
        provider._tenant_ready = False
        provider._breaker_lock = threading.Lock()
        provider._read_failures = 0
        provider._write_failures = 0
        provider._read_breaker_open_until = 0.0
        provider._write_breaker_open_until = 0.0

        provider._ensure_tenant()
        assert provider._tenant_ready is True

    def test_polls_until_ready(self, monkeypatch):
        """_ensure_tenant polls tenants.status() until ready_for_ingestion."""
        provider = HydraDBMemoryProvider()
        fake_client = FakeHydraDBClient()
        fake_client.tenants = FakeTenants(
            existing_tenants=["hermes"],
            ready_after_attempts=3,
        )

        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        provider._api_key = "test-key"
        provider._tenant_id = "hermes"
        provider._tenant_ready = False
        provider._breaker_lock = threading.Lock()
        provider._read_failures = 0
        provider._write_failures = 0
        provider._read_breaker_open_until = 0.0
        provider._write_breaker_open_until = 0.0

        provider._ensure_tenant()

        assert fake_client.tenants.status_calls == 3
        assert provider._tenant_ready is True

    def test_handles_timeout_after_max_attempts(self, monkeypatch):
        """_ensure_tenant sets _tenant_ready=True after polling timeout."""
        provider = HydraDBMemoryProvider()
        fake_client = FakeHydraDBClient()
        fake_client.tenants = FakeTenants(
            existing_tenants=["hermes"],
            ready_after_attempts=999,
        )

        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        monkeypatch.setattr(time, "sleep", lambda s: None)
        provider._api_key = "test-key"
        provider._tenant_id = "hermes"
        provider._tenant_ready = False
        provider._breaker_lock = threading.Lock()
        provider._read_failures = 0
        provider._write_failures = 0
        provider._read_breaker_open_until = 0.0
        provider._write_breaker_open_until = 0.0

        provider._ensure_tenant()

        assert fake_client.tenants.status_calls == 60
        assert provider._tenant_ready is True

    def test_skips_when_no_api_key(self, monkeypatch):
        """_ensure_tenant returns early when _api_key is empty."""
        provider = HydraDBMemoryProvider()
        fake_client = FakeHydraDBClient()
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        provider._api_key = ""
        provider._tenant_id = "hermes"
        provider._tenant_ready = False
        provider._breaker_lock = threading.Lock()
        provider._read_failures = 0
        provider._write_failures = 0
        provider._read_breaker_open_until = 0.0
        provider._write_breaker_open_until = 0.0

        provider._ensure_tenant()

        assert fake_client.tenants.list_calls == 0
        assert not provider._tenant_ready

    def test_skips_when_write_breaker_open(self, monkeypatch):
        """_ensure_tenant returns early when write circuit breaker is open."""
        provider = HydraDBMemoryProvider()
        fake_client = FakeHydraDBClient()
        monkeypatch.setattr(provider, "_get_client", lambda: fake_client)
        provider._api_key = "test-key"
        provider._tenant_id = "hermes"
        provider._tenant_ready = False
        provider._breaker_lock = threading.Lock()
        provider._read_breaker_open_until = 0.0
        provider._write_breaker_open_until = time.time() + 9999
        provider._read_failures = 0
        provider._write_failures = 0

        provider._ensure_tenant()

        assert fake_client.tenants.list_calls == 0
        assert not provider._tenant_ready


# ============================================================================
# 7. TestHydraDBTools
# ============================================================================


class TestHydraDBTools:
    """Tests for tool dispatch: hydradb_search, hydradb_profile, hydradb_conclude."""

    def test_hydradb_search_returns_results(self, provider, fake_client):
        """hydradb_search returns formatted memory chunks as JSON."""
        fake_client.query_results = [
            FakeEnvelope(FakeData(chunks=[
                FakeData(chunk_content="Search result A", relevancy_score=0.8),
                FakeData(chunk_content="Search result B", relevancy_score=0.6),
            ]))
        ]

        result = provider.handle_tool_call("hydradb_search", {"query": "test"})
        data = json.loads(result)

        assert "error" not in data
        assert "result" in data
        assert "Search result A" in data["result"]
        assert "Search result B" in data["result"]

    def test_hydradb_search_empty_result(self, provider, fake_client):
        """hydradb_search returns fallback message when no chunks found."""
        fake_client.query_results = [
            FakeEnvelope(FakeData(chunks=[]))
        ]

        result = provider.handle_tool_call("hydradb_search", {"query": "nothing"})
        data = json.loads(result)

        assert "result" in data
        assert "No relevant memories found" in data["result"]

    def test_hydradb_profile_returns_profile(self, provider, fake_client):
        """hydradb_profile queries for user profile data."""
        fake_client.query_results = [
            FakeEnvelope(FakeData(chunks=[
                FakeData(chunk_content="User prefers Python", relevancy_score=0.8),
            ]))
        ]

        result = provider.handle_tool_call("hydradb_profile", {})
        data = json.loads(result)

        assert "result" in data
        assert "User prefers Python" in data["result"]

        assert len(fake_client.query_calls) == 1
        call = fake_client.query_calls[0]
        assert call["query"] == "user profile preferences traits"
        assert call["mode"] == "thinking"
        assert call["max_results"] == 5

    def test_hydradb_profile_empty_result(self, provider, fake_client):
        """hydradb_profile returns fallback message when no profile data."""
        fake_client.query_results = [
            FakeEnvelope(FakeData(chunks=[]))
        ]

        result = provider.handle_tool_call("hydradb_profile", {})
        data = json.loads(result)

        assert "result" in data
        assert "No profile data found" in data["result"]

    def test_hydradb_conclude_stores_fact(self, provider, fake_client):
        """hydradb_conclude stores a durable fact via context.ingest."""
        result = provider.handle_tool_call("hydradb_conclude",
                                           {"fact": "User likes dark mode"})
        data = json.loads(result)

        assert data["result"] == "Fact stored."

        assert len(fake_client.context.ingest_calls) == 1
        call = fake_client.context.ingest_calls[0]
        assert call["type"] == "memory"
        assert call["upsert"] == "true"

        memories = json.loads(call["memories"])
        assert memories[0]["text"] == "User likes dark mode"
        assert memories[0]["infer"] is False

    def test_unknown_tool_returns_error(self, provider):
        """handle_tool_call returns error JSON for unknown tool names."""
        result = provider.handle_tool_call("hydradb_unknown", {})
        data = json.loads(result)

        assert "error" in data
        assert "Unknown tool" in data["error"]

    def test_tools_respect_read_circuit_breaker(self, provider, fake_client):
        """hydradb_search returns error when read circuit breaker is open."""
        provider._read_breaker_open_until = time.time() + 9999

        result = provider.handle_tool_call("hydradb_search", {"query": "test"})
        data = json.loads(result)

        assert "error" in data
        assert "circuit breaker" in data["error"].lower()
        assert len(fake_client.query_calls) == 0

    def test_tools_respect_write_circuit_breaker(self, provider, fake_client):
        """hydradb_conclude returns error when write circuit breaker is open."""
        provider._write_breaker_open_until = time.time() + 9999

        result = provider.handle_tool_call("hydradb_conclude", {"fact": "test"})
        data = json.loads(result)

        assert "error" in data
        assert "circuit breaker" in data["error"].lower()
        assert len(fake_client.context.ingest_calls) == 0

    def test_tool_exception_returns_error(self, provider, fake_client):
        """handle_tool_call returns error JSON when tool raises an exception."""
        original = provider._tool_search

        def _raise(*a, **kw):
            raise RuntimeError("Simulated failure")
        provider._tool_search = _raise  # type: ignore[method-assign]

        try:
            result = provider.handle_tool_call("hydradb_search", {"query": "x"})
            data = json.loads(result)
            assert "error" in data
            assert "Simulated failure" in data["error"]
        finally:
            provider._tool_search = original  # type: ignore[method-assign]

    def test_get_tool_schemas_returns_three_schemas(self):
        """get_tool_schemas returns 3 function schemas (search, profile, conclude)."""
        schemas = HydraDBMemoryProvider.get_tool_schemas()
        assert len(schemas) == 3
        names = [s["function"]["name"] for s in schemas]
        assert "hydradb_search" in names
        assert "hydradb_profile" in names
        assert "hydradb_conclude" in names

    def test_get_config_schema_returns_fields(self):
        """get_config_schema returns 4 field descriptors."""
        schema = HydraDBMemoryProvider.get_config_schema()
        assert len(schema) == 4
        keys = [f["key"] for f in schema]
        assert "api_key" in keys
        assert "tenant_id" in keys
        assert "sub_tenant_id" in keys
        assert "query_mode" in keys

    def test_system_prompt_block_returns_static_text(self):
        """system_prompt_block returns the expected static string."""
        text = HydraDBMemoryProvider.system_prompt_block()
        assert "HydraDB Memory" in text
        assert "Active" in text
