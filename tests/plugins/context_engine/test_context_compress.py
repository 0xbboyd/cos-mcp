"""
TestContextCompress — TST-03: compress() pipeline for both engines.

Tests CTX-04, HYD-01/02/03/04, MUN-01/02/03/04.
"""
from __future__ import annotations

import time
from copy import deepcopy

import pytest

from tests.plugins.context_engine.conftest import make_test_messages


# ============================================================================
# HydraDB compress()
# ============================================================================


class TestHydraDBCompress:
    """compress() tests for HydraDBContextEngine."""

    def test_compress_returns_shorter_list(self, hydra_engine):
        """compress() returns a message list that is shorter than input."""
        messages = make_test_messages(count=20)
        original_len = len(messages)
        result = hydra_engine.compress(messages)
        assert isinstance(result, list)
        assert len(result) < original_len, (
            f"Expected {len(result)} < {original_len}"
        )

    def test_compress_preserves_system_prompt(self, hydra_engine):
        """compress() preserves the system message at position 0."""
        messages = make_test_messages(count=20, include_system=True)
        system_msg = messages[0]
        result = hydra_engine.compress(messages)
        if result[0].get("role") == "system":
            assert result[0] == system_msg

    def test_compress_preserves_head_tail(self, hydra_engine):
        """compress() preserves protect_first_n head and protect_last_n tail."""
        hydra_engine.protect_first_n = 3
        hydra_engine.protect_last_n = 4
        messages = make_test_messages(count=20, include_system=True)
        # Head is messages[1:4], tail is messages[-4:]
        head = messages[1:4]
        tail = messages[-4:]
        result = hydra_engine.compress(messages)
        # Head should be early in result (after system)
        assert result[1:4] == head, f"Head mismatch: {result[1:4]} != {head}"
        # Tail should be at the end
        assert result[-4:] == tail, f"Tail mismatch: {result[-4:]} != {tail}"

    def test_compress_increments_compression_count(self, hydra_engine):
        """compress() increments compression_count after successful compression."""
        messages = make_test_messages(count=20)
        assert hydra_engine.compression_count == 0
        hydra_engine.compress(messages)
        assert hydra_engine.compression_count == 1
        hydra_engine.compress(messages)
        assert hydra_engine.compression_count == 2

    def test_compress_no_mutation_of_input(self, hydra_engine):
        """compress() does not mutate the original input message list."""
        messages = make_test_messages(count=20)
        original = deepcopy(messages)
        result = hydra_engine.compress(messages)
        assert messages == original, "Input messages were mutated"
        assert result is not messages, "Result should be a new list"

    def test_compress_guard_too_few_messages(self, hydra_engine):
        """compress() returns input unchanged when messages < 2."""
        messages = make_test_messages(count=1, include_system=True)
        result = hydra_engine.compress(messages)
        assert result is messages

    def test_compress_guard_window_too_small(self, hydra_engine):
        """compress() returns input when protect windows leave no compression room."""
        hydra_engine.protect_first_n = 5
        hydra_engine.protect_last_n = 5
        messages = make_test_messages(count=10, include_system=True)
        result = hydra_engine.compress(messages)
        # protect_first_n=5 after system=1 + protect_last_n=5 → window_start >= window_end
        assert result is messages

    def test_compress_hard_guard_not_shorter(self, hydra_engine):
        """compress() returns input unchanged when output isn't actually shorter."""
        messages = make_test_messages(count=5, include_system=False)
        # With protect_first_n=3, protect_last_n=6 on 5 messages, window won't build
        result = hydra_engine.compress(messages)
        assert result is messages

    def test_compress_summary_block_has_ctx_id(self, hydra_engine):
        """compress() summary block includes [ctx-id: ...] anchor."""
        messages = make_test_messages(count=20)
        result = hydra_engine.compress(messages)
        # Find the summary message (role="system" but not the original system prompt)
        summary_msgs = [m for m in result if m.get("role") == "system"]
        # The summary should be one of them
        found_ctx_id = False
        for msg in summary_msgs:
            content = msg.get("content", "")
            if "[ctx-id:" in content:
                found_ctx_id = True
                # Check it has entity type sections
                assert any(tag in content for tag in
                           ["## Topics", "## Decisions", "## Facts", "## Relationships"])
                break
        assert found_ctx_id, "No summary message with [ctx-id:] found"

    def test_compress_entity_storage_fire_and_forget(self, hydra_engine, fake_hydra_client):
        """compress() spawns entity storage thread that calls client.context.ingest()."""
        messages = make_test_messages(count=20)
        hydra_engine.compress(messages)
        # Wait for daemon thread
        if hydra_engine._entity_thread:
            hydra_engine._entity_thread.join(timeout=2)
        assert len(fake_hydra_client.context.ingest_calls) >= 1, (
            "Expected at least 1 ingest call for entity storage"
        )
        call = fake_hydra_client.context.ingest_calls[0]
        assert call["type"] == "context"
        assert call["upsert"] == "true"

    def test_compress_entity_storage_breaker_open(self, hydra_engine, fake_hydra_client, monkeypatch):
        """compress() skips entity storage when write breaker is open."""
        from cos_mcp.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        # Force breaker open
        for _ in range(3):
            breaker.record_write_failure()
        monkeypatch.setattr(hydra_engine, "_breaker", breaker)
        hydra_engine._breaker = breaker

        messages = make_test_messages(count=20)
        fake_hydra_client.context.ingest_calls.clear()
        hydra_engine.compress(messages)
        # Daemon thread checks breaker too; since breaker is open, no ingest
        if hydra_engine._entity_thread:
            hydra_engine._entity_thread.join(timeout=2)
        assert len(fake_hydra_client.context.ingest_calls) == 0


# ============================================================================
# MuninnDB compress()
# ============================================================================


class TestMuninnDBCompress:
    """compress() tests for MuninnDBContextEngine."""

    def test_compress_returns_shorter_list(self, muninn_engine):
        """compress() returns a message list that is shorter than input."""
        messages = make_test_messages(count=20)
        original_len = len(messages)
        result = muninn_engine.compress(messages)
        assert isinstance(result, list)
        assert len(result) < original_len

    def test_compress_preserves_system_prompt(self, muninn_engine):
        """compress() preserves the system message at position 0."""
        messages = make_test_messages(count=20, include_system=True)
        system_msg = messages[0]
        result = muninn_engine.compress(messages)
        if result[0].get("role") == "system":
            assert result[0] == system_msg

    def test_compress_preserves_head_tail(self, muninn_engine):
        """compress() preserves protect_first_n head and protect_last_n tail."""
        muninn_engine.protect_first_n = 3
        muninn_engine.protect_last_n = 4
        messages = make_test_messages(count=20, include_system=True)
        head = messages[1:4]
        tail = messages[-4:]
        result = muninn_engine.compress(messages)
        assert result[1:4] == head
        assert result[-4:] == tail

    def test_compress_increments_compression_count(self, muninn_engine):
        """compress() increments compression_count after successful compression."""
        messages = make_test_messages(count=20)
        assert muninn_engine.compression_count == 0
        muninn_engine.compress(messages)
        assert muninn_engine.compression_count == 1
        muninn_engine.compress(messages)
        assert muninn_engine.compression_count == 2

    def test_compress_no_mutation_of_input(self, muninn_engine):
        """compress() does not mutate the original input message list."""
        messages = make_test_messages(count=20)
        original = deepcopy(messages)
        result = muninn_engine.compress(messages)
        assert messages == original
        assert result is not messages

    def test_compress_guard_too_few_messages(self, muninn_engine):
        """compress() returns input unchanged when messages < 2."""
        messages = make_test_messages(count=1, include_system=True)
        result = muninn_engine.compress(messages)
        assert result is messages

    def test_compress_summary_block_has_ctx_id(self, muninn_engine):
        """compress() summary block includes [ctx-id: ...] anchor."""
        messages = make_test_messages(count=20)
        result = muninn_engine.compress(messages)
        summary_msgs = [m for m in result if m.get("role") == "system"]
        found_ctx_id = False
        for msg in summary_msgs:
            content = msg.get("content", "")
            if "[ctx-id:" in content:
                found_ctx_id = True
                assert any(tag in content for tag in
                           ["## Topics", "## Decisions", "## Facts", "## Relationships"])
                break
        assert found_ctx_id, "No summary message with [ctx-id:] found"

    def test_compress_synchronous_engram_storage(self, muninn_engine, fake_muninn_backend):
        """compress() stores entities synchronously via backend.ingest()."""
        messages = make_test_messages(count=20)
        fake_muninn_backend.ingest_calls.clear()
        muninn_engine.compress(messages)
        assert len(fake_muninn_backend.ingest_calls) >= 1, (
            "Expected at least 1 ingest call for synchronous engram storage"
        )
        call = fake_muninn_backend.ingest_calls[0]
        assert call["memory_type_label"] == "context"

    def test_compress_entity_storage_breaker_open(self, muninn_engine, fake_muninn_backend, monkeypatch):
        """compress() skips entity storage when write breaker is open."""
        from cos_mcp.circuit_breaker import CircuitBreaker
        breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=120.0)
        for _ in range(3):
            breaker.record_write_failure()
        monkeypatch.setattr(muninn_engine, "_breaker", breaker)

        messages = make_test_messages(count=20)
        fake_muninn_backend.ingest_calls.clear()
        muninn_engine.compress(messages)
        assert len(fake_muninn_backend.ingest_calls) == 0
