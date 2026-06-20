"""HydraDB context result formatter.

Graph-aware formatting with DAG paths and ctx-id anchors for context
compression and retrieval results.
"""

from __future__ import annotations

from typing import Any, List

from cos_mcp.formatting.context_base import ContextFormatter


class HydraDBContextFormatter(ContextFormatter):
    """Format HydraDB context results — graph-aware formatting.

    Adds DAG path information, relationship edges, and ctx-id anchor
    labels to search and expand results. Mirrors the ``HydraDBFormatter``
    extraction pattern but includes graph context annotations.
    """

    def format_compress_summary(self, result: Any) -> str:
        """Format a compression summary block from HydraDB entities.

        Extracts entity list, builds ``[ctx-id: ...]`` headers, and
        formats topics/decisions/facts as a bulleted list.

        Args:
            result: Compression result — may be a dict with entities
                    or a list of entity descriptors.

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
                if summary:
                    header = f"[ctx-id: {ctx_id}]" if ctx_id else ""
                    lines.append(
                        f"- **{entity_type.title()}**: {summary} {header}".strip()
                    )
            elif isinstance(ent, str):
                lines.append(f"- {ent}")

        return "\n".join(lines) if lines else ""

    def format_search_result(self, result: Any, min_score: float = 0.3) -> str:
        """Format context search results with graph-aware annotations.

        Mirrors ``HydraDBFormatter.format()``: iterates ``result.data.chunks``,
        filters by ``relevancy_score >= min_score``, extracts ``chunk_content``.
        Adds graph context annotations (relationship edges, hop depth) when
        available.

        Args:
            result: SDK response object with ``.data.chunks``.
            min_score: Minimum relevancy_score to include a chunk.

        Returns:
            Clean prose text with graph annotations, or empty string.
        """
        chunks = getattr(result.data, "chunks", None) if hasattr(result, "data") else []
        if not chunks:
            return ""

        lines: List[str] = []
        for c in chunks:
            score = getattr(c, "relevancy_score", 0) or 0
            if score < min_score:
                continue
            content = getattr(c, "chunk_content", "") or ""
            if not content.strip():
                continue

            # Graph context annotations
            annotations: List[str] = []
            ctx_id = getattr(c, "ctx_id", None)
            if ctx_id:
                annotations.append(f"[ctx-id: {ctx_id}]")

            hop_depth = getattr(c, "hop_depth", None)
            if hop_depth is not None:
                annotations.append(f"(hop: {hop_depth})")

            edges = getattr(c, "relationship_edges", None)
            if edges:
                edge_str = ", ".join(str(e) for e in edges[:3])
                annotations.append(f"[edges: {edge_str}]")

            annotation_str = " ".join(annotations)
            if annotation_str:
                lines.append(f"{content.strip()} {annotation_str}")
            else:
                lines.append(content.strip())

        return "\n\n".join(lines) if lines else ""

    def format_expand_result(self, result: Any) -> str:
        """Format expanded context entities with DAG path information.

        Extracts all chunks matching a ctx-id, formats with multi-hop
        traversal paths and hierarchical indentation.

        Args:
            result: SDK response with expand results (chunks + paths).

        Returns:
            Formatted multi-hop context, or no-results message if empty.
        """
        chunks = getattr(result.data, "chunks", None) if hasattr(result, "data") else []
        if not chunks:
            return "No relevant context found for that ctx-id."

        lines: List[str] = []
        for c in chunks:
            content = getattr(c, "chunk_content", "") or ""
            if not content.strip():
                continue

            ctx_id = getattr(c, "ctx_id", "")
            hop_path = getattr(c, "hop_path", None)
            hop_depth = getattr(c, "hop_depth", 0) or 0

            indent = "  " * min(hop_depth, 3)
            header = f"[ctx-id: {ctx_id}]" if ctx_id else ""
            if hop_path:
                header += f" path: {' → '.join(hop_path)}"

            if header:
                lines.append(f"{indent}{header}")
                lines.append(f"{indent}{content.strip()}")
            else:
                lines.append(f"{indent}{content.strip()}")

        return "\n".join(lines) if lines else "No relevant context found for that ctx-id."
