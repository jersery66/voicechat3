"""Engine behind the local decision service.

This serves the project's own readout, not Tacit: see the warning at the top of
``decision/engine/readout.py``.  A Tacit checkpoint is refused by default
because a legal probability vector from the wrong readout is worse than an
error -- it looks like an answer.

A thin, honest wrapper: verify the checkpoint, load the readout, answer
questions.  No caching of answers, no defaulting to a label, no fallback that
invents a distribution -- if the model cannot answer, the caller gets an error
and the application falls back through its own audited path.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from decision.engine.readout import Readout, ReadoutError, ReadoutQuestion
from decision.integrity import (
    IntegrityReport,
    tacit_checkpoint_markers,
    verify_model_dir,
)


#: Readout method -> route name.  "one_forward" is reserved for official Tacit.
_ROUTE_BY_METHOD = {
    "next_token": "label_token",
    "candidate": "candidate",
}


class EngineUnavailable(RuntimeError):
    """The engine cannot serve: weights missing, corrupt, or unloadable."""


@dataclass
class DecisionEngine:
    """Owns the readout and the loaded checkpoint."""

    model_path: str
    manifest_path: Optional[str] = None
    dtype: str = "bfloat16"
    device: str = "auto"
    readout_mode: str = "auto"
    rotations: int = 1
    temperature: float = 1.0
    require_manifest: bool = True
    allow_unverified: bool = False
    #: Refuse a Tacit checkpoint unless the operator says so explicitly: this
    #: service implements its own readout, not Tacit's.
    refuse_tacit_checkpoint: bool = True

    _readout: Optional[Readout] = field(default=None, init=False, repr=False)
    _integrity: Optional[IntegrityReport] = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _calls: int = field(default=0, init=False)
    _errors: int = field(default=0, init=False)
    _latencies: list[float] = field(default_factory=list, init=False)
    _started_at: float = field(default_factory=time.time, init=False)
    _tacit_markers: list[str] = field(default_factory=list, init=False)

    # -- startup ----------------------------------------------------------

    def verify(self) -> IntegrityReport:
        report = verify_model_dir(
            self.model_path,
            self.manifest_path,
            check_hashes=True,
            require_manifest=self.require_manifest,
            extra_files_ok=True,  # the manifest itself may live in the directory
        )
        self._integrity = report
        if not report.ok and not self.allow_unverified:
            report.raise_for_status()
        return report

    def load(self) -> "DecisionEngine":
        """Verify then load.  Raises :class:`EngineUnavailable` on any failure."""
        root = Path(self.model_path).expanduser()
        if not root.is_dir():
            raise EngineUnavailable(f"model_path is not a directory: {root}")
        markers = tacit_checkpoint_markers(root)
        self._tacit_markers = markers
        if markers and self.refuse_tacit_checkpoint:
            raise EngineUnavailable(
                "this looks like a Tacit checkpoint but this service implements its own "
                "readout (option-string tokens, no chat template), which is NOT Tacit's "
                f"letter readout; evidence: {', '.join(markers)}. Serve the official path "
                "(decision.providers.tacit) instead, or pass --allow-non-tacit-readout to "
                "run this readout deliberately as an experimental method."
            )
        try:
            self.verify()
        except Exception as exc:
            raise EngineUnavailable(str(exc)) from exc

        try:
            from decision.engine.scorer import TransformersScorer
        except Exception as exc:  # pragma: no cover - environment dependent
            raise EngineUnavailable(f"transformers scorer unavailable: {exc}") from exc

        scorer = TransformersScorer(
            model_path=str(root), dtype=self.dtype, device=self.device
        )
        try:
            scorer.load()
        except ReadoutError as exc:
            raise EngineUnavailable(str(exc)) from exc
        except Exception as exc:  # OOM, bad dtype, driver mismatch ...
            raise EngineUnavailable(f"checkpoint failed to load: {type(exc).__name__}: {exc}") from exc

        self._readout = Readout(
            scorer,
            mode=self.readout_mode,
            rotations=self.rotations,
            temperature=self.temperature,
        )
        return self

    @property
    def ready(self) -> bool:
        return self._readout is not None

    def attach(self, readout: Readout) -> "DecisionEngine":
        """Bind an already-built readout (used by tests and benchmark harnesses).

        Production always goes through :meth:`load`, which verifies the
        checkpoint first; this exists so the HTTP layer and the provider adapter
        can be exercised without a multi-gigabyte checkpoint.
        """
        self._readout = readout
        return self

    def _ensure(self) -> Readout:
        if self._readout is None:
            raise EngineUnavailable("engine is not loaded")
        return self._readout

    # -- serving ----------------------------------------------------------

    def decide_one(
        self,
        state: str,
        *,
        name: str,
        question: str,
        options: Sequence[str],
    ) -> dict[str, Any]:
        readout = self._ensure()
        spec = ReadoutQuestion(name=name, text=question, options=tuple(options))
        started = time.perf_counter()
        try:
            with self._lock:
                result = readout.decide(state, spec)
        except ReadoutError:
            self._errors += 1
            raise
        latency_ms = (time.perf_counter() - started) * 1000.0
        self._calls += 1
        self._latencies.append(latency_ms)
        if len(self._latencies) > 512:
            del self._latencies[:-512]
        return {
            "name": name,
            "answer": result.answer,
            "probs": dict(result.probabilities),
            "probabilities": dict(result.probabilities),
            # Deliberately not "one_forward": that string is Tacit's route, and
            # this readout is not Tacit.  See docs/design/agent_decision_layer.md.
            "route": _ROUTE_BY_METHOD.get(result.method, result.method),
            "method": result.method,
            "rounds": result.rounds,
            "entropy": round(result.entropy, 6),
            "latency_ms": round(latency_ms, 2),
            "notes": list(result.notes),
        }

    def decide_many(self, items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for item in items:
            results.append(
                self.decide_one(
                    str(item.get("state") or ""),
                    name=str(item.get("name") or ""),
                    question=str(item.get("question") or ""),
                    options=list(item.get("options") or ()),
                )
            )
        return results

    # -- introspection ----------------------------------------------------

    def percentile(self, q: float) -> float:
        if not self._latencies:
            return 0.0
        ordered = sorted(self._latencies)
        index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
        return round(ordered[index], 2)

    def health(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": "ok" if self.ready else "unavailable",
            "model_path": Path(self.model_path).name,
            "dtype": self.dtype,
            "device": self.device,
            "readout_mode": self.readout_mode,
            "rotations": self.rotations,
            "readout_semantics": "option_label_tokens (not official Tacit letter readout)",
            "tacit_checkpoint_markers": list(self._tacit_markers),
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "calls": self._calls,
            "errors": self._errors,
        }
        if self._integrity is not None:
            payload["integrity"] = {
                "ok": self._integrity.ok,
                "files_checked": self._integrity.files_checked,
                "hashed_files": self._integrity.hashed_files,
                "total_bytes": self._integrity.total_bytes,
                "problems": list(self._integrity.problems[:8]),
            }
        if self.ready:
            try:
                payload["model"] = self._readout.describe()
            except Exception:
                payload["model"] = {"status": "unknown"}
        return payload

    def stats(self) -> dict[str, Any]:
        return {
            "calls": self._calls,
            "errors": self._errors,
            "latency_ms": {
                "p50": self.percentile(0.50),
                "p90": self.percentile(0.90),
                "p95": self.percentile(0.95),
                "p99": self.percentile(0.99),
            },
            "vram": (self._readout.scorer.vram() if self.ready else {"available": False}),
        }
