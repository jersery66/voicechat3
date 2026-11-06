"""Tacit checkpoint adapter (Tacit-1.7B / 2B / 4B).

Kept separate from :mod:`decision.providers.anyjev` on purpose.  The two
backends look similar but are not the same API:

- ``Decider`` reads K-option choices from a general causal LM and carries a
  ``level`` (raw / L0) plus rotation settings.
- ``Tacit`` is a self-distilled checkpoint: one prefill per decision, it
  returns ``route`` (``"one_forward"`` or ``"cot"``) and can escalate a capped
  share of decisions to its own reasoning.

Collapsing them into one adapter would have to ignore exactly the fields that
make each backend verifiable, so they stay separate and share only the socket
transport.

The in-process path is implemented but unexecuted in this repository: no Tacit
checkpoint is installed, and nothing here downloads weights implicitly.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

from decision.contracts import DecisionRequest, RawDecision
from decision.providers.base import (
    DecisionBackendUnavailable,
    QuestionSpec,
    build_questions,
    build_scale_choice_question,
    build_state_text,
    compose_decision,
)
from decision.providers.http_transport import HttpDecisionTransport
from decision.timing import InterventionIntent, normalize_intervention

_ROUTE_VALUES = ("one_forward", "cot", "batch", "sequential", "inprocess")


def _parse_tacit_payload(payload: Any) -> tuple[str, dict[str, float], Optional[str]]:
    """Read one Tacit decision: ``{"answer", "probs", "route"}``."""
    if not isinstance(payload, dict):
        raise DecisionBackendUnavailable(
            f"tacit gateway returned {type(payload).__name__}, expected an object"
        )
    answer = payload.get("answer") or payload.get("label")
    probabilities = payload.get("probs") or payload.get("probabilities")
    if not isinstance(answer, str) or not isinstance(probabilities, dict) or not probabilities:
        raise DecisionBackendUnavailable(
            "tacit gateway response has no usable answer/probs; "
            f"keys={sorted(payload)}"
        )
    route = payload.get("route")
    return answer.strip(), {str(k): float(v) for k, v in probabilities.items()}, (
        str(route) if route else None
    )


class TacitDecisionProvider:
    """Tacit decision backend, over a local gateway or in-process."""

    name = "tacit"

    def __init__(
        self,
        *,
        transport: str = "server",
        endpoint: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 5.0,
        allow_remote_endpoint: bool = False,
        allow_model_download: bool = False,
        adaptive: bool = False,
        tau: float = 0.5,
        max_cot_share: Optional[float] = 0.2,
        tacit_factory: Optional[Any] = None,
        template: str = "en",
    ) -> None:
        self.transport = str(transport or "server").strip().lower()
        if self.transport not in {"server", "inprocess"}:
            raise ValueError("tacit transport must be 'server' or 'inprocess'")
        # The English wording is what the official checkpoint was trained
        # against; "zh" exists so the difference is measured, not assumed.
        self.template = str(template or "en").strip().lower()
        self.questions = build_questions(self.template)
        self.endpoint = endpoint
        self.model = model
        self.timeout = float(timeout)
        self.allow_remote_endpoint = bool(allow_remote_endpoint)
        self.allow_model_download = bool(allow_model_download)
        self.adaptive = bool(adaptive)
        self.tau = float(tau)
        self.max_cot_share = max_cot_share
        self._tacit = None
        self._tacit_factory = tacit_factory

        self._http: Optional[HttpDecisionTransport] = None
        if self.transport == "server":
            if not endpoint:
                raise DecisionBackendUnavailable(
                    "tacit server transport needs decision.endpoint "
                    "(e.g. http://127.0.0.1:8100)"
                )
            self._http = HttpDecisionTransport(
                endpoint,
                timeout=self.timeout,
                allow_remote=self.allow_remote_endpoint,
            )

    # -- provider protocol -------------------------------------------------

    def is_available(self) -> bool:
        if self.transport == "server":
            return self._http is not None
        try:
            return self._ensure_tacit() is not None
        except DecisionBackendUnavailable:
            return False

    def describe(self) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "provider": self.name,
            "transport": self.transport,
            "model": self.model,
            "adaptive": self.adaptive,
            "tau": self.tau,
        }
        if self._http is not None:
            detail.update(self._http.describe())
        if self._tacit is not None:
            stats = getattr(self._tacit, "stats", None)
            if stats is not None:
                detail["stats"] = stats
        return detail

    def decide(self, request: DecisionRequest) -> RawDecision:
        state = build_state_text(request)
        started = time.perf_counter()
        if self.transport == "server":
            answers, routes = self._decide_over_http(state, self.questions)
        else:
            answers, routes = self._decide_inprocess(state, self.questions)

        intervention = normalize_intervention(answers["intervention"][0])

        # The "which questionnaire" question is only worth a forward pass when
        # the answer is "offer a scale".  Asking it unconditionally would put a
        # scale name in front of the model on every turn and invite it to name
        # one when it should be listening.
        scale_choice: Optional[tuple[str, dict[str, float]]] = None
        if intervention == InterventionIntent.OFFER_SCALE.value:
            spec = build_scale_choice_question(request)
            if spec is not None:
                extra_answers, extra_routes = self._ask(state, (spec,))
                answers.update(extra_answers)
                routes.extend(extra_routes)
                scale_choice = extra_answers[spec.name]

        latency_ms = (time.perf_counter() - started) * 1000.0
        escalated = [value for value in routes if value == "cot"]
        return compose_decision(
            provider=self.name,
            request=request,
            answers=answers,
            latency_ms=latency_ms,
            route="cot" if escalated else "one_forward",
            reason="tacit" + ("+cot" if escalated else ""),
            scale_choice=scale_choice,
        )

    # -- transports --------------------------------------------------------

    def _ask(
        self, state: str, specs: tuple[QuestionSpec, ...]
    ) -> tuple[dict[str, tuple[str, dict[str, float]]], list[str]]:
        if self.transport == "server":
            return self._decide_over_http(state, specs)
        return self._decide_inprocess(state, specs)

    def _decide_over_http(
        self, state: str, specs: tuple[QuestionSpec, ...]
    ) -> tuple[dict[str, tuple[str, dict[str, float]]], list[str]]:
        assert self._http is not None
        answers: dict[str, tuple[str, dict[str, float]]] = {}
        routes: list[str] = []
        for spec in specs:
            payload = self._http.post({"state": state, **spec.payload()})
            answer, probabilities, route = _parse_tacit_payload(payload)
            answers[spec.name] = (answer, probabilities)
            routes.append(route or "one_forward")
        return answers, routes

    def _decide_inprocess(
        self, state: str, specs: tuple[QuestionSpec, ...]
    ) -> tuple[dict[str, tuple[str, dict[str, float]]], list[str]]:
        tacit = self._ensure_tacit()
        if tacit is None:
            raise DecisionBackendUnavailable("tacit checkpoint is not available")
        # One `decide_batch` call, not one per question: official Tacit batches
        # the questions of one turn into a single forward, and that is the
        # latency this project quotes.
        batch = [
            {
                "state": state,
                "question": spec.text,
                "options": list(spec.options),
                "kind": "choice",
            }
            for spec in specs
        ]
        try:
            results = tacit.decide_batch(batch)
        except Exception as exc:  # a broken engine must not look like a decision
            raise DecisionBackendUnavailable(
                f"tacit decide_batch failed: {type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(results, list) or len(results) != len(batch):
            raise DecisionBackendUnavailable(
                f"tacit returned {len(results) if isinstance(results, list) else '?'} "
                f"decisions for {len(batch)} questions"
            )

        answers: dict[str, tuple[str, dict[str, float]]] = {}
        routes: list[str] = []
        for spec, result in zip(specs, results):
            if not isinstance(result, dict):
                raise DecisionBackendUnavailable(
                    f"tacit returned {type(result).__name__} for {spec.name!r}"
                )
            answer = result.get("answer")
            probabilities = result.get("probs")
            if not answer or not isinstance(probabilities, dict) or not probabilities:
                raise DecisionBackendUnavailable(
                    f"tacit returned an unusable decision for {spec.name!r}"
                )
            answers[spec.name] = (str(answer), {str(k): float(v) for k, v in probabilities.items()})
            routes.append(str(result.get("route") or "one_forward"))
        return answers, routes

    def _ensure_tacit(self):
        if self._tacit is not None:
            return self._tacit
        if self._tacit_factory is not None:
            self._tacit = self._tacit_factory(self)
            return self._tacit
        if not self.model:
            return None

        candidate = Path(self.model).expanduser()
        is_directory = candidate.is_dir()
        if not is_directory:
            if not self.allow_model_download:
                raise DecisionBackendUnavailable(
                    f"decision.model {self.model!r} is not a local directory; "
                    "set decision.allow_model_download to true only if a hub fetch is acceptable, "
                    "otherwise point decision.model_path at a verified checkpoint"
                )

        # Offline is enforced before the import, not after: `Tacit.from_pretrained`
        # calls AutoTokenizer/AutoModelForCausalLM without local_files_only, so the
        # environment is the only thing that stops a repo id from reaching the hub.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        try:
            from anyjev import Tacit
        except ImportError:
            # The project venv deliberately ships without the decision stack
            # (its torch build conflicts with the project's).  Fall back to a
            # persistent worker process in the isolated decision venv.
            tacit = self._ensure_subprocess_tacit(candidate, is_directory)
            if tacit is not None:
                self._tacit = tacit
                return self._tacit
            raise DecisionBackendUnavailable(
                "anyjev is not installed in this environment and no isolated "
                "decision python was found; set VOICECHAT_TACIT_PYTHON to the "
                "python inside the decision bundle venv (e.g. "
                "D:/offline/tacit-bundle/venv-decision/Scripts/python.exe)"
            )
        except Exception as exc:  # pragma: no cover - optional dependency
            raise DecisionBackendUnavailable(
                f"anyjev[hf] is not installed: {exc}. Install it in an isolated env; "
                "do not add it to the project .venv."
            ) from exc
        try:
            self._tacit = Tacit.from_pretrained(
                str(candidate) if is_directory else self.model,
                adaptive=self.adaptive,
                tau=self.tau,
                max_cot_share=self.max_cot_share,
            )
        except Exception as exc:
            raise DecisionBackendUnavailable(
                f"tacit checkpoint failed to load: {type(exc).__name__}: {exc}"
            ) from exc
        return self._tacit

    def _isolated_tacit_python(self) -> Optional[str]:
        """Locate a python that can import anyjev (the decision bundle venv)."""
        import shutil

        configured = os.environ.get("VOICECHAT_TACIT_PYTHON")
        if configured and Path(configured).is_file():
            return configured
        default = Path(r"D:/offline/tacit-bundle/venv-decision/Scripts/python.exe")
        if default.is_file():
            return str(default)
        found = shutil.which("tacit-decision-python")
        return found

    def _ensure_subprocess_tacit(self, candidate: Path, is_directory: bool):
        """Spawn the isolated-env worker once; returns a decide_batch client.

        Returns None when no isolated python can be located (the caller then
        raises the explanatory DecisionBackendUnavailable).
        """
        import subprocess
        import threading

        python = self._isolated_tacit_python()
        if not python:
            return None
        worker = Path(__file__).resolve().parents[2] / "scripts" / "decision" / "tacit_worker.py"
        if not worker.is_file():
            return None
        if not is_directory and not self.allow_model_download:
            return None

        cmd = [
            python, str(worker),
            "--model", str(candidate) if is_directory else self.model,
            "--adaptive", "1" if self.adaptive else "0",
            "--tau", str(self.tau),
            "--max-cot-share", str(self.max_cot_share),
        ]

        class _SubprocessTacit:
            """JSON-line client exposing the same decide_batch interface."""

            def __init__(self, cmd, timeout):
                self._cmd = cmd
                self._timeout = float(timeout)
                self._lock = threading.Lock()
                self._proc = None

            def _start(self):
                self._proc = subprocess.Popen(
                    self._cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                )
                ready = self._read_line(timeout=max(self._timeout, 180.0))
                if ready is None or not ready.get("ready"):
                    reason = (ready or {}).get("error", "worker did not report ready")
                    self._stop()
                    raise DecisionBackendUnavailable(f"tacit worker failed to start: {reason}")

            def _read_line(self, timeout):
                # Windows selectors cannot select() on pipes (sockets only),
                # so the read runs in a helper thread with a join timeout.
                import threading
                import time as _time

                proc = self._proc
                result: dict = {}

                def _consume():
                    try:
                        result["line"] = proc.stdout.readline()
                    except Exception as exc:  # pragma: no cover - broken pipe
                        result["error"] = exc

                reader = threading.Thread(target=_consume, daemon=True)
                reader.start()
                reader.join(timeout)
                if reader.is_alive() or "line" not in result:
                    return None
                line = result["line"]
                if not line:
                    return None
                return json.loads(line)

            def _stop(self):
                try:
                    if self._proc is not None:
                        self._proc.stdin.close()
                        self._proc.terminate()
                except Exception:
                    pass
                self._proc = None

            def decide_batch(self, batch):
                with self._lock:
                    if self._proc is None:
                        self._start()
                    payload = json.dumps({"batch": batch}, ensure_ascii=False)
                    try:
                        self._proc.stdin.write(payload + "\n")
                        self._proc.stdin.flush()
                    except Exception as exc:
                        self._stop()
                        raise DecisionBackendUnavailable(
                            f"tacit worker stdin failed: {type(exc).__name__}: {exc}"
                        ) from exc
                    reply = self._read_line(timeout=self._timeout)
                    if reply is None:
                        self._stop()
                        raise DecisionBackendUnavailable(
                            "tacit worker did not answer within the decision timeout"
                        )
                    if not reply.get("ok"):
                        raise DecisionBackendUnavailable(
                            f"tacit worker error: {reply.get('error')}"
                        )
                    return reply.get("results")

        self._worker_proc_holder = _SubprocessTacit(cmd, self.timeout)
        return self._worker_proc_holder

