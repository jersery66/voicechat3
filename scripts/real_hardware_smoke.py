"""Operator entrypoint for the existing Blackwell live acceptance probe.

This wrapper intentionally delegates to ``blackwell_live_probe``.  It does
not own model-server lifecycle, start/stop services, select a GPU, or change
runtime configuration.  The deployment under test is named by one runtime
configuration file, exactly as the probe itself expects.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deployment.runtime_config import get_runtime_config, load_runtime_config
from scripts.acceptance import blackwell_live_probe

ENGINE_EXECUTABLES = {
    "llamacpp": "llama-server",
    "vllm": "vllm",
    "ollama": "ollama",
}


def engine_executable_from_config(config_path: str | None) -> str | None:
    """Pick the version-probe executable from the engine the config declares.

    The probe used to receive no executable by default, so the artifact simply
    recorded that no version was taken. Reading the declared engine keeps the
    recorded version truthful for llama.cpp deployments as well as vLLM ones.
    """
    try:
        if config_path:
            config = load_runtime_config(config_path)
        else:
            config = get_runtime_config()
    except Exception:
        return None
    return ENGINE_EXECUTABLES.get(config.dialogue.engine or "")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the existing read-only live acceptance probe"
    )
    parser.add_argument(
        "--config",
        default=None,
        help="runtime configuration to probe (default: config/runtime.yaml)",
    )
    parser.add_argument("--distro", default=None)
    parser.add_argument("--output-root", default=str(Path("test_output") / "blackwell_acceptance"))
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument(
        "--server-executable",
        default=None,
        help=(
            "inference server executable used for the version probe; "
            "defaults to the engine declared in the runtime configuration"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return blackwell_live_probe.run_probe(
        args.config,
        distro=args.distro,
        output_root=args.output_root,
        timeout_seconds=args.timeout_seconds,
        server_executable=args.server_executable or engine_executable_from_config(args.config),
    )


if __name__ == "__main__":
    raise SystemExit(main())
