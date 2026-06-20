"""
HydraDB Memory Provider for Hermes Agent.

A cloud-backed persistent memory provider that stores agent memories in
HydraDB and retrieves them each turn via hybrid search (semantic + BM25 +
graph traversal + recency scoring). Shared across all Hermes profiles
via a single HydraDB tenant with optional per-profile sub-tenant isolation.

Architecture: ~/.hermes/hermes-agent/plugins/memory/hydradb/

See: research/hydradb-provider-design.md (blueprint)
     research/hydradb-v2-research.md (API reference)
     research/hermes-memory-provider-research.md (provider contract)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

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
# Config helpers (module-level — called by the class)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "api_key": "",
    "tenant_id": "hermes",
    "sub_tenant_id": None,  # None → auto-set to agent_identity in initialize()
    "query_mode": "thinking",
    "query_by": "hybrid",
    "max_results": 10,
}


def _load_config(hermes_home: str = "") -> dict:
    """Read HYDRA_DB_API_KEY from env, then merge overrides from hydradb.json.

    Args:
        hermes_home: Path to the Hermes home directory. If empty, falls back
            to the HERMES_HOME environment variable. If both are empty,
            file-based config loading is skipped entirely.
    """
    cfg = dict(DEFAULT_CONFIG)
    cfg["api_key"] = os.environ.get("HYDRA_DB_API_KEY", "")

    # Resolve hermes_home: kwarg → env → skip file loading
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
# HydraDBMemoryProvider
# ---------------------------------------------------------------------------


class HydraDBMemoryProvider(MemoryProvider):
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

    # --- Config layer -------------------------------------------------------

    def _load_config(self) -> dict:
        """Read config from env + hydradb.json (instance wrapper)."""
        return _load_config(self._hermes_home)

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

    # --- Lifecycle (ABC requirements) ---------------------------------------

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

    def initialize(self, session_id: str, **kwargs) -> None:
        """Load config, capture identity, ensure tenant exists.

        ``kwargs`` always includes ``hermes_home`` and ``platform``.
        ``agent_identity`` is the active profile name (from Hermes runtime).
        """
        self._hermes_home = kwargs.get("hermes_home", "")
        cfg = self._load_config()

        self._api_key = cfg["api_key"]
        self._tenant_id = cfg["tenant_id"]
        self._sub_tenant_id = (
            cfg.get("sub_tenant_id")
            or kwargs.get("agent_identity", "")
            or "default"
        )
        self._query_mode = cfg.get("query_mode", "thinking")
        self._query_by = cfg.get("query_by", "hybrid")
        self._max_results = cfg.get("max_results", 10)
        self._user_name = kwargs.get("agent_identity", "User")
        self._agent_context = kwargs.get("agent_context", "primary")

        # Threading primitives
        self._client = None
        self._client_lock = threading.Lock()
        self._prefetch_result = ""
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._mirror_thread: Optional[threading.Thread] = None

        # Circuit breaker — independent read/write gauges
        self._read_failures = 0
        self._write_failures = 0
        self._read_breaker_open_until = 0.0
        self._write_breaker_open_until = 0.0
        self._breaker_lock = threading.Lock()

        # Tenant provisioning
        self._tenant_ready = False

        logger.info(
            "HydraDB provider initialized: tenant=%s sub=%s mode=%s",
            self._tenant_id,
            self._sub_tenant_id,
            self._query_mode,
        )

        self._ensure_tenant()

    # --- Tenant provisioning ------------------------------------------------

    def _ensure_tenant(self) -> None:
        """Create tenant if needed, poll until ready for ingestion.

        Polls every 5s, max 60 attempts (5 min). Handles 409 conflict
        as "already exists". On timeout, logs warning and proceeds —
        the circuit breaker handles downstream API unavailability.
        """
        if self._tenant_ready:
            return
        if self._is_write_breaker_open():
            return
        if not self._api_key:
            logger.warning("HydraDB: no API key — skipping tenant provisioning")
            return

        try:
            client = self._get_client()

            # 1. Check existence
            existing = client.tenants.list()
            tenant_ids = existing.data.tenant_ids or [] if existing.data else []
            if self._tenant_id not in tenant_ids:
                try:
                    logger.info("Creating HydraDB tenant: %s", self._tenant_id)
                    client.tenants.create(tenant_id=self._tenant_id)
                except Exception as e:
                    # 409 TENANT_ALREADY_EXISTS — race with another profile
                    if hasattr(e, "status_code") and e.status_code == 409:
                        logger.info(
                            "Tenant %s already exists (409 — race)",
                            self._tenant_id,
                        )
                    elif (
                        hasattr(e, "response")
                        and getattr(e.response, "status_code", None) == 409
                    ):
                        logger.info(
                            "Tenant %s already exists (409 — race)",
                            self._tenant_id,
                        )
                    else:
                        raise

            # 2. Poll until ready

            for attempt in range(1, 61):  # 60 attempts * 5s = 5 min
                status = client.tenants.status(tenant_id=self._tenant_id)
                infra = status.data.infra if status.data else None
                if infra and infra.ready_for_ingestion:
                    logger.info(
                        "HydraDB tenant %s ready (attempt %d)",
                        self._tenant_id,
                        attempt,
                    )
                    self._tenant_ready = True
                    self._record_write_success()
                    return
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
            self._tenant_ready = True
            self._record_write_success()  # Don't count timeout as a failure

        except Exception as e:
            self._record_write_failure()
            logger.warning("HydraDB tenant provisioning failed: %s", e)
            # Proceed anyway — circuit breaker handles downstream API issues

    # --- Client -------------------------------------------------------------

    def _get_client(self):
        """Lazy, thread-safe HydraDB client singleton."""
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                from hydra_db import HydraDB as HydraClient
                self._client = HydraClient(token=self._api_key)
            return self._client

    # --- Circuit breaker ---------------------------------------------------

    def _is_read_breaker_open(self) -> bool:
        """Check whether the read circuit breaker is currently open."""
        with self._breaker_lock:
            if (
                self._read_breaker_open_until
                and time.time() < self._read_breaker_open_until
            ):
                return True
            return False

    def _is_write_breaker_open(self) -> bool:
        """Check whether the write circuit breaker is currently open."""
        with self._breaker_lock:
            if (
                self._write_breaker_open_until
                and time.time() < self._write_breaker_open_until
            ):
                return True
            return False

    def _record_read_success(self) -> None:
        """Reset the read circuit breaker on a successful read operation."""
        with self._breaker_lock:
            self._read_failures = 0
            self._read_breaker_open_until = 0.0

    def _record_write_success(self) -> None:
        """Reset the write circuit breaker on a successful write operation."""
        with self._breaker_lock:
            self._write_failures = 0
            self._write_breaker_open_until = 0.0

    def _record_read_failure(self) -> None:
        """Increment read failure counter; trip breaker at threshold."""
        with self._breaker_lock:
            self._read_failures += 1
            if self._read_failures >= 5:
                self._read_breaker_open_until = time.time() + 120
                logger.warning(
                    "HydraDB read circuit breaker OPEN — 120s cooldown"
                )

    def _record_write_failure(self) -> None:
        """Increment write failure counter; trip breaker at threshold."""
        with self._breaker_lock:
            self._write_failures += 1
            if self._write_failures >= 5:
                self._write_breaker_open_until = time.time() + 120
                logger.warning(
                    "HydraDB write circuit breaker OPEN — 120s cooldown"
                )

    # --- Read path ----------------------------------------------------------

    @staticmethod
    def system_prompt_block() -> str:
        """Static text injected into the system prompt."""
        return "HydraDB Memory. Active. Memories are retrieved each turn."

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return the cached result from the last ``queue_prefetch()``."""
        with self._prefetch_lock:
            result = self._prefetch_result
            self._prefetch_result = ""
        if not result:
            return ""
        return f"## HydraDB Memory\n{result}"

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Fire a background HydraDB query for the upcoming turn."""
        if self._is_read_breaker_open():
            return

        def _run():
            try:
                client = self._get_client()
                result = client.query(
                    tenant_id=self._tenant_id,
                    sub_tenant_id=self._sub_tenant_id,
                    query=query,
                    type="memory",
                    query_by=self._query_by,
                    mode=self._query_mode,
                    max_results=self._max_results,
                    graph_context=True,
                )
                context_str = self._format_chunks(result)
                if context_str and context_str.strip():
                    with self._prefetch_lock:
                        self._prefetch_result = context_str
                self._record_read_success()
            except Exception:
                self._record_read_failure()
                logger.debug("HydraDB prefetch failed", exc_info=True)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="hydradb-prefetch"
        )
        self._prefetch_thread.start()

    @staticmethod
    def _format_chunks(result, min_score: float = 0.3) -> str:
        """Format query chunks into clean prose — strips build_string() framing.

        ``build_string()`` has 72-89% overhead (headers, IDs, scores).
        This extracts only ``chunk_content``, dropping chunks below
        ``min_score``.
        """
        chunks = getattr(result.data, "chunks", None) or []
        if not chunks:
            return ""
        lines = []
        for c in chunks:
            score = getattr(c, "relevancy_score", 0) or 0
            if score < min_score:
                continue
            content = getattr(c, "chunk_content", "") or ""
            if content.strip():
                lines.append(content.strip())
        return "\n\n".join(lines)

    # --- Write path ---------------------------------------------------------

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
        if self._is_write_breaker_open():
            return

        def _sync():
            try:
                client = self._get_client()
                text = f"User: {user_content}\nAssistant: {assistant_content}"
                memories = json.dumps([
                    {
                        "text": text,
                        "infer": True,
                        "user_name": self._user_name,
                    }
                ])
                client.context.ingest(
                    type="memory",
                    tenant_id=self._tenant_id,
                    sub_tenant_id=self._sub_tenant_id,
                    memories=memories,
                    upsert="true",
                )
                self._record_write_success()
            except Exception:
                self._record_write_failure()
                logger.debug("HydraDB sync_turn failed", exc_info=True)

        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="hydradb-sync"
        )
        self._sync_thread.start()

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Mirror built-in memory writes into HydraDB (verbatim, infer=False)."""
        if self._is_write_breaker_open():
            return

        def _write():
            try:
                client = self._get_client()
                # Stable content-hash ID for deterministic upsert/delete
                entry_id = (
                    f"hermes_{target}_"
                    f"{hashlib.sha256(content.encode()).hexdigest()[:16]}"
                )

                if action == "remove":
                    client.context.delete(
                        type="memory",
                        tenant_id=self._tenant_id,
                        sub_tenant_id=self._sub_tenant_id,
                        ids=[entry_id],
                    )
                else:
                    # add or replace — upsert handles both
                    mem_metadata = json.dumps(
                        {"target": target, "source": "builtin_mirror"}
                    )
                    memories = json.dumps([
                        {
                            "id": entry_id,
                            "text": f"[{target}] {content}",
                            "infer": False,
                            "user_name": self._user_name,
                            "metadata": mem_metadata,
                        }
                    ])
                    client.context.ingest(
                        type="memory",
                        tenant_id=self._tenant_id,
                        sub_tenant_id=self._sub_tenant_id,
                        memories=memories,
                        upsert="true",
                    )
                self._record_write_success()
            except Exception:
                self._record_write_failure()
                logger.debug("HydraDB on_memory_write failed", exc_info=True)

        self._mirror_thread = threading.Thread(
            target=_write, daemon=True, name="hydradb-mirror"
        )
        self._mirror_thread.start()

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
                return self._tool_conclude(args)
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        except Exception as e:
            logger.warning("HydraDB tool '%s' failed: %s", tool_name, e)
            return json.dumps({"error": str(e)})

    def _tool_search(self, args: dict) -> str:
        """Run an on-demand memory search."""
        if self._is_read_breaker_open():
            return json.dumps({"error": "HydraDB read circuit breaker is open"})
        try:
            client = self._get_client()
            result = client.query(
                tenant_id=self._tenant_id,
                sub_tenant_id=self._sub_tenant_id,
                query=args["query"],
                type="memory",
                query_by=self._query_by,
                mode="fast",
                max_results=5,
                graph_context=True,
            )
            context = self._format_chunks(result, min_score=0.2)
            self._record_read_success()
            return json.dumps({"result": context or "No relevant memories found."})
        except Exception as e:
            self._record_read_failure()
            logger.debug("HydraDB tool search failed", exc_info=True)
            return json.dumps({"error": str(e)})

    def _tool_profile(self, args: dict) -> str:
        """Retrieve user profile summary."""
        if self._is_read_breaker_open():
            return json.dumps({"error": "HydraDB read circuit breaker is open"})
        try:
            client = self._get_client()
            result = client.query(
                tenant_id=self._tenant_id,
                sub_tenant_id=self._sub_tenant_id,
                query="user profile preferences traits",
                type="memory",
                query_by="hybrid",
                mode="thinking",
                max_results=5,
                graph_context=True,
            )
            context = self._format_chunks(result, min_score=0.2)
            self._record_read_success()
            return json.dumps({"result": context or "No profile data found."})
        except Exception as e:
            self._record_read_failure()
            logger.debug("HydraDB tool profile failed", exc_info=True)
            return json.dumps({"error": str(e)})

    def _tool_conclude(self, args: dict) -> str:
        """Store a durable fact."""
        if self._is_write_breaker_open():
            return json.dumps({"error": "HydraDB write circuit breaker is open"})
        try:
            client = self._get_client()
            memories = json.dumps([
                {
                    "text": args["fact"],
                    "infer": False,
                    "user_name": self._user_name,
                }
            ])
            client.context.ingest(
                type="memory",
                tenant_id=self._tenant_id,
                sub_tenant_id=self._sub_tenant_id,
                memories=memories,
                upsert="true",
            )
            self._record_write_success()
            return json.dumps({"result": "Fact stored."})
        except Exception as e:
            self._record_write_failure()
            logger.debug("HydraDB tool conclude failed", exc_info=True)
            return json.dumps({"error": str(e)})

    # --- Session hooks ------------------------------------------------------

    def on_session_end(self, messages: list) -> None:
        """Ingest a session summary as an episodic memory."""
        if self._agent_context != "primary":
            return
        if self._is_write_breaker_open():
            return

        def _summary():
            try:
                # Take the last few user/assistant messages as a summary
                tail = [
                    m for m in messages[-20:]
                    if m.get("role") in ("user", "assistant")
                ]
                text = "\n".join(
                    f"{m['role'].title()}: {m.get('content', '')}"
                    for m in tail[-10:]
                )
                if not text.strip():
                    return
                client = self._get_client()
                memories = json.dumps([
                    {
                        "text": text,
                        "infer": True,
                        "user_name": self._user_name,
                    }
                ])
                client.context.ingest(
                    type="memory",
                    tenant_id=self._tenant_id,
                    sub_tenant_id=self._sub_tenant_id,
                    memories=memories,
                    upsert="true",
                )
                self._record_write_success()
            except Exception:
                self._record_write_failure()
                logger.debug("HydraDB on_session_end failed", exc_info=True)

        t = threading.Thread(target=_summary, daemon=True)
        t.start()

    def shutdown(self) -> None:
        """Join background threads, clear client."""
        for thread in (
            self._prefetch_thread,
            self._sync_thread,
            self._mirror_thread,
        ):
            if thread and thread.is_alive():
                thread.join(timeout=5.0)
        with self._client_lock:
            self._client = None


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register this provider with the Hermes Agent plugin system."""
    ctx.register_memory_provider(HydraDBMemoryProvider())
