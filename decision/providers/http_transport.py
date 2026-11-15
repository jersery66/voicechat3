"""Minimal HTTP transport for a local decision gateway.

Deliberately not httpx and not ``requests``:

- the decision gateway is a loopback service with no authentication, so the
  transport hard-refuses a non-loopback endpoint unless explicitly allowed;
- the system HTTP proxy must be bypassed.  In this deployment the proxy answers
  ``502`` for local targets, and an interview turn must never be forwarded to a
  proxy process;
- one connection per request is fine: a decision is a single request, and
  reuse across threads buys nothing here.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional

from decision.providers.base import DecisionTransportError, require_loopback

_DEFAULT_TIMEOUT = 5.0


class HttpDecisionTransport:
    """POST JSON to a loopback decision endpoint and decode JSON back."""

    def __init__(
        self,
        endpoint: str,
        *,
        path: str = "/v1/decide",
        timeout: float = _DEFAULT_TIMEOUT,
        allow_remote: bool = False,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("decision transport timeout must be positive")
        self.endpoint = require_loopback(endpoint.rstrip("/"), allow_remote=allow_remote)
        self.path = path if path.startswith("/") else f"/{path}"
        self.timeout = float(timeout)
        self.headers = dict(headers or {})
        # ``ProxyHandler({})`` gives an opener with an empty proxy table, so
        # ``http_proxy`` / ``https_proxy`` in the environment are ignored.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @property
    def url(self) -> str:
        return f"{self.endpoint}{self.path}"

    def post(self, payload: Mapping[str, Any]) -> Any:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        headers.update(self.headers)
        request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            raise DecisionTransportError(
                f"decision gateway returned HTTP {exc.code}: {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DecisionTransportError(f"decision gateway unreachable: {exc}") from exc

        if not raw.strip():
            raise DecisionTransportError("decision gateway returned an empty body")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DecisionTransportError(f"decision gateway returned invalid JSON: {exc}") from exc

    def get(self, path: str) -> Any:
        url = f"{self.endpoint}{path if path.startswith('/') else '/' + path}"
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise DecisionTransportError(f"decision gateway unreachable: {exc}") from exc

    def describe(self) -> dict[str, Any]:
        return {"transport": "http", "url": self.url, "timeout": self.timeout}
