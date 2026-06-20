"""HydraDB context engine — graph-backed context compression and retrieval.

Full compress() pipeline: entity extraction → fire-and-forget graph ingest →
summary block assembly. Exposes hydradb_context_search and hydradb_context_expand
tools with graph traversal.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from typing import Any, Dict, List, Optional

from cos_mcp.base_context_engine import BaseContextEngine
from cos_mcp.backends.base import MemoryBackend
from cos_mcp.backends.hydradb import HydraDBBackend
from cos_mcp.formatting.context_base import ContextFormatter
from cos_mcp.formatting.hydradb_context import HydraDBContextFormatter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "api_key": "",
    "tenant_id": "hermes",
    "sub_tenant_id": None,
    "query_mode": "thinking",
    "query_by": "hybrid",
    "max_results": 10,
    "threshold_percent": 0.75,
    "protect_first_n": 3,
    "protect_last_n": 6,
    "entity_extraction_mode": "balanced",
    "entity_per_message_cap": 3,
    "dedup_threshold": 0.7,
    "summary_max_tokens": 800,
}

# ---------------------------------------------------------------------------
# Tool schemas (HYD-05)
# ---------------------------------------------------------------------------

SEARCH_CONTEXT_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "hydradb_context_search",
        "description": (
            "Search HydraDB context for relevant compressed conversation context. "
            "Use this to recall topics, decisions, facts, and relationships from "
            "earlier in the conversation that were compressed into the context graph. "
            "Returns clean prose results with graph context annotations."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to search for in compressed context "
                        "(topics, decisions, facts, relationships)"
                    ),
                },
            },
            "required": ["query"],
        },
    },
}

EXPAND_CONTEXT_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "hydradb_context_expand",
        "description": (
            "Retrieve the full context entities for a specific ctx-id or topic "
            "from compressed conversation history. Use this when a summary block "
            "references a [ctx-id: ...] anchor to get the complete entities behind "
            "that compression point. Supports multi-hop graph traversal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ctx_id": {
                    "type": "string",
                    "description": (
                        "The ctx-id anchor from a summary block "
                        "(e.g., 'user_3_a1b2c3d4'). Retrieves all entities from "
                        "that compression point."
                    ),
                },
                "topic": {
                    "type": "string",
                    "description": (
                        "A topic keyword to expand. Retrieves all context entities "
                        "related to this topic across compression points."
                    ),
                },
            },
            "required": ["ctx_id"],
        },
    },
}

# ---------------------------------------------------------------------------
# Compiled regex patterns for entity extraction (Task 2)
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r'[.!?]+\s+')
_CAPITALIZED_PHRASE = re.compile(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b')

_DECISION_MARKERS = [
    "decided", "chose", "choice", "picked", "selected",
    "settled on", "going with", "will use", "going to use",
    "plan to", "opted for",
]

_FACTUAL_COPULA = [
    "is", "are", "was", "were", "has", "have",
    "uses", "runs", "supports", "provides",
]

_RELATION_VERBS = [
    "depends on", "built with", "uses", "runs on",
    "part of", "connects to", "requires", "implements",
]


# ---------------------------------------------------------------------------
# HydraDBContextEngine
# ---------------------------------------------------------------------------


class HydraDBContextEngine(BaseContextEngine):
    """HydraDB graph-backed context engine.

    Implements the full compress() pipeline with entity extraction,
    fire-and-forget graph storage, and tool-based retrieval.

    Inherits from BaseContextEngine (Phase 5) which provides circuit
    breaker, token tracking, should_compress(), and lifecycle stubs.
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

    def initialize(self, session_id: str, **kwargs) -> None:
        """Load config, create backend/formatter, set up circuit breaker."""
        super().initialize(session_id, **kwargs)
        # _config is already populated by _create_backend() during super init
        if not hasattr(self, "_config"):
            self._config = self._load_config()
        self._client_lock = threading.Lock()
        self._client = None
        logger.info(
            "HydraDB context engine initialized with tenant=%s sub_tenant=%s",
            self._config.get("tenant_id", "hermes"),
            self._config.get("sub_tenant_id")
            or kwargs.get("agent_identity", "default"),
        )

    # --- Backend / Formatter / Client --------------------------------------

    def _create_backend(self, kwargs: dict) -> MemoryBackend:
        """Return configured HydraDBBackend (replaces Phase 5 stub)."""
        cfg = self._load_config()
        self._config = cfg  # Cache for later use
        return HydraDBBackend(
            api_key=cfg["api_key"],
            tenant_id=cfg.get("tenant_id", "hermes"),
            sub_tenant_id=(
                cfg.get("sub_tenant_id")
                or kwargs.get("agent_identity", "default")
            ),
            query_mode=cfg.get("query_mode", "thinking"),
            query_by=cfg.get("query_by", "hybrid"),
            max_results=cfg.get("max_results", 10),
        )

    def _create_formatter(self) -> ContextFormatter:
        """Return HydraDBContextFormatter (replaces Phase 5 stub)."""
        return HydraDBContextFormatter()

    def _get_client(self):
        """Thread-safe HydraDB SDK client singleton (double-checked locking).

        Mirrors the memory provider's client singleton pattern.
        """
        if self._client is not None:
            return self._client
        with self._client_lock:
            if self._client is None:
                from hydra_db import HydraDB

                self._client = HydraDB(token=self._config["api_key"])
            return self._client

    # --- Config (CFG-01, CFG-02, CFG-03) -----------------------------------

    def _load_config(self) -> dict:
        """Load API key from env, non-secret config from hydradb-context.json.

        Extends Phase 5 implementation with entity extraction config defaults.
        """
        api_key = os.environ.get("HYDRA_DB_API_KEY", "")

        defaults = dict(DEFAULT_CONFIG)
        defaults["api_key"] = api_key

        cfg = self._load_config_file(self._hermes_home, self.name, defaults)

        # Apply threshold values to instance attributes
        if "threshold_percent" in cfg:
            self.threshold_percent = float(cfg["threshold_percent"])
        if "protect_first_n" in cfg:
            self.protect_first_n = int(cfg["protect_first_n"])
        if "protect_last_n" in cfg:
            self.protect_last_n = int(cfg["protect_last_n"])

        return cfg

    # --- Entity Extraction (HYD-01) ----------------------------------------

    def _extract_entities(
        self,
        messages: List[Dict[str, Any]],
        window_start_idx: int,
        window_end_idx: int,
    ) -> List[dict]:
        """Extract topics, decisions, facts, relationships from message window.

        Pure Python heuristics — no LLM calls. Per-message cap, global trigram
        Jaccard dedup, configurable aggressiveness.
        """
        mode = self._config.get("entity_extraction_mode", "balanced")
        cap = int(self._config.get("entity_per_message_cap", 3))
        dedup_threshold = float(self._config.get("dedup_threshold", 0.7))

        # Collect text from all messages in window for frequency tracking
        window_texts: List[str] = []
        msg_idx_map: List[int] = []  # maps window_texts[i] → messages index

        for i in range(window_start_idx, window_end_idx):
            msg = messages[i]
            if msg.get("role") == "system":
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                text = " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            else:
                text = str(content)
            if text.strip():
                window_texts.append(text)
                msg_idx_map.append(i)

        if len(window_texts) <= 1:
            return []

        # Frequency tracker for topics
        all_words = " ".join(window_texts).lower().split()
        word_freq = Counter(all_words)

        all_entities: List[dict] = []

        for rel_idx, text in enumerate(window_texts):
            abs_idx = msg_idx_map[rel_idx]
            entities = self._extract_entities_from_text(
                text, abs_idx, mode, word_freq, window_texts
            )
            if entities:
                entities.sort(key=lambda e: e["confidence"], reverse=True)
                all_entities.extend(entities[:cap])

        # Global dedup within each type
        if len(all_entities) > 1:
            all_entities = self._dedup_entities(all_entities, dedup_threshold)

        return all_entities

    def _extract_entities_from_text(
        self,
        text: str,
        msg_idx: int,
        mode: str,
        word_freq: Counter,
        all_texts: List[str],
    ) -> List[dict]:
        """Extract entities from a single message text."""
        entities: List[dict] = []

        sentences = _SENTENCE_SPLIT.split(text)
        sentences = [s.strip() for s in sentences if len(s.strip()) >= 10]

        if not sentences:
            return entities

        # Confidence modifiers based on mode
        if mode == "conservative":
            topic_base = 0.7
            decision_base = 0.7
            fact_base = 0.7
            rel_base = 0.65
            topic_boost = 0.1
        elif mode == "aggressive":
            topic_base = 0.4
            decision_base = 0.4
            fact_base = 0.3
            rel_base = 0.35
            topic_boost = 0.15
        else:  # balanced
            topic_base = 0.5
            decision_base = 0.55
            fact_base = 0.6
            rel_base = 0.5
            topic_boost = 0.2

        # Topic extraction
        entities.extend(
            self._extract_topics(
                text, msg_idx, topic_base, topic_boost, word_freq, all_texts
            )
        )

        # Decision extraction
        decisions = self._extract_decisions(sentences, msg_idx, decision_base)
        entities.extend(decisions)

        # Fact extraction (exclude sentences already captured as decisions)
        decision_sentences = {d["summary"] for d in decisions}
        entities.extend(
            self._extract_facts(sentences, decision_sentences, msg_idx, fact_base)
        )

        # Relationship extraction
        entities.extend(
            self._extract_relationships(sentences, msg_idx, rel_base)
        )

        return entities

    @staticmethod
    def _extract_topics(
        text: str,
        msg_idx: int,
        base_conf: float,
        boost: float,
        word_freq: Counter,
        all_texts: List[str],
    ) -> List[dict]:
        """Extract topic entities from text."""
        topics: List[dict] = []
        seen: set = set()
        num_texts = max(len(all_texts), 1)

        # Capitalized phrases
        for match in _CAPITALIZED_PHRASE.finditer(text):
            phrase = match.group(1).strip()
            if len(phrase) < 5 or phrase.lower() in seen:
                continue
            seen.add(phrase.lower())

            freq = word_freq.get(phrase.lower(), 0)
            confidence = min(base_conf + (freq * boost / num_texts), 0.9)

            topics.append({
                "type": "topic",
                "summary": phrase[:200],
                "source_msg_idx": msg_idx,
                "confidence": round(confidence, 2),
            })

        # Quoted phrases
        quoted = re.findall(r'"([^"]{5,100})"', text) + re.findall(
            r"'([^']{5,100})'", text
        )
        for phrase in quoted:
            if phrase.lower() in seen:
                continue
            seen.add(phrase.lower())
            freq = word_freq.get(phrase.lower(), 0)
            confidence = min(base_conf + (freq * boost / num_texts), 0.9)
            topics.append({
                "type": "topic",
                "summary": phrase[:200],
                "source_msg_idx": msg_idx,
                "confidence": round(confidence, 2),
            })

        return topics

    @staticmethod
    def _extract_decisions(
        sentences: List[str], msg_idx: int, base_conf: float
    ) -> List[dict]:
        """Extract decision entities from sentences."""
        decisions: List[dict] = []
        for sent in sentences:
            sent_lower = sent.lower()
            for marker in _DECISION_MARKERS:
                if marker in sent_lower:
                    conf = base_conf
                    if marker in ("decided", "chose", "settled on"):
                        conf = min(base_conf + 0.15, 0.9)
                    decisions.append({
                        "type": "decision",
                        "summary": sent[:200],
                        "source_msg_idx": msg_idx,
                        "confidence": round(conf, 2),
                    })
                    break  # One decision type per sentence
        return decisions

    @staticmethod
    def _extract_facts(
        sentences: List[str],
        decision_sentences: set,
        msg_idx: int,
        base_conf: float,
    ) -> List[dict]:
        """Extract fact entities from sentences."""
        facts: List[dict] = []
        for sent in sentences:
            if sent in decision_sentences:
                continue
            sent_lower = sent.lower()
            for copula in _FACTUAL_COPULA:
                if re.search(r'\b' + re.escape(copula) + r'\b', sent_lower):
                    conf = base_conf
                    if copula in ("is", "are", "was", "were"):
                        conf = min(base_conf + 0.1, 0.9)
                    facts.append({
                        "type": "fact",
                        "summary": sent[:200],
                        "source_msg_idx": msg_idx,
                        "confidence": round(conf, 2),
                    })
                    break
        return facts

    @staticmethod
    def _extract_relationships(
        sentences: List[str], msg_idx: int, base_conf: float
    ) -> List[dict]:
        """Extract relationship entities from sentences."""
        relationships: List[dict] = []
        for sent in sentences:
            sent_lower = sent.lower()
            for verb in _RELATION_VERBS:
                if verb in sent_lower:
                    conf = base_conf
                    if verb in ("depends on", "requires", "implements"):
                        conf = min(base_conf + 0.15, 0.9)
                    relationships.append({
                        "type": "relationship",
                        "summary": sent[:200],
                        "source_msg_idx": msg_idx,
                        "confidence": round(conf, 2),
                    })
                    break
        return relationships

    @staticmethod
    def _dedup_entities(entities: List[dict], threshold: float) -> List[dict]:
        """Deduplicate entities within same type using trigram Jaccard similarity.

        For entities of the same type, compares trigram sets. If Jaccard
        similarity exceeds threshold, keeps the higher-confidence entity.
        """
        if len(entities) <= 1:
            return entities

        # Group by type
        by_type: Dict[str, List[dict]] = {}
        for e in entities:
            by_type.setdefault(e["type"], []).append(e)

        result: List[dict] = []
        for etype, group in by_type.items():
            if len(group) <= 1:
                result.extend(group)
                continue

            # Compute trigram sets for each entity
            trigram_sets: List[set] = []
            for e in group:
                summary = e["summary"].lower()
                cleaned = re.sub(r'[^a-z0-9\s]', '', summary)
                trigrams: set = set()
                for i in range(len(cleaned) - 2):
                    trigrams.add(cleaned[i : i + 3])
                trigram_sets.append(trigrams)

            # Compare pairs, keep higher confidence
            kept = [True] * len(group)
            for i in range(len(group)):
                if not kept[i]:
                    continue
                si = trigram_sets[i]
                if not si:
                    continue
                for j in range(i + 1, len(group)):
                    if not kept[j]:
                        continue
                    sj = trigram_sets[j]
                    if not sj:
                        continue
                    intersection = len(si & sj)
                    union = len(si | sj)
                    jaccard = intersection / union if union > 0 else 0.0
                    if jaccard > threshold:
                        if group[i]["confidence"] >= group[j]["confidence"]:
                            kept[j] = False
                        else:
                            kept[i] = False
                            break

            result.extend(e for e, k in zip(group, kept) if k)

        return result

    # --- compress() Pipeline (HYD-02, CTX-04) -----------------------------

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: Optional[int] = None,
        focus_topic: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Compact the message list via entity extraction and summary assembly.

        Returns a NEW message list — never mutates the original input.
        Hard guard: returns input unchanged if the compressed list isn't shorter.

        Pipeline:
            1. Guards (minimum message count, window boundaries, token threshold)
            2. Entity extraction (pure Python heuristics)
            3. Fire-and-forget entity storage (daemon thread)
            4. Summary block assembly (≤800 tokens, ctx-id anchor)
            5. Output assembly [system, head, summary, tail]
            6. Hard guard (never return a list that isn't shorter)
        """
        # Step 1 — Guards
        if not messages or len(messages) < 2:
            return messages

        start_offset = 1 if messages[0].get("role") == "system" else 0
        window_start = start_offset + self.protect_first_n
        window_end = len(messages) - self.protect_last_n

        if window_start >= window_end:
            return messages

        if current_tokens is not None and self.threshold_tokens > 0:
            if current_tokens < self.threshold_tokens:
                return messages

        # Step 2 — Entity extraction
        try:
            entities = self._extract_entities(messages, window_start, window_end)
        except Exception as exc:
            logger.warning("Entity extraction failed: %s", exc)
            return messages

        if not entities:
            return messages

        # Step 3 — Fire-and-forget entity storage
        ctx_id = (
            f"{self._user_name}_{self.compression_count}_"
            f"{hashlib.md5(str(time.time()).encode()).hexdigest()[:8]}"
        )

        if not self._breaker.is_write_open():
            self._spawn_entity_storage(entities, ctx_id)

        # Step 4 — Build summary block
        summary_msg = self._build_summary_block(entities, ctx_id, focus_topic)

        # Step 5 — Assemble output (NEW list, never mutates input)
        system_msg = messages[0] if start_offset == 1 else None

        head = list(messages[start_offset:window_start])
        tail = list(messages[window_end:])

        output: List[Dict[str, Any]] = []
        if system_msg:
            output.append(system_msg)
        output.extend(head)
        output.append(summary_msg)
        output.extend(tail)

        # Step 6 — Hard guard: never return a list that isn't shorter
        if len(output) >= len(messages):
            return messages

        self.compression_count += 1

        logger.info(
            "Compression #%d: %d messages → %d messages (%d entities extracted)",
            self.compression_count,
            len(messages),
            len(output),
            len(entities),
        )

        return output

    # --- Summary Block (HYD-04) -------------------------------------------

    def _build_summary_block(
        self,
        entities: List[dict],
        ctx_id: str,
        focus_topic: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build a summary system message from extracted entities.

        Format:
            [ctx-id: ...]
            (blank line)
            ## Topics (if any)
            - summary (confidence: X.XX)
            ## Decisions (if any)
            ...
            ## Facts (if any)
            ...
            ## Relationships (if any)
            ...

        Capped at ~summary_max_tokens (default 800). Proportional truncation
        when the estimate exceeds the cap.
        """
        summary_max = int(self._config.get("summary_max_tokens", 800))

        # Group entities by type
        by_type: Dict[str, List[dict]] = {
            "topic": [],
            "decision": [],
            "fact": [],
            "relationship": [],
        }
        for e in entities:
            etype = e.get("type", "fact")
            if etype in by_type:
                by_type[etype].append(e)

        def _build_lines(groups: Dict[str, List[dict]]) -> List[str]:
            """Build the summary content lines from grouped entities."""
            lines: List[str] = [f"[ctx-id: {ctx_id}]", ""]
            type_order = [
                ("topic", "Topics"),
                ("decision", "Decisions"),
                ("fact", "Facts"),
                ("relationship", "Relationships"),
            ]
            for etype, display_name in type_order:
                group = groups.get(etype, [])
                if not group:
                    continue
                # Sort: focus_topic matches first, then by confidence descending
                if focus_topic:
                    ft_lower = focus_topic.lower()
                    group.sort(
                        key=lambda e: (
                            0 if ft_lower in e["summary"].lower() else 1,
                            -e["confidence"],
                        ),
                    )
                else:
                    group.sort(key=lambda e: -e["confidence"])

                lines.append(f"## {display_name}")
                for e in group:
                    conf = e.get("confidence", 0.0)
                    summary = e.get("summary", "")[:200]
                    lines.append(f"- {summary} (confidence: {conf:.2f})")
                lines.append("")
            return lines

        lines = _build_lines(by_type)
        content = "\n".join(lines).strip()

        # Token estimation: word_count × 1.3
        estimated_tokens = int(len(content.split()) * 1.3)

        if estimated_tokens > summary_max:
            # Proportional truncation: keep entities from each category
            truncation_ratio = summary_max / max(estimated_tokens, 1)
            truncated_groups: Dict[str, List[dict]] = {}
            for etype, group in by_type.items():
                if not group:
                    continue
                group.sort(key=lambda e: -e["confidence"])
                keep = max(1, int(len(group) * truncation_ratio))
                truncated_groups[etype] = group[:keep]

            lines = _build_lines(truncated_groups)
            lines.append(
                f"[Truncated — use hydradb_context_expand with ctx-id: {ctx_id} "
                f"to retrieve full context]"
            )
            content = "\n".join(lines).strip()

        return {"role": "system", "content": content}

    # --- Entity Storage (HYD-03) ------------------------------------------

    def _spawn_entity_storage(self, entities: List[dict], ctx_id: str) -> None:
        """Fire-and-forget daemon thread for entity storage via client.context.ingest().

        Stores entities as a graph entry in HydraDB with type="context".
        Thread is tracked as self._entity_thread for shutdown join.
        """

        if self._breaker.is_write_open():
            logger.debug("Skipping entity storage — write circuit breaker open")
            return

        def _run() -> None:
            try:
                client = self._get_client()
                metadata = {
                    "ctx_id": ctx_id,
                    "source": "hydradb-context-engine",
                    "compression_count": self.compression_count,
                    "extraction_mode": self._config.get(
                        "entity_extraction_mode", "balanced"
                    ),
                    "entity_count": len(entities),
                    "timestamp": time.time(),
                }
                entity_text = json.dumps(entities, ensure_ascii=False)
                metadata_str = json.dumps(metadata, ensure_ascii=False)

                # Build entry matching HydraDBBackend's ingest pattern
                entry: Dict[str, Any] = {
                    "text": entity_text,
                    "infer": False,
                    "metadata": metadata_str,
                }
                memories = json.dumps([entry])

                client.context.ingest(
                    type="context",
                    tenant_id=self._config.get("tenant_id", "hermes"),
                    sub_tenant_id=self._config.get("sub_tenant_id")
                    or self._user_name
                    or "default",
                    memories=memories,
                    upsert="true",
                )
                self._breaker.record_write_success()
                logger.debug(
                    "Entity storage complete: %d entities (ctx-id: %s)",
                    len(entities),
                    ctx_id,
                )
            except Exception:
                self._breaker.record_write_failure()
                logger.debug("Entity storage failed", exc_info=True)

        self._entity_thread = self._spawn_daemon(
            target=_run, name="hydradb-context-entity"
        )

    # --- Tool Schemas (HYD-05) ---------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas if engine is active, [] otherwise (defensive gating).

        Returns [] when:
            - agent_context != "primary" (non-primary agent shouldn't register tools)
            - _backend is None (engine not properly initialized)
        """
        if not hasattr(self, "_agent_context") or self._agent_context != "primary":
            return []
        if not hasattr(self, "_backend") or self._backend is None:
            return []
        return [SEARCH_CONTEXT_SCHEMA, EXPAND_CONTEXT_SCHEMA]

    # --- Tool: context_search (HYD-06) -------------------------------------

    def _tool_context_search(self, args: dict) -> str:
        """Search HydraDB context for relevant compressed conversation context.

        Queries with type="context", graph_context=True, query_mode="thinking".
        Returns clean prose via HydraDBContextFormatter.format_search_result().
        """
        query = args.get("query", "").strip()
        if not query:
            return json.dumps({"error": "query parameter is required"})

        if self._breaker.is_read_open():
            return json.dumps(
                {"error": "HydraDB read circuit breaker is open"}
            )

        try:
            result = self._backend.query(
                query_text=query,
                memory_type="context",
                graph_context=True,
                query_mode=self._config.get("query_mode", "thinking"),
                query_by=self._config.get("query_by", "hybrid"),
                max_results=self._config.get("max_results", 10),
            )
            formatted = self._formatter.format_search_result(result, min_score=0.2)
            self._breaker.record_read_success()
            return json.dumps(
                {"result": formatted or "No relevant context found."}
            )
        except Exception as e:
            self._breaker.record_read_failure()
            logger.debug("context_search failed: %s", e, exc_info=True)
            return json.dumps({"error": str(e)})

    # --- Tool: context_expand (HYD-07) -------------------------------------

    def _tool_context_expand(self, args: dict) -> str:
        """Retrieve full context entities by ctx-id or topic with multi-hop traversal.

        Uses graph_context=True for multi-hop. Handles "no results" gracefully.
        """
        ctx_id = args.get("ctx_id", "").strip()
        topic = args.get("topic", "").strip()

        if not ctx_id and not topic:
            return json.dumps(
                {"error": "At least one of ctx_id or topic is required"}
            )

        if self._breaker.is_read_open():
            return json.dumps(
                {"error": "HydraDB read circuit breaker is open"}
            )

        try:
            query_text = topic if topic else ""
            kwargs: Dict[str, Any] = {
                "memory_type": "context",
                "graph_context": True,
                "query_mode": "thinking",
                "max_results": self._config.get("max_results", 10),
            }
            if ctx_id:
                kwargs["context_id"] = ctx_id

            result = self._backend.query(query_text=query_text, **kwargs)
            formatted = self._formatter.format_expand_result(result)
            self._breaker.record_read_success()
            return json.dumps(
                {
                    "result": formatted
                    or "No relevant context found for that ctx-id/topic."
                }
            )
        except Exception as e:
            self._breaker.record_read_failure()
            logger.debug("context_expand failed: %s", e, exc_info=True)
            return json.dumps({"error": str(e)})

    # --- Session Lifecycle (HYD-08, HYD-09) -------------------------------

    def on_session_start(self, session_id: str, **kwargs) -> None:
        """Verify backend reachable, provision tenant idempotently.

        Reuses HydraDBBackend.provision() which handles tenant creation,
        polling, and 409 conflict for pre-existing tenants.
        Does not crash if backend is unreachable — logs warning, agent
        continues without context engine.
        """
        super().on_session_start(session_id, **kwargs)

        if self._agent_context != "primary":
            logger.debug(
                "Skipping on_session_start for agent_context=%s",
                self._agent_context,
            )
            return

        try:
            self._backend.provision()
            logger.info(
                "HydraDB context engine session started: %s (tenant=%s)",
                session_id,
                self._config.get("tenant_id", "hermes"),
            )
        except Exception as e:
            logger.warning(
                "HydraDB context engine session start failed: %s", e
            )

    def on_session_end(
        self, session_id: str, messages: List[Dict[str, Any]]
    ) -> None:
        """Flush pending entity storage threads with timeout.

        Joins self._entity_thread with 30s timeout. Skips when
        agent_context != "primary".
        """
        super().on_session_end(session_id, messages)

        if self._agent_context != "primary":
            return

        if self._entity_thread and self._entity_thread.is_alive():
            logger.debug("Waiting for entity storage thread to complete...")
            self._entity_thread.join(timeout=30.0)
            if self._entity_thread.is_alive():
                logger.warning(
                    "Entity storage thread did not complete within 30s timeout"
                )

        logger.info(
            "HydraDB context engine session ended: %s", session_id
        )

    # --- Tool Dispatch (HYD-10) --------------------------------------------

    def handle_tool_call(
        self, name: str, args: Dict[str, Any], **kwargs
    ) -> str:
        """Dispatch a tool call and return JSON-string result.

        Follows hydradb-memory pattern: if/elif dispatch, JSON error for
        unknown tools, all exceptions caught → json.dumps({"error": str(e)}).
        """
        try:
            if name == "hydradb_context_search":
                return self._tool_context_search(args)
            elif name == "hydradb_context_expand":
                return self._tool_context_expand(args)
            else:
                return json.dumps(
                    {"error": f"Unknown context engine tool: {name}"}
                )
        except Exception as e:
            logger.warning(
                "HydraDB context tool '%s' failed: %s", name, e
            )
            return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Plugin entry point (REG-01)
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register this context engine with the Hermes Agent plugin system."""
    ctx.register_context_engine(HydraDBContextEngine())
