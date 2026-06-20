"""MuninnDB local cognitive backend implementation.

Wraps MuninnDB's REST API (port 8475) with a requests.Session.
All cognitive primitives — ACT-R temporal scoring, Hebbian learning,
Bayesian confidence, PAS — are engine-native.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import requests

from cos_mcp.backends.base import MemoryBackend

logger = logging.getLogger(__name__)

# MuninnDB content limit per engram
MAX_CONTENT_LENGTH = 15000


class MuninnDBBackend(MemoryBackend):
    """MuninnDB cognitive memory backend — local REST API.

    Uses MuninnDB's engine-native cognitive primitives for memory
    storage and retrieval. One vault per Hermes profile for isolation.

    Credentials:
        MUNINN_API_KEY in ~/.hermes/.env (optional for default vault)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8475",
        vault: str = "default",
        api_key: str = "",
        max_results: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._vault = vault
        self._api_key = api_key
        self._max_results = max_results

        self._session = requests.Session()
        if api_key:
            self._session.headers["Authorization"] = f"Bearer {api_key}"

    # --- HTTP helpers ---

    def _post(
        self, path: str, payload: dict, timeout: int = 30
    ) -> dict:
        """POST to MuninnDB REST API. Returns parsed JSON or raises."""
        resp = self._session.post(
            f"{self._base_url}{path}",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _get(
        self,
        path: str,
        params: Optional[dict] = None,
        timeout: int = 30,
    ) -> dict:
        """GET from MuninnDB REST API. Returns parsed JSON or raises."""
        resp = self._session.get(
            f"{self._base_url}{path}",
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # --- MemoryBackend interface ---

    def query(
        self,
        query_text: str,
        max_results: int = 10,
        query_mode: str = "thinking",
        query_by: str = "hybrid",
        graph_context: bool = True,
        memory_type: Optional[str] = None,
        min_confidence: Optional[float] = None,
    ) -> Any:
        """Search MuninnDB via ACTIVATE endpoint.

        Returns the raw activations list from the response.
        Use MuninnDBFormatter to extract text.

        query_mode, query_by, graph_context are accepted for interface
        compatibility but ignored — MuninnDB uses its own cognitive
        scoring (ACT-R, Hebbian, PAS).
        """
        payload: Dict[str, Any] = {
            "vault": self._vault,
            "context": [query_text],
            "max_results": max_results,
            "threshold": 0.05,
        }
        if memory_type:
            payload["memory_type"] = memory_type

        resp = self._post("/api/activate", payload)
        activations = resp.get("activations", [])

        # Post-filter by confidence if requested
        if min_confidence is not None and activations:
            activations = [
                a
                for a in activations
                if a.get("confidence", 1.0) >= min_confidence
            ]

        return activations

    def ingest(
        self,
        text: str,
        infer: bool = True,
        user_name: str = "",
        metadata: Optional[dict] = None,
        memory_id: Optional[str] = None,
        memory_type_label: Optional[str] = None,
        tags: Optional[List[str]] = None,
        confidence: float = 1.0,
    ) -> None:
        """Write an engram to MuninnDB.

        Trims content to MAX_CONTENT_LENGTH (16KB MuninnDB limit).
        Tags are normalized to hyphenated-lowercase.
        """
        # Trim for MuninnDB's content limit
        if len(text) > MAX_CONTENT_LENGTH:
            text = text[:MAX_CONTENT_LENGTH] + "..."

        # Use first 120 chars as concept label
        concept = text[:120].replace("\n", " ")

        # Normalize tags
        normalized_tags: List[str] = []
        if tags:
            normalized_tags = [
                t.lower().replace(" ", "-").replace("_", "-")
                for t in tags
            ]
        if "hermes-memory" not in normalized_tags:
            normalized_tags.append("hermes-memory")

        payload: Dict[str, Any] = {
            "vault": self._vault,
            "concept": concept,
            "content": text,
            "confidence": confidence,
            "tags": normalized_tags,
        }

        if memory_type_label:
            payload["type_label"] = memory_type_label

        self._post("/api/engrams", payload)

    def delete(self, memory_id: str) -> None:
        """Delete is not supported by MuninnDB's current API.

        The original provider noted this as a TODO — MuninnDB may
        add idempotent_id support in the future. Silently no-ops.
        """
        logger.debug(
            "MuninnDB delete not supported (id=%s) — skipping", memory_id
        )

    def health_check(self) -> bool:
        """Check MuninnDB server is reachable via /api/health."""
        try:
            resp = self._session.get(
                f"{self._base_url}/api/health", timeout=5
            )
            return resp.status_code == 200
        except Exception:
            return False

    def provision(self) -> bool:
        """Verify MuninnDB is reachable.

        Vaults are created via ``muninn init`` CLI, not via API.
        This just checks connectivity.
        """
        if self.health_check():
            logger.info(
                "MuninnDB reachable at %s (vault=%s)",
                self._base_url,
                self._vault,
            )
            return True
        logger.warning(
            "MuninnDB not reachable at %s — continuing anyway",
            self._base_url,
        )
        # Return True even if unreachable — circuit breaker handles
        # downstream failures. Vault might come online later.
        return True

    def shutdown(self) -> None:
        """Close the HTTP session."""
        self._session.close()
