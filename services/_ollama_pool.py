# Ollama Client Pool - shared ollama.Client singleton for the entire application.
#
# Avoids repeated connection handshakes across `llm_service` and `report_service`.
# Thread-safe (ollama.Client itself uses httpx which is thread-safe for concurrent requests).

from __future__ import annotations

import threading
from typing import Optional

import ollama

from services.logger import get_logger

logger = get_logger(__name__)


_client_lock = threading.Lock()
_client_cache: dict[str, ollama.Client] = {}


def get_ollama_client(host: str) -> ollama.Client:
    """Return a process-wide singleton :class:`ollama.Client` for the given host.

    Args:
        host: Ollama HTTP endpoint (e.g. ``http://localhost:11434``).

    Returns:
        Shared ollama.Client instance.
    """
    with _client_lock:
        client = _client_cache.get(host)
        if client is None:
            client = ollama.Client(host=host)
            _client_cache[host] = client
            logger.debug(f"Created shared ollama.Client for {host}")
        return client


def reset_clients() -> None:
    """Drop all cached clients (used for tests / forced reconnection)."""
    with _client_lock:
        _client_cache.clear()
