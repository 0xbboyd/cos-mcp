"""MuninnDB activation result formatter.

Formats MuninnDB ACTIVATE response activations into clean prose
for the model, with confidence indicators for low-confidence items.
"""

from __future__ import annotations

from typing import Any, List

from cos_mcp.formatting.base import MemoryFormatter


class MuninnDBFormatter(MemoryFormatter):
    """Format MuninnDB activations into clean prose.

    MuninnDB activations include: concept, content, score, confidence,
    why (explanation), hop_path (graph traversal path), dormant flag.
    Dormant (soft-deleted) memories are skipped.
    """

    def format(self, result: Any, min_score: float = 0.3) -> str:
        """Format activation items into clean prose.

        Args:
            result: List of activation dicts from MuninnDB ACTIVATE.
            min_score: Ignored — MuninnDB uses confidence, not score.
                Filtering is done at the backend level.

        Returns:
            Clean prose text, or empty string if no activations qualify.
        """
        activations: List[dict] = result if isinstance(result, list) else []
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
