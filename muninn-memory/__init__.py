"""MuninnDB Memory Provider for Hermes Agent.

A local cognitive memory provider backed by MuninnDB — a neuroscience-inspired
database with ACT-R temporal scoring, Hebbian co-activation learning, Bayesian
confidence, predictive activation (PAS), and 16 typed relationship types — all
engine-native.

Thin provider — all shared infrastructure (circuit breaker, threading,
config loading) lives in ``cos_mcp``. This module defines MuninnDB-specific
backend, formatter, tool schemas, and handlers.

Architecture: ~/.hermes/hermes-agent/plugins/memory/muninn/
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from cos_mcp.base_provider import BaseMemoryProvider
from cos_mcp.backends.muninn import MuninnDBBackend
from cos_mcp.formatting.muninn import MuninnDBFormatter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "base_url": "http://127.0.0.1:8475",
    "vault": "default",
    "api_key": "",
}


def _load_config(hermes_home: str = "") -> dict:
    """Read MUNINN_API_KEY from env, merge overrides from muninn.json."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["api_key"] = os.environ.get("MUNINN_API_KEY", "")

    home = hermes_home or os.environ.get("HERMES_HOME", "")
    if home:
        muninn_json = os.path.join(home, "muninn.json")
        if os.path.isfile(muninn_json):
            try:
                with open(muninn_json) as f:
                    overrides = json.load(f)
                cfg.update(overrides)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load muninn.json: %s", e)

    return cfg


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "name": "muninn_search",
    "description": (
        "Search MuninnDB memory for relevant facts, preferences, decisions, "
        "and past context. MuninnDB uses ACT-R temporal scoring (frequently "
        "accessed memories surface; stale ones fade), Hebbian co-activation "
        "(related memories emerge automatically), and Bayesian confidence "
        "(contradicted memories rank lower)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in memory.",
            },
            "memory_type": {
                "type": "string",
                "description": (
                    "Optional filter by memory type. One of: fact, decision, "
                    "preference, observation, issue, task, procedure, event, "
                    "goal, constraint, identity, reference."
                ),
                "enum": [
                    "fact", "decision", "preference", "observation",
                    "issue", "task", "procedure", "event",
                    "goal", "constraint", "identity", "reference",
                ],
            },
            "min_confidence": {
                "type": "number",
                "description": (
                    "Minimum confidence threshold (0.0-1.0). Lower values "
                    "include uncertain/contradicted memories. Default 0.5."
                ),
            },
        },
        "required": ["query"],
    },
}

PROFILE_SCHEMA = {
    "name": "muninn_profile",
    "description": (
        "Retrieve the user profile from MuninnDB memory — preferences, "
        "identity, and stable facts about the user."
    ),
    "parameters": {"type": "object", "properties": {}},
}

REMEMBER_SCHEMA = {
    "name": "muninn_remember",
    "description": (
        "Store a durable fact, decision, or preference in MuninnDB memory. "
        "MuninnDB automatically classifies the memory type, links it to "
        "related memories via overlapping tags, and runs contradiction "
        "detection against existing memories."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "concept": {
                "type": "string",
                "description": "Short label for the memory (e.g. 'prefers concise responses').",
            },
            "content": {
                "type": "string",
                "description": "The full fact, decision, or preference to store.",
            },
            "memory_type": {
                "type": "string",
                "description": (
                    "Optional classification. One of: fact, decision, "
                    "preference, observation, issue, task, procedure, "
                    "event, goal, constraint, identity, reference."
                ),
                "enum": [
                    "fact", "decision", "preference", "observation",
                    "issue", "task", "procedure", "event",
                    "goal", "constraint", "identity", "reference",
                ],
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional topic tags for auto-association.",
            },
        },
        "required": ["concept", "content"],
    },
}


# ---------------------------------------------------------------------------
# MuninnDBMemoryProvider
# ---------------------------------------------------------------------------


class MuninnDBMemoryProvider(BaseMemoryProvider):
    """MuninnDB-backed cognitive memory provider.

    Uses MuninnDB's local cognitive engine for memory storage and retrieval.
    All cognitive primitives — ACT-R temporal scoring, Hebbian learning,
    Bayesian confidence, PAS — are engine-native. The plugin is a thin
    HTTP adapter to the REST API.

    Credentials:
        MUNINN_API_KEY in ~/.hermes/.env (optional for default vault)
    Non-secret config:
        ~/.hermes/muninn.json  (base_url, vault)
    """

    name = "muninn"
    heading_label = "MuninnDB"

    # --- Lifecycle ----------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Check that requests is importable — no network call."""
        try:
            import requests  # noqa: F401
            return True
        except ImportError:
            return False

    def _create_backend(self, kwargs: dict) -> MuninnDBBackend:
        """Create MuninnDB backend from config."""
        cfg = _load_config(self._hermes_home)

        return MuninnDBBackend(
            base_url=cfg["base_url"],
            vault=cfg["vault"],
            api_key=cfg["api_key"],
            max_results=10,
        )

    def _create_formatter(self) -> MuninnDBFormatter:
        """Create MuninnDB activation formatter."""
        return MuninnDBFormatter()

    # --- Config -------------------------------------------------------------

    @staticmethod
    def get_config_schema() -> list:
        """Field descriptors for ``hermes memory setup``."""
        return [
            {
                "key": "api_key",
                "description": "MuninnDB API key (mk_...). Optional for default vault.",
                "secret": True,
                "required": False,
                "env_var": "MUNINN_API_KEY",
            },
            {
                "key": "base_url",
                "description": "MuninnDB REST API base URL.",
                "default": "http://127.0.0.1:8475",
            },
            {
                "key": "vault",
                "description": (
                    "MuninnDB vault name for memory scoping. One vault per "
                    "profile for isolation. Default: 'default'."
                ),
                "default": "default",
            },
        ]

    @staticmethod
    def save_config(values: dict, hermes_home: str) -> None:
        """Write non-secret config to ``muninn.json``."""
        secrets = {"api_key"}
        safe = {k: v for k, v in values.items() if k not in secrets}
        path = os.path.join(hermes_home, "muninn.json")
        with open(path, "w") as f:
            json.dump(safe, f, indent=2)

    # --- System prompt ------------------------------------------------------

    @staticmethod
    def system_prompt_block() -> str:
        """Static text injected into the system prompt."""
        return (
            "MuninnDB Cognitive Memory. Active. Memories retrieved each turn. "
            "MuninnDB uses ACT-R temporal scoring (frequent access → stronger recall), "
            "Hebbian learning (co-activated memories auto-associate), and "
            "Bayesian confidence (contradicted memories are discounted). "
            "You can search memory with muninn_search, retrieve user profile "
            "with muninn_profile, and store facts with muninn_remember."
        )

    # --- Tools --------------------------------------------------------------

    @staticmethod
    def get_tool_schemas() -> list:
        """Return OpenAI function-calling schemas for memory tools."""
        return [SEARCH_SCHEMA, PROFILE_SCHEMA, REMEMBER_SCHEMA]

    def handle_tool_call(
        self, tool_name: str, args: dict, **kwargs
    ) -> str:
        """Dispatch a tool call and return a JSON-string result."""
        try:
            if tool_name == "muninn_search":
                return self._tool_search(args)
            elif tool_name == "muninn_profile":
                return self._tool_profile(args)
            elif tool_name == "muninn_remember":
                return self._tool_remember(args)
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            logger.warning("MuninnDB tool '%s' failed: %s", tool_name, e)
            return json.dumps({"error": str(e)})

    def _tool_search(self, args: dict) -> str:
        """Run an on-demand memory search."""
        if self._breaker.is_read_open():
            return json.dumps(
                {"error": "MuninnDB read circuit breaker is open"}
            )
        try:
            query = args.get("query", "")
            memory_type = args.get("memory_type")
            min_confidence = args.get("min_confidence")

            result = self._backend.query(
                query_text=query,
                max_results=8,
                memory_type=memory_type,
                min_confidence=min_confidence,
            )

            context = self._formatter.format(result)
            self._breaker.record_read_success()
            return json.dumps(
                {"result": context or "No relevant memories found."}
            )
        except Exception as e:
            self._breaker.record_read_failure()
            logger.debug("MuninnDB tool search failed", exc_info=True)
            return json.dumps({"error": str(e)})

    def _tool_profile(self, args: dict) -> str:
        """Retrieve user profile summary."""
        if self._breaker.is_read_open():
            return json.dumps(
                {"error": "MuninnDB read circuit breaker is open"}
            )
        try:
            # Search for preferences with low recency bias
            activations = self._backend.query(
                query_text="user profile identity preferences traits",
                max_results=8,
                memory_type="preference",
            )
            # Also pull identity memories
            identity = self._backend.query(
                query_text="who is the user",
                max_results=4,
                memory_type="identity",
            )

            all_items = activations + identity
            context = self._formatter.format(all_items)
            self._breaker.record_read_success()
            return json.dumps(
                {"result": context or "No profile data found."}
            )
        except Exception as e:
            self._breaker.record_read_failure()
            logger.debug("MuninnDB tool profile failed", exc_info=True)
            return json.dumps({"error": str(e)})

    def _tool_remember(self, args: dict) -> str:
        """Store a durable fact."""
        if self._breaker.is_write_open():
            return json.dumps(
                {"error": "MuninnDB write circuit breaker is open"}
            )
        try:
            concept = args.get("concept", "")
            content = args.get("content", "")
            memory_type = args.get("memory_type")
            tags = args.get("tags", [])

            # Ensure hermes-memory tag is present
            if "hermes-memory" not in tags:
                tags = list(tags) + ["hermes-memory"]

            self._backend.ingest(
                text=f"{concept}: {content}",
                infer=False,
                user_name=self._user_name,
                memory_type_label=memory_type,
                tags=tags,
            )
            self._breaker.record_write_success()
            return json.dumps({"result": "Memory stored."})
        except Exception as e:
            self._breaker.record_write_failure()
            logger.debug("MuninnDB tool remember failed", exc_info=True)
            return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register this provider with the Hermes Agent plugin system."""
    ctx.register_memory_provider(MuninnDBMemoryProvider())
