"""HydraDB query result formatter.

Extracts clean prose from HydraDB SDK response objects, avoiding the
72-89% framing overhead of ``build_string()``.
"""

from __future__ import annotations

from typing import Any

from cos_mcp.formatting.base import MemoryFormatter


class HydraDBFormatter(MemoryFormatter):
    """Format HydraDB query chunks into clean prose.

    Filters by ``relevancy_score >= min_score``, extracts only
    ``chunk_content``, joins with double newlines.
    """

    def format(self, result: Any, min_score: float = 0.3) -> str:
        """Extract clean memory text from HydraDB query results.

        Args:
            result: SDK response object with ``.data.chunks``.
            min_score: Minimum relevancy_score to include a chunk.

        Returns:
            Clean prose text, or empty string if no chunks qualify.
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
