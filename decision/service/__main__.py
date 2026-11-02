"""``python -m decision.service`` -- run the offline decision service.

Examples
--------
Verify a checkpoint without loading torch::

    python -m decision.service --model-path /models/Tacit-1.7B --check-only

Serve it on loopback, taking every parameter from the runtime config::

    python -m decision.service --config config/runtime.yaml

Serve a specific checkpoint directly::

    python -m decision.service --model-path D:/models/Tacit-1.7B --port 8100
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from typing import Any, Optional

from decision.integrity import ModelIntegrityError, summarise, verify_model_dir
from decision.service.engine import DecisionEngine, EngineUnavailable
from decision.service.server import DecisionService


def _log(message: str) -> None:
    print(message, flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m decision.service",
        description="Local Jev-style decision service (offline, loopback only)",
    )
    parser.add_argument("--config", help="runtime.yaml to read the decision section from")
    parser.add_argument("--model-path", help="local checkpoint directory (overrides config)")
    parser.add_argument("--manifest", help="integrity manifest path (default: <model>/tacit_manifest.json)")
    parser.add_argument("--dtype", default=None, help="auto | bfloat16 | float16 | float32")
    parser.add_argument("--device", default=None, help="auto | cpu | cuda")
    parser.add_argument("--mode", default=None, help="auto | next_token | candidate")
    parser.add_argument("--rotations", type=int, default=None, help="option-order rotations (>=1)")
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--allow-remote", action="store_true", help="bind a non-loopback host")
    parser.add_argument("--allow-unverified", action="store_true", help="serve without a manifest (unsafe)")
    parser.add_argument("--check-only", action="store_true", help="verify integrity, then exit")
    parser.add_argument("--json", action="store_true", help="machine-readable output for probes")
    return parser


def _from_config(path: str) -> dict[str, Any]:
    from deployment.runtime_config import load_runtime_config

    config = load_runtime_config(path)
    decision = config.decision
    endpoint = decision.endpoint or ""
    port = None
    if endpoint:
        try:
            from urllib.parse import urlparse

            port = urlparse(endpoint).port
        except ValueError:
            port = None
    return {
        "model_path": decision.model_path or decision.model,
        "dtype": decision.model_dtype,
        "port": port,
        "manifest": None,
        "mode": None,
        "rotations": None,
        "temperature": None,
        "allow_unverified": False,
        "require_manifest": True,
        "endpoint": endpoint or None,
    }


def _resolve_settings(args: argparse.Namespace) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "model_path": args.model_path,
        "dtype": args.dtype or "bfloat16",
        "device": args.device or "auto",
        "mode": args.mode or "auto",
        "rotations": args.rotations if args.rotations is not None else 1,
        "temperature": args.temperature if args.temperature is not None else 1.0,
        "manifest": args.manifest,
        "port": args.port if args.port is not None else 8100,
        "require_manifest": True,
        "allow_unverified": bool(args.allow_unverified),
    }
    if args.config:
        from_config = _from_config(args.config)
        if not args.model_path and from_config.get("model_path"):
            settings["model_path"] = from_config["model_path"]
        if not args.dtype and from_config.get("dtype"):
            settings["dtype"] = from_config["dtype"]
        if args.port is None and from_config.get("port"):
            settings["port"] = from_config["port"]
    return settings


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = _resolve_settings(args)

    model_path = settings.get("model_path")
    if not model_path:
        parser.error("--model-path is required (or set decision.model_path in the config)")
        return 2

    if args.check_only:
        report = verify_model_dir(
            model_path,
            settings.get("manifest"),
            require_manifest=settings["require_manifest"],
            extra_files_ok=True,
        )
        message = summarise(report)
        if args.json:
            print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        else:
            _log(message)
            for problem in report.problems[:20]:
                _log(f"  - {problem}")
            _log("next: python scripts/decision/tacit_model_check.py --help")
        return 0 if report.ok else 3

    engine = DecisionEngine(
        model_path=str(model_path),
        manifest_path=settings.get("manifest"),
        dtype=settings["dtype"],
        device=settings["device"],
        readout_mode=settings["mode"],
        rotations=int(settings["rotations"]),
        temperature=float(settings["temperature"]),
        require_manifest=bool(settings["require_manifest"]),
        allow_unverified=bool(settings["allow_unverified"]),
    )
    try:
        engine.load()
    except (EngineUnavailable, ModelIntegrityError) as exc:
        _log(f"[DecisionService] refusing to start: {exc}")
        return 3

    try:
        service = DecisionService(
            engine,
            host=args.host,
            port=int(settings["port"]),
            allow_remote=bool(args.allow_remote),
            log=_log,
        )
        service.build()
    except OSError as exc:
        _log(f"[DecisionService] cannot bind {args.host}:{settings['port']}: {exc}")
        return 4
    except ValueError as exc:
        _log(f"[DecisionService] {exc}")
        return 4

    health = engine.health()
    _log(
        f"[DecisionService] listening on {service.url} "
        f"model={health.get('model_path')} dtype={health.get('dtype')} "
        f"device={health.get('device')} mode={health.get('readout_mode')} "
        f"rotations={health.get('rotations')}"
    )
    vram = (health.get("model") or {}).get("vram") or {}
    if vram.get("available"):
        _log(
            f"[DecisionService] vram allocated_peak={vram.get('allocated_peak_mib')}MiB "
            f"reserved_peak={vram.get('reserved_peak_mib')}MiB "
            f"device_total={vram.get('total_mib')}MiB"
        )

    stopping = {"flag": False}

    def _handle(signum: int, _frame: Any) -> None:
        if stopping["flag"]:
            return
        stopping["flag"] = True
        _log(f"[DecisionService] signal {signum} received, shutting down")
        # ``BaseServer.shutdown()`` deadlocks if called from the thread that is
        # running ``serve_forever()``, so hand it to a short-lived helper.
        threading.Thread(target=service.stop, name="decision-stop", daemon=True).start()

    for name in ("SIGINT", "SIGTERM"):
        candidate = getattr(signal, name, None)
        if candidate is None:
            continue
        try:
            signal.signal(candidate, _handle)
        except (ValueError, OSError):  # non-main thread
            pass

    try:
        service.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        _log("[DecisionService] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
