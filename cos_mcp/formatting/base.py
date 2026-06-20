"""Abstract base class for memory result formatters.

Each formatter knows how to extract clean prose from backend-native
response objects, stripping framing overhead (headers, IDs, scores).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MemoryFormatter(ABC):
    """Format backend query results into clean text for the model.

    Implementations are backend-specific — they know the shape of
    the response objects and how to extract the meaningful content.
    """

    @abstractmethod
    def format(self, result: Any, min_score: float = 0.3) -> str:
        """Extract clean memory text from a backend query result.

        Args:
            result: Backend-native query response (SDK object, dict, etc.)
            min_score: Minimum relevancy/confidence score threshold.

        Returns:
            Clean prose text, or empty string if nothing meets threshold.
        """
        ...
