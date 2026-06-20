"""MuninnDB context engine — cognitive-backed context compression and retrieval.

Phase 5 skeleton. Full compress() pipeline, entity extraction (cognitive),
and tools implemented in Phase 7.
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


class MuninnDBContextEngine(BaseContextEngine):
    """MuninnDB cognitive context engine.

    Uses MuninnDB's neuroscience-inspired primitives for context
    compression and retrieval: ACT-R temporal decay (frequently
    accessed context stays strong; stale context fades), Hebbian
    co-activation (related context auto-associates), and Bayesian
    confidence (contradicted context is discounted).

    Phase 7 fills in:
        - compress() pipeline (entity extraction → engram storage → window trim)
        - _extract_entities() cognitive heuristics
        - _create_backend(), _create_formatter() real implementations
        - context_search / context_expand tool schemas + handlers
    """

    name = "muninn-context"

    # --- Lifecycle ----------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Check credentials and requests import — no network calls."""
        if not os.environ.get("MUNINN_API_KEY"):
            return False
        try:
            import requests  # noqa: F401
            return True
        except ImportError:
            return False

    def _create_backend(self, kwargs: dict) -> MemoryBackend:
        """Phase 7 stub — returns MuninnDBBackend with base_url, vault,
        api_key from config."""
        raise NotImplementedError(
            "Phase 7: returns MuninnDBBackend with base_url, vault, "
            "api_key from config"
        )

    def _create_formatter(self) -> ContextFormatter:
        """Phase 7 stub — returns MuninnDBContextFormatter()."""
        raise NotImplementedError(
            "Phase 7: returns MuninnDBContextFormatter()"
        )

    # --- Config (CFG-01, CFG-02, CFG-03) -----------------------------------

    def _load_config(self) -> dict:
        """Load API key from env, non-secret config from muninn-context.json.

        API key: ``os.environ["MUNINN_API_KEY"]`` (CFG-01).
        Non-secret: ``{hermes_home}/muninn-context.json`` (CFG-02, CFG-03).
        """
        # API key from env — never stored in JSON
        api_key = os.environ.get("MUNINN_API_KEY", "")

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
    ctx.register_context_engine(MuninnDBContextEngine())
