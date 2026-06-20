"""Base context engine with shared infrastructure.

Encapsulates circuit breaker, token tracking, should_compress() default,
config loading, and session lifecycle stubs. Subclasses provide entity
extraction, compress() assembly, tool schemas, and handlers.

Mirrors the ``BaseMemoryProvider`` pattern — thick shared base, thin plugins.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

from agent.context_engine import ContextEngine

from cos_mcp.circuit_breaker import CircuitBreaker
from cos_mcp.backends.base import MemoryBackend
from cos_mcp.formatting.context_base import ContextFormatter

logger = logging.getLogger(__name__)


class BaseContextEngine(ContextEngine):
    """Shared infrastructure for context engine plugins.

    Subclasses must set:
        - ``name`` (class attr, e.g. ``"hydradb-context"``)
        - ``_create_backend()`` → MemoryBackend
        - ``_create_formatter()`` → ContextFormatter

    Optionally override:
        - ``is_available()`` → bool
        - ``compress()`` → list of messages
        - ``get_tool_schemas()`` → list of OpenAI function-calling schemas
        - ``handle_tool_call()`` → str
        - ``on_session_start()``, ``on_session_end()``
    """

    name: str = ""  # Set by subclass (CTX-01)

    # ------------------------------------------------------------------
    # ABC class attributes (CTX-05)
    # ------------------------------------------------------------------

    last_prompt_tokens: int = 0
    last_completion_tokens: int = 0
    last_total_tokens: int = 0
    threshold_tokens: int = 0
    context_length: int = 0
    compression_count: int = 0

    # ------------------------------------------------------------------
    # Tunable parameters (CTX-06)
    # ------------------------------------------------------------------

    threshold_percent: float = 0.75
    protect_first_n: int = 3
    protect_last_n: int = 6

    # ------------------------------------------------------------------
    # Cache token tracking (Pitfall 10 — separate from prompt tokens)
    # ------------------------------------------------------------------

    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self, session_id: str, **kwargs) -> None:
        """Load config, create backend/formatter, set up circuit breaker.

        Args:
            session_id: Hermes session identifier.
            **kwargs: May include hermes_home, agent_context, agent_identity,
                      model, platform.
        """
        self._hermes_home: str = kwargs.get("hermes_home", "")
        self._agent_context: str = kwargs.get("agent_context", "primary")
        self._user_name: str = kwargs.get("agent_identity", "User")

        # Subclass hooks — create backend and formatter
        self._backend: MemoryBackend = self._create_backend(kwargs)
        self._formatter: ContextFormatter = self._create_formatter()

        # Circuit breaker — independent per engine, lower threshold (CFG-06)
        self._breaker = CircuitBreaker(
            failure_threshold=3, cooldown_seconds=120.0
        )
        self._breaker.set_label(self.name)

        # Thread tracking (Pitfall 7 — fire-and-forget safety)
        self._entity_thread: Optional[threading.Thread] = None
        self._session_thread: Optional[threading.Thread] = None

        logger.info(
            "%s context engine initialized (agent=%s)",
            self.name,
            self._agent_context,
        )

    def _create_backend(self, kwargs: dict) -> MemoryBackend:
        """Create the backend instance. Override in subclass.

        Subclass returns HydraDBBackend or MuninnDBBackend.
        """
        raise NotImplementedError

    def _create_formatter(self) -> ContextFormatter:
        """Create the formatter instance. Override in subclass.

        Subclass returns HydraDBContextFormatter or MuninnDBContextFormatter.
        """
        raise NotImplementedError

    @classmethod
    def is_available(cls) -> bool:
        """Check if this engine can be used. Override in subclass."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Token tracking — dual-format usage dict handling (CTX-02, Pitfall 10)
    # ------------------------------------------------------------------

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """Update tracked token usage from an API response.

        Prefers canonical fields (``input_tokens`` / ``output_tokens``)
        when available. Falls back to legacy keys (``prompt_tokens`` /
        ``completion_tokens``) for older hosts.

        Cache tokens (``cache_read_tokens``, ``cache_write_tokens``) are
        tracked separately and never counted toward prompt tokens.
        Reasoning tokens are ignored (not tracked).
        """
        # Prefer canonical fields, fall back to legacy
        self.last_prompt_tokens = (
            usage.get("input_tokens")
            or usage.get("prompt_tokens", 0)
        )
        self.last_completion_tokens = (
            usage.get("output_tokens")
            or usage.get("completion_tokens", 0)
        )

        # Cache tokens tracked separately — NOT added to prompt count
        self.cache_read_tokens = usage.get("cache_read_tokens", 0)
        self.cache_write_tokens = usage.get("cache_write_tokens", 0)

        # Total excludes cache and reasoning tokens
        self.last_total_tokens = (
            self.last_prompt_tokens + self.last_completion_tokens
        )

        # Recalculate threshold from current context_length
        if self.context_length:
            self.threshold_tokens = int(
                self.context_length * self.threshold_percent
            )

    # ------------------------------------------------------------------
    # Compression gate (CTX-03, Pitfall 11)
    # ------------------------------------------------------------------

    def should_compress(self, prompt_tokens: int = None) -> bool:
        """Return True if compaction should fire this turn.

        Uses ``last_prompt_tokens`` unless an explicit ``prompt_tokens``
        override is provided.
        """
        tokens = (
            prompt_tokens
            if prompt_tokens is not None
            else self.last_prompt_tokens
        )
        return tokens >= self.threshold_tokens

    # ------------------------------------------------------------------
    # Model switch (CTX-08)
    # ------------------------------------------------------------------

    def update_model(
        self,
        model: str,
        context_length: int,
        base_url: str = "",
        api_key: str = "",
        provider: str = "",
        api_mode: str = "",
    ) -> None:
        """Called when the user switches models or on fallback activation.

        Updates context_length and recalculates threshold_tokens from
        threshold_percent.
        """
        self.context_length = context_length
        self.threshold_tokens = int(context_length * self.threshold_percent)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Called when a new conversation session begins.

        Default no-op. Subclasses override for backend verification
        and tenant provisioning.
        """

    def on_session_end(
        self, session_id: str, messages: List[Dict[str, Any]]
    ) -> None:
        """Called at real session boundaries (CLI exit, /reset, expiry).

        Default no-op. Subclasses override for thread flushing.
        """

    def on_session_reset(self) -> None:
        """Reset per-session state (CTX-07).

        Zeroes token counters and compression_count.
        """
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self.compression_count = 0

    def shutdown(self) -> None:
        """Join background threads, release backend resources.

        Joins tracked threads (``_entity_thread``, ``_session_thread``)
        with a 30-second timeout, then calls ``self._backend.shutdown()``.
        """
        for thread in (
            self._entity_thread,
            self._session_thread,
        ):
            if thread and thread.is_alive():
                thread.join(timeout=30.0)
        self._backend.shutdown()

    # ------------------------------------------------------------------
    # compress() — NOT implemented here; delegated to subclasses
    # ------------------------------------------------------------------

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> List[Dict[str, Any]]:
        """Compact the message list and return the (shorter) list.

        Subclasses MUST override this. The base class raises
        NotImplementedError to force explicit implementation.
        """
        raise NotImplementedError(
            "compress() must be implemented by the context engine plugin"
        )

    # ------------------------------------------------------------------
    # Tools stubs
    # ------------------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas this engine provides to the agent.

        Default returns empty list (no tools). Subclasses override for
        context_search, context_expand, etc.
        """
        return []

    def handle_tool_call(
        self, name: str, args: Dict[str, Any], **kwargs
    ) -> str:
        """Handle a tool call from the agent.

        Returns a JSON error string for unknown tools. Subclasses
        override with tool dispatch logic.
        """
        return json.dumps({"error": f"Unknown context engine tool: {name}"})

    # ------------------------------------------------------------------
    # Config loading helpers (CFG-01, CFG-02, CFG-03)
    # ------------------------------------------------------------------

    @staticmethod
    def _load_config_file(
        hermes_home: str, config_name: str, defaults: dict
    ) -> dict:
        """Load non-secret config from ``{hermes_home}/{config_name}.json``.

        Args:
            hermes_home: Root directory (from ``initialize()`` kwarg).
            config_name: Base name of the config file (e.g. ``"hydradb-context"``).
            defaults: Config dict to merge JSON overrides into.

        Returns:
            Merged config dict (defaults + JSON overrides).

        Never hardcodes ``~/.hermes`` — all paths derived from ``hermes_home``.
        """
        cfg = dict(defaults)
        if hermes_home:
            config_path = os.path.join(hermes_home, f"{config_name}.json")
            if os.path.isfile(config_path):
                try:
                    with open(config_path) as f:
                        overrides = json.load(f)
                    cfg.update(overrides)
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(
                        "Failed to load %s: %s", config_path, e
                    )
        return cfg

    # ------------------------------------------------------------------
    # Shared daemon thread spawner (Pitfall 7)
    # ------------------------------------------------------------------

    @staticmethod
    def _spawn_daemon(
        target: Any, name: str
    ) -> threading.Thread:
        """Spawn a daemon thread for fire-and-forget operations.

        Args:
            target: Callable to run in the thread.
            name: Thread name (e.g. ``"hydradb-context-entity"``).

        Returns:
            The started thread (caller should track for shutdown join).
        """
        t = threading.Thread(target=target, daemon=True, name=name)
        t.start()
        return t
