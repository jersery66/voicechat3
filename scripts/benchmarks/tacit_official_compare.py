#!/usr/bin/env python
"""Official Tacit vs. this project's readout, on one case set and one GPU.

Why this exists
---------------
``decision/engine/readout.py`` reads the probability of the *option label
string* (" chat", "start_scale") at the answer position of a prompt this project
invented.  Official AnyJev reads the probability of a *position letter* (A, B, C)
after rendering the prompt through the checkpoint's chat template.  Those are
different estimators, and for a Tacit checkpoint -- trained to answer with a
letter -- the second is the one it was trained for.

So the two paths cannot be claimed equivalent, and this script measures them
side by side instead: same 24 synthetic turns, same grading code
(:func:`decision_benchmark.run_cases`), same GPU, one after the other.

    python scripts/benchmarks/tacit_official_compare.py \
        --model-path D:/models/tacit/Tacit-1.7B --order-rounds 4

Requires ``anyjev`` (official package) for the reference path; the readout path
needs only transformers.  If ``anyjev`` is missing the official path is skipped
with an explicit message -- never replaced by a fake.

Nothing here downloads weights: the checkpoint must already be on disk, and the
process sets ``HF_HUB_OFFLINE`` before touching transformers.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "benchmarks"))

from decision_benchmark import (  # noqa: E402
    DEFAULT_CASES,
    DEFAULT_OUT,
    render_markdown,
    run_cases,
)
from decision.providers.base import DECISION_QUESTIONS  # noqa: E402

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


# ---------------------------------------------------------------------------
# GPU instrumentation: process view (torch) + whole-card view (nvidia-smi)
# ---------------------------------------------------------------------------


class CardSampler(threading.Thread):
    """Sample the whole card, so a second process's memory cannot hide."""

    def __init__(self, interval: float = 0.2) -> None:
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_flag = threading.Event()
        self.peak_mib = 0
        self.samples = 0

    def run(self) -> None:
        while not self.stop_flag.is_set():
            used = _nvidia_smi_used_mib()
            if used is not None:
                self.peak_mib = max(self.peak_mib, used)
                self.samples += 1
            self.stop_flag.wait(self.interval)

    def stop(self) -> dict[str, Any]:
        self.stop_flag.set()
        self.join(timeout=5)
        return {"peak_used_mib": self.peak_mib, "samples": self.samples}


def _nvidia_smi_used_mib() -> Optional[int]:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = completed.stdout.strip().splitlines()
    if not line:
        return None
    try:
        return int(float(line[0]))
    except ValueError:
        return None


def _torch_peak(torch_module) -> dict[str, Any]:
    if not torch_module.cuda.is_available():
        return {"available": False}
    free, total = torch_module.cuda.mem_get_info()
    return {
        "available": True,
        "allocated_peak_mib": round(torch_module.cuda.max_memory_allocated() / 2**20, 1),
        "reserved_peak_mib": round(torch_module.cuda.max_memory_reserved() / 2**20, 1),
        "total_mib": round(total / 2**20, 1),
        "free_mib": round(free / 2**20, 1),
    }


# ---------------------------------------------------------------------------
# Deciders
# ---------------------------------------------------------------------------


def official_decider(model_path: str, dtype: str) -> tuple[Any, dict[str, Any], Any]:
    """`Tacit.decide_batch`, i.e. the published inference path."""
    from anyjev import Tacit

    started = time.perf_counter()
    tacit = Tacit.from_pretrained(model_path, dtype=dtype)
    load_seconds = time.perf_counter() - started

    def decide(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        payload = [
            {
                "state": item["state"],
                "question": item["question"],
                "options": list(item["options"]),
                "kind": "choice",
            }
            for item in items
        ]
        return tacit.decide_batch(payload)

    meta = {
        "backend": "anyjev Tacit (official)",
        "readout": "letter labels A/B/C.., chat template, one forward",
        "load_seconds": round(load_seconds, 2),
        "stats": getattr(tacit, "stats", {}),
    }
    return decide, meta, tacit


def readout_decider(
    model_path: str, dtype: str, mode: str, rotations: int
) -> tuple[Any, dict[str, Any], Any]:
    """This project's readout: option-label-string tokens, no chat template."""
    from decision.engine.readout import Readout, ReadoutQuestion
    from decision.engine.scorer import TransformersScorer

    scorer = TransformersScorer(model_path=model_path, dtype=dtype)
    started = time.perf_counter()
    scorer.load()
    load_seconds = time.perf_counter() - started
    readout = Readout(scorer, mode=mode, rotations=rotations)

    def decide(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        results = []
        for item in items:
            spec = ReadoutQuestion(
                name=str(item["name"]),
                text=str(item["question"]),
                options=tuple(item["options"]),
            )
            result = readout.decide(str(item["state"]), spec)
            results.append(
                {
                    "answer": result.answer,
                    "probs": dict(result.probabilities),
                    "route": "label_token" if result.method == "next_token" else "candidate",
                    "method": result.method,
                }
            )
        return results

    meta = {
        "backend": "voicechat3 decision.engine.readout (experimental)",
        "readout": "option-string tokens, custom prompt, no chat template",
        "load_seconds": round(load_seconds, 2),
        "readout_config": readout.describe(),
    }
    return decide, meta, scorer


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _load_cases(path: str, limit: int = 0) -> list[dict[str, Any]]:
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            cases.append(json.loads(text))
    return cases[:limit] if limit else cases


def _release(obj: Any, torch_module) -> None:
    del obj
    gc.collect()
    if torch_module is not None and torch_module.cuda.is_available():
        torch_module.cuda.empty_cache()


def run_one(
    *,
    name: str,
    build,
    cases: Sequence[dict[str, Any]],
    order_rounds: int,
    out_dir: Path,
    model_path: str,
    case_file: str,
    torch_module,
) -> dict[str, Any]:
    if torch_module is not None and torch_module.cuda.is_available():
        torch_module.cuda.empty_cache()
        torch_module.cuda.reset_peak_memory_stats()
    card = CardSampler()
    card.start()

    build_result = build()
    decide, meta, handle = build_result[0], build_result[1], build_result[2]
    load_peak = _torch_peak(torch_module)

    # warmup: the first forward pays kernel autotuning, not the model
    decided = decide(
        [
            {
                "state": "warmup",
                "name": spec.name,
                "question": spec.text,
                "options": list(spec.options),
            }
            for spec in DECISION_QUESTIONS
        ]
    )
    assert isinstance(decided, list)

    if torch_module is not None and torch_module.cuda.is_available():
        torch_module.cuda.reset_peak_memory_stats()
    result = run_cases(decide, cases, order_rounds=order_rounds)
    infer_peak = _torch_peak(torch_module)
    card_stats = card.stop()

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "label": name,
        "case_file": case_file,
        "case_source": "synthetic template utterances (no patient data)",
        "model": {**meta, "model_path": Path(model_path).name},
        "vram": {
            "process_load_peak": load_peak,
            "process_inference_peak": infer_peak,
            "card_peak": card_stats,
            "measurement": "torch.cuda.max_memory_allocated + nvidia-smi sampling (200 ms)",
        },
        "result": result,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = name.replace("/", "_").replace("\\", "_").lower()
    json_path = out_dir / f"{slug}.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(render_markdown(report), flush=True)
    print(f"artifact: {json_path}\n", flush=True)

    _release(handle, torch_module)
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare official Tacit inference with this project's readout",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--model-path", required=True, help="local Tacit checkpoint directory")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--order-rounds", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="grade only the first N cases")
    parser.add_argument("--mode", default="auto", help="readout mode: auto | next_token | candidate")
    parser.add_argument("--rotations", type=int, default=1)
    parser.add_argument("--only", choices=("both", "official", "readout"), default="both")
    args = parser.parse_args(argv)

    model_root = Path(args.model_path).expanduser()
    if not model_root.is_dir():
        print(f"model_path is not a directory: {model_root}", file=sys.stderr)
        print("prepare it with: see docs/deployment/agent_jev_offline_runbook.md", file=sys.stderr)
        return 3

    try:
        import torch
    except ImportError:
        print("torch is not installed in this interpreter", file=sys.stderr)
        return 3

    cases = _load_cases(args.cases, args.limit)
    out_dir = Path(args.out)
    reports: list[dict[str, Any]] = []

    if args.only in ("both", "official"):
        try:
            import anyjev  # noqa: F401
        except ImportError:
            print(
                "official path skipped: anyjev is not installed in this interpreter.\n"
                "  pip install anyjev==0.3.0   (isolated env; see "
                "deployment/offline/requirements-decision.lock.txt)",
                file=sys.stderr,
            )
            if args.only == "official":
                return 4
        else:
            reports.append(
                run_one(
                    name=f"{Path(args.model_path).name}-official-tacit",
                    build=lambda: official_decider(str(model_root), args.dtype),
                    cases=cases,
                    order_rounds=args.order_rounds,
                    out_dir=out_dir,
                    model_path=str(model_root),
                    case_file=args.cases,
                    torch_module=torch,
                )
            )

    if args.only in ("both", "readout"):
        reports.append(
            run_one(
                name=f"{Path(args.model_path).name}-project-readout",
                build=lambda: readout_decider(str(model_root), args.dtype, args.mode, args.rotations),
                cases=cases,
                order_rounds=args.order_rounds,
                out_dir=out_dir,
                model_path=str(model_root),
                case_file=args.cases,
                torch_module=torch,
            )
        )

    if len(reports) == 2:
        print("\n## Side by side (action question)\n")
        print("| metric | official Tacit | project readout |")
        print("| --- | --- | --- |")
        for metric in ("accuracy", "macro_f1", "brier", "ece", "order_flip_rate"):
            cells = []
            for report in reports:
                entry = report["result"]["per_question"]["action"]
                value = entry.get(metric)
                cells.append("-" if value is None else f"{value:.4f}")
            print(f"| action.{metric} | {cells[0]} | {cells[1]} |")
        for metric in ("p50", "p95"):
            cells = [f"{r['result']['latency_ms'][metric]:.1f}" for r in reports]
            print(f"| latency_ms.{metric} | {cells[0]} | {cells[1]} |")
        for metric in ("process_inference_peak", "card_peak"):
            cells = []
            for r in reports:
                vram = r["vram"][metric]
                cells.append(
                    f"{vram.get('allocated_peak_mib', vram.get('peak_used_mib'))}"
                )
            print(f"| vram.{metric} (MiB) | {cells[0]} | {cells[1]} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
