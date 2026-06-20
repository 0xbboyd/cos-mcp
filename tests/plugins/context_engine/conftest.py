"""
Test fixtures for Context Engine plugin tests.

Sets up fake agent.context_engine, fake hydra_db, and fake backends
so both context engines can be imported and tested without the real
Hermes Agent runtime or live API calls.

Pattern: mirrors tests/plugins/memory/conftest.py exactly.
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import threading
import types
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
_HYDRADB_ENGINE_PATH = os.path.join(
    _PROJECT_ROOT, "plugins", "context_engine", "hydradb-context", "__init__.py"
)
_MUNINN_ENGINE_PATH = os.path.join(
    _PROJECT_ROOT, "plugins", "context_engine", "muninn-context", "__init__.py"
)

# ---------------------------------------------------------------------------
# 1. Create fake agent package
# ---------------------------------------------------------------------------

_agent_mod = types.ModuleType("agent")
sys.modules["agent"] = _agent_mod

# -- agent.context_engine module --

class ContextEngine:
    """Fake base class matching the real ContextEngine ABC for testing."""
    name: str = ""

    def initialize(self, session_id: str, **kwargs) -> None:
        pass

    def update_from_response(self, usage: dict) -> None:
        pass

    def should_compress(self, prompt_tokens: int = None) -> bool:
        return False

    def compress(self, messages: List[Dict[str, Any]], current_tokens: int = None,
                 focus_topic: str = None) -> List[Dict[str, Any]]:
        return messages

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []

    def handle_tool_call(self, name: str, args: Dict[str, Any], **kwargs) -> str:
        return "{}"

    def on_session_start(self, session_id: str, **kwargs) -> None:
        pass

    def on_session_end(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        pass

    def on_session_reset(self) -> None:
        pass

    def update_model(self, model: str, context_length: int, **kwargs) -> None:
        pass

    def shutdown(self) -> None:
        pass


_ctx_engine_mod = types.ModuleType("agent.context_engine")
_ctx_engine_mod.ContextEngine = ContextEngine
sys.modules["agent.context_engine"] = _ctx_engine_mod
_agent_mod.context_engine = _ctx_engine_mod

# ---------------------------------------------------------------------------
# 2. Create fake hydra_db module (stub — full fakes in fixtures)
# ---------------------------------------------------------------------------

_hydra_db_mod = types.ModuleType("hydra_db")

class FakeHydraDBStub:
    """Minimal stub so ``is_available()`` can import ``HydraDB``."""

    def __init__(self, token=None):
        self.token = token

_hydra_db_mod.HydraDB = FakeHydraDBStub
sys.modules["hydra_db"] = _hydra_db_mod

# ---------------------------------------------------------------------------
# 3. Load the actual engine modules via importlib (directory names have hyphens)
# ---------------------------------------------------------------------------

# HydraDB context engine
_hydra_spec = importlib.util.spec_from_file_location(
    "hydradb_context_engine", _HYDRADB_ENGINE_PATH
)
assert _hydra_spec is not None, f"Could not find hydradb-context at {_HYDRADB_ENGINE_PATH}"
hydradb_context_engine = importlib.util.module_from_spec(_hydra_spec)
sys.modules["hydradb_context_engine"] = hydradb_context_engine
_hydra_spec.loader.exec_module(hydradb_context_engine)

# MuninnDB context engine
_muninn_spec = importlib.util.spec_from_file_location(
    "muninn_context_engine", _MUNINN_ENGINE_PATH
)
assert _muninn_spec is not None, f"Could not find muninn-context at {_MUNINN_ENGINE_PATH}"
muninn_context_engine = importlib.util.module_from_spec(_muninn_spec)
sys.modules["muninn_context_engine"] = muninn_context_engine
_muninn_spec.loader.exec_module(muninn_context_engine)

# Re-export for convenience
HydraDBContextEngine = hydradb_context_engine.HydraDBContextEngine
MuninnDBContextEngine = muninn_context_engine.MuninnDBContextEngine

# ---------------------------------------------------------------------------
# 4. Reusable fake data classes (matching v1.0 memory test pattern)
# ---------------------------------------------------------------------------


class FakeData:
    """Generic attribute bag that mimics API response objects."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeEnvelope:
    """Wraps a FakeData object like a HydraDB API response envelope."""

    def __init__(self, data=None):
        self.data = data


class FakeContext:
    """Fake client.context — tracks ingest/delete calls."""

    def __init__(self):
        self.ingest_calls: List[dict] = []
        self.delete_calls: List[dict] = []

    def ingest(self, **kwargs):
        self.ingest_calls.append(kwargs)
        return FakeEnvelope(FakeData())

    def delete(self, **kwargs):
        self.delete_calls.append(kwargs)
        return FakeEnvelope(FakeData())


class FakeTenants:
    """Fake client.tenants — list/create/status with configurable behaviour."""

    def __init__(self, *, existing_tenants=None, ready_after_attempts=1,
                 raise_on_create=None, raise_on_status=None):
        self._existing = existing_tenants or []
        self._ready_after = ready_after_attempts
        self._raise_on_create = raise_on_create
        self._raise_on_status = raise_on_status
        self.status_calls = 0
        self.create_calls: List[str] = []
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


class FakeHydraDBClient:
    """Complete fake HydraDB SDK client — tracks query/context calls."""

    def __init__(self, token=None):
        self.token = token
        self.tenants = FakeTenants()
        self.context = FakeContext()
        self.query_results: List[FakeEnvelope] = []
        self.query_calls: List[dict] = []
        self._query_index = 0

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if self._query_index < len(self.query_results):
            result = self.query_results[self._query_index]
            self._query_index += 1
            return result
        return FakeEnvelope(FakeData(chunks=[]))


# ---------------------------------------------------------------------------
# 5. Fake backends
# ---------------------------------------------------------------------------


class FakeHydraDBBackend:
    """Fake HydraDBBackend that tracks calls without making API requests.

    Injected via monkeypatch on _create_backend().
    """

    def __init__(self):
        self.query_calls: List[dict] = []
        self.ingest_calls: List[dict] = []
        self.delete_calls: List[dict] = []
        self.query_results: List[FakeEnvelope] = []
        self._query_index = 0
        self._provisioned = False
        self._health_status = True
        self._provision_success = True
        self._raise_on_query: Optional[Exception] = None
        self._raise_on_ingest: Optional[Exception] = None
        self._raise_on_health: Optional[Exception] = None
        self._raise_on_provision: Optional[Exception] = None
        self.shutdown_calls = 0

    def query(self, query_text: str = "", **kwargs) -> Any:
        self.query_calls.append({"query_text": query_text, **kwargs})
        if self._raise_on_query:
            raise self._raise_on_query
        if self._query_index < len(self.query_results):
            result = self.query_results[self._query_index]
            self._query_index += 1
            return result
        return FakeEnvelope(FakeData(chunks=[]))

    def ingest(self, text: str = "", **kwargs) -> None:
        self.ingest_calls.append({"text": text, **kwargs})
        if self._raise_on_ingest:
            raise self._raise_on_ingest

    def delete(self, memory_id: str) -> None:
        self.delete_calls.append({"memory_id": memory_id})

    def health_check(self) -> bool:
        if self._raise_on_health:
            raise self._raise_on_health
        return self._health_status

    def provision(self) -> bool:
        if self._raise_on_provision:
            raise self._raise_on_provision
        return self._provision_success

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class FakeMuninnDBBackend:
    """Fake MuninnDBBackend that tracks calls without making HTTP requests.

    Injected via monkeypatch on _create_backend().
    """

    def __init__(self):
        self.query_calls: List[dict] = []
        self.ingest_calls: List[dict] = []
        self.delete_calls: List[dict] = []
        self.query_results: List[List[dict]] = []  # list of activation lists
        self._query_index = 0
        self._health_status = True
        self._provision_success = True
        self._raise_on_query: Optional[Exception] = None
        self._raise_on_ingest: Optional[Exception] = None
        self._raise_on_health: Optional[Exception] = None
        self._raise_on_provision: Optional[Exception] = None
        self.shutdown_calls = 0

    def query(self, query_text: str = "", **kwargs) -> Any:
        self.query_calls.append({"query_text": query_text, **kwargs})
        if self._raise_on_query:
            raise self._raise_on_query
        if self._query_index < len(self.query_results):
            result = self.query_results[self._query_index]
            self._query_index += 1
            return result
        return []

    def ingest(self, text: str = "", **kwargs) -> None:
        self.ingest_calls.append({"text": text, **kwargs})
        if self._raise_on_ingest:
            raise self._raise_on_ingest

    def delete(self, memory_id: str) -> None:
        self.delete_calls.append({"memory_id": memory_id})

    def health_check(self) -> bool:
        if self._raise_on_health:
            raise self._raise_on_health
        return self._health_status

    def provision(self) -> bool:
        if self._raise_on_provision:
            raise self._raise_on_provision
        return self._provision_success

    def shutdown(self) -> None:
        self.shutdown_calls += 1


# ---------------------------------------------------------------------------
# 6. Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_hydra_backend():
    """Return a fresh FakeHydraDBBackend."""
    return FakeHydraDBBackend()


@pytest.fixture
def fake_muninn_backend():
    """Return a fresh FakeMuninnDBBackend."""
    return FakeMuninnDBBackend()


@pytest.fixture
def fake_hydra_client():
    """Return a fresh FakeHydraDBClient."""
    client = FakeHydraDBClient()
    client.tenants = FakeTenants(existing_tenants=["hermes"])
    return client


# ---------------------------------------------------------------------------
# Engine fixtures — HydraDB
# ---------------------------------------------------------------------------


def _make_hydra_engine(backend: FakeHydraDBBackend, fake_client: FakeHydraDBClient,
                       monkeypatch, api_key: str = "test-key",
                       agent_context: str = "primary",
                       hermes_home: str = "/tmp/hermes_test",
                       agent_identity: str = "test-profile",
                       config_overrides: dict = None) -> HydraDBContextEngine:
    """Initialize a HydraDBContextEngine with fake backend and client."""
    monkeypatch.setenv("HYDRA_DB_API_KEY", api_key)
    monkeypatch.setattr(
        HydraDBContextEngine, "_create_backend",
        lambda self, kwargs: backend
    )
    monkeypatch.setattr(
        HydraDBContextEngine, "_get_client",
        lambda self: fake_client
    )

    engine = HydraDBContextEngine()
    engine.initialize(
        "test-session",
        hermes_home=hermes_home,
        agent_identity=agent_identity,
        agent_context=agent_context,
        model="test-model",
    )

    # Re-attach _get_client for background threads
    engine._get_client = lambda: fake_client  # type: ignore[method-assign]

    return engine


@pytest.fixture
def hydra_engine(fake_hydra_backend, fake_hydra_client, monkeypatch):
    """Return an initialized HydraDBContextEngine with fake backend/client."""
    return _make_hydra_engine(fake_hydra_backend, fake_hydra_client, monkeypatch)


@pytest.fixture
def hydra_engine_non_primary(fake_hydra_backend, fake_hydra_client, monkeypatch):
    """HydraDB engine initialized with agent_context='subagent'."""
    return _make_hydra_engine(
        fake_hydra_backend, fake_hydra_client, monkeypatch,
        agent_context="subagent"
    )


# ---------------------------------------------------------------------------
# Engine fixtures — MuninnDB
# ---------------------------------------------------------------------------


def _make_muninn_engine(backend: FakeMuninnDBBackend, monkeypatch,
                        api_key: str = "test-muninn-key",
                        agent_context: str = "primary",
                        hermes_home: str = "/tmp/hermes_test",
                        agent_identity: str = "test-user",
                        config_overrides: dict = None) -> MuninnDBContextEngine:
    """Initialize a MuninnDBContextEngine with fake backend."""
    monkeypatch.setenv("MUNINN_API_KEY", api_key)
    monkeypatch.setattr(
        MuninnDBContextEngine, "_create_backend",
        lambda self, kwargs: backend
    )

    engine = MuninnDBContextEngine()
    engine.initialize(
        "test-session",
        hermes_home=hermes_home,
        agent_identity=agent_identity,
        agent_context=agent_context,
        model="test-model",
    )
    return engine


@pytest.fixture
def muninn_engine(fake_muninn_backend, monkeypatch):
    """Return an initialized MuninnDBContextEngine with fake backend."""
    return _make_muninn_engine(fake_muninn_backend, monkeypatch)


@pytest.fixture
def muninn_engine_non_primary(fake_muninn_backend, monkeypatch):
    """MuninnDB engine initialized with agent_context='subagent'."""
    return _make_muninn_engine(
        fake_muninn_backend, monkeypatch, agent_context="subagent"
    )


# ---------------------------------------------------------------------------
# Message builders for compress() tests
# ---------------------------------------------------------------------------


def make_test_messages(count: int = 20, include_system: bool = True) -> List[Dict[str, Any]]:
    """Build a list of test messages suitable for compress() testing.

    Returns messages that will trigger entity extraction and compression.
    """
    messages: List[Dict[str, Any]] = []
    if include_system:
        messages.append({"role": "system", "content": "You are a helpful assistant."})

    for i in range(count):
        if i % 2 == 0:
            messages.append({
                "role": "user",
                "content": (
                    f"Message {i}: We decided to use Python for the backend. "
                    f"The API server runs on port 8080. This is an important decision "
                    f"about the architecture. The system uses PostgreSQL."
                ),
            })
        else:
            messages.append({
                "role": "assistant",
                "content": (
                    f"Response {i}: I chose Flask for the web framework. "
                    f"The deployment depends on Docker containers. "
                    f"This implementation requires Python 3.12. "
                    f"The project structure is well organized."
                ),
            })
    return messages
