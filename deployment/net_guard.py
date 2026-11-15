"""Runtime network guard for offline acceptance runs.

An "offline" claim is only worth something if it is enforced.  This module
patches the socket layer so that any attempt to open a connection to a
non-loopback address raises :class:`OfflineViolation` and is recorded.  It is
used by ``scripts/decision/offline_decision_check.py`` so the acceptance probe
proves *by construction* that the decision path made no outbound call.

Loopback stays allowed on purpose: the decision client talks to the local
service over 127.0.0.1, and that is the only network traffic we permit.

Usage::

    from deployment.net_guard import install_network_guard

    guard = install_network_guard()
    try:
        ...  # exercise the decision path
    finally:
        guard.restore()
    assert guard.violations == (), guard.violations
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

_LOCAL_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}

#: Proxy environment variables that would let a loopback connection re-originate
#: the request outside this process.  The guard clears them for its lifetime:
#: a credential-free interview appliance must never depend on a local proxy, and
#: a request that slips through a proxy is exactly the egress we are proving
#: does not happen.
_PROXY_ENV_KEYS = (
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "ftp_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "FTP_PROXY",
)

#: On Windows ``urllib`` reads proxy settings from the registry as well as the
#: environment, so clearing env vars is not enough: the default opener would
#: still route through a local proxy that re-originates the request outside this
#: process.  The guard therefore also stubs out ``getproxies``.
_PROXY_LOOKUP_NAMES = ("getproxies", "getproxies_environment")


class OfflineViolation(RuntimeError):
    """Raised when code under the guard tries to reach a non-loopback address."""


def is_loopback_host(host: Any) -> bool:
    """True for 127.0.0.0/8, ::1 and the localhost aliases."""
    if host is None:
        return False
    text = str(host).strip().strip("[]").lower()
    if not text:
        return False
    if text in _LOCAL_NAMES:
        return True
    if text.startswith("::ffff:"):
        text = text.split("::ffff:", 1)[1]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


@dataclass
class NetworkGuard:
    """Installed socket patch plus the evidence it collected."""

    allow_loopback: bool = True
    max_records: int = 64
    violations: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)
    cleared_proxy_env: list[str] = field(default_factory=list)
    _original_connect: Optional[Callable[..., Any]] = None
    _original_connect_ex: Optional[Callable[..., Any]] = None
    _original_create: Optional[Callable[..., Any]] = None
    _original_getaddrinfo: Optional[Callable[..., Any]] = None
    _proxy_env_backup: dict[str, str] = field(default_factory=dict, repr=False)
    _proxy_lookup_backup: dict[str, Any] = field(default_factory=dict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- classification ---------------------------------------------------

    def _describe(self, address: Any) -> str:
        if isinstance(address, tuple) and len(address) >= 2:
            return f"{address[0]}:{address[1]}"
        return str(address)

    def _host_of(self, address: Any) -> Any:
        if isinstance(address, tuple) and address:
            return address[0]
        return address

    def _permitted(self, address: Any) -> bool:
        if self.allow_loopback and is_loopback_host(self._host_of(address)):
            return True
        return False

    def _record(self, bucket: list[str], entry: str) -> None:
        with self._lock:
            if len(bucket) < self.max_records:
                bucket.append(entry)

    # -- install / restore ------------------------------------------------

    def install(self) -> "NetworkGuard":
        if self._original_connect is not None:
            return self
        self._original_connect = socket.socket.connect
        self._original_connect_ex = socket.socket.connect_ex
        self._original_create = socket.create_connection
        self._original_getaddrinfo = socket.getaddrinfo

        guard = self

        def guarded_connect(sock: Any, address: Any) -> Any:
            if not guard._permitted(address):
                entry = guard._describe(address)
                guard._record(guard.violations, entry)
                raise OfflineViolation(f"outbound connection blocked by offline guard: {entry}")
            guard._record(guard.allowed, guard._describe(address))
            return guard._original_connect(sock, address)

        def guarded_connect_ex(sock: Any, address: Any) -> Any:
            if not guard._permitted(address):
                entry = guard._describe(address)
                guard._record(guard.violations, entry)
                raise OfflineViolation(f"outbound connection blocked by offline guard: {entry}")
            guard._record(guard.allowed, guard._describe(address))
            return guard._original_connect_ex(sock, address)

        def guarded_create(address: Any, *args: Any, **kwargs: Any) -> Any:
            if not guard._permitted(address):
                entry = guard._describe(address)
                guard._record(guard.violations, entry)
                raise OfflineViolation(f"outbound connection blocked by offline guard: {entry}")
            guard._record(guard.allowed, guard._describe(address))
            return guard._original_create(address, *args, **kwargs)

        def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
            if not (guard.allow_loopback and is_loopback_host(host)):
                entry = f"dns:{host}"
                guard._record(guard.violations, entry)
                raise OfflineViolation(f"DNS lookup blocked by offline guard: {host}")
            return guard._original_getaddrinfo(host, *args, **kwargs)

        socket.socket.connect = guarded_connect  # type: ignore[assignment]
        socket.socket.connect_ex = guarded_connect_ex  # type: ignore[assignment]
        socket.create_connection = guarded_create  # type: ignore[assignment]
        socket.getaddrinfo = guarded_getaddrinfo  # type: ignore[assignment]

        for key in _PROXY_ENV_KEYS:
            if key in os.environ:
                self._proxy_env_backup[key] = os.environ.pop(key)
                self.cleared_proxy_env.append(key)

        import urllib.request as _urllib_request

        for name in _PROXY_LOOKUP_NAMES:
            original = getattr(_urllib_request, name, None)
            if original is None:
                continue
            self._proxy_lookup_backup[name] = original
            setattr(_urllib_request, name, lambda *args, **kwargs: {})
        return self

    def restore(self) -> None:
        if self._original_connect is not None:
            socket.socket.connect = self._original_connect  # type: ignore[assignment]
        if self._original_connect_ex is not None:
            socket.socket.connect_ex = self._original_connect_ex  # type: ignore[assignment]
        if self._original_create is not None:
            socket.create_connection = self._original_create  # type: ignore[assignment]
        if self._original_getaddrinfo is not None:
            socket.getaddrinfo = self._original_getaddrinfo  # type: ignore[assignment]
        self._original_connect = None
        self._original_connect_ex = None
        self._original_create = None
        self._original_getaddrinfo = None
        for key, value in self._proxy_env_backup.items():
            os.environ[key] = value
        self._proxy_env_backup.clear()

        import urllib.request as _urllib_request

        for name, original in self._proxy_lookup_backup.items():
            setattr(_urllib_request, name, original)
        self._proxy_lookup_backup.clear()

    @property
    def installed(self) -> bool:
        return self._original_connect is not None

    def summary(self) -> dict[str, Any]:
        return {
            "installed": self.installed,
            "allow_loopback": self.allow_loopback,
            "violations": list(self.violations),
            "allowed_count": len(self.allowed),
            "allowed_sample": sorted(set(self.allowed))[:8],
            "proxy_env_cleared": list(self.cleared_proxy_env),
            "proxy_lookup_suppressed": sorted(self._proxy_lookup_backup),
        }

    def __enter__(self) -> "NetworkGuard":
        return self.install()

    def __exit__(self, *exc_info: Any) -> None:
        self.restore()


def install_network_guard(*, allow_loopback: bool = True) -> NetworkGuard:
    return NetworkGuard(allow_loopback=allow_loopback).install()
