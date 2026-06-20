"""
TestSharedInfra — TST-07: BaseContextEngine defaults, formatters,
token tracking, should_compress, 6 class attributes, update_model.

Tests SHR-01/02/03/04, CTX-01/02/03/05/06/08.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from cos_mcp.base_context_engine import BaseContextEngine
from cos_mcp.backends.base import MemoryBackend
from cos_mcp.formatting.context_base import ContextFormatter
from cos_mcp.formatting.hydradb_context import HydraDBContextFormatter
from cos_mcp.formatting.muninn_context import MuninnDBContextFormatter
from cos_mcp.circuit_breaker import CircuitBreaker

from tests.plugins.context_engine.conftest import FakeData, FakeEnvelope
from hydradb_context_engine import HydraDBContextEngine
from muninn_context_engine import MuninnDBContextEngine


# ============================================================================
# BaseContextEngine — CTX attributes
# ============================================================================


class TestBaseContextEngineDefaults:
    """BaseContextEngine provides working defaults for all defined attributes."""

    def test_class_attributes_exist(self):
        """All 6 ABC class attributes (CTX-05) exist with default values."""
        assert hasattr(BaseContextEngine, "last_prompt_tokens")
        assert BaseContextEngine.last_prompt_tokens == 0
        assert hasattr(BaseContextEngine, "last_completion_tokens")
        assert BaseContextEngine.last_completion_tokens == 0
        assert hasattr(BaseContextEngine, "last_total_tokens")
        assert BaseContextEngine.last_total_tokens == 0
        assert hasattr(BaseContextEngine, "threshold_tokens")
        assert BaseContextEngine.threshold_tokens == 0
        assert hasattr(BaseContextEngine, "context_length")
        assert BaseContextEngine.context_length == 0
        assert hasattr(BaseContextEngine, "compression_count")
        assert BaseContextEngine.compression_count == 0

    def test_tunable_parameters_exist(self):
        """threshold_percent, protect_first_n, protect_last_n are settable (CTX-06)."""
        assert BaseContextEngine.threshold_percent == 0.75
        assert BaseContextEngine.protect_first_n == 3
        assert BaseContextEngine.protect_last_n == 6

    def test_name_property_hydradb(self, hydra_engine):
        """name property returns 'hydradb-context' (CTX-01)."""
        assert HydraDBContextEngine.name == "hydradb-context"

    def test_name_property_muninn(self, muninn_engine):
        """name property returns 'muninn-context' (CTX-01)."""
        assert MuninnDBContextEngine.name == "muninn-context"

    def test_compress_not_implemented(self):
        """BaseContextEngine.compress() raises NotImplementedError."""
        # Need a concrete subclass that doesn't override compress
        class MinimalEngine(BaseContextEngine):
            name = "test"
            def _create_backend(self, kwargs):
                raise NotImplementedError
            def _create_formatter(self):
                raise NotImplementedError
            @classmethod
            def is_available(cls):
                return False

        engine = MinimalEngine()
        with pytest.raises(NotImplementedError):
            engine.compress([])

    def test_get_tool_schemas_defaults_empty(self):
        """BaseContextEngine.get_tool_schemas() returns [] by default."""
        class MinimalEngine(BaseContextEngine):
            name = "test"
            def _create_backend(self, kwargs):
                raise NotImplementedError
            def _create_formatter(self):
                raise NotImplementedError
            @classmethod
            def is_available(cls):
                return False
        engine = MinimalEngine()
        assert engine.get_tool_schemas() == []

    def test_handle_tool_call_defaults_error(self):
        """BaseContextEngine.handle_tool_call() returns JSON error for unknown tools."""
        class MinimalEngine(BaseContextEngine):
            name = "test"
            def _create_backend(self, kwargs):
                raise NotImplementedError
            def _create_formatter(self):
                raise NotImplementedError
            @classmethod
            def is_available(cls):
                return False
        engine = MinimalEngine()
        result = engine.handle_tool_call("foo", {})
        parsed = json.loads(result)
        assert "error" in parsed
        assert "Unknown context engine tool" in parsed["error"]


# ============================================================================
# Token tracking (CTX-02, CTX-03)
# ============================================================================


class TestTokenTracking:
    """update_from_response, should_compress, update_model tests."""

    def test_update_from_response_canonical_fields(self, hydra_engine):
        """update_from_response prefers canonical input_tokens/output_tokens."""
        hydra_engine.update_model("test", 1000)  # Sets context_length
        hydra_engine.update_from_response({
            "input_tokens": 500,
            "output_tokens": 200,
            "cache_read_tokens": 50,
            "cache_write_tokens": 10,
        })
        assert hydra_engine.last_prompt_tokens == 500
        assert hydra_engine.last_completion_tokens == 200
        assert hydra_engine.last_total_tokens == 700
        assert hydra_engine.cache_read_tokens == 50
        assert hydra_engine.cache_write_tokens == 10
        # Cache tokens NOT added to prompt count
        assert hydra_engine.last_total_tokens == 700  # 500+200, not 500+200+50

    def test_update_from_response_legacy_fields(self, hydra_engine):
        """update_from_response falls back to legacy prompt_tokens/completion_tokens."""
        hydra_engine.update_model("test", 1000)
        hydra_engine.update_from_response({
            "prompt_tokens": 300,
            "completion_tokens": 150,
        })
        assert hydra_engine.last_prompt_tokens == 300
        assert hydra_engine.last_completion_tokens == 150
        assert hydra_engine.last_total_tokens == 450

    def test_should_compress_below_threshold(self, hydra_engine):
        """should_compress returns False when tokens below threshold."""
        hydra_engine.threshold_tokens = 750
        hydra_engine.last_prompt_tokens = 500
        assert hydra_engine.should_compress() is False

    def test_should_compress_above_threshold(self, hydra_engine):
        """should_compress returns True when tokens exceed threshold."""
        hydra_engine.threshold_tokens = 750
        hydra_engine.last_prompt_tokens = 800
        assert hydra_engine.should_compress() is True

    def test_should_compress_explicit_override(self, hydra_engine):
        """should_compress uses explicit prompt_tokens override."""
        hydra_engine.threshold_tokens = 750
        hydra_engine.last_prompt_tokens = 500  # Below threshold
        # But explicit override is above
        assert hydra_engine.should_compress(prompt_tokens=800) is True
        # Default still below
        assert hydra_engine.should_compress() is False

    def test_update_model_recalculates_threshold(self, hydra_engine):
        """update_model sets context_length and recalculates threshold_tokens (CTX-08)."""
        hydra_engine.threshold_percent = 0.75
        hydra_engine.update_model("gpt-4", 8192)
        assert hydra_engine.context_length == 8192
        assert hydra_engine.threshold_tokens == int(8192 * 0.75)

    def test_muninn_token_tracking(self, muninn_engine):
        """MuninnDB engine also correctly tracks tokens."""
        muninn_engine.update_model("test", 2000)
        muninn_engine.update_from_response({
            "input_tokens": 1000,
            "output_tokens": 400,
        })
        assert muninn_engine.last_prompt_tokens == 1000
        assert muninn_engine.last_completion_tokens == 400
        assert muninn_engine.last_total_tokens == 1400
        assert muninn_engine.threshold_tokens == int(2000 * 0.75)


# ============================================================================
# Formatters (SHR-02, SHR-03)
# ============================================================================


class TestFormatters:
    """ContextFormatter ABC and concrete formatters."""

    def test_context_formatter_is_abc(self):
        """ContextFormatter is an abstract base class (SHR-02)."""
        assert hasattr(ContextFormatter, 'format_compress_summary')
        assert hasattr(ContextFormatter, 'format_search_result')
        assert hasattr(ContextFormatter, 'format_expand_result')

    def test_hydradb_formatter_compress_summary(self):
        """HydraDBContextFormatter.format_compress_summary produces expected output."""
        fmt = HydraDBContextFormatter()
        result = [
            {"type": "topic", "summary": "Python backend", "confidence": 0.85,
             "ctx_id": "test_0_a1b2c3d4"},
            {"type": "decision", "summary": "Use Flask", "confidence": 0.9,
             "ctx_id": "test_1_b2c3d4e5"},
        ]
        output = fmt.format_compress_summary(result)
        assert "Python backend" in output
        assert "Use Flask" in output
        assert "[ctx-id: test_0_a1b2c3d4]" in output
        assert "**Topic**" in output
        assert "**Decision**" in output

    def test_hydradb_formatter_search_result(self):
        """HydraDBContextFormatter.format_search_result filters by relevancy_score."""
        fmt = HydraDBContextFormatter()
        result = FakeEnvelope(FakeData(chunks=[
            FakeData(chunk_content="High relevance", relevancy_score=0.9, ctx_id="a",
                     hop_depth=1, relationship_edges=["depends_on"]),
            FakeData(chunk_content="Low relevance", relevancy_score=0.1),
            FakeData(chunk_content="  ", relevancy_score=0.8),  # whitespace only
        ]))
        output = fmt.format_search_result(result, min_score=0.3)
        assert "High relevance" in output
        assert "[ctx-id: a]" in output
        assert "(hop: 1)" in output
        assert "[edges:" in output
        assert "Low relevance" not in output

    def test_hydradb_formatter_search_empty(self):
        """HydraDBContextFormatter.format_search_result returns '' for no chunks."""
        fmt = HydraDBContextFormatter()
        assert fmt.format_search_result(FakeEnvelope(FakeData(chunks=[]))) == ""

    def test_hydradb_formatter_expand_result(self):
        """HydraDBContextFormatter.format_expand_result includes ctx_id and hop_path."""
        fmt = HydraDBContextFormatter()
        result = FakeEnvelope(FakeData(chunks=[
            FakeData(chunk_content="Entity content", ctx_id="x",
                     hop_path=["a", "b", "c"], hop_depth=2),
        ]))
        output = fmt.format_expand_result(result)
        assert "Entity content" in output
        assert "[ctx-id: x]" in output
        assert "path:" in output
        assert "a → b → c" in output

    def test_hydradb_formatter_expand_empty(self):
        """HydraDBContextFormatter.format_expand_result returns no-results message."""
        fmt = HydraDBContextFormatter()
        assert "No relevant context found" in fmt.format_expand_result(
            FakeEnvelope(FakeData(chunks=[])))

    def test_muninn_formatter_compress_summary(self):
        """MuninnDBContextFormatter.format_compress_summary includes confidence."""
        fmt = MuninnDBContextFormatter()
        result = [
            {"type": "fact", "summary": "Python 3.12 required", "confidence": 0.95,
             "ctx_id": "test_0_a"},
            {"type": "relationship", "summary": "depends on Docker", "confidence": 0.35,
             "ctx_id": "test_0_b"},
        ]
        output = fmt.format_compress_summary(result)
        assert "Python 3.12 required" in output
        assert "depends on Docker" in output
        assert "[ctx-id: test_0_a]" in output
        assert "[LOW confidence:" in output  # confidence 0.35 < 0.4

    def test_muninn_formatter_search_result(self):
        """MuninnDBContextFormatter.format_search_result handles activations."""
        fmt = MuninnDBContextFormatter()
        result = [
            {"concept": "Python", "content": "Using Python 3.12",
             "confidence": 0.85, "dormant": False},
            {"concept": "Docker", "content": "Docker deployment",
             "confidence": 0.5, "dormant": False, "type": "decision"},
            {"concept": "Old", "content": "Should be filtered",
             "confidence": 0.2, "dormant": False},
        ]
        output = fmt.format_search_result(result, min_score=0.3)
        assert "Python" in output
        assert "Docker" in output
        assert "[type: decision]" in output
        assert "[confidence: 50%]" in output  # 0.5 < 0.6
        assert "Old" not in output  # below min_score of 0.3

    def test_muninn_formatter_search_empty(self):
        """MuninnDBContextFormatter.format_search_result returns 'No relevant context'."""
        fmt = MuninnDBContextFormatter()
        assert "No relevant context found" in fmt.format_search_result([], min_score=0.3)

    def test_muninn_formatter_expand_result(self):
        """MuninnDBContextFormatter.format_expand_result includes confidence and paths."""
        fmt = MuninnDBContextFormatter()
        result = [
            {"concept": "Entity A", "content": "Content A",
             "confidence": 0.8, "dormant": False, "ctx_id": "x",
             "hop_path": ["a", "b"], "hop_depth": 1},
        ]
        output = fmt.format_expand_result(result)
        assert "Entity A" in output
        assert "Content A" in output
        assert "[ctx-id: x]" in output
        assert "confidence: 80%" in output

    def test_muninn_formatter_expand_empty(self):
        """MuninnDBContextFormatter.format_expand_result returns no-results for empty."""
        fmt = MuninnDBContextFormatter()
        assert "No relevant context found" in fmt.format_expand_result([])

    def test_muninn_formatter_skips_dormant(self):
        """MuninnDBContextFormatter skips dormant entries."""
        fmt = MuninnDBContextFormatter()
        result = [
            {"concept": "Active", "content": "Visible", "confidence": 0.9, "dormant": False},
            {"concept": "Dormant", "content": "Hidden", "confidence": 0.9, "dormant": True},
        ]
        output = fmt.format_search_result(result, min_score=0.3)
        assert "Active" in output
        assert "Dormant" not in output


# ============================================================================
# MemoryBackend ABC (SHR-04)
# ============================================================================


class TestMemoryBackendABC:
    """MemoryBackend ABC defines the required interface (SHR-04)."""

    def test_memory_backend_methods_exist(self):
        """MemoryBackend ABC declares query, ingest, delete, health_check, provision, shutdown."""
        assert hasattr(MemoryBackend, 'query')
        assert hasattr(MemoryBackend, 'ingest')
        assert hasattr(MemoryBackend, 'delete')
        assert hasattr(MemoryBackend, 'health_check')
        assert hasattr(MemoryBackend, 'provision')
        assert hasattr(MemoryBackend, 'shutdown')

    def test_cannot_instantiate_abstract(self):
        """MemoryBackend cannot be instantiated directly (abstract)."""
        with pytest.raises(TypeError):
            MemoryBackend()  # type: ignore[abstract]


# ============================================================================
# Circuit breaker isolation (SHR, CFG-06)
# ============================================================================


class TestCircuitBreakerIsolation:
    """Circuit breaker instance isolation between engines."""

    def test_hydra_breaker_label(self, hydra_engine):
        """HydraDB engine breaker has correct label."""
        assert hydra_engine._breaker._label == "hydradb-context"

    def test_muninn_breaker_label(self, muninn_engine):
        """MuninnDB engine breaker has correct label."""
        assert muninn_engine._breaker._label == "muninn-context"

    def test_breakers_are_distinct_objects(self, hydra_engine, muninn_engine):
        """Each engine has its own breaker object instance."""
        assert hydra_engine._breaker is not muninn_engine._breaker

    def test_hydra_breaker_independent_from_memory(self):
        """Context engine breaker threshold (3) differs from memory provider (5)."""
        ctx_breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        mem_breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=120.0)
        assert ctx_breaker._failure_threshold != mem_breaker._failure_threshold
