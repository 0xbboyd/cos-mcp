"""Abstract base class for context result formatters.

Each formatter knows how to extract clean prose from backend-native
response objects for context operations — compression summaries,
search results, and expand (ctx-id lookup) results.

Mirrors the ``MemoryFormatter`` pattern but with context-specific methods.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ContextFormatter(ABC):
    """Format backend context results into clean text for the model.

    Implementations are backend-specific — they know the shape of
    the response objects and how to extract meaningful content for
    context compression and retrieval.
    """

    @abstractmethod
    def format_compress_summary(self, result: Any) -> str:
        """Format a compression summary block for insertion into the message list.

        Args:
            result: Backend-native compression result (entity list, DAG,
                    activation chains, etc.).

        Returns:
            A formatted summary string suitable for insertion as a
            system message, or empty string if nothing to summarize.
        """
        ...

    @abstractmethod
    def format_search_result(self, result: Any, min_score: float = 0.3) -> str:
        """Format context search results into clean prose.

        Args:
            result: Backend-native query response.
            min_score: Minimum relevancy/confidence score threshold.

        Returns:
            Clean prose text, or empty string if nothing meets threshold.
        """
        ...

    @abstractmethod
    def format_expand_result(self, result: Any) -> str:
        """Format context expand results (ctx-id lookup) into clean prose.

        Args:
            result: Backend-native expand response (DAG path,
                    activation chains, etc.).

        Returns:
            Clean prose text with entity relationships, or
            ``"No relevant context found for that ctx-id."`` if empty.
        """
        ...
