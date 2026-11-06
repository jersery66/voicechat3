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
    args = parser.parse_args()

    # Offline is enforced before any transformers import touches the hub.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

    try:
        from anyjev import Tacit
    except Exception as exc:  # pragma: no cover - bundle misconfiguration
        print(json.dumps({"ready": False, "error": f"anyjev import failed: {exc}"}), flush=True)
        return 1

    try:
        tacit = Tacit.from_pretrained(
            args.model,
            adaptive=bool(args.adaptive),
            tau=float(args.tau),
            max_cot_share=float(args.max_cot_share),
        )
    except Exception as exc:
        print(json.dumps({"ready": False, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
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
            print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
