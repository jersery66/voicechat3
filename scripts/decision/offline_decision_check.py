#!/usr/bin/env python
"""Offline acceptance probe for the Jev-style decision path.

What this proves, and what it refuses to prove
---------------------------------------------
It proves that on *this* machine, with *this* checkpoint and the network guard
installed, the decision path answers the five observation questions and the
validator accepts or abstains as designed.

It never fabricates a result.  If the checkpoint is missing or fails integrity,
the script exits non-zero and prints the offline bundle checklist instead of
running a fake provider -- simulated numbers must never be reported as inference.

    # full run: verify, spawn the service, decide 24 cases, measure VRAM
    python scripts/decision/offline_decision_check.py --model-path D:/models/Tacit-1.7B

    # use a service someone else started (no spawn, no integrity re-check)
    python scripts/decision/offline_decision_check.py --endpoint http://127.0.0.1:8100

Exit codes: 0 ok / 3 checkpoint unusable / 4 service unavailable / 5 offline violation.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "decision"))

from decision.contracts import DecisionRequest  # noqa: E402
from decision.integrity import MANIFEST_NAME, summarise, verify_model_dir  # noqa: E402
from decision.providers.anyjev import AnyJevDecisionProvider  # noqa: E402
from decision.validator import DecisionValidator  # noqa: E402
from deployment.net_guard import install_network_guard  # noqa: E402
from service_process import (  # noqa: E402
    LocalDecisionService,
    ServiceStartError,
    free_port,
)

DEFAULT_CASES = PROJECT_ROOT / "scripts" / "benchmarks" / "cases" / "decision_cases_synthetic.jsonl"
DEFAULT_OUT = PROJECT_ROOT / "test_output" / "offline_decision"

CHECKLIST = """\
离线安装包制作清单（在联网机器上执行，再把产物拷到访谈机）
  1. 权重
     - huggingface-cli download morriszjm/Tacit-1.7B --local-dir Tacit-1.7B
     - 升级候选：Tacit-2B，同一目录结构
     - 目录内必须有 config.json / tokenizer* / *.safetensors（含分片索引与全部分片）
  2. 完整性清单
     - python scripts/decision/tacit_model_check.py --model-path <dir> --write --model-id morriszjm/Tacit-1.7B
     - 把生成的 tacit_manifest.json 与权重一起拷贝
  3. Python 依赖（独立 venv，不污染主 .venv）
     - python -m venv <bundle>/venv-decision
     - <bundle>/venv-decision/Scripts/pip install -r deployment/offline/requirements-decision.lock.txt
       （联网阶段可用 pip download -d wheelhouse 预取 wheel，离线阶段 pip install --no-index --find-links）
  4. 拷贝后校验
     - python scripts/decision/tacit_model_check.py --model-path <dir> --json
  5. 启停脚本
     - scripts/engines/tacit/decision_service.sh start|stop|status|restart|health
  6. 断网验收
     - python scripts/decision/offline_decision_check.py --model-path <dir>
"""


def _parse_args(argv: Optional[list[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline acceptance probe for the decision layer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--model-path", help="local checkpoint directory (absolute)")
    parser.add_argument("--manifest", help=f"integrity manifest (default: <model>/{MANIFEST_NAME})")
    parser.add_argument("--endpoint", help="attach to an already-running service instead of spawning")
    parser.add_argument("--config", help="runtime.yaml; supplies model_path/model_dtype/port when omitted")
    parser.add_argument("--cases", default=str(DEFAULT_CASES), help="JSONL case file")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="artifact directory")
    parser.add_argument("--dtype", default=None)
    parser.add_argument("--device", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--gpu-index", type=int, default=0, help="GPU to sample for system VRAM peak")
    parser.add_argument("--timeout", type=float, default=600.0, help="service startup timeout (seconds)")
    parser.add_argument("--allow-unverified", action="store_true",
                        help="skip the integrity manifest requirement (recorded in the artifact)")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _load_cases(path: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise SystemExit(f"{path}:{line_no} is not valid JSON: {exc}")
        if not isinstance(payload, dict) or not payload.get("text"):
            raise SystemExit(f"{path}:{line_no} needs a 'text' field")
        cases.append(payload)
    if not cases:
        raise SystemExit(f"{path} contains no cases")
    return cases


class VramSampler:
    """System-wide GPU peak from nvidia-smi, sampled off the main thread."""

    def __init__(self, gpu_index: int = 0, interval: float = 0.4) -> None:
        self.gpu_index = int(gpu_index)
        self.interval = interval
        self.baseline_mib: Optional[float] = None
        self.peak_mib: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _read(self) -> Optional[float]:
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    f"--id={self.gpu_index}",
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        try:
            return float(completed.stdout.strip().splitlines()[0])
        except (ValueError, IndexError):
            return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            value = self._read()
            if value is not None:
                if self.peak_mib is None or value > self.peak_mib:
                    self.peak_mib = value
            self._stop.wait(self.interval)

    def start(self) -> "VramSampler":
        self.baseline_mib = self._read()
        self.peak_mib = self.baseline_mib
        self._thread = threading.Thread(target=self._loop, name="vram-sampler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        delta = None
        if self.baseline_mib is not None and self.peak_mib is not None:
            delta = round(self.peak_mib - self.baseline_mib, 1)
        return {
            "gpu_index": self.gpu_index,
            "baseline_mib": self.baseline_mib,
            "peak_mib": self.peak_mib,
            "delta_mib": delta,
            "source": "nvidia-smi",
        }


def _resolve_model_path(args: argparse.Namespace) -> tuple[Optional[str], dict[str, Any]]:
    settings: dict[str, Any] = {}
    if args.config:
        from deployment.runtime_config import load_runtime_config

        decision = load_runtime_config(args.config).decision
        settings = {
            "model_path": decision.model_path,
            "dtype": decision.model_dtype,
            "endpoint": decision.endpoint,
            "abstain_threshold": decision.abstain_threshold,
        }
    model_path = args.model_path or settings.get("model_path")
    if args.dtype is None:
        args.dtype = settings.get("dtype") or "bfloat16"
    if args.device is None:
        args.device = "auto"
    return model_path, settings


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return round(ordered[index], 2)


def _run_cases(
    endpoint: str,
    cases: list[dict[str, Any]],
    *,
    validator: DecisionValidator,
    timeout: float,
) -> dict[str, Any]:
    provider = AnyJevDecisionProvider(endpoint=endpoint, timeout=timeout, batch=True)
    latencies: list[float] = []
    records: list[dict[str, Any]] = []
    accepted = 0
    abstained = 0
    errors = 0

    for case in cases:
        request = DecisionRequest(
            user_text=str(case["text"]),
            session_state=str(case.get("state") or "interview"),
            turn_index=int(case.get("turn") or 1),
            active_scale=None,
            game_active=False,
            recent_history="",
        )
        started = time.perf_counter()
        try:
            raw = provider.decide(request)
        except Exception as exc:
            errors += 1
            records.append(
                {
                    "id": case.get("id"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue
        latency = (time.perf_counter() - started) * 1000.0
        latencies.append(latency)
        verdict = validator.validate(raw, request)
        if verdict.accepted:
            accepted += 1
        elif verdict.abstained:
            abstained += 1
        records.append(
            {
                "id": case.get("id"),
                "text_len": len(str(case["text"])),
                "action": raw.action,
                "confidence": round(float(raw.confidence or 0.0), 4),
                "route": raw.route,
                "accepted": bool(verdict.accepted),
                "abstained": bool(verdict.abstained),
                "reason": verdict.reason,
                "latency_ms": round(latency, 2),
                "expected_action": case.get("expected_action"),
            }
        )

    return {
        "cases": len(cases),
        "decided": len(latencies),
        "accepted": accepted,
        "abstained": abstained,
        "errors": errors,
        "accept_rate": round(accepted / len(cases), 4) if cases else 0.0,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p90": _percentile(latencies, 0.90),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        },
        "records": records,
    }


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    model_path, settings = _resolve_model_path(args)
    endpoint = args.endpoint or settings.get("endpoint")

    guard = install_network_guard(allow_loopback=True)
    sampler = VramSampler(gpu_index=args.gpu_index).start()
    service: Optional[LocalDecisionService] = None
    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "case_file": str(args.cases),
        "case_source": "synthetic template utterances (no patient data)",
        "real_inference": False,
    }

    try:
        # ---- checkpoint gate ------------------------------------------
        integrity: Optional[dict[str, Any]] = None
        if args.endpoint is None:
            if not model_path:
                print("no --model-path / --endpoint given", file=sys.stderr)
                print(CHECKLIST, file=sys.stderr)
                return 3
            root = Path(model_path).expanduser()
            if not root.is_dir():
                print(f"checkpoint directory missing: {root}", file=sys.stderr)
                print(CHECKLIST, file=sys.stderr)
                return 3
            result = verify_model_dir(
                root,
                args.manifest,
                require_manifest=not args.allow_unverified,
                extra_files_ok=True,
            )
            integrity = result.as_dict()
            print(summarise(result))
            if not result.ok:
                for problem in result.problems[:20]:
                    print(f"  - {problem}", file=sys.stderr)
                print(CHECKLIST, file=sys.stderr)
                return 3

        # ---- service ---------------------------------------------------
        cases = _load_cases(args.cases)
        if endpoint:
            service = LocalDecisionService.attach(endpoint)
            try:
                health = service.health()
            except Exception as exc:
                print(f"decision service at {endpoint} is not reachable: {exc}", file=sys.stderr)
                return 4
        else:
            port = args.port or free_port()
            service = LocalDecisionService.spawn(
                model_path=str(Path(model_path).expanduser()),
                manifest=args.manifest,
                dtype=args.dtype,
                device=args.device,
                port=port,
                project_root=str(PROJECT_ROOT),
            )
            try:
                health = service.wait_healthy(timeout=args.timeout)
            except ServiceStartError as exc:
                print(f"decision service failed to start: {exc}", file=sys.stderr)
                return 4
            service.spawned = True

        if str(health.get("status")) != "ok":
            print(f"service health is {health.get('status')!r}", file=sys.stderr)
            return 4
        report["service_health"] = health
        report["integrity"] = integrity or health.get("integrity")

        # ---- decisions -------------------------------------------------
        validator = DecisionValidator(
            abstain_threshold=float(settings.get("abstain_threshold") or 0.55)
        )
        outcome = _run_cases(service.base_url, cases, validator=validator, timeout=30.0)
        report["outcome"] = outcome
        report["real_inference"] = outcome["decided"] > 0
        report["stats"] = service.stats()
        report["vram"] = sampler.stop()
        report["network"] = guard.summary()
        report["model_path_name"] = Path(model_path).expanduser().name if model_path else None
        report["dtype"] = args.dtype
        report["device"] = args.device

        # ---- verdict ---------------------------------------------------
        problems: list[str] = []
        if report["network"]["violations"]:
            problems.append("network_guard_violation")
        if outcome["decided"] == 0:
            problems.append("no_successful_decisions")
        if outcome["errors"]:
            problems.append(f"decision_errors:{outcome['errors']}")
        report["verdict"] = "ok" if not problems else "failed"
        report["problems"] = problems

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%S")
        artifact = out_dir / f"offline_decision_{stamp}.json"
        artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            latency = outcome["latency_ms"]
            vram = report["vram"]
            model_vram = (report["stats"].get("vram") or {})
            print("")
            print(f"verdict            : {report['verdict']}")
            print(f"cases              : {outcome['cases']} (decided {outcome['decided']})")
            print(f"accepted/abstained : {outcome['accepted']}/{outcome['abstained']}")
            print(f"latency p50/p95    : {latency['p50']} / {latency['p95']} ms")
            if vram.get("peak_mib") is not None:
                print(
                    f"gpu used baseline/peak : {vram['baseline_mib']} / {vram['peak_mib']} MiB "
                    f"(delta {vram['delta_mib']} MiB, nvidia-smi)"
                )
            if model_vram.get("available"):
                print(
                    f"service torch peak : allocated {model_vram.get('allocated_peak_mib')} MiB / "
                    f"reserved {model_vram.get('reserved_peak_mib')} MiB"
                )
            print(f"network violations : {len(report['network']['violations'])}")
            print(f"artifact           : {artifact}")
            for problem in problems:
                print(f"  ! {problem}", file=sys.stderr)

        return 0 if report["verdict"] == "ok" else 4

    finally:
        sampler.stop()
        if service is not None and service.spawned:
            service.stop()
        guard.restore()


if __name__ == "__main__":
    sys.exit(main())
