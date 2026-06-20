"""MuninnDB context result formatter.

Cognitive formatting with confidence weights, ACT-R activation chains,
and memory type labels for context compression and retrieval.
"""

from __future__ import annotations

from typing import Any, List

from cos_mcp.formatting.context_base import ContextFormatter


class MuninnDBContextFormatter(ContextFormatter):
    """Format MuninnDB context results — cognitive formatting.

    Adds confidence annotations, ACT-R activation chain information,
    and memory type labels to search and expand results. Mirrors the
    ``MuninnDBFormatter`` pattern but includes cognitive annotations
    for context operations.
    """

    def format_compress_summary(self, result: Any) -> str:
        """Format a compression summary block from MuninnDB entities.

        Extracts entities with memory type labels (fact, decision, topic,
        relationship), confidence scores. Builds ``[ctx-id: ...]`` headers.
        Formats as bulleted list with confidence indicators for
        low-confidence items.

        Args:
            result: Compression result — dict with entities or list.

        Returns:
            Formatted summary string for system message insertion.
        """
        if result is None:
            return ""

        entities = []
        if isinstance(result, dict):
            entities = result.get("entities", [])
        elif isinstance(result, list):
            entities = result

        if not entities:
            return ""

        lines: List[str] = []
        for ent in entities:
            if isinstance(ent, dict):
                ctx_id = ent.get("ctx_id", "")
                entity_type = ent.get("type", "fact")
                summary = ent.get("summary", "")
                confidence = ent.get("confidence")

                if summary:
                    parts = []
                    parts.append(f"- **{entity_type.title()}**: {summary}")

                    header_parts = []
                    if ctx_id:
                        header_parts.append(f"[ctx-id: {ctx_id}]")
                    if confidence is not None:
                        if confidence < 0.4:
                            header_parts.append(f"[LOW confidence: {confidence:.0%}]")
                        elif confidence < 0.6:
                            header_parts.append(f"[confidence: {confidence:.0%}]")
                    if header_parts:
                        parts.append(" ".join(header_parts))

                    lines.append(" ".join(parts))
            elif isinstance(ent, str):
                lines.append(f"- {ent}")

        return "\n".join(lines) if lines else ""

    def format_search_result(self, result: Any, min_score: float = 0.3) -> str:
        """Format context search results with confidence annotations.

        Mirrors ``MuninnDBFormatter.format()``: iterates activation list,
        filters dormant entries, extracts concept + content + confidence.
        Adds confidence annotation ``[confidence: X%]`` for scores below
        0.6. Skips items below Bayesian confidence threshold.

        Args:
            result: List of activation dicts from MuninnDB ACTIVATE.
            min_score: Minimum confidence threshold (0.0–1.0).

        Returns:
            Clean prose text with confidence annotations.
        """
        activations: List[dict] = (
            result if isinstance(result, list) else []
        )
        if not activations:
            return "No relevant context found."

        lines: List[str] = []
        for item in activations:
            concept = item.get("concept", "")
            content = item.get("content", "")
            confidence = item.get("confidence")
            dormant = item.get("dormant", False)

            if dormant:
                continue  # Skip soft-deleted memories

            # Skip items below confidence threshold
            if confidence is not None and confidence < min_score:
                continue

            parts: List[str] = []
            if concept:
                parts.append(f"**{concept}**")
            if confidence is not None and confidence < 0.6:
                parts.append(f"[confidence: {confidence:.0%}]")

            # Memory type label if present
            mem_type = item.get("memory_type") or item.get("type")
            if mem_type:
                parts.append(f"[type: {mem_type}]")

            if content:
                parts.append(content)

            if parts:
                lines.append(" • " + " — ".join(parts))

        return "\n".join(lines) if lines else "No relevant context found."

    def format_expand_result(self, result: Any) -> str:
        """Format expanded context with activation chains.

        Extracts activation chains (Hebbian co-activated engrams),
        formats with hop_path info, groups by ctx-id. Filters by
        Bayesian confidence.

        Args:
            result: Expand results — list of activation dicts with chains.

        Returns:
            Formatted activation chains.
        """
        activations: List[dict] = (
            result if isinstance(result, list) else []
        )
        if not activations:
            return "No relevant context found for that ctx-id."

        lines: List[str] = []
        for item in activations:
            concept = item.get("concept", "")
            content = item.get("content", "")
            confidence = item.get("confidence")
            dormant = item.get("dormant", False)
            ctx_id = item.get("ctx_id", "")
            hop_path = item.get("hop_path")
            hop_depth = item.get("hop_depth", 0) or 0

            if dormant:
                continue

            # Skip low-confidence items
            if confidence is not None and confidence < 0.3:
                continue

            indent = "  " * min(hop_depth, 3)
            header_parts = []
            if ctx_id:
                header_parts.append(f"[ctx-id: {ctx_id}]")
            if hop_path:
                header_parts.append(f"path: {' → '.join(hop_path[:5])}")
            if confidence is not None:
                header_parts.append(f"[confidence: {confidence:.0%}]")

            header = " ".join(header_parts) if header_parts else ""

            if concept:
                entity_line = f"{indent}**{concept}**"
            else:
                entity_line = ""

            if header:
                lines.append(f"{entity_line} {header}".strip())
            elif entity_line:
                lines.append(entity_line)

            if content:
                lines.append(f"{indent}  {content.strip()}")

        return "\n".join(lines) if lines else "No relevant context found for that ctx-id."
