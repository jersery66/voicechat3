"""Loopback-only HTTP front for the decision engine.

Deliberately built on ``http.server``: the service must start on an offline
machine with nothing but the standard library, and it must not add a web
framework to the interview appliance.

Log lines never contain interview text -- only method, path, status, item count
and latency.  That matches the rest of the project's privacy contract.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional, Sequence
from urllib.parse import urlparse

MAX_BODY_BYTES = 1024 * 1024
MAX_ITEMS_PER_REQUEST = 32
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _is_loopback(host: str) -> bool:
    return str(host or "").strip().lower() in LOOPBACK_HOSTS


class DecisionService:
    """Bind an engine to a loopback port.

    ``engine`` only needs ``decide_one``, ``decide_many``, ``health`` and
    ``stats``, which lets the tests drive the real HTTP stack with a stub.
    """

    def __init__(
        self,
        engine: Any,
        *,
        host: str = "127.0.0.1",
        port: int = 8100,
        allow_remote: bool = False,
        log: Optional[Any] = None,
    ) -> None:
        if not allow_remote and not _is_loopback(host):
            raise ValueError(
                f"refusing to bind decision service to non-loopback host {host!r}; "
                "pass allow_remote=True only on a network you control end to end"
            )
        self.engine = engine
        self.host = host
        self.port = int(port)
        self.allow_remote = bool(allow_remote)
        self._log = log
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    # -- properties -------------------------------------------------------

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host
        return f"http://{host}:{self.port}"

    @property
    def running(self) -> bool:
        return self._httpd is not None and self._thread is not None and self._thread.is_alive()

    # -- lifecycle --------------------------------------------------------

    def build(self) -> ThreadingHTTPServer:
        handler = _make_handler(self.engine, self._log)
        httpd = ThreadingHTTPServer((self.host, self.port), handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        # Port 0 lets the OS pick one; record the resolved port so callers can
        # address the service without guessing.
        self.port = httpd.server_address[1]
        return httpd

    def start(self) -> "DecisionService":
        if self.running:
            return self
        httpd = self._httpd or self.build()
        self._thread = threading.Thread(
            target=httpd.serve_forever, name="decision-service", daemon=True
        )
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        httpd = self._httpd or self.build()
        httpd.serve_forever()

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd, self._thread = None, None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(timeout=5.0)

    def wait_ready(self, timeout: float = 10.0) -> bool:
        """Poll the TCP port until the listener accepts a connection."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection((self.url_host(), self.port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.05)
        return False

    def url_host(self) -> str:
        return "127.0.0.1" if self.host in {"0.0.0.0", "::"} else self.host

    def __enter__(self) -> "DecisionService":
        return self.start()

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()


def _emit(log: Optional[Any], message: str) -> None:
    if log is None:
        return
    try:
        log(message)
    except Exception:
        pass


def _make_handler(engine: Any, log: Optional[Any]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "voicechat3-decision/1"
        sys_version = ""

        # -- plumbing -----------------------------------------------------

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            # BaseHTTPRequestHandler would echo the query string; we keep the
            # access log to one metadata line the operator can grep.
            _emit(log, f"[DecisionService] {self.command} {urlparse(self.path).path} -> {args[1] if len(args) > 1 else '?'}")

        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _error(self, status: int, message: str, *, detail: Optional[str] = None) -> None:
            payload = {"error": message}
            if detail:
                payload["detail"] = detail
            self._send(status, payload)

        def _read_json(self) -> Any:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("invalid Content-Length")
            if length <= 0:
                raise ValueError("empty body")
            if length > MAX_BODY_BYTES:
                raise ValueError(f"body exceeds {MAX_BODY_BYTES} bytes")
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise ValueError(f"body is not valid JSON: {exc}")

        # -- routes -------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/health":
                self._send(HTTPStatus.OK, dict(engine.health()))
                return
            if path == "/v1/stats":
                self._send(HTTPStatus.OK, dict(engine.stats()))
                return
            self._error(HTTPStatus.NOT_FOUND, "unknown_path")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/v1/decide":
                self._error(HTTPStatus.NOT_FOUND, "unknown_path")
                return
            try:
                payload = self._read_json()
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_request", detail=str(exc))
                return
            try:
                response = self._decide(payload)
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_request", detail=str(exc))
                return
            except Exception as exc:  # model failure, OOM, bad label ...
                _emit(log, f"[DecisionService] decide failed: {type(exc).__name__}")
                self._error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "decision_unavailable",
                    detail=f"{type(exc).__name__}: {exc}",
                )
                return
            self._send(HTTPStatus.OK, response)

        # -- payload handling ---------------------------------------------

        def _decide(self, payload: Any) -> dict[str, Any]:
            if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                items = payload["items"]
                if not items:
                    raise ValueError("items is empty")
                if len(items) > MAX_ITEMS_PER_REQUEST:
                    raise ValueError(f"items exceeds {MAX_ITEMS_PER_REQUEST}")
                for item in items:
                    _validate_item(item)
                results = engine.decide_many(items)
                return {"results": results, "count": len(results)}
            _validate_item(payload)
            return dict(engine.decide_one(
                str(payload.get("state") or ""),
                name=str(payload.get("name") or ""),
                question=str(payload.get("question") or ""),
                options=list(payload.get("options") or ()),
            ))

    return Handler


def _validate_item(item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError("each item must be an object")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("each item needs a non-empty 'name'")
    question = item.get("question")
    if not isinstance(question, str) or not question.strip():
        raise ValueError(f"item {name!r} needs a non-empty 'question'")
    options = item.get("options")
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        raise ValueError(f"item {name!r} needs an 'options' list")
    if not 2 <= len(options) <= 26:
        raise ValueError(f"item {name!r} needs 2..26 options, got {len(options)}")
    if any(not isinstance(option, str) or not option.strip() for option in options):
        raise ValueError(f"item {name!r} has a blank option")
