"""Circuit breaker for memory provider and context engine resilience.

Provides independent read and write gauges — each trips after a configurable
number of consecutive failures and opens for a configurable cooldown period.
Thread-safe via a shared lock.

Defaults (v1.0): 5 failures → 120s cooldown (memory providers).
Context engines typically use 3 failures → 120s cooldown (lower I/O frequency).
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Dual-gauge circuit breaker with independent read/write tracking.

    Each gauge counts consecutive failures. At ``failure_threshold``
    consecutive failures, the breaker opens for ``cooldown_seconds``.
    A single success resets the counter.

    Configurable thresholds allow context engines to use a lower
    ``failure_threshold`` (3 vs 5) for faster trip on infrequent I/O.

    Thread-safe: all state transitions hold ``_lock``.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 120.0,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._read_failures = 0
        self._write_failures = 0
        self._read_open_until = 0.0
        self._write_open_until = 0.0
        self._lock = threading.Lock()
        self._label = ""  # Set by the provider for log messages

    def set_label(self, label: str) -> None:
        """Set a label for log messages (e.g. 'HydraDB' or 'MuninnDB')."""
        self._label = label

    # --- Read gauge ---

    def is_read_open(self) -> bool:
        """Return True if the read circuit breaker is currently open."""
        with self._lock:
            if self._read_open_until and time.time() < self._read_open_until:
                return True
            return False

    def record_read_success(self) -> None:
        """Reset the read failure counter and close the breaker."""
        with self._lock:
            self._read_failures = 0
            self._read_open_until = 0.0

    def record_read_failure(self) -> None:
        """Increment read failure counter; trip at configured threshold."""
        with self._lock:
            self._read_failures += 1
            if self._read_failures >= self._failure_threshold:
                self._read_open_until = time.time() + self._cooldown_seconds
                logger.warning(
                    "%s read circuit breaker OPEN — %ds cooldown",
                    self._label,
                    self._cooldown_seconds,
                )

    # --- Write gauge ---

    def is_write_open(self) -> bool:
        """Return True if the write circuit breaker is currently open."""
        with self._lock:
            if self._write_open_until and time.time() < self._write_open_until:
                return True
            return False

    def record_write_success(self) -> None:
        """Reset the write failure counter and close the breaker."""
        with self._lock:
            self._write_failures = 0
            self._write_open_until = 0.0

    def record_write_failure(self) -> None:
        """Increment write failure counter; trip at configured threshold."""
        with self._lock:
            self._write_failures += 1
            if self._write_failures >= self._failure_threshold:
                self._write_open_until = time.time() + self._cooldown_seconds
                logger.warning(
                    "%s write circuit breaker OPEN — %ds cooldown",
                    self._label,
                    self._cooldown_seconds,
                )
