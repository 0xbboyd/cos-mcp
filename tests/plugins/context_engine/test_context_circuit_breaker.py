"""
TestContextCircuitBreaker — TST-06: breaker open/close, read/write independence,
per-engine isolation, and memory provider isolation.

Tests CFG-04, CFG-05, CFG-06.
"""
from __future__ import annotations

import json
import time

import pytest

from cos_mcp.circuit_breaker import CircuitBreaker


# ============================================================================
# CircuitBreaker unit tests (shared infra)
# ============================================================================


class TestCircuitBreakerUnit:
    """Unit tests for the CircuitBreaker class itself."""

    def test_opens_after_3_consecutive_read_failures(self):
        """Read circuit breaker opens after 3 consecutive failures (context engine threshold)."""
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        now = time.time()
        for _ in range(2):
            breaker.record_read_failure()
        assert not breaker.is_read_open()

        breaker.record_read_failure()
        assert breaker.is_read_open()
        assert breaker._read_open_until > now

    def test_opens_after_3_consecutive_write_failures(self):
        """Write circuit breaker opens after 3 consecutive failures."""
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        for _ in range(2):
            breaker.record_write_failure()
        assert not breaker.is_write_open()

        breaker.record_write_failure()
        assert breaker.is_write_open()

    def test_record_success_resets_failure_count(self):
        """record_*_success resets failure counter and closes breaker."""
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        for _ in range(2):
            breaker.record_read_failure()
        assert breaker._read_failures == 2

        breaker.record_read_success()
        assert breaker._read_failures == 0
        assert not breaker.is_read_open()

    def test_record_success_after_trip_resets(self):
        """record_*_success resets breaker even after it tripped."""
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        for _ in range(3):
            breaker.record_write_failure()
        assert breaker.is_write_open()

        breaker.record_write_success()
        assert not breaker.is_write_open()
        assert breaker._write_failures == 0

    def test_read_write_independence(self):
        """Read and write gauges are completely independent."""
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        # Trip read only
        for _ in range(3):
            breaker.record_read_failure()
        assert breaker.is_read_open()
        assert not breaker.is_write_open()
        assert breaker._write_failures == 0

    def test_cooldown_expires(self, monkeypatch):
        """Breaker re-closes after cooldown_seconds have elapsed."""
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)

        # Trip the breaker
        now = 1000.0
        monkeypatch.setattr(time, "time", lambda: now)
        for _ in range(3):
            breaker.record_read_failure()
        assert breaker.is_read_open()

        # Advance past cooldown
        monkeypatch.setattr(time, "time", lambda: now + 121.0)
        assert not breaker.is_read_open()

    def test_set_label(self):
        """set_label sets the internal label for logging."""
        breaker = CircuitBreaker(failure_threshold=3)
        breaker.set_label("hydradb-context")
        assert breaker._label == "hydradb-context"


# ============================================================================
# HydraDB circuit breaker
# ============================================================================


class TestHydraDBCircuitBreaker:
    """Circuit breaker integration tests for HydraDBContextEngine."""

    def test_breaker_independent_instance(self, hydra_engine):
        """HydraDB engine has its own independent circuit breaker instance."""
        assert hydra_engine._breaker is not None
        assert isinstance(hydra_engine._breaker, CircuitBreaker)
        assert hydra_engine._breaker._failure_threshold == 3
        assert hydra_engine._breaker._cooldown_seconds == 120.0

    def test_breaker_independent_from_memory_provider(self):
        """Context engine breaker is an independent instance from memory provider breakers."""
        breaker1 = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        breaker1.set_label("hydradb-context")
        breaker2 = CircuitBreaker(failure_threshold=5, cooldown_seconds=120.0)
        breaker2.set_label("hydradb-memory")

        # Trip the context breaker
        for _ in range(3):
            breaker1.record_read_failure()
        assert breaker1.is_read_open()
        assert not breaker2.is_read_open()

        # Trip the memory breaker
        for _ in range(5):
            breaker2.record_read_failure()
        assert breaker2.is_read_open()
        # Context breaker is still independently open
        assert breaker1.is_read_open()

    def test_write_breaker_blocks_compress_entity_storage(self, hydra_engine, fake_hydra_client, monkeypatch):
        """Write breaker open prevents entity storage during compress()."""
        from cos_mcp.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        for _ in range(3):
            breaker.record_write_failure()
        assert breaker.is_write_open()

        # Replace engine's breaker with our tripped one
        monkeypatch.setattr(hydra_engine, "_breaker", breaker)

        messages = [
            {"role": "user", "content": "We decided to use Python. The API server runs on port 8080."},
            {"role": "assistant", "content": "I chose Flask. The deployment depends on Docker."},
            {"role": "user", "content": "The implementation requires Python 3.12."},
            {"role": "assistant", "content": "The project structure is well organized."},
            {"role": "user", "content": "We need to consider scaling. PostgreSQL is the database."},
            {"role": "assistant", "content": "I'll set up Docker Compose. Redis for caching."},
            {"role": "user", "content": "What about monitoring? Prometheus is standard."},
            {"role": "assistant", "content": "Yes, Prometheus and Grafana. Good choice."},
        ]
        fake_hydra_client.context.ingest_calls.clear()
        hydra_engine.compress(messages)
        # Daemon thread should not be spawned, or if spawned, should skip
        if hydra_engine._entity_thread:
            hydra_engine._entity_thread.join(timeout=2)
        assert len(fake_hydra_client.context.ingest_calls) == 0

    def test_read_breaker_blocks_context_search(self, hydra_engine, fake_hydra_backend, monkeypatch):
        """Read breaker open blocks context_search tool."""
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

    def test_read_success_resets_write_count(self, hydra_engine):
        """Record read success does not affect write failure count (independence)."""
        breaker = hydra_engine._breaker
        for _ in range(2):
            breaker.record_write_failure()
        assert breaker._write_failures == 2

        breaker.record_read_success()
        assert breaker._write_failures == 2  # Unchanged
        assert breaker._read_failures == 0


# ============================================================================
# MuninnDB circuit breaker
# ============================================================================


class TestMuninnDBCircuitBreaker:
    """Circuit breaker integration tests for MuninnDBContextEngine."""

    def test_breaker_independent_instance(self, muninn_engine):
        """MuninnDB engine has its own independent circuit breaker instance."""
        assert muninn_engine._breaker is not None
        assert isinstance(muninn_engine._breaker, CircuitBreaker)
        assert muninn_engine._breaker._failure_threshold == 3

    def test_breaker_independent_from_hydra_breaker(self, hydra_engine, muninn_engine):
        """Each engine's breaker is independent from the other."""
        # Trip HydraDB breaker
        for _ in range(3):
            hydra_engine._breaker.record_read_failure()
        assert hydra_engine._breaker.is_read_open()

        # MuninnDB breaker should be unaffected
        assert not muninn_engine._breaker.is_read_open()
        assert muninn_engine._breaker._read_failures == 0

    def test_write_breaker_recovery(self, muninn_engine, fake_muninn_backend, monkeypatch):
        """Write failure recording opens breaker; success resets it."""
        from cos_mcp.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        monkeypatch.setattr(muninn_engine, "_breaker", breaker)

        # Simulate 2 failures, 1 success — breaker should not open
        for _ in range(2):
            breaker.record_write_failure()
        assert not breaker.is_write_open()

        breaker.record_write_success()
        assert breaker._write_failures == 0

        # Now 3 failures — breaker should open
        for _ in range(3):
            breaker.record_write_failure()
        assert breaker.is_write_open()

        # Success resets
        breaker.record_write_success()
        assert not breaker.is_write_open()
