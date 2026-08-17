"""Operator entrypoint for the existing Blackwell live acceptance probe.

This wrapper intentionally delegates to ``blackwell_live_probe``.  It does
not own vLLM lifecycle, start/stop services, select a GPU, or change runtime
configuration.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.acceptance import blackwell_live_probe
from scripts.acceptance.probe_support import SUPPORTED_PROFILES


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the existing read-only Blackwell live acceptance probe"
    )
    parser.add_argument("--profile", required=True, choices=sorted(SUPPORTED_PROFILES))
    parser.add_argument("--distro", default=None)
    parser.add_argument("--output-root", default=str(Path("test_output") / "blackwell_acceptance"))
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--vllm-executable", default="~/.venvs/voicechat-vllm/bin/vllm")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return blackwell_live_probe.run_probe(
        args.profile,
        distro=args.distro,
        output_root=args.output_root,
        timeout_seconds=args.timeout_seconds,
        vllm_executable=args.vllm_executable,
    )


if __name__ == "__main__":
    raise SystemExit(main())
