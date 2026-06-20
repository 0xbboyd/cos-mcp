"""Shared infrastructure for Hermes Agent memory provider and context engine plugins.

This package provides base classes, circuit breaker, backend abstraction,
memory formatting utilities, and context engine formatting shared by the
HydraDB and MuninnDB providers and context engines.

Note: ``BaseMemoryProvider`` (``cos_mcp.base_provider``) and
``BaseContextEngine`` (``cos_mcp.base_context_engine``) import from
``agent.*`` ABCs which are only available inside the Hermes Agent runtime.
Import them directly from their submodule when needed.
"""

from cos_mcp.circuit_breaker import CircuitBreaker
from cos_mcp.backends.base import MemoryBackend
from cos_mcp.backends.hydradb import HydraDBBackend
from cos_mcp.backends.muninn import MuninnDBBackend
from cos_mcp.formatting.base import MemoryFormatter
from cos_mcp.formatting.hydradb import HydraDBFormatter
from cos_mcp.formatting.muninn import MuninnDBFormatter
from cos_mcp.formatting.context_base import ContextFormatter
from cos_mcp.formatting.hydradb_context import HydraDBContextFormatter
from cos_mcp.formatting.muninn_context import MuninnDBContextFormatter

# BaseContextEngine requires the Hermes Agent runtime (agent.context_engine).
# Import gracefully when available; direct imports from
# ``cos_mcp.base_context_engine`` always work when the runtime is on sys.path.
try:
    from cos_mcp.base_context_engine import BaseContextEngine
except ImportError:
    BaseContextEngine = None  # type: ignore[assignment]

__all__ = [
    "CircuitBreaker",
    "MemoryBackend",
    "HydraDBBackend",
    "MuninnDBBackend",
    "MemoryFormatter",
    "HydraDBFormatter",
    "MuninnDBFormatter",
    "ContextFormatter",
    "HydraDBContextFormatter",
    "MuninnDBContextFormatter",
    "BaseContextEngine",
]
