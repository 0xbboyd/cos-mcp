"""Base memory provider with shared infrastructure.

Encapsulates circuit breaker, background threading, config loading,
and the read/write path patterns shared by all cos-mcp providers.
Subclasses provide backend, formatter, tool schemas, and handlers.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

from cos_mcp.circuit_breaker import CircuitBreaker
from cos_mcp.backends.base import MemoryBackend
from cos_mcp.formatting.base import MemoryFormatter

logger = logging.getLogger(__name__)


class BaseMemoryProvider(MemoryProvider):
    """Shared infrastructure for memory provider plugins.

    Subclasses must set:
        - ``name`` (class attr, e.g. ``\"hydradb\"``)
        - ``_create_backend()`` → MemoryBackend
        - ``_create_formatter()`` → MemoryFormatter
        - ``get_tool_schemas()`` → list of OpenAI function-calling schemas
        - ``handle_tool_call(tool_name, args, **kwargs)`` → str
        - ``system_prompt_block()`` → str
        - ``is_available()`` → bool
    """

    name: str = ""  # Set by subclass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Check if this provider can be used. Override in subclass."""
        raise NotImplementedError

    def initialize(self, session_id: str, **kwargs) -> None:
        """Load config, create backend/formatter, set up threading."""
        self._hermes_home: str = kwargs.get("hermes_home", "")
        self._agent_context: str = kwargs.get("agent_context", "primary")
        self._user_name: str = kwargs.get("agent_identity", "User")

        # Subclass hooks
        self._backend: MemoryBackend = self._create_backend(kwargs)
        self._formatter: MemoryFormatter = self._create_formatter()

        # Circuit breaker
        self._breaker = CircuitBreaker()
        self._breaker.set_label(self.name)

        # Threading primitives
        self._prefetch_result: str = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._mirror_thread: Optional[threading.Thread] = None

        # Tenant / vault provisioning
        self._backend.provision()

        logger.info(
            "%s provider initialized (agent=%s)",
            self.name,
            self._agent_context,
        )

    def _create_backend(self, kwargs: dict) -> MemoryBackend:
        """Create the backend instance. Override in subclass."""
        raise NotImplementedError

    def _create_formatter(self) -> MemoryFormatter:
        """Create the formatter instance. Override in subclass."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Config (subclasses may override)
    # ------------------------------------------------------------------

    @staticmethod
    def get_config_schema() -> list:
        """Field descriptors for ``hermes memory setup``. Override in subclass."""
        raise NotImplementedError

    @staticmethod
    def save_config(values: dict, hermes_home: str) -> None:
        """Write non-secret config to disk. Override in subclass."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    @staticmethod
    def system_prompt_block() -> str:
        """Static text injected into the system prompt. Override in subclass."""
        raise NotImplementedError

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return the cached result from the last ``queue_prefetch()``."""
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## {self.name.title()} Memory\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Fire a background memory query for the upcoming turn."""
        if self._breaker.is_read_open():
            return

        def _run() -> None:
            try:
                result = self._backend.query(query_text=query)
                context_str = self._formatter.format(result)
                if context_str and context_str.strip():
                    with self._prefetch_lock:
                        self._prefetch_result = context_str
                self._breaker.record_read_success()
            except Exception:
                self._breaker.record_read_failure()
                logger.debug(
                    "%s prefetch failed", self.name, exc_info=True
                )

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name=f"{self.name}-prefetch"
        )
        self._prefetch_thread.start()

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list | None = None,
    ) -> None:
        """Fire-and-forget ingest of the user+assistant turn pair."""
        if self._agent_context != "primary":
            return
        if self._breaker.is_write_open():
            return

        def _sync() -> None:
            try:
                text = f"User: {user_content}\nAssistant: {assistant_content}"
                self._backend.ingest(
                    text=text,
                    infer=True,
                    user_name=self._user_name,
                )
                self._breaker.record_write_success()
            except Exception:
                self._breaker.record_write_failure()
                logger.debug(
                    "%s sync_turn failed", self.name, exc_info=True
                )

        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name=f"{self.name}-sync"
        )
        self._sync_thread.start()

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Mirror built-in memory writes into the backend."""
        if self._breaker.is_write_open():
            return

        def _write() -> None:
            try:
                if action == "remove":
                    # Generate stable content-hash ID for deterministic delete
                    # Subclasses with hash-based IDs can override _make_mirror_id
                    memory_id = self._make_mirror_id(target, content)
                    self._backend.delete(memory_id)
                else:
                    memory_id = self._make_mirror_id(target, content)
                    self._backend.ingest(
                        text=f"[{target}] {content}",
                        infer=False,
                        user_name=self._user_name,
                        metadata={"target": target, "source": "builtin_mirror"},
                        memory_id=memory_id,
                    )
                self._breaker.record_write_success()
            except Exception:
                self._breaker.record_write_failure()
                logger.debug(
                    "%s on_memory_write failed", self.name, exc_info=True
                )

        self._mirror_thread = threading.Thread(
            target=_write, daemon=True, name=f"{self.name}-mirror"
        )
        self._mirror_thread.start()

    def _make_mirror_id(self, target: str, content: str) -> str:
        """Generate a stable ID for built-in memory mirroring.

        Override in subclasses that use backend-specific ID generation.
        Default uses a simple prefix-based ID.
        """
        import hashlib

        return (
            f"hermes_{target}_"
            f"{hashlib.sha256(content.encode()).hexdigest()[:16]}"
        )

    # ------------------------------------------------------------------
    # Session hooks
    # ------------------------------------------------------------------

    def on_session_end(self, messages: list) -> None:
        """Ingest session summary as episodic memory."""
        if self._agent_context != "primary":
            return
        if self._breaker.is_write_open():
            return

        def _summary() -> None:
            try:
                tail = [
                    m
                    for m in messages[-20:]
                    if m.get("role") in ("user", "assistant")
                ]
                text = "\n".join(
                    f"{m['role'].title()}: {m.get('content', '')}"
                    for m in tail[-10:]
                )
                if not text.strip():
                    return
                self._backend.ingest(
                    text=text,
                    infer=True,
                    user_name=self._user_name,
                )
                self._breaker.record_write_success()
            except Exception:
                self._breaker.record_write_failure()
                logger.debug(
                    "%s on_session_end failed", self.name, exc_info=True
                )

        t = threading.Thread(target=_summary, daemon=True)
        t.start()

    def shutdown(self) -> None:
        """Join background threads, release backend resources."""
        for thread in (
            self._prefetch_thread,
            self._sync_thread,
            self._mirror_thread,
        ):
            if thread and thread.is_alive():
                thread.join(timeout=5.0)
        self._backend.shutdown()

    # ------------------------------------------------------------------
    # Tools (subclasses implement schemas + dispatch)
    # ------------------------------------------------------------------

    @staticmethod
    def get_tool_schemas() -> list:
        """Return OpenAI function-calling schemas. Override in subclass."""
        raise NotImplementedError

    def handle_tool_call(
        self, tool_name: str, args: dict, **kwargs
    ) -> str:
        """Dispatch a tool call. Override in subclass."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared tool helpers
    # ------------------------------------------------------------------

    def _tool_search_impl(
        self,
        args: dict,
        max_results: int = 5,
        min_score: float = 0.2,
        query_mode: str = "fast",
    ) -> str:
        """Shared search implementation for tool handlers."""
        if self._breaker.is_read_open():
            return json.dumps(
                {"error": f"{self.name} read circuit breaker is open"}
            )
        try:
            result = self._backend.query(
                query_text=args["query"],
                max_results=max_results,
                query_mode=query_mode,
            )
            context = self._formatter.format(result, min_score=min_score)
            self._breaker.record_read_success()
            return json.dumps(
                {"result": context or "No relevant memories found."}
            )
        except Exception as e:
            self._breaker.record_read_failure()
            logger.debug(
                "%s tool search failed", self.name, exc_info=True
            )
            return json.dumps({"error": str(e)})

    def _tool_conclude_impl(self, args: dict) -> str:
        """Shared conclude/remember implementation for tool handlers."""
        if self._breaker.is_write_open():
            return json.dumps(
                {"error": f"{self.name} write circuit breaker is open"}
            )
        try:
            self._backend.ingest(
                text=args["fact"],
                infer=False,
                user_name=self._user_name,
            )
            self._breaker.record_write_success()
            return json.dumps({"result": "Fact stored."})
        except Exception as e:
            self._breaker.record_write_failure()
            logger.debug(
                "%s tool conclude failed", self.name, exc_info=True
            )
            return json.dumps({"error": str(e)})
