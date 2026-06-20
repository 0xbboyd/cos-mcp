"""
TestContextTools — TST-04: Tool schemas, context_search, context_expand,
tool dispatch, and error handling for both engines.

Tests HYD-05/06/07/10, MUN-05/06/07/10.
"""
from __future__ import annotations

import json

import pytest

from tests.plugins.context_engine.conftest import FakeEnvelope, FakeData


# ============================================================================
# HydraDB tools
# ============================================================================


class TestHydraDBTools:
    """Tool tests for HydraDBContextEngine."""

    def test_get_tool_schemas_active(self, hydra_engine):
        """get_tool_schemas returns tool schemas when engine is active (primary)."""
        schemas = hydra_engine.get_tool_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) == 2
        names = [s["function"]["name"] for s in schemas]
        assert "hydradb_context_search" in names
        assert "hydradb_context_expand" in names

    def test_get_tool_schemas_non_primary(self, hydra_engine_non_primary):
        """get_tool_schemas returns [] when agent_context != 'primary'."""
        schemas = hydra_engine_non_primary.get_tool_schemas()
        assert schemas == []

    def test_context_search_returns_results(self, hydra_engine, fake_hydra_backend):
        """context_search returns formatted results for a valid query."""
        fake_hydra_backend.query_results = [
            FakeEnvelope(FakeData(chunks=[
                FakeData(chunk_content="Topic: Python backend", relevancy_score=0.9,
                         ctx_id="test_0_abc123"),
                FakeData(chunk_content="Decision: use Flask", relevancy_score=0.8),
            ]))
        ]
        result = hydra_engine.handle_tool_call("hydradb_context_search",
                                                {"query": "Python"})
        parsed = json.loads(result)
        assert "result" in parsed
        assert "Topic: Python backend" in parsed["result"]
        assert "Decision: use Flask" in parsed["result"]

    def test_context_search_empty_query(self, hydra_engine):
        """context_search returns error when query is empty."""
        result = hydra_engine.handle_tool_call("hydradb_context_search",
                                                {"query": ""})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_context_search_no_results(self, hydra_engine, fake_hydra_backend):
        """context_search returns 'No relevant context found' for empty results."""
        fake_hydra_backend.query_results = [
            FakeEnvelope(FakeData(chunks=[]))
        ]
        result = hydra_engine.handle_tool_call("hydradb_context_search",
                                                {"query": "nothing"})
        parsed = json.loads(result)
        assert "result" in parsed
        assert "No relevant context" in parsed["result"]

    def test_context_expand_returns_results(self, hydra_engine, fake_hydra_backend):
        """context_expand returns formatted results for a ctx_id."""
        fake_hydra_backend.query_results = [
            FakeEnvelope(FakeData(chunks=[
                FakeData(chunk_content="Expanded entity 1", ctx_id="test_0_a1b2c3d4",
                         hop_depth=0),
                FakeData(chunk_content="Expanded entity 2", ctx_id="test_0_a1b2c3d4",
                         hop_depth=1, hop_path=["a", "b"]),
            ]))
        ]
        result = hydra_engine.handle_tool_call("hydradb_context_expand",
                                                {"ctx_id": "test_0_a1b2c3d4"})
        parsed = json.loads(result)
        assert "result" in parsed
        assert "Expanded entity 1" in parsed["result"]
        assert "Expanded entity 2" in parsed["result"]

    def test_context_expand_no_args(self, hydra_engine):
        """context_expand returns error when neither ctx_id nor topic provided."""
        result = hydra_engine.handle_tool_call("hydradb_context_expand",
                                                {"ctx_id": "", "topic": ""})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_context_expand_no_results(self, hydra_engine, fake_hydra_backend):
        """context_expand returns no-results message for empty results."""
        fake_hydra_backend.query_results = [
            FakeEnvelope(FakeData(chunks=[]))
        ]
        result = hydra_engine.handle_tool_call("hydradb_context_expand",
                                                {"ctx_id": "nonexistent"})
        parsed = json.loads(result)
        assert "result" in parsed
        assert "No relevant context" in parsed["result"]

    def test_tool_dispatch_unknown_tool(self, hydra_engine):
        """handle_tool_call returns error JSON for unknown tools."""
        result = hydra_engine.handle_tool_call("unknown_tool", {"arg": "val"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Unknown context engine tool" in parsed["error"]

    def test_tool_dispatch_routes_correctly(self, hydra_engine, fake_hydra_backend):
        """handle_tool_call dispatches to correct handler based on tool name."""
        fake_hydra_backend.query_results = [
            FakeEnvelope(FakeData(chunks=[
                FakeData(chunk_content="Search result", relevancy_score=0.9),
            ])),
            FakeEnvelope(FakeData(chunks=[
                FakeData(chunk_content="Expand result", ctx_id="x"),
            ])),
        ]
        search_result = hydra_engine.handle_tool_call(
            "hydradb_context_search", {"query": "test"})
        expand_result = hydra_engine.handle_tool_call(
            "hydradb_context_expand", {"ctx_id": "x"})
        assert "Search result" in json.loads(search_result)["result"]
        assert "Expand result" in json.loads(expand_result)["result"]

    def test_context_search_breaker_open(self, hydra_engine, fake_hydra_backend, monkeypatch):
        """context_search returns error when read breaker is open."""
        from cos_mcp.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        for _ in range(3):
            breaker.record_read_failure()
        monkeypatch.setattr(hydra_engine, "_breaker", breaker)
        result = hydra_engine.handle_tool_call("hydradb_context_search",
                                                {"query": "test"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "circuit breaker" in parsed["error"].lower()

    def test_context_search_exception_handling(self, hydra_engine, fake_hydra_backend):
        """context_search returns JSON error on exception."""
        fake_hydra_backend._raise_on_query = RuntimeError("Backend down")
        result = hydra_engine.handle_tool_call("hydradb_context_search",
                                                {"query": "test"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Backend down" in parsed["error"]


# ============================================================================
# MuninnDB tools
# ============================================================================


class TestMuninnDBTools:
    """Tool tests for MuninnDBContextEngine."""

    def test_get_tool_schemas_active(self, muninn_engine):
        """get_tool_schemas returns tool schemas when engine is active."""
        schemas = muninn_engine.get_tool_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) == 2
        names = [s["function"]["name"] for s in schemas]
        assert "muninn_context_search" in names
        assert "muninn_context_expand" in names

    def test_get_tool_schemas_non_primary(self, muninn_engine_non_primary):
        """get_tool_schemas returns [] when agent_context != 'primary'."""
        schemas = muninn_engine_non_primary.get_tool_schemas()
        assert schemas == []

    def test_context_search_returns_results(self, muninn_engine, fake_muninn_backend):
        """context_search returns formatted results for a valid query."""
        fake_muninn_backend.query_results = [[
            {"concept": "Python backend", "content": "Using Python for backend",
             "confidence": 0.85, "dormant": False},
            {"concept": "Flask decision", "content": "Chose Flask framework",
             "confidence": 0.75, "dormant": False},
        ]]
        result = muninn_engine.handle_tool_call("muninn_context_search",
                                                 {"query": "Python"})
        parsed = json.loads(result)
        assert "result" in parsed
        assert "Python backend" in parsed["result"]

    def test_context_search_empty_query(self, muninn_engine):
        """context_search returns error when query is empty."""
        result = muninn_engine.handle_tool_call("muninn_context_search",
                                                 {"query": ""})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_context_search_no_results(self, muninn_engine, fake_muninn_backend):
        """context_search returns 'No relevant context found' for empty results."""
        fake_muninn_backend.query_results = [[]]
        result = muninn_engine.handle_tool_call("muninn_context_search",
                                                 {"query": "nothing"})
        parsed = json.loads(result)
        assert "result" in parsed
        assert "No relevant context" in parsed["result"]

    def test_context_expand_returns_results(self, muninn_engine, fake_muninn_backend):
        """context_expand returns formatted results for a ctx_id."""
        fake_muninn_backend.query_results = [[
            {"concept": "Entity A", "content": "Expanded context A",
             "confidence": 0.9, "dormant": False, "ctx_id": "test_0_a1b2c3d4"},
            {"concept": "Entity B", "content": "Expanded context B",
             "confidence": 0.8, "dormant": False, "ctx_id": "test_0_a1b2c3d4"},
        ]]
        result = muninn_engine.handle_tool_call("muninn_context_expand",
                                                 {"ctx_id": "test_0_a1b2c3d4"})
        parsed = json.loads(result)
        assert "result" in parsed
        assert "Entity A" in parsed["result"]

    def test_context_expand_no_args(self, muninn_engine):
        """context_expand returns error when neither ctx_id nor topic provided."""
        result = muninn_engine.handle_tool_call("muninn_context_expand",
                                                 {"ctx_id": "", "topic": ""})
        parsed = json.loads(result)
        assert "error" in parsed

    def test_tool_dispatch_unknown_tool(self, muninn_engine):
        """handle_tool_call returns error JSON for unknown tools."""
        result = muninn_engine.handle_tool_call("unknown_tool", {"arg": "val"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Unknown context engine tool" in parsed["error"]

    def test_context_search_breaker_open(self, muninn_engine, fake_muninn_backend, monkeypatch):
        """context_search returns error when read breaker is open."""
        from cos_mcp.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        for _ in range(3):
            breaker.record_read_failure()
        monkeypatch.setattr(muninn_engine, "_breaker", breaker)
        result = muninn_engine.handle_tool_call("muninn_context_search",
                                                 {"query": "test"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "circuit breaker" in parsed["error"].lower()

    def test_context_search_exception_handling(self, muninn_engine, fake_muninn_backend):
        """context_search returns JSON error on exception."""
        fake_muninn_backend._raise_on_query = RuntimeError("Backend error")
        result = muninn_engine.handle_tool_call("muninn_context_search",
                                                 {"query": "test"})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Backend error" in parsed["error"]
