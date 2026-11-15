#!/usr/bin/env python
"""Lifecycle controller for the local decision service.

The rest of the project manages model servers with shell scripts that rely on
``/proc``; the decision service runs on Windows as well, so the identity logic
lives here in Python and ``scripts/engines/tacit/decision_service.sh`` is a thin
wrapper.

Ownership contract (same idea as the llama.cpp scripts): a lifecycle action is
only permitted when the pid file, the metadata file and the live process command
line agree.  Anything else is reported as an ownership mismatch instead of being
killed by guesswork.

    python scripts/decision/service_ctl.py status  --name tacit-1.7b
    python scripts/decision/service_ctl.py start   --name tacit-1.7b --model-path D:/models/Tacit-1.7B
    python scripts/decision/service_ctl.py health  --name tacit-1.7b
    python scripts/decision/service_ctl.py recover --name tacit-1.7b --model-path D:/models/Tacit-1.7B

Exit codes: 0 success / 1 unexpected failure / 2 bad arguments /
3 checkpoint unusable / 4 service did not become healthy.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from decision.integrity import summarise, verify_model_dir  # noqa: E402
from service_process import free_port, http_json  # noqa: E402

SERVICE_TOKEN = "decision.service"


# ---------------------------------------------------------------------------
# Paths and metadata
# ---------------------------------------------------------------------------


def runtime_dir() -> Path:
    override = os.environ.get("VOICECHAT_TACIT_RUNTIME_DIR")
    base = Path(override) if override else Path.home() / ".voicechat" / "tacit"
    return base.expanduser()


def slot_paths(name: str) -> dict[str, Path]:
    root = runtime_dir()
    root.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "pid": root / f"{name}.pid",
        "meta": root / f"{name}.meta",
        "log": root / f"{name}.log",
    }


def read_meta(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def write_meta(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".meta.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Process identity
# ---------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_command_line(pid: int) -> Optional[str]:
    """Best-effort command line; ``None`` means "cannot verify on this host"."""
    if os.name != "nt":
        proc = Path(f"/proc/{pid}/cmdline")
        if proc.is_file():
            try:
                return proc.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            except OSError:
                return None
        try:
            completed = subprocess.run(
                ["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, timeout=5
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return completed.stdout.strip() or None

    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.stdout is None:
        return None
    line = completed.stdout.strip()
    return line or None


def identity_state(name: str) -> dict[str, Any]:
    paths = slot_paths(name)
    pid_path, meta_path = paths["pid"], paths["meta"]
    detail: dict[str, Any] = {
        "service_name": name,
        "state": "",
        "pid": None,
        "pid_file": str(pid_path),
        "metadata_file": str(meta_path),
        "log_file": str(paths["log"]),
        "detail": "",
        "meta": None,
    }

    if not pid_path.is_file():
        if meta_path.is_file():
            detail.update(state="STALE_PID", detail="metadata exists without pid file")
        else:
            detail.update(state="NO_PID_FILE", detail="no owned pid metadata")
        return detail

    raw = pid_path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw.isdigit():
        detail.update(state="STALE_PID", detail="pid file is not a number")
        return detail
    pid = int(raw)
    detail["pid"] = pid

    if not pid_alive(pid):
        detail.update(state="STALE_PID", detail="pid file does not reference a live process")
        return detail

    meta = read_meta(meta_path)
    if not meta or meta.get("service_name") != name:
        detail.update(state="OWNERSHIP_MISMATCH", detail="metadata missing or for another slot")
        return detail
    detail["meta"] = meta

    if int(meta.get("pid") or 0) != pid:
        detail.update(state="OWNERSHIP_MISMATCH", detail="metadata pid does not match pid file")
        return detail

    command_line = process_command_line(pid)
    if command_line is None:
        detail.update(
            state="SERVICE_RUNNING",
            detail="process alive and metadata matches; command line not inspectable on this host",
        )
        return detail
    problems = []
    if SERVICE_TOKEN not in command_line:
        problems.append("not a decision.service process")
    if str(meta.get("model_path") or "") and str(meta["model_path"]) not in command_line:
        problems.append("model_path mismatch")
    if str(meta.get("port") or "") and str(meta["port"]) not in command_line:
        problems.append("port mismatch")
    if problems:
        detail.update(state="OWNERSHIP_MISMATCH", detail="; ".join(problems))
        return detail

    detail.update(state="SERVICE_RUNNING", detail="owned decision.service process matches metadata")
    return detail


def service_health(meta: dict[str, Any], timeout: float = 3.0) -> Optional[dict[str, Any]]:
    port = meta.get("port")
    if not port:
        return None
    try:
        return http_json(f"http://127.0.0.1:{int(port)}/health", timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def effective_state(name: str) -> dict[str, Any]:
    detail = identity_state(name)
    if detail["state"] == "SERVICE_RUNNING":
        health = service_health(detail["meta"] or {})
        if health is None or str(health.get("status")) != "ok":
            detail["state"] = "SERVICE_UNHEALTHY"
            detail["detail"] = "process alive but /health is not ok"
        else:
            detail["health"] = health
    return detail


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    detail = effective_state(args.name)
    print(f"service_name={detail['service_name']}")
    print(f"state={detail['state']}")
    print(f"pid={detail['pid'] or ''}")
    print(f"pid_file={detail['pid_file']}")
    print(f"metadata_file={detail['metadata_file']}")
    print(f"log_file={detail['log_file']}")
    print(f"detail={detail['detail']}")
    meta = detail.get("meta") or {}
    print(f"model_path={meta.get('model_path', '')}")
    print(f"port={meta.get('port', '')}")
    if args.json:
        print(json.dumps(detail, ensure_ascii=False, indent=2))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    detail = identity_state(args.name)
    meta = detail.get("meta") or {}
    if detail["state"] != "SERVICE_RUNNING" or not meta:
        print(f"state={detail['state']}", file=sys.stderr)
        print(f"detail={detail['detail']}", file=sys.stderr)
        return 4
    health = service_health(meta, timeout=args.timeout)
    if health is None:
        print("health=unreachable", file=sys.stderr)
        return 4
    print(json.dumps(health, ensure_ascii=False, indent=2))
    return 0 if str(health.get("status")) == "ok" else 4


def _tail(path: Path, lines: int = 10) -> str:
    if not path.is_file():
        return ""
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return " | ".join(content[-lines:])


def _spawn(
    name: str,
    *,
    model_path: str,
    port: int,
    dtype: str,
    device: str,
    mode: Optional[str],
    rotations: Optional[int],
    manifest: Optional[str],
    python: Optional[str],
) -> subprocess.Popen:
    paths = slot_paths(name)
    command = [
        python or sys.executable,
        "-m",
        "decision.service",
        "--model-path",
        model_path,
        "--port",
        str(port),
        "--dtype",
        dtype,
        "--device",
        device,
    ]
    if manifest:
        command += ["--manifest", manifest]
    if mode:
        command += ["--mode", mode]
    if rotations is not None:
        command += ["--rotations", str(rotations)]

    env = dict(os.environ)
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    log_handle = paths["log"].open("a", encoding="utf-8", errors="replace")
    log_handle.write(f"\n=== start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    log_handle.flush()

    kwargs: dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _wait_health(port: int, process: subprocess.Popen, name: str, timeout: float) -> Optional[dict[str, Any]]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            return None
        try:
            health = http_json(f"http://127.0.0.1:{int(port)}/health", timeout=3.0)
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.5)
            continue
        if str(health.get("status")) == "ok":
            return health
        time.sleep(0.5)
    return None


def cmd_start(args: argparse.Namespace) -> int:
    detail = effective_state(args.name)
    if detail["state"] == "SERVICE_RUNNING":
        print(f"already running: pid={detail['pid']} port={(detail.get('meta') or {}).get('port')}")
        return 0
    if detail["state"] in {"STALE_PID", "OWNERSHIP_MISMATCH", "SERVICE_UNHEALTHY"}:
        print(
            f"refusing to start: slot is {detail['state']} ({detail['detail']}); "
            f"run 'stop --force' or 'recover' first",
            file=sys.stderr,
        )
        return 1

    if not args.model_path:
        print("--model-path is required", file=sys.stderr)
        return 2
    root = Path(args.model_path).expanduser()
    if not root.is_dir():
        print(f"checkpoint directory missing: {root}", file=sys.stderr)
        return 3

    if not args.skip_verify:
        report = verify_model_dir(
            root, args.manifest, require_manifest=not args.allow_unverified, extra_files_ok=True
        )
        print(summarise(report))
        if not report.ok:
            for problem in report.problems[:10]:
                print(f"  - {problem}", file=sys.stderr)
            return 3

    port = int(args.port or free_port())
    process = _spawn(
        args.name,
        model_path=str(root),
        port=port,
        dtype=args.dtype,
        device=args.device,
        mode=args.mode,
        rotations=args.rotations,
        manifest=args.manifest,
        python=args.python,
    )
    paths = slot_paths(args.name)
    paths["pid"].write_text(str(process.pid), encoding="utf-8")
    write_meta(
        paths["meta"],
        {
            "service_name": args.name,
            "pid": process.pid,
            "model_path": str(root),
            "port": port,
            "dtype": args.dtype,
            "device": args.device,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        },
    )

    health = _wait_health(port, process, args.name, args.timeout)
    if health is None:
        print(
            f"service did not become healthy within {args.timeout:.0f}s; "
            f"log: {_tail(paths['log'])}",
            file=sys.stderr,
        )
        _terminate(process.pid)
        paths["pid"].unlink(missing_ok=True)
        paths["meta"].unlink(missing_ok=True)
        return 4

    print(f"started pid={process.pid} port={port}")
    vram = (health.get("model") or {}).get("vram") or {}
    if vram.get("available"):
        print(
            f"vram allocated_peak={vram.get('allocated_peak_mib')}MiB "
            f"reserved_peak={vram.get('reserved_peak_mib')}MiB"
        )
    return 0


def _terminate(pid: int, timeout: float = 15.0) -> None:
    """Stop an owned process.

    Windows has no SIGTERM for console processes: ``taskkill`` without ``/F`` only
    posts ``WM_CLOSE``, which a headless Python service never handles, so a
    polite-then-force sequence would just burn the timeout.  We therefore force
    on Windows and escalate on POSIX.
    """
    if not pid_alive(pid):
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=max(5.0, timeout),
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.2)

    if os.name != "nt":
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def cmd_stop(args: argparse.Namespace) -> int:
    detail = effective_state(args.name)
    paths = slot_paths(args.name)
    state = detail["state"]
    if state == "NO_PID_FILE":
        print("not running (no pid file)")
        return 0
    if state in {"STALE_PID", "OWNERSHIP_MISMATCH"} and not args.force:
        print(
            f"refusing to stop: slot is {state} ({detail['detail']}); "
            "pass --force only after confirming no other process owns this slot",
            file=sys.stderr,
        )
        return 1
    pid = detail.get("pid")
    if pid:
        if state == "OWNERSHIP_MISMATCH" and args.force:
            print(f"forcing stop of unverified pid {pid}", file=sys.stderr)
        _terminate(pid)
        print(f"stopped pid={pid}")
    paths["pid"].unlink(missing_ok=True)
    paths["meta"].unlink(missing_ok=True)
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    code = cmd_stop(argparse.Namespace(name=args.name, force=args.force))
    if code != 0:
        return code
    return cmd_start(args)


def cmd_recover(args: argparse.Namespace) -> int:
    """Bring the slot back to a healthy owned service, or report why not."""
    detail = effective_state(args.name)
    state = detail["state"]
    if state == "SERVICE_RUNNING":
        print(f"healthy: pid={detail['pid']} port={(detail.get('meta') or {}).get('port')}")
        return 0
    print(f"recovering from state {state} ({detail['detail']})")
    if state in {"STALE_PID", "OWNERSHIP_MISMATCH", "SERVICE_UNHEALTHY"}:
        for attempt in range(1, args.max_attempts + 1):
            code = cmd_stop(argparse.Namespace(name=args.name, force=True))
            if code == 0:
                break
            print(f"stop attempt {attempt} failed, retrying in 2s", file=sys.stderr)
            time.sleep(2)
    last = 1
    for attempt in range(1, args.max_attempts + 1):
        last = cmd_start(args)
        if last == 0:
            return 0
        if last in {2, 3}:
            return last  # arguments or checkpoint: retrying will not help
        print(f"start attempt {attempt} failed, retrying in {2 * attempt}s", file=sys.stderr)
        time.sleep(2 * attempt)
    print(f"recover failed after {args.max_attempts} attempts", file=sys.stderr)
    return last


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Decision service lifecycle controller")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--name", required=True, help="service slot name (e.g. tacit-1.7b)")
        target.add_argument("--json", action="store_true")

    common(sub.add_parser("status", help="inspect the slot without side effects"))

    health = sub.add_parser("health", help="print /health from the running service")
    common(health)
    health.add_argument("--timeout", type=float, default=5.0)

    for name, help_text in (("start", "start the service"), ("restart", "stop then start")):
        node = sub.add_parser(name, help=help_text)
        common(node)
        node.add_argument("--model-path")
        node.add_argument("--manifest")
        node.add_argument("--port", type=int)
        node.add_argument("--dtype", default="bfloat16")
        node.add_argument("--device", default="auto")
        node.add_argument("--mode", default=None, help="auto | next_token | candidate")
        node.add_argument("--rotations", type=int, default=None)
        node.add_argument("--python", default=None, help="interpreter of the isolated decision venv")
        node.add_argument("--timeout", type=float, default=600.0)
        node.add_argument("--skip-verify", action="store_true")
        node.add_argument("--allow-unverified", action="store_true")
        node.add_argument("--force", action="store_true")

    stop = sub.add_parser("stop", help="stop the service")
    common(stop)
    stop.add_argument("--force", action="store_true")

    recover = sub.add_parser("recover", help="force a clean restart with retries")
    common(recover)
    recover.add_argument("--model-path")
    recover.add_argument("--manifest")
    recover.add_argument("--port", type=int)
    recover.add_argument("--dtype", default="bfloat16")
    recover.add_argument("--device", default="auto")
    recover.add_argument("--mode", default=None)
    recover.add_argument("--rotations", type=int, default=None)
    recover.add_argument("--python", default=None)
    recover.add_argument("--timeout", type=float, default=600.0)
    recover.add_argument("--skip-verify", action="store_true")
    recover.add_argument("--allow-unverified", action="store_true")
    recover.add_argument("--force", action="store_true", default=True)
    recover.add_argument("--max-attempts", type=int, default=3)

    return parser


HANDLERS = {
    "status": cmd_status,
    "health": cmd_health,
    "start": cmd_start,
    "stop": cmd_stop,
    "restart": cmd_restart,
    "recover": cmd_recover,
}


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.name.replace("-", "").replace("_", "").isalnum():
        parser.error("--name must be alphanumeric with dashes or underscores")
    if not hasattr(args, "force"):
        args.force = False
    return HANDLERS[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
