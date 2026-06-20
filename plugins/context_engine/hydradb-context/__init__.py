"""HydraDB context engine — graph-backed context compression and retrieval.

Phase 5 skeleton. Full compress() pipeline, entity extraction, and tools
implemented in Phase 6.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from cos_mcp.base_context_engine import BaseContextEngine
from cos_mcp.backends.base import MemoryBackend
from cos_mcp.formatting.context_base import ContextFormatter

logger = logging.getLogger(__name__)


class HydraDBContextEngine(BaseContextEngine):
    """HydraDB graph-backed context engine.

    Replaces lossy LLM summarization with persistent DAG-compressed
    context stored in HydraDB's graph database. Entities are extracted
    via heuristics, stored as graph nodes, and retrieved via
    context_search and context_expand tools.

    Phase 6 fills in:
        - compress() pipeline (entity extraction → storage → window trim)
        - _extract_entities() heuristics
        - _create_backend(), _create_formatter() real implementations
        - context_search / context_expand tool schemas + handlers
    """

    name = "hydradb-context"

    # --- Lifecycle ----------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Check credentials and SDK import — no network calls."""
        if not os.environ.get("HYDRA_DB_API_KEY"):
            return False
        try:
            import hydra_db  # noqa: F401
            return True
        except ImportError:
            return False

    def _create_backend(self, kwargs: dict) -> MemoryBackend:
        """Phase 6 stub — returns HydraDBBackend with api_key, tenant_id,
        sub_tenant_id from config."""
        raise NotImplementedError(
            "Phase 6: returns HydraDBBackend with api_key, tenant_id, "
            "sub_tenant_id from config"
        )

    def _create_formatter(self) -> ContextFormatter:
        """Phase 6 stub — returns HydraDBContextFormatter()."""
        raise NotImplementedError("Phase 6: returns HydraDBContextFormatter()")

    # --- Config (CFG-01, CFG-02, CFG-03) -----------------------------------

    def _load_config(self) -> dict:
        """Load API key from env, non-secret config from hydradb-context.json.

        API key: ``os.environ["HYDRA_DB_API_KEY"]`` (CFG-01).
        Non-secret: ``{hermes_home}/hydradb-context.json`` (CFG-02, CFG-03).
        """
        # API key from env — never stored in JSON
        api_key = os.environ.get("HYDRA_DB_API_KEY", "")

        defaults = {
            "api_key": api_key,
            "query_mode": "thinking",
            "max_results": 10,
            "threshold_percent": 0.75,
            "protect_first_n": 3,
            "protect_last_n": 6,
        }

        cfg = self._load_config_file(
            self._hermes_home, self.name, defaults
        )

        # Apply threshold values to instance attributes
        if "threshold_percent" in cfg:
            self.threshold_percent = float(cfg["threshold_percent"])
        if "protect_first_n" in cfg:
            self.protect_first_n = int(cfg["protect_first_n"])
        if "protect_last_n" in cfg:
            self.protect_last_n = int(cfg["protect_last_n"])

        return cfg


# ---------------------------------------------------------------------------
# Plugin entry point (REG-01)
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register this context engine with the Hermes Agent plugin system."""
    ctx.register_context_engine(HydraDBContextEngine())
