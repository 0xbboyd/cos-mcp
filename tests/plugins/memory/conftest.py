"""
Test fixtures for HydraDB Memory Provider tests.

Sets up fake agent.memory_provider and hydra_db modules so the provider
can be imported without the real Hermes Agent runtime or HydraDB SDK.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import threading
import time
import types

import pytest

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_PROVIDER_PATH = os.path.join(_PROJECT_ROOT, "hydradb-memory", "__init__.py")

# ---------------------------------------------------------------------------
# 1. Create fake agent package
# ---------------------------------------------------------------------------

_agent_mod = types.ModuleType("agent")
sys.modules["agent"] = _agent_mod

_mem_provider_mod = types.ModuleType("agent.memory_provider")


class MemoryProvider:
    """Fake base class matching the real MemoryProvider ABC for testing."""
    name: str = ""


_mem_provider_mod.MemoryProvider = MemoryProvider  # type: ignore[attr-defined]
sys.modules["agent.memory_provider"] = _mem_provider_mod
_agent_mod.memory_provider = _mem_provider_mod  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# 2. Create fake hydra_db module (stub — full fakes in fixtures)
# ---------------------------------------------------------------------------

_hydra_db_mod = types.ModuleType("hydra_db")


class FakeHydraDBStub:
    """Minimal stub so ``is_available()`` can import ``HydraDB``."""

    def __init__(self, token=None):
        self.token = token


_hydra_db_mod.HydraDB = FakeHydraDBStub  # type: ignore[attr-defined]
sys.modules["hydra_db"] = _hydra_db_mod

# ---------------------------------------------------------------------------
# 3. Load the actual provider module via importlib (directory name has hyphen)
# ---------------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location("hydradb_memory", _PROVIDER_PATH)
assert _spec is not None, f"Could not find provider at {_PROVIDER_PATH}"
hydradb_memory = importlib.util.module_from_spec(_spec)
sys.modules["hydradb_memory"] = hydradb_memory
_spec.loader.exec_module(hydradb_memory)  # type: ignore[union-attr]

# Re-export for convenience
HydraDBMemoryProvider = hydradb_memory.HydraDBMemoryProvider
DEFAULT_CONFIG = hydradb_memory.DEFAULT_CONFIG
_load_config = hydradb_memory._load_config

# ---------------------------------------------------------------------------
# 4. Reusable fake data classes (used by test file and fixtures)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 5. Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_client():
    """Return a fresh FakeHydraDBClient with default tenant setup."""
    client = FakeHydraDBClient()
    client.tenants = FakeTenants(existing_tenants=["hermes"])
    return client


@pytest.fixture
def provider_no_init():
    """Return a HydraDBMemoryProvider instance without calling initialize()."""
    return HydraDBMemoryProvider()


@pytest.fixture
def provider(provider_no_init, fake_client, monkeypatch):
    """Return an initialized provider with fake client and mocked time.sleep.

    ``_ensure_tenant`` is skipped so tests that don't care about tenant
    provisioning run quickly.
    """
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(provider_no_init, "_get_client", lambda: fake_client)
    monkeypatch.setattr(provider_no_init, "_ensure_tenant", lambda: None)
    monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
    provider_no_init.initialize(
        "test-session",
        hermes_home="/tmp/hermes_test",
        agent_identity="test-profile",
        agent_context="primary",
    )
    # Re-attach — _get_client may be called by background threads
    provider_no_init._get_client = lambda: fake_client  # type: ignore[method-assign]
    return provider_no_init


@pytest.fixture
def provider_with_tenant(provider_no_init, fake_client, monkeypatch):
    """Return a provider that goes through full _ensure_tenant flow.

    The fake client reports the tenant as existing and ready on first poll.
    ``time.sleep`` is mocked to avoid real polling delays.
    """
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(provider_no_init, "_get_client", lambda: fake_client)
    monkeypatch.setenv("HYDRA_DB_API_KEY", "test-key")
    provider_no_init.initialize(
        "test-session",
        hermes_home="/tmp/hermes_test",
        agent_identity="test-profile",
        agent_context="primary",
    )
    provider_no_init._get_client = lambda: fake_client  # type: ignore[method-assign]
    return provider_no_init
