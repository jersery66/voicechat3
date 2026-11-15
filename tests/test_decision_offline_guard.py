"""Offline network guard: it must actually block, and must not block loopback."""

from __future__ import annotations

import os
import socket
import urllib.error
import urllib.request

import pytest

from deployment.net_guard import (
    NetworkGuard,
    OfflineViolation,
    install_network_guard,
    is_loopback_host,
)


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.1.2.3", "::1", "localhost", "LOCALHOST", "[::1]", "::ffff:127.0.0.1"],
)
def test_loopback_detection_accepts_local_addresses(host):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize(
    "host",
    ["8.8.8.8", "example.com", "10.0.0.5", "172.16.0.1", "0.0.0.0", "", None],
)
def test_loopback_detection_rejects_remote_or_unspecified(host):
    assert is_loopback_host(host) is False


def test_remote_connect_is_blocked_and_recorded():
    guard = install_network_guard()
    try:
        with pytest.raises(OfflineViolation):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("93.184.216.34", 80))
    finally:
        guard.restore()
    assert any("93.184.216.34" in entry for entry in guard.violations)


def test_remote_create_connection_is_blocked():
    guard = install_network_guard()
    try:
        with pytest.raises(OfflineViolation):
            socket.create_connection(("example.org", 443))
    finally:
        guard.restore()
    assert guard.violations


def test_remote_dns_lookup_is_blocked():
    guard = install_network_guard()
    try:
        with pytest.raises(OfflineViolation):
            socket.getaddrinfo("models.example.invalid", 443)
    finally:
        guard.restore()
    assert any(entry.startswith("dns:") for entry in guard.violations)


def test_loopback_traffic_is_allowed():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    guard = install_network_guard()
    try:
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        client.close()
    finally:
        guard.restore()
        server.close()
    assert guard.violations == []


def test_guard_restores_the_original_socket_functions():
    original_connect = socket.socket.connect
    original_create = socket.create_connection
    guard = install_network_guard()
    assert socket.socket.connect is not original_connect
    guard.restore()
    assert socket.socket.connect is original_connect
    assert socket.create_connection is original_create


def test_guard_is_idempotent_and_opts_in_via_context_manager():
    with NetworkGuard() as guard:
        assert guard.installed is True
        guard.install()  # second install must not double-wrap
        assert guard.installed is True
    assert guard.installed is False
    assert guard.summary()["installed"] is False


def test_urllib_request_under_guard_cannot_escape_loopback():
    guard = install_network_guard()
    try:
        with pytest.raises((OfflineViolation, urllib.error.URLError, OSError)):
            urllib.request.urlopen("http://93.184.216.34/", timeout=2)
    finally:
        guard.restore()
    assert guard.violations


def test_guard_clears_proxy_environment_and_restores_it(monkeypatch):
    """A loopback proxy would let a request re-originate outside this process."""
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:60700")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:60700")
    monkeypatch.delenv("all_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)

    guard = install_network_guard()
    try:
        remaining = {key.lower() for key in os.environ}
        assert "http_proxy" not in remaining
        assert "https_proxy" not in remaining
        assert {"http_proxy", "https_proxy"} <= {key.lower() for key in guard.cleared_proxy_env}
        # Windows also reads proxy settings from the registry; the guard stubs
        # the lookup so urllib cannot silently route through a local proxy.
        assert "getproxies" in guard.summary()["proxy_lookup_suppressed"]
        import urllib.request as urllib_request

        assert urllib_request.getproxies() == {}
    finally:
        guard.restore()

    assert os.environ["http_proxy"] == "http://127.0.0.1:60700"
    assert os.environ["https_proxy"] == "http://127.0.0.1:60700"
    import urllib.request as urllib_request

    assert urllib_request.getproxies is not None
    assert callable(urllib_request.getproxies)


def test_summary_is_json_safe():
    guard = install_network_guard()
    try:
        with pytest.raises(OfflineViolation):
            socket.create_connection(("1.1.1.1", 80))
    finally:
        guard.restore()
    summary = guard.summary()
    assert summary["installed"] is False
    assert isinstance(summary["violations"], list)
    assert summary["allowed_count"] == 0
