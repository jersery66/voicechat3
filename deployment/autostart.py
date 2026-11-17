"""Zero-configuration startup for a workstation.

Goal: ``python main.py`` on any machine should reach a working session without
anyone editing a config file, exporting a variable, or starting a service by
hand. This module runs *before* ``config.py`` is imported (config reads the
environment at import time), and does four things:

1. respect an explicit ``VOICECHAT_RUNTIME_CONFIG`` completely — an experiment
   that pins a contract must never be silently re-pointed;
2. otherwise probe the machine (loopback + WSL NAT addresses) and write
   ``config/runtime.local.yaml`` with what was actually found;
3. when no model server answers, start the WSL2 llama.cpp stack and probe again;
4. export ``VOICECHAT_RUNTIME_CONFIG`` so ``config.py`` picks the generated file.

Everything is best-effort: a failure here must degrade the launch, never block
it. Set ``VOICECHAT_NO_AUTOSTART=1`` to disable the whole step (diagnostics,
unit tests, or a machine where startup must be byte-for-byte reproducible).

The detection/rendering itself lives in ``scripts/launch/autoconfigure.py`` and
is reused as a subprocess: that file is standard-library only and stays
runnable before the virtual environment has any dependencies.

CLI (used by the launcher scripts):
    python -m deployment.autostart --status
    python -m deployment.autostart --start-services [--wait 180]
    python -m deployment.autostart --stop-services
    python -m deployment.autostart --json
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

APP_ROOT = Path(__file__).resolve().parents[1]
AUTOCONFIGURE_SCRIPT = APP_ROOT / "scripts" / "launch" / "autoconfigure.py"
STUB_SCRIPT = APP_ROOT / "scripts" / "launch" / "offline_stub_server.py"
WSL_STACK_SCRIPT = APP_ROOT / "scripts" / "engines" / "llama_cpp" / "start_stack_wsl.sh"
LOCAL_CONFIG = APP_ROOT / "config" / "runtime.local.yaml"
PID_FILE = APP_ROOT / "logs" / "wsl_stack.pid"
LOG_DIR = APP_ROOT / "logs"

DIALOGUE_PORT = 8000
AGENT_PORT = 8001
STUB_PORT = 8010

ENV_RUNTIME_CONFIG = "VOICECHAT_RUNTIME_CONFIG"
ENV_NO_AUTOSTART = "VOICECHAT_NO_AUTOSTART"
ENV_ALLOW_STUB = "VOICECHAT_ALLOW_STUB"

# Windows: detach the keeper so it outlives this process (the WSL distro is torn
# down when its last session ends, and takes every llama-server with it).
_DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


# ------------------------------------------------------------------ probing
def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe_models(host: str, port: int, timeout: float = 1.5) -> Optional[list[str]]:
    """Model ids served on ``host:port``; ``None`` when nothing answers."""
    try:
        with _opener().open(f"http://{host}:{port}/v1/models", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError, socket.timeout):
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    ids = []
    for item in data or []:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
    return ids


def service_status() -> dict[str, Any]:
    dialogue = probe_models("127.0.0.1", DIALOGUE_PORT)
    agent = probe_models("127.0.0.1", AGENT_PORT)
    return {
        "dialogue": dialogue,
        "agent": agent,
        "ready": bool(dialogue) and bool(agent),
    }


# ------------------------------------------------------------ autoconfigure
def run_autoconfigure(*, write: bool = True, timeout: float = 90.0) -> dict[str, Any]:
    """Run the detection script with this interpreter and return its JSON report."""
    if not AUTOCONFIGURE_SCRIPT.is_file():
        return {}
    args = [sys.executable, str(AUTOCONFIGURE_SCRIPT), "--json"]
    if not write:
        args.append("--check")
    try:
        completed = subprocess.run(
            args, capture_output=True, timeout=timeout, cwd=str(APP_ROOT),
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    for line in reversed((completed.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def _chosen_is_real(state: dict[str, Any]) -> bool:
    chosen = state.get("chosen") or {}
    return bool(chosen) and not chosen.get("is_stub")


# ------------------------------------------------------------- WSL stack
def _decode_wsl(output: bytes) -> str:
    """`wsl -l -q` prints UTF-16LE; `hostname -I` prints UTF-8."""
    if not output:
        return ""
    if b"\x00" in output[:40]:
        return output.decode("utf-16-le", "replace")
    return output.decode("utf-8", "replace")


def find_wsl_distro() -> Optional[str]:
    wsl = shutil.which("wsl")
    if not wsl:
        return None
    try:
        raw = subprocess.run([wsl, "-l", "-q"], capture_output=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in _decode_wsl(raw).splitlines():
        name = line.replace("\x00", "").strip()
        if name:
            return name
    return None


def _to_wsl_path(win_path: Path) -> str:
    posix = str(win_path).replace("\\", "/")
    if len(posix) > 1 and posix[1] == ":":
        return f"/mnt/{posix[0].lower()}{posix[2:]}"
    return posix


def start_model_stack(*, wait_seconds: int = 180, bind: str = "0.0.0.0") -> bool:
    """Start the WSL llama.cpp stack and wait until both ports answer.

    Binding ``0.0.0.0`` is deliberate: WSL2's localhost forwarding only covers
    wildcard-bound sockets on some hosts, and a loopback-bound server is then
    unreachable from Windows. The WSL NAT address is not routable from the LAN,
    so this does not publish the model.
    """
    status = service_status()
    if status["ready"]:
        return True

    wsl = shutil.which("wsl")
    distro = find_wsl_distro()
    if not wsl or not distro:
        return False
    if not WSL_STACK_SCRIPT.is_file():
        return False

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    script = _to_wsl_path(WSL_STACK_SCRIPT)
    boot_log = _to_wsl_path(LOG_DIR / "wsl_stack_boot.log")
    # The trailing sleep is the keeper: without a live session WSL tears the
    # distro down and every llama-server dies with it.
    inner = (
        f"HOST={bind} bash '{script}' >> '{boot_log}' 2>&1; "
        f"echo '--- keeper ---' >> '{boot_log}'; sleep 86400"
    )
    try:
        proc = subprocess.Popen(
            [wsl, "-d", distro, "--", "bash", "-lc", inner],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=_DETACHED, close_fds=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    try:
        PID_FILE.write_text(str(proc.pid), encoding="ascii")
    except OSError:
        pass

    deadline = time.time() + max(10, wait_seconds)
    while time.time() < deadline:
        if service_status()["ready"]:
            return True
        time.sleep(3)
    return service_status()["ready"]


def stop_model_stack() -> None:
    distro = find_wsl_distro()
    if distro:
        try:
            subprocess.run(
                [shutil.which("wsl") or "wsl", "-d", distro, "--", "bash", "-lc",
                 "pkill -f 'llama-server.*--port 800' || true"],
                capture_output=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            pid = 0
        if pid:
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                           capture_output=True, timeout=20)
        try:
            PID_FILE.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ stub
def start_offline_stub() -> bool:
    """Start the rehearsal server (only when explicitly allowed by the operator)."""
    if probe_models("127.0.0.1", STUB_PORT) is not None:
        return True
    if not STUB_SCRIPT.is_file():
        return False
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            [sys.executable, str(STUB_SCRIPT), "--port", str(STUB_PORT)],
            stdin=subprocess.DEVNULL,
            stdout=open(LOG_DIR / "offline_stub.out.log", "ab"),
            stderr=subprocess.DEVNULL,
            creationflags=_DETACHED, close_fds=True, cwd=str(APP_ROOT),
        )
    except OSError:
        return False
    deadline = time.time() + 15
    while time.time() < deadline:
        if probe_models("127.0.0.1", STUB_PORT, timeout=1.0) is not None:
            return True
        time.sleep(0.4)
    return False


# ------------------------------------------------------------------- ensure
def ensure(*, allow_start: bool = True, wait_seconds: int = 180,
           verbose: bool = True) -> Optional[str]:
    """Make this machine runnable and return the config path to use.

    Returns ``None`` when autostart is disabled (the caller then keeps whatever
    ``config/runtime.yaml`` says).
    """
    def say(message: str) -> None:
        if verbose:
            print(f"[autostart] {message}", flush=True)

    if os.environ.get(ENV_NO_AUTOSTART, "").strip() not in ("", "0", "false", "False"):
        return None

    explicit = os.environ.get(ENV_RUNTIME_CONFIG, "").strip()
    if explicit:
        # An operator pinned a contract: never re-point it behind their back.
        say(f"使用显式配置 {explicit}")
        return explicit

    state = run_autoconfigure(write=True)
    if not state:
        say("探测脚本不可用，沿用 config/runtime.yaml")
        return None

    chosen = state.get("chosen") or {}
    if not _chosen_is_real(state) and allow_start:
        reason = "当前只有离线桩" if chosen else "未发现模型服务"
        say(f"{reason}，尝试启动 WSL 模型栈…")
        if start_model_stack(wait_seconds=wait_seconds):
            state = run_autoconfigure(write=True)
            chosen = state.get("chosen") or {}
            say(f"模型栈就绪：{chosen.get('base_url')} ({state.get('dialogue_model')})")
        else:
            say("模型栈未能启动")

    if not _chosen_is_real(state):
        if os.environ.get(ENV_ALLOW_STUB, "").strip() not in ("", "0", "false", "False"):
            if start_offline_stub():
                state = run_autoconfigure(write=True)
                chosen = state.get("chosen") or {}
                say("已切换到离线联调桩（非真实推理）")
        else:
            say("没有可用的模型服务；界面会提示连接失败。"
                f"需要联调可设 {ENV_ALLOW_STUB}=1 使用离线桩。")

    if LOCAL_CONFIG.is_file():
        os.environ[ENV_RUNTIME_CONFIG] = str(LOCAL_CONFIG)
        if _chosen_is_real(state):
            say(f"对话 {chosen.get('base_url')} · {state.get('dialogue_model')}")
        return str(LOCAL_CONFIG)

    say("未能生成 config/runtime.local.yaml，沿用默认配置")
    return None


# ---------------------------------------------------------------------- CLI
def _print_status() -> int:
    status = service_status()
    for port, key, label in ((DIALOGUE_PORT, "dialogue", "对话"),
                             (AGENT_PORT, "agent", "Agent")):
        models = status[key]
        if models:
            print(f"  [OK] :{port} {label}  {', '.join(models)}")
        else:
            print(f"  [--] :{port} {label}  未就绪")
    print(f"  WSL 发行版: {find_wsl_distro() or '未安装'}")
    return 0 if status["ready"] else 1


def status_report() -> dict[str, Any]:
    """Machine-readable status, ASCII-only on the wire.

    Launchers used to parse the localised lines above; on a Windows console
    whose code page differs from the child's, those lines arrive empty. JSON
    with ``ensure_ascii`` is immune to that.
    """
    status = service_status()
    return {
        "dialogue": {
            "port": DIALOGUE_PORT,
            "ready": bool(status["dialogue"]),
            "models": status["dialogue"] or [],
        },
        "agent": {
            "port": AGENT_PORT,
            "ready": bool(status["agent"]),
            "models": status["agent"] or [],
        },
        "stub": {
            "port": STUB_PORT,
            "running": probe_models("127.0.0.1", STUB_PORT, timeout=1.0) is not None,
        },
        "wsl_distro": find_wsl_distro(),
        "ready": status["ready"],
    }


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="自检、自启、生成运行配置")
    parser.add_argument("--status", action="store_true", help="只报告模型服务状态")
    parser.add_argument("--start-services", action="store_true", help="启动 WSL 模型栈")
    parser.add_argument("--stop-services", action="store_true", help="停止模型栈")
    parser.add_argument("--write-config", action="store_true", help="只探测并写配置")
    parser.add_argument("--json", action="store_true", help="输出探测结果 JSON")
    parser.add_argument("--wait", type=int, default=180, help="等待模型就绪的秒数")
    parser.add_argument("--bind", default="0.0.0.0", help="llama-server 绑定地址")
    args = parser.parse_args(argv)

    if args.status:
        if args.json:
            print(json.dumps(status_report(), ensure_ascii=True))
            return 0 if service_status()["ready"] else 1
        return _print_status()
    if args.stop_services:
        stop_model_stack()
        print("已停止模型服务")
        return 0
    if args.start_services:
        ok = start_model_stack(wait_seconds=args.wait, bind=args.bind)
        _print_status()
        return 0 if ok else 3
    if args.write_config:
        state = run_autoconfigure(write=True)
        print(json.dumps(state, ensure_ascii=False) if args.json else
              f"配置已写入 {LOCAL_CONFIG}")
        return 0 if state else 4

    path = ensure(allow_start=not args.status, wait_seconds=args.wait)
    if args.json:
        print(json.dumps(run_autoconfigure(write=False), ensure_ascii=False))
    return 0 if path else 0


if __name__ == "__main__":
    sys.exit(main())
