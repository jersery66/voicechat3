"""Local decision service over real HTTP: shapes, guards, and provider parity."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

import pytest

from decision.contracts import DecisionRequest
from decision.engine.readout import Readout, ReadoutError
from decision.providers.anyjev import AnyJevDecisionProvider
from decision.providers.base import DECISION_QUESTIONS
from decision.service.engine import DecisionEngine
from decision.service.server import DecisionService

_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class StubScorer:
    def __init__(self, logits=None):
        self.logits = dict(logits or {})

    def label_tokens(self, label):
        return (1000 + (sum(label.encode("utf-8")) % 500),)

    def token_logprobs(self, prompt, token_ids):
        return [self.logits.get(tid, -float(index)) for index, tid in enumerate(token_ids)]

    def sequence_logprobs(self, prompt, continuations):
        return [0.0 for _ in continuations]

    def describe(self):
        return {"backend": "stub"}

    def vram(self):
        return {"available": False, "device": "cpu"}


def _engine(**kwargs):
    readout = Readout(kwargs.pop("scorer", StubScorer()), mode="next_token")
    return DecisionEngine(model_path="<stub-checkpoint>", **kwargs).attach(readout)


@pytest.fixture()
def service():
    service = DecisionService(_engine(), port=0, log=lambda message: None)
    service.build()
    service.start()
    assert service.wait_ready(5.0) is True
    try:
        yield service
    finally:
        service.stop()


def get(url, timeout=5.0):
    with _NO_PROXY.open(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post(url, payload, timeout=10.0):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with _NO_PROXY.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def status_of(url, payload=None):
    try:
        if payload is None:
            get(url)
        else:
            post(url, payload)
    except urllib.error.HTTPError as exc:
        return exc.code
    return 200


def test_non_loopback_bind_is_refused():
    with pytest.raises(ValueError, match="non-loopback"):
        DecisionService(_engine(), host="0.0.0.0", port=0)
    # explicit opt-in still allowed
    DecisionService(_engine(), host="0.0.0.0", port=0, allow_remote=True)


def test_health_and_stats(service):
    health = get(f"{service.url}/health")
    assert health["status"] == "ok"
    assert health["model_path"] == "<stub-checkpoint>"
    assert health["readout_mode"] == "auto"          # engine setting
    assert health["model"]["mode"] == "next_token"    # actual readout
    assert "integrity" not in health  # never verified for an attached readout

    stats = get(f"{service.url}/v1/stats")
    assert stats["calls"] == 0
    assert set(stats["latency_ms"]) == {"p50", "p90", "p95", "p99"}


def test_single_decision_shape(service):
    payload = {"state": "【本轮发言】x", "name": "action", "question": "下一步？", "options": ["chat", "start_scale"]}
    response = post(f"{service.url}/v1/decide", payload)
    assert response["name"] == "action"
    assert response["answer"] in {"chat", "start_scale"}
    assert response["route"] in {"label_token", "candidate"}  # "one_forward" is Tacit-only
    assert abs(sum(response["probs"].values()) - 1.0) < 1e-6
    assert response["probs"] == response["probabilities"]


def test_batch_preserves_request_order(service):
    items = [
        {"state": "s", "name": f"q{index}", "question": "text", "options": ["yes", "no"]}
        for index in range(5)
    ]
    response = post(f"{service.url}/v1/decide", {"items": items})
    assert response["count"] == 5
    assert [item["name"] for item in response["results"]] == [f"q{index}" for index in range(5)]


def test_counter_tracks_calls(service):
    payload = {"state": "s", "name": "action", "question": "q", "options": ["chat", "start_scale"]}
    post(f"{service.url}/v1/decide", payload)
    post(f"{service.url}/v1/decide", payload)
    stats = get(f"{service.url}/v1/stats")
    assert stats["calls"] == 2
    assert stats["errors"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "action", "question": "q", "options": ["chat"]},
        {"name": "", "question": "q", "options": ["a", "b"]},
        {"name": "action", "question": "", "options": ["a", "b"]},
        {"name": "action", "question": "q", "options": "ab"},
        {"name": "action", "question": "q", "options": ["a", ""]},
        {"items": []},
        {"items": [{"name": "x"}]},
    ],
)
def test_invalid_payloads_are_rejected_with_400(service, payload):
    assert status_of(f"{service.url}/v1/decide", payload) == 400


def test_unknown_paths_return_404(service):
    assert status_of(f"{service.url}/v1/unknown") == 404
    assert status_of(f"{service.url}/", {"name": "x"}) == 404


def test_engine_failure_returns_503():
    class FailingEngine:
        def decide_one(self, state, *, name, question, options):
            raise ReadoutError("model blew up")

        def decide_many(self, items):
            raise ReadoutError("model blew up")

        def health(self):
            return {"status": "ok"}

        def stats(self):
            return {}

    service = DecisionService(FailingEngine(), port=0)
    service.build()
    service.start()
    try:
        assert service.wait_ready(5.0)
        payload = {"state": "s", "name": "action", "question": "q", "options": ["a", "b"]}
        assert status_of(f"{service.url}/v1/decide", payload) == 503
    finally:
        service.stop()


def test_oversized_body_is_rejected(service):
    # Sent at the socket level: the server rejects on Content-Length before
    # reading the body, so a high-level client may see a reset instead of a
    # response.  What matters is the status line, not the client's comfort.
    with socket.create_connection((service.url_host(), service.port), timeout=5) as sock:
        head = (
            "POST /v1/decide HTTP/1.1\r\n"
            "Host: 127.0.0.1\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {1024 * 1024 + 10}\r\n\r\n"
        )
        sock.sendall(head.encode("ascii"))
        raw = sock.recv(4096).decode("utf-8", "replace")
    assert raw, "the service must answer instead of hanging up silently"
    assert "400" in raw.splitlines()[0]


def test_access_log_never_contains_interview_text():
    lines: list[str] = []
    service = DecisionService(_engine(), port=0, log=lines.append)
    service.build()
    service.start()
    try:
        assert service.wait_ready(5.0)
        secret = "我不想活了这句话绝不能出现在日志里"
        payload = {"state": secret, "name": "action", "question": "q", "options": ["chat", "start_scale"]}
        post(f"{service.url}/v1/decide", payload)
    finally:
        service.stop()
    assert lines, "the service should log at least the request line"
    joined = "\n".join(lines)
    assert "不想活" not in joined
    assert "/v1/decide" in joined


def test_provider_speaks_to_the_service_unchanged(service):
    provider = AnyJevDecisionProvider(endpoint=service.url, timeout=10.0, batch=True)
    request = DecisionRequest(
        user_text="最近睡不好",
        session_state="interview",
        turn_index=3,
        active_scale=None,
        game_active=False,
        recent_history="来访者：我不知道说什么",
    )
    raw = provider.decide(request)

    assert raw.route == "batch"
    assert raw.provider == "anyjev_decider"
    assert set(raw.per_question) == {spec.name for spec in DECISION_QUESTIONS}
    for distribution in raw.per_question.values():
        assert abs(sum(distribution.values()) - 1.0) < 1e-6
    assert raw.action in DECISION_QUESTIONS[2].options
    assert 0.0 <= raw.confidence <= 1.0


def test_batch_rejection_by_the_engine_is_explicit():
    """A broken batch must surface as 400/503, never as a silent partial answer."""

    class BadBatchEngine:
        def decide_one(self, state, *, name, question, options):
            raise AssertionError("single path must not be used")

        def decide_many(self, items):
            raise ValueError("batch not supported here")

        def health(self):
            return {"status": "ok"}

        def stats(self):
            return {}

    service = DecisionService(BadBatchEngine(), port=0)
    service.build()
    service.start()
    try:
        assert service.wait_ready(5.0)
        payload = {"items": [{"state": "s", "name": "a", "question": "q", "options": ["a", "b"]}]}
        assert status_of(f"{service.url}/v1/decide", payload) == 400
    finally:
        service.stop()


def test_port_zero_is_reported_back(service):
    assert service.port > 0
    assert service.url.endswith(str(service.port))


def test_stop_releases_the_port(service):
    port = service.port
    service.stop()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
