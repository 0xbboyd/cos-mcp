"""HydraDB Cloud backend implementation.

Wraps the hydradb-sdk sync client with lazy, thread-safe singleton
instantiation and tenant auto-provisioning.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from cos_mcp.backends.base import MemoryBackend

logger = logging.getLogger(__name__)


class HydraDBBackend(MemoryBackend):
    """HydraDB Cloud backend — managed graph database for AI memory.

    Uses the ``hydra_db.HydraDB`` sync client. Tenant provisioning is
    lazy: the first call to ``provision()`` creates the tenant and polls
    until ready (up to 5 minutes).

    API contract:
        - ``upsert`` is ``Optional[str]`` — pass ``\"true\"`` (string), not ``True``.
        - Metadata for ``type=memory`` must be JSON-encoded string, not object.
    """

    def __init__(
        self,
        api_key: str,
        tenant_id: str = "hermes",
        sub_tenant_id: str = "default",
        query_mode: str = "thinking",
        query_by: str = "hybrid",
        max_results: int = 10,
    ) -> None:
        self._api_key = api_key
        self._tenant_id = tenant_id
        self._sub_tenant_id = sub_tenant_id
        self._query_mode = query_mode
        self._query_by = query_by
        self._max_results = max_results

        self._client = None
        self._client_lock = threading.Lock()
        self._provisioned = False

    # --- Client singleton (lazy, thread-safe) ---

    def _get_client(self):
        """Lazy, thread-safe HydraDB client singleton (double-checked locking)."""
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                from hydra_db import HydraDB as HydraClient

                self._client = HydraClient(token=self._api_key)
            return self._client

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
        """Run a hybrid search against HydraDB.

        Returns the raw SDK response object — use HydraDBFormatter
        to extract text from it.
        """
        client = self._get_client()
        # memory_type and min_confidence are not natively supported
        # by HydraDB v2 API — they're accepted for interface compatibility
        # but ignored. Use metadata filters for type filtering instead.
        return client.query(
            tenant_id=self._tenant_id,
            sub_tenant_id=self._sub_tenant_id,
            query=query_text,
            type=memory_type if memory_type else "memory",
            query_by=query_by,
            mode=query_mode,
            max_results=max_results,
            graph_context=graph_context,
        )

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
        """Ingest a memory entry into HydraDB."""
        client = self._get_client()
        entry: Dict[str, Any] = {
            "text": text,
            "infer": infer,
            "user_name": user_name,
        }
        if memory_id:
            entry["id"] = memory_id
        if metadata:
            entry["metadata"] = json.dumps(metadata)

        memories = json.dumps([entry])
        client.context.ingest(
            type=memory_type_label if memory_type_label else "memory",
            tenant_id=self._tenant_id,
            sub_tenant_id=self._sub_tenant_id,
            memories=memories,
            upsert="true",  # string, not bool — SDK contract
        )

    def delete(self, memory_id: str) -> None:
        """Delete a memory entry from HydraDB by ID."""
        client = self._get_client()
        client.context.delete(
            type="memory",
            tenant_id=self._tenant_id,
            sub_tenant_id=self._sub_tenant_id,
            ids=[memory_id],
        )

    def health_check(self) -> bool:
        """Check HydraDB connectivity by listing tenants."""
        try:
            client = self._get_client()
            client.tenants.list()
            return True
        except Exception:
            return False

    def provision(self) -> bool:
        """Create tenant if needed, poll until ready for ingestion.

        Handles 409 conflict (tenant already exists from another profile).
        Polls every 5s, max 60 attempts (5 min). On timeout, logs warning
        and returns True (circuit breaker handles downstream unavailability).
        """
        if self._provisioned:
            return True
        if not self._api_key:
            logger.warning("HydraDB: no API key — skipping tenant provisioning")
            return False

        try:
            client = self._get_client()

            # 1. Check existence, create if needed
            existing = client.tenants.list()
            tenant_ids = (
                existing.data.tenant_ids or [] if existing.data else []
            )
            if self._tenant_id not in tenant_ids:
                try:
                    logger.info(
                        "Creating HydraDB tenant: %s", self._tenant_id
                    )
                    client.tenants.create(tenant_id=self._tenant_id)
                except Exception as e:
                    # 409 TENANT_ALREADY_EXISTS — race with another profile
                    status = None
                    if hasattr(e, "status_code"):
                        status = e.status_code
                    elif hasattr(e, "response") and hasattr(
                        e.response, "status_code"
                    ):
                        status = e.response.status_code
                    if status == 409:
                        logger.info(
                            "Tenant %s already exists (409 — race)",
                            self._tenant_id,
                        )
                    else:
                        raise

            # 2. Poll until ready
            for attempt in range(1, 61):
                status = client.tenants.status(
                    tenant_id=self._tenant_id
                )
                infra = status.data.infra if status.data else None
                if infra and infra.ready_for_ingestion:
                    logger.info(
                        "HydraDB tenant %s ready (attempt %d)",
                        self._tenant_id,
                        attempt,
                    )
                    self._provisioned = True
                    return True
                logger.info(
                    "HydraDB tenant %s: waiting... (attempt %d/60)",
                    self._tenant_id,
                    attempt,
                )
                time.sleep(5)

            logger.warning(
                "HydraDB tenant %s not ready after 5 min — proceeding anyway",
                self._tenant_id,
            )
            self._provisioned = True
            return True

        except Exception as e:
            logger.warning("HydraDB tenant provisioning failed: %s", e)
            return False

    def shutdown(self) -> None:
        """Clear the cached client singleton."""
        with self._client_lock:
            self._client = None

    # --- Content-hash ID helper ---

    @staticmethod
    def make_mirror_id(target: str, content: str) -> str:
        """Generate a stable content-hash ID for built-in memory mirroring.

        Used by ``on_memory_write`` for deterministic upsert/delete.
        """
        return (
            f"hermes_{target}_"
            f"{hashlib.sha256(content.encode()).hexdigest()[:16]}"
        )
