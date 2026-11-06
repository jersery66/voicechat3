#!/usr/bin/env python
"""Tacit inference worker for the isolated decision venv.

Run by ``decision/providers/tacit.py`` when the host process cannot import
``anyjev`` (the project .venv must not contain the decision stack: its torch
build conflicts with the project's, so the bundle keeps a dedicated venv).

Protocol (JSON lines over stdin/stdout):
  startup   -> {"ready": true}                       (after the model is loaded)
  request   -> {"batch": [{"state", "question", "options", "kind"}, ...]}
  response  -> {"ok": true, "results": [{"answer", "probs", "route"}, ...]}
             | {"ok": false, "error": "..."}

Usage:
  python tacit_worker.py --model <checkpoint dir> [--adaptive 1] \
      [--tau 0.5] [--max-cot-share 0.2]
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--adaptive", type=int, default=0)
    parser.add_argument("--tau", type=float, default=0.5)
    parser.add_argument("--max-cot-share", type=float, default=0.2)
    parser.add_argument("--device", default="auto",
                        help="auto | cpu | cuda (auto falls back to cpu on OOM)")
    args = parser.parse_args()

    # Offline is enforced before any transformers import touches the hub.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    try:
        from anyjev import Tacit
    except Exception as exc:  # pragma: no cover - bundle misconfiguration
        print(json.dumps({"ready": False, "error": f"anyjev import failed: {exc}"}), flush=True)
        return 1

    def _load(device: str):
        return Tacit.from_pretrained(
            args.model,
            device_map=device,
            dtype="float32" if device == "cpu" else "bfloat16",
            adaptive=bool(args.adaptive),
            tau=float(args.tau),
            max_cot_share=float(args.max_cot_share),
        )

    device = (args.device or "auto").strip().lower()
    try:
        tacit = _load("auto" if device == "auto" else device)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        # The dialogue/TTS/ASR stack can leave only a few GB of VRAM; the
        # decision needs ~4GB, so a busy GPU is the common case, not an error.
        # Fall back to CPU (slower per turn, identical decisions) instead of
        # letting the whole decision layer look unavailable.
        if device == "cpu" or "out of memory" not in message.lower():
            print(json.dumps({"ready": False, "error": message}), flush=True)
            return 1
        print(f"cuda load failed ({message}); retrying on cpu", file=sys.stderr, flush=True)
        try:
            tacit = _load("cpu")
        except Exception as exc2:
            print(json.dumps({"ready": False,
                              "error": f"cpu fallback failed: {type(exc2).__name__}: {exc2}"}),
                  flush=True)
            return 1

    print(json.dumps({"ready": True}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            batch = request.get("batch")
            if not isinstance(batch, list):
                raise ValueError("request must carry a 'batch' list")
            results = tacit.decide_batch(batch)
            if not isinstance(results, list):
                raise ValueError(f"decide_batch returned {type(results).__name__}")
            print(json.dumps({"ok": True, "results": results}), flush=True)
        except Exception as exc:
            _dump_failure(request, exc)
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


def _dump_failure(request, exc) -> None:
    """Keep the exact payload that broke a decision, for offline diagnosis."""
    try:
        batch = request.get("batch") if isinstance(request, dict) else None
        summary = []
        if isinstance(batch, list):
            for item in batch[:8]:
                if isinstance(item, dict):
                    summary.append({
                        "keys": sorted(item),
                        "state_type": type(item.get("state")).__name__,
                        "state_len": len(item.get("state") or ""),
                        "question": str(item.get("question"))[:80],
                        "options_type": type(item.get("options")).__name__,
                        "n_options": len(item.get("options") or []),
                        "kind": item.get("kind"),
                    })
        path = os.environ.get("VOICECHAT_TACIT_FAILURE_DUMP", "logs/tacit_failed_payload.json")
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"error": f"{type(exc).__name__}: {exc}",
                       "summary": summary, "request": request}, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
