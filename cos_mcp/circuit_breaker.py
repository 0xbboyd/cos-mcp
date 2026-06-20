"""Circuit breaker for memory provider resilience.

Provides independent read and write gauges — each trips after 5 consecutive
failures and opens for 120 seconds. Thread-safe via a shared lock.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Dual-gauge circuit breaker with independent read/write tracking.

    Each gauge counts consecutive failures. At 5 failures, the breaker
    opens for 120 seconds. A single success resets the counter.

    Thread-safe: all state transitions hold ``_lock``.
    """

    def __init__(self) -> None:
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
        """Increment read failure counter; trip at threshold 5."""
        with self._lock:
            self._read_failures += 1
            if self._read_failures >= 5:
                self._read_open_until = time.time() + 120
                logger.warning(
                    "%s read circuit breaker OPEN — 120s cooldown",
                    self._label,
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
        """Increment write failure counter; trip at threshold 5."""
        with self._lock:
            self._write_failures += 1
            if self._write_failures >= 5:
                self._write_open_until = time.time() + 120
                logger.warning(
                    "%s write circuit breaker OPEN — 120s cooldown",
                    self._label,
                )
