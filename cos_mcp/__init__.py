"""Shared infrastructure for Hermes Agent memory provider plugins.

This package provides the base classes, circuit breaker, backend abstraction,
and memory formatting utilities shared by the HydraDB and MuninnDB providers.

Note: ``BaseMemoryProvider`` imports from ``agent.memory_provider`` which is
only available inside the Hermes Agent runtime. Import it directly from
``cos_mcp.base_provider`` when needed.
"""

from cos_mcp.circuit_breaker import CircuitBreaker
from cos_mcp.backends.base import MemoryBackend
from cos_mcp.backends.hydradb import HydraDBBackend
from cos_mcp.formatting.base import MemoryFormatter
from cos_mcp.formatting.hydradb import HydraDBFormatter

__all__ = [
    "CircuitBreaker",
    "MemoryBackend",
    "HydraDBBackend",
    "MemoryFormatter",
    "HydraDBFormatter",
]
