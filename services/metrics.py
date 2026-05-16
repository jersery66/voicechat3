# Metrics Collector - lightweight in-process performance tracker.
#
# Usage:
#     from services.metrics import measure, get_metrics
#
#     @measure("llm.chat")
#     def chat(...): ...
#
#     # OR
#     with get_metrics().timer("rag.search"):
#         do_search()
#
#     stats = get_metrics().snapshot()  # → {"llm.chat": {"count": ..., "p50": ..., "p95": ..., "avg": ...}}

from __future__ import annotations

import functools
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Callable, Deque, Dict, Iterator, Optional

from services.logger import get_logger

logger = get_logger(__name__)


_DEFAULT_BUFFER = 200  # Keep at most N most-recent samples per metric


class Metrics:
    """Process-wide ring-buffer metric registry.

    Thread-safe; reads (snapshot) hold a short lock.
    """

    def __init__(self, buffer_size: int = _DEFAULT_BUFFER) -> None:
        self._buffer_size = buffer_size
        self._samples: Dict[str, Deque[float]] = {}
        self._counters: Dict[str, int] = {}
        self._lock = threading.Lock()

    def record(self, name: str, duration_ms: float) -> None:
        """Record a duration (milliseconds) for the named metric."""
        with self._lock:
            buf = self._samples.get(name)
            if buf is None:
                buf = deque(maxlen=self._buffer_size)
                self._samples[name] = buf
            buf.append(duration_ms)
            self._counters[name] = self._counters.get(name, 0) + 1

    @contextmanager
    def timer(self, name: str) -> Iterator[None]:
        """Context manager that records elapsed wall time on exit."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.record(name, (time.perf_counter() - t0) * 1000.0)

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        """Return a stats summary for every metric (count / avg / p50 / p95 / max)."""
        result: Dict[str, Dict[str, float]] = {}
        with self._lock:
            for name, buf in self._samples.items():
                if not buf:
                    continue
                samples = sorted(buf)
                n = len(samples)
                result[name] = {
                    "count": float(self._counters.get(name, n)),
                    "avg": sum(samples) / n,
                    "p50": samples[n // 2],
                    "p95": samples[min(n - 1, int(n * 0.95))],
                    "max": samples[-1],
                }
        return result

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._counters.clear()


_metrics_singleton: Optional[Metrics] = None


def get_metrics() -> Metrics:
    """Return the shared :class:`Metrics` instance."""
    global _metrics_singleton
    if _metrics_singleton is None:
        _metrics_singleton = Metrics()
    return _metrics_singleton


def measure(name: str) -> Callable:
    """Decorator that records execution time of a function as a Metrics sample.

    The decorator preserves the wrapped function's signature.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with get_metrics().timer(name):
                return func(*args, **kwargs)

        return wrapper

    return decorator
