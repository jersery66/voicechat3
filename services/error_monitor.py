# Error Monitor - centralised aggregation of WARNING+ log records.
#
# Wires up a :class:`logging.Handler` that mirrors WARNING / ERROR / CRITICAL
# records to a JSON-lines file under ``logs/errors.jsonl`` and to an in-memory
# ring buffer that can be read by the UI stats panel.

from __future__ import annotations

import json
import logging
import os
import threading
import traceback
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional

_BUFFER_SIZE = 200


class _RingHandler(logging.Handler):
    """Logging handler that mirrors records to disk and an in-memory ring buffer."""

    def __init__(self, log_path: str, buffer: Deque[Dict]) -> None:
        super().__init__(level=logging.WARNING)
        self._log_path = log_path
        self._buffer = buffer
        self._file_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - logging API
        try:
            entry = {
                "ts": datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                entry["traceback"] = "".join(traceback.format_exception(*record.exc_info))
            self._buffer.append(entry)
            with self._file_lock:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            # Never let logging itself raise; fall back to default handler stderr.
            self.handleError(record)


class ErrorMonitor:
    """Application-wide error monitor.

    Use :func:`install` once during startup to attach the handler to the root
    logger; query :meth:`get_recent` from the UI as needed.
    """

    def __init__(self, log_dir: str) -> None:
        self._buffer: Deque[Dict] = deque(maxlen=_BUFFER_SIZE)
        os.makedirs(log_dir, exist_ok=True)
        self._handler = _RingHandler(
            log_path=os.path.join(log_dir, "errors.jsonl"),
            buffer=self._buffer,
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._installed = False

    def install(self) -> None:
        if self._installed:
            return
        logging.getLogger().addHandler(self._handler)
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        logging.getLogger().removeHandler(self._handler)
        self._installed = False

    def get_recent(self, n: int = 50) -> List[Dict]:
        """Return up to ``n`` most-recent error records (newest last)."""
        if n <= 0:
            return []
        items = list(self._buffer)
        return items[-n:]


_monitor: Optional[ErrorMonitor] = None


def get_error_monitor(log_dir: Optional[str] = None) -> ErrorMonitor:
    """Return the shared error monitor, lazily installing it on first call."""
    global _monitor
    if _monitor is None:
        if log_dir is None:
            # Default: <APP_ROOT>/logs
            app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            log_dir = os.path.join(app_root, "logs")
        _monitor = ErrorMonitor(log_dir)
        _monitor.install()
    return _monitor
