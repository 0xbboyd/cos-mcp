"""Abstract base class for memory backends.

Each backend wraps a specific storage engine (HydraDB Cloud, MuninnDB local)
and exposes a uniform interface for query, ingest, delete, and health checks.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class MemoryBackend(ABC):
    """Abstract interface for a memory storage backend.

    Implementations wrap SDKs or REST APIs for specific databases.
    All methods are synchronous (Hermes provider contract).
    """

    @abstractmethod
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
        """Search memory and return results in backend-native format.

        Returns an opaque result object — the corresponding Formatter
        knows how to extract text from it.
        """
        ...

    @abstractmethod
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
        """Store a memory entry in the backend."""
        ...

    @abstractmethod
    def delete(self, memory_id: str) -> None:
        """Delete a memory entry by ID."""
        ...

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the backend is reachable and healthy."""
        ...

    @abstractmethod
    def provision(self) -> bool:
        """Ensure the backend is ready for use (create tenants, vaults, etc.).

        Returns True when ready, False if provisioning failed (circuit
        breaker handles downstream unavailability).
        """
        ...

    @abstractmethod
    def shutdown(self) -> None:
        """Release backend resources (connections, sessions, etc.)."""
        ...
