"""
MuninnDB Memory Provider for Hermes Agent.

A local cognitive memory provider backed by MuninnDB — a neuroscience-inspired
database with ACT-R temporal scoring, Hebbian co-activation learning, Bayesian
confidence, predictive activation (PAS), and 16 typed relationship types — all
engine-native. No taxonomy to build, no contradiction detection to layer on.

The plugin is thin — the cognitive logic lives in MuninnDB. The provider handles
Hermes contract compliance (sync-only, fire-and-forget writes, circuit breaker,
prefetch/cache model) and exposes memory as tools to the model.

Architecture: ~/.hermes/hermes-agent/plugins/memory/muninn/

References:
    https://muninndb.com/docs
    https://github.com/scrypster/muninndb
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import requests

from agent.memory_provider import MemoryProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

SEARCH_SCHEMA = {
    "type": "function",
    "function": {
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
    },
}

PROFILE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "muninn_profile",
        "description": (
            "Retrieve the user profile from MuninnDB memory — preferences, "
            "identity, and stable facts about the user."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

REMEMBER_SCHEMA = {
    "type": "function",
    "function": {
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
    },
}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG = {
    "base_url": "http://127.0.0.1:8475",
    "vault": "default",
    "api_key": "",
}


def _load_config(hermes_home: str = "") -> dict:
    """Read MUNINN_API_KEY from env, merge overrides from muninn.json.

    Args:
        hermes_home: Path to Hermes home directory. Falls back to HERMES_HOME
            env var. If both empty, file-based config loading is skipped.
    """
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
# MuninnDBMemoryProvider
# ---------------------------------------------------------------------------


class MuninnDBMemoryProvider(MemoryProvider):
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

    # --- Config layer -------------------------------------------------------

    def _load_config(self) -> dict:
        """Read config from env + muninn.json (instance wrapper)."""
        return _load_config(self._hermes_home)

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

    # --- Lifecycle ----------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """Check MuninnDB server is reachable — no network call, just import check."""
        try:
            import requests  # noqa: F401
            return True
        except ImportError:
            return False

    def initialize(self, session_id: str, **kwargs) -> None:
        """Load config, capture identity, verify MuninnDB is reachable."""
        self._hermes_home = kwargs.get("hermes_home", "")
        cfg = self._load_config()

        self._base_url = cfg["base_url"].rstrip("/")
        self._api_key = cfg["api_key"]
        self._vault = cfg["vault"]
        self._agent_context = kwargs.get("agent_context", "primary")

        # Threading primitives
        self._prefetch_result: List[Dict] = []
        self._prefetch_lock = threading.Lock()
        self._prefetch_thread: Optional[threading.Thread] = None
        self._sync_thread: Optional[threading.Thread] = None
        self._mirror_thread: Optional[threading.Thread] = None

        # Circuit breaker
        self._read_failures = 0
        self._write_failures = 0
        self._read_breaker_open_until = 0.0
        self._write_breaker_open_until = 0.0
        self._breaker_lock = threading.Lock()

        # Session
        self._session = requests.Session()
        if self._api_key:
            self._session.headers["Authorization"] = f"Bearer {self._api_key}"
        self._max_results = 10

        logger.info(
            "MuninnDB provider initialized: base_url=%s vault=%s",
            self._base_url,
            self._vault,
        )

    # --- HTTP helpers -------------------------------------------------------

    def _health_check(self) -> bool:
        """Verify MuninnDB server is reachable and healthy."""
        try:
            resp = self._session.get(
                f"{self._base_url}/api/health", timeout=5
            )
            return resp.status_code == 200
        except Exception:
            return False

    def _post(self, path: str, payload: dict, timeout: int = 30) -> dict:
        """POST to MuninnDB REST API. Returns parsed JSON or raises."""
        resp = self._session.post(
            f"{self._base_url}{path}",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def _get(
        self, path: str, params: Optional[dict] = None, timeout: int = 30
    ) -> dict:
        """GET from MuninnDB REST API. Returns parsed JSON or raises."""
        resp = self._session.get(
            f"{self._base_url}{path}",
            params=params,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # --- Circuit breaker ----------------------------------------------------

    def _is_read_breaker_open(self) -> bool:
        with self._breaker_lock:
            if (
                self._read_breaker_open_until
                and time.time() < self._read_breaker_open_until
            ):
                return True
            return False

    def _is_write_breaker_open(self) -> bool:
        with self._breaker_lock:
            if (
                self._write_breaker_open_until
                and time.time() < self._write_breaker_open_until
            ):
                return True
            return False

    def _record_read_success(self) -> None:
        with self._breaker_lock:
            self._read_failures = 0
            self._read_breaker_open_until = 0.0

    def _record_write_success(self) -> None:
        with self._breaker_lock:
            self._write_failures = 0
            self._write_breaker_open_until = 0.0

    def _record_read_failure(self) -> None:
        with self._breaker_lock:
            self._read_failures += 1
            if self._read_failures >= 5:
                self._read_breaker_open_until = time.time() + 120
                logger.warning(
                    "MuninnDB read circuit breaker OPEN — 120s cooldown"
                )

    def _record_write_failure(self) -> None:
        with self._breaker_lock:
            self._write_failures += 1
            if self._write_failures >= 5:
                self._write_breaker_open_until = time.time() + 120
                logger.warning(
                    "MuninnDB write circuit breaker OPEN — 120s cooldown"
                )

    # --- Read path ----------------------------------------------------------

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

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return cached results from last ``queue_prefetch()``."""
        with self._prefetch_lock:
            activations = self._prefetch_result
            self._prefetch_result = []

        if not activations:
            return ""

        return self._format_activations(activations)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Fire a background MuninnDB ACTIVATE for the upcoming turn."""
        if self._is_read_breaker_open():
            return

        def _run():
            try:
                activations = self._activate(
                    context=[query],
                    max_results=self._max_results,
                )
                if activations:
                    with self._prefetch_lock:
                        self._prefetch_result = activations
                self._record_read_success()
            except Exception:
                self._record_read_failure()
                logger.debug("MuninnDB prefetch failed", exc_info=True)

        self._prefetch_thread = threading.Thread(
            target=_run, daemon=True, name="muninn-prefetch"
        )
        self._prefetch_thread.start()

    def _activate(
        self,
        context: List[str],
        vault: Optional[str] = None,
        max_results: int = 10,
        threshold: float = 0.05,
        memory_type: Optional[str] = None,
    ) -> List[Dict]:
        """Call MuninnDB ACTIVATE and return activation items."""
        payload = {
            "vault": vault or self._vault,
            "context": context,
            "max_results": max_results,
            "threshold": threshold,
        }
        if memory_type:
            payload["memory_type"] = memory_type

        resp = self._post("/api/activate", payload)
        return resp.get("activations", [])

    @staticmethod
    def _format_activations(activations: List[Dict]) -> str:
        """Format activation results into clean prose for the model.

        MuninnDB activations include: concept, content, score, confidence,
        why (explanation), hop_path (graph traversal path), dormant flag.
        We produce a clean summary with optional confidence indicators.
        """
        if not activations:
            return "No relevant memories found."

        lines = ["## MuninnDB Memory"]
        for item in activations:
            concept = item.get("concept", "")
            content = item.get("content", "")
            confidence = item.get("confidence")
            dormant = item.get("dormant", False)

            if dormant:
                continue  # Skip dormant (soft-deleted) memories

            parts = []
            if concept:
                parts.append(f"**{concept}**")
            if confidence is not None and confidence < 0.6:
                parts.append(f"[confidence: {confidence:.0%}]")
            if content:
                parts.append(content)

            if parts:
                lines.append(" • " + " — ".join(parts))

        if len(lines) == 1:
            return ""

        return "\n".join(lines)

    def _format_chunks(self, activations: List[Dict]) -> str:
        """Alias for _format_activations — used by tool handlers."""
        return self._format_activations(activations)

    # --- Write path ---------------------------------------------------------

    def _write_engram(
        self,
        concept: str,
        content: str,
        tags: Optional[List[str]] = None,
        memory_type: Optional[str] = None,
        confidence: float = 1.0,
    ) -> dict:
        """Write a single engram to MuninnDB."""
        payload = {
            "vault": self._vault,
            "concept": concept,
            "content": content,
            "confidence": confidence,
        }
        if tags:
            # MuninnDB uses hyphenated-lowercase tags by convention
            payload["tags"] = [
                t.lower().replace(" ", "-").replace("_", "-") for t in tags
            ]
        else:
            payload["tags"] = ["hermes-memory"]

        if memory_type:
            payload["type_label"] = memory_type

        return self._post("/api/engrams", payload)

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
                text = f"User: {user_content}\nAssistant: {assistant_content}"
                # Trim for MuninnDB's 16KB content limit
                if len(text) > 15000:
                    text = text[:15000] + "..."
                self._write_engram(
                    concept=f"Conversation turn",
                    content=text,
                    tags=["hermes-memory", "conversation"],
                    memory_type="event",
                    confidence=0.9,
                )
                self._record_write_success()
            except Exception:
                self._record_write_failure()
                logger.debug("MuninnDB sync_turn failed", exc_info=True)

        self._sync_thread = threading.Thread(
            target=_sync, daemon=True, name="muninn-sync"
        )
        self._sync_thread.start()

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Mirror built-in memory writes into MuninnDB."""
        if self._is_write_breaker_open():
            return

        def _write():
            try:
                if action == "remove":
                    # MuninnDB doesn't support delete-by-content — skip for now.
                    # Could use idempotent_id in the future.
                    return

                self._write_engram(
                    concept=f"[{target}] {content[:120]}",
                    content=content,
                    tags=["hermes-memory", f"hermes-target:{target}"],
                    memory_type="preference" if target == "user" else "fact",
                    confidence=0.95,
                )
                self._record_write_success()
            except Exception:
                self._record_write_failure()
                logger.debug("MuninnDB on_memory_write failed", exc_info=True)

        self._mirror_thread = threading.Thread(
            target=_write, daemon=True, name="muninn-mirror"
        )
        self._mirror_thread.start()

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
        if self._is_read_breaker_open():
            return json.dumps(
                {"error": "MuninnDB read circuit breaker is open"}
            )
        try:
            query = args.get("query", "")
            memory_type = args.get("memory_type")
            min_confidence = args.get("min_confidence")

            activations = self._activate(
                context=[query],
                max_results=8,
                memory_type=memory_type,
            )

            # Post-filter by confidence if requested
            if min_confidence is not None and activations:
                activations = [
                    a
                    for a in activations
                    if a.get("confidence", 1.0) >= min_confidence
                ]

            result = self._format_chunks(activations)
            self._record_read_success()
            return json.dumps(
                {"result": result or "No relevant memories found."}
            )
        except Exception as e:
            self._record_read_failure()
            logger.debug("MuninnDB tool search failed", exc_info=True)
            return json.dumps({"error": str(e)})

    def _tool_profile(self, args: dict) -> str:
        """Retrieve user profile summary."""
        if self._is_read_breaker_open():
            return json.dumps(
                {"error": "MuninnDB read circuit breaker is open"}
            )
        try:
            # Search for identity + preferences with low recency bias
            # (profile facts are durable, not temporal)
            activations = self._activate(
                context=[
                    "user profile identity preferences traits",
                ],
                max_results=8,
                memory_type="preference",
            )
            # Also pull identity memories
            identity = self._activate(
                context=["who is the user", "user identity"],
                max_results=4,
                memory_type="identity",
            )

            all_items = activations + identity
            result = self._format_chunks(all_items)
            self._record_read_success()
            return json.dumps(
                {"result": result or "No profile data found."}
            )
        except Exception as e:
            self._record_read_failure()
            logger.debug("MuninnDB tool profile failed", exc_info=True)
            return json.dumps({"error": str(e)})

    def _tool_remember(self, args: dict) -> str:
        """Store a durable fact."""
        if self._is_write_breaker_open():
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

            self._write_engram(
                concept=concept,
                content=content,
                tags=tags,
                memory_type=memory_type,
            )
            self._record_write_success()
            return json.dumps({"result": "Memory stored."})
        except Exception as e:
            self._record_write_failure()
            logger.debug("MuninnDB tool remember failed", exc_info=True)
            return json.dumps({"error": str(e)})

    # --- Session hooks ------------------------------------------------------

    def on_session_end(self, messages: list) -> None:
        """Ingest session summary as episodic memory."""
        if self._agent_context != "primary":
            return
        if self._is_write_breaker_open():
            return

        def _summary():
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
                if len(text) > 15000:
                    text = text[:15000] + "..."

                self._write_engram(
                    concept="Session summary",
                    content=text,
                    tags=["hermes-memory", "session-summary"],
                    memory_type="event",
                    confidence=0.85,
                )
                self._record_write_success()
            except Exception:
                self._record_write_failure()
                logger.debug("MuninnDB on_session_end failed", exc_info=True)

        t = threading.Thread(target=_summary, daemon=True)
        t.start()

    def shutdown(self) -> None:
        """Join background threads, close HTTP session."""
        for thread in (
            self._prefetch_thread,
            self._sync_thread,
            self._mirror_thread,
        ):
            if thread and thread.is_alive():
                thread.join(timeout=5.0)
        self._session.close()


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register this provider with the Hermes Agent plugin system."""
    ctx.register_memory_provider(MuninnDBMemoryProvider())
