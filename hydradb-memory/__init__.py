"""HydraDB Memory Provider for Hermes Agent.

A cloud-backed persistent memory provider that stores agent memories in
HydraDB and retrieves them each turn via hybrid search (semantic + BM25 +
graph traversal + recency scoring).

Thin provider — all shared infrastructure (circuit breaker, threading,
config loading) lives in ``cos_mcp``. This module defines HydraDB-specific
backend, formatter, tool schemas, and handlers.

Architecture: ~/.hermes/hermes-agent/plugins/memory/hydradb/
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from cos_mcp.base_provider import BaseMemoryProvider
from cos_mcp.backends.hydradb import HydraDBBackend
from cos_mcp.formatting.hydradb import HydraDBFormatter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "api_key": "",
    "tenant_id": "hermes",
    "sub_tenant_id": None,
    "query_mode": "thinking",
    "query_by": "hybrid",
    "max_results": 10,
}


def _load_config(hermes_home: str = "") -> dict:
    """Read HYDRA_DB_API_KEY from env, merge overrides from hydradb.json."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["api_key"] = os.environ.get("HYDRA_DB_API_KEY", "")

    home = hermes_home or os.environ.get("HERMES_HOME", "")
    if home:
        hydradb_json = os.path.join(home, "hydradb.json")
        if os.path.isfile(hydradb_json):
            try:
                with open(hydradb_json) as f:
                    overrides = json.load(f)
                cfg.update(overrides)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load hydradb.json: %s", e)

    return cfg


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "hydradb_search",
        "description": "Search HydraDB memory for relevant facts, preferences, and past context.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for in memory.",
                },
            },
            "required": ["query"],
        },
    },
}

PROFILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "hydradb_profile",
        "description": "Retrieve the user profile summary from HydraDB memory.",
        "parameters": {"type": "object", "properties": {}},
    },
}

CONCLUDE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "hydradb_conclude",
        "description": "Store a durable fact or conclusion in HydraDB memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "The fact or conclusion to store.",
                },
            },
            "required": ["fact"],
        },
    },
}


# ---------------------------------------------------------------------------
# HydraDBMemoryProvider
# ---------------------------------------------------------------------------


class HydraDBMemoryProvider(BaseMemoryProvider):
    """HydraDB-backed persistent memory provider.

    Uses HydraDB's cloud API for memory storage and hybrid retrieval
    (semantic + BM25 + graph). One tenant shared across all Hermes
    profiles; per-profile isolation via sub_tenant_id.

    Credentials:
        HYDRA_DB_API_KEY in ~/.hermes/.env
    Non-secret config:
        ~/.hermes/hydradb.json  (tenant_id, sub_tenant_id, query_mode)
    """

    name = "hydradb"

    # --- Lifecycle ----------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Check credentials and SDK import — no network calls."""
        if not os.environ.get("HYDRA_DB_API_KEY"):
            return False
        try:
            from hydra_db import HydraDB  # noqa: F401
            return True
        except ImportError:
            return False

    def _create_backend(self, kwargs: dict) -> HydraDBBackend:
        """Create HydraDB backend from config."""
        cfg = _load_config(self._hermes_home)

        return HydraDBBackend(
            api_key=cfg["api_key"],
            tenant_id=cfg["tenant_id"],
            sub_tenant_id=(
                cfg.get("sub_tenant_id")
                or kwargs.get("agent_identity", "")
                or "default"
            ),
            query_mode=cfg.get("query_mode", "thinking"),
            query_by=cfg.get("query_by", "hybrid"),
            max_results=cfg.get("max_results", 10),
        )

    def _create_formatter(self) -> HydraDBFormatter:
        """Create HydraDB chunk formatter."""
        return HydraDBFormatter()

    # --- Config -------------------------------------------------------------

    @staticmethod
    def get_config_schema() -> list:
        """Field descriptors for ``hermes memory setup``."""
        return [
            {
                "key": "api_key",
                "description": "HydraDB API key",
                "secret": True,
                "required": True,
                "env_var": "HYDRA_DB_API_KEY",
                "url": "https://app.hydradb.com",
            },
            {
                "key": "tenant_id",
                "description": (
                    "Tenant identifier (one per deployment, shared by all profiles)"
                ),
                "default": "hermes",
            },
            {
                "key": "sub_tenant_id",
                "description": (
                    "Sub-tenant for memory scoping. Leave empty to auto-use "
                    "the profile name (per-profile isolation). Set to 'shared' "
                    "for cross-profile memory."
                ),
                "default": "",
            },
            {
                "key": "query_mode",
                "description": (
                    "Query mode: 'thinking' (reranking + graph traversal) "
                    "or 'fast' (low latency)"
                ),
                "default": "thinking",
                "choices": ["thinking", "fast"],
            },
        ]

    @staticmethod
    def save_config(values: dict, hermes_home: str) -> None:
        """Write non-secret config to ``hydradb.json``."""
        secrets = {"api_key"}
        safe = {k: v for k, v in values.items() if k not in secrets}
        path = os.path.join(hermes_home, "hydradb.json")
        with open(path, "w") as f:
            json.dump(safe, f, indent=2)

    # --- System prompt ------------------------------------------------------

    @staticmethod
    def system_prompt_block() -> str:
        """Static text injected into the system prompt."""
        return "HydraDB Memory. Active. Memories are retrieved each turn."

    # --- Tools --------------------------------------------------------------

    @staticmethod
    def get_tool_schemas() -> list:
        """Return OpenAI function-calling schemas for memory tools."""
        return [SEARCH_SCHEMA, PROFILE_SCHEMA, CONCLUDE_SCHEMA]

    def handle_tool_call(
        self, tool_name: str, args: dict, **kwargs
    ) -> str:
        """Dispatch a tool call and return a JSON-string result."""
        try:
            if tool_name == "hydradb_search":
                return self._tool_search(args)
            elif tool_name == "hydradb_profile":
                return self._tool_profile(args)
            elif tool_name == "hydradb_conclude":
                return self._tool_conclude_impl(args)
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            logger.warning("HydraDB tool '%s' failed: %s", tool_name, e)
            return json.dumps({"error": str(e)})

    def _tool_search(self, args: dict) -> str:
        """Run an on-demand memory search."""
        return self._tool_search_impl(
            args, max_results=5, min_score=0.2, query_mode="fast"
        )

    def _tool_profile(self, args: dict) -> str:
        """Retrieve user profile summary."""
        if self._breaker.is_read_open():
            return json.dumps(
                {"error": "HydraDB read circuit breaker is open"}
            )
        try:
            result = self._backend.query(
                query_text="user profile preferences traits",
                max_results=5,
                query_mode="thinking",
                query_by="hybrid",
                graph_context=True,
            )
            context = self._formatter.format(result, min_score=0.2)
            self._breaker.record_read_success()
            return json.dumps(
                {"result": context or "No profile data found."}
            )
        except Exception as e:
            self._breaker.record_read_failure()
            logger.debug("HydraDB tool profile failed", exc_info=True)
            return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register this provider with the Hermes Agent plugin system."""
    ctx.register_memory_provider(HydraDBMemoryProvider())
