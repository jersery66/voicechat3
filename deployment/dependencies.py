"""Provision this machine: report what is missing and install it.

The launcher used to carry its own list of pip names, its own idea of which
tier was "heavy", and no idea whether the machine had a GPU. That is exactly
the knowledge that has to be re-litigated on every new device, so it lives here
instead — one manifest, one installer, callable from the launcher *and* from the
application (which only ever reports, never installs while a session is open).

Tiers
-----
``core``   opening the GUIs needs these; without them the app cannot start.
``voice``  speech in and out: torch, torchaudio, funasr, transformers, librosa.
``tts``    the VoxCPM2 package (synthesis), only needed for spoken replies.

GPU handling
------------
``torch`` from PyPI is CPU-only on Windows. When an NVIDIA device is present the
CUDA wheel is installed from the PyTorch index instead, and the index is chosen
from the driver's reported CUDA version (a 5090 needs cu128 or newer). Every
candidate is tried in order and the install is skipped gracefully if none works,
because a CPU build can still run the whole pipeline — just slower.

Usage:
    python -m deployment.dependencies --check
    python -m deployment.dependencies --install --tier core
    python -m deployment.dependencies --install --tier voice --gpu auto
    python -m deployment.dependencies --check --json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

APP_ROOT = Path(__file__).resolve().parents[1]

# PyPI mirrors, in the order they are tried. A domestic mirror is usually an
# order of magnitude faster than pypi.org from China, and the launcher may run
# on a device where that matters.
MIRRORS = (
    ("tuna", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("aliyun", "https://mirrors.aliyun.com/pypi/simple"),
    ("pypi", "https://pypi.org/simple"),
)

PYTORCH_INDEX = "https://download.pytorch.org/whl"


@dataclass(frozen=True)
class Dependency:
    module: str          # import name used for detection
    spec: str            # pip requirement
    tier: str            # core | voice | tts
    note: str = ""
    size_mb: int = 0     # rough download size, for the report


# `module` is what `importlib.util.find_spec` sees; `spec` is what pip wants.
# Several packages ship a different import name (PyYAML -> yaml).
MANIFEST: tuple[Dependency, ...] = (
    Dependency("PySide6", "PySide6>=6.5.0", "core", "界面框架", 180),
    Dependency("numpy", "numpy>=1.24", "core", "数值计算", 20),
    Dependency("yaml", "PyYAML>=6.0", "core", "运行时配置解析", 1),
    Dependency("requests", "requests>=2.28", "core", "健康检查", 1),
    Dependency("sounddevice", "sounddevice>=0.4", "core", "麦克风/扬声器", 1),
    Dependency("soundfile", "soundfile>=0.12", "core", "音频读写", 1),
    Dependency("miniaudio", "miniaudio>=1.5", "core", "无 ffmpeg 解码", 1),
    Dependency("pygame", "pygame>=2.6", "core", "放松中心播放", 10),
    Dependency("openai", "openai>=1.0", "core", "OpenAI 兼容客户端", 3),
    Dependency("jieba", "jieba>=0.42", "core", "中文分词", 30),
    Dependency("reportlab", "reportlab>=4.0", "core", "PDF 报告", 5),
    Dependency("moviepy", "moviepy>=2.0", "core", "视频处理", 10),
    Dependency("pydub", "pydub>=0.25", "core", "音频拼接", 1),
    Dependency("scipy", "scipy>=1.10", "core", "信号处理", 40),
    Dependency("PIL", "pillow>=9.0", "core", "图像", 3),
    Dependency("matplotlib", "matplotlib>=3.8", "core", "统计图", 10),
    Dependency("pandas", "pandas>=2.0", "core", "统计表", 15),
    Dependency("tqdm", "tqdm>=4.60", "core", "进度条", 1),
    Dependency("regex", "regex>=2022", "core", "文本清洗", 1),
    Dependency("pydantic", "pydantic>=2.0", "core", "数据校验", 3),
    Dependency("torch", "torch", "voice", "语音模型运行时", 2500),
    Dependency("torchaudio", "torchaudio", "voice", "音频张量/IO", 5),
    Dependency("funasr", "funasr>=1.0", "voice", "语音识别框架", 30),
    Dependency("transformers", "transformers>=4.30", "voice", "模型加载", 40),
    Dependency("librosa", "librosa>=0.10", "voice", "音频特征", 15),
    Dependency("voxcpm", "voxcpm", "tts", "VoxCPM2 语音合成", 30),
)

TIERS = ("core", "voice", "tts", "all")


# --------------------------------------------------------------- detection
def present(module: str) -> bool:
    """True when the import is resolvable without executing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def missing(tier: str = "all") -> list[Dependency]:
    """Dependencies of ``tier`` whose import is not resolvable."""
    if tier in ("all", "", None):
        wanted = MANIFEST
    else:
        wanted = tuple(d for d in MANIFEST if d.tier == tier)
    return [d for d in wanted if not present(d.module)]


def nvidia_info() -> dict[str, object]:
    """Driver/GPU facts used to pick the CUDA wheel. Empty dict when absent."""
    exe = shutil.which("nvidia-smi") or (
        r"C:\Windows\System32\nvidia-smi.exe"
        if Path(r"C:\Windows\System32\nvidia-smi.exe").exists() else None
    )
    if not exe:
        return {}
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True, timeout=15, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return {}
    if not out:
        return {}
    first = out.splitlines()[0]
    parts = [p.strip() for p in first.split(",")]
    info: dict[str, object] = {
        "name": parts[0] if parts else "NVIDIA GPU",
        "driver": parts[1] if len(parts) > 1 else "",
        "memory": parts[2] if len(parts) > 2 else "",
    }
    try:
        detail = subprocess.run(
            [exe], capture_output=True, timeout=15, text=True,
            encoding="utf-8", errors="replace",
        ).stdout
        # Older drivers print "CUDA Version: 12.8"; 600-series drivers print
        # "CUDA UMD Version: 13.4". Both describe the same capability level.
        match = re.search(r"CUDA (?:UMD )?Version:\s*([\d.]+)", detail)
        if match:
            info["cuda"] = match.group(1)
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def torch_channels(gpu: dict[str, object]) -> list[str]:
    """CUDA wheel channels to try, newest first; ``cpu`` as the last resort."""
    if not gpu:
        return ["cpu"]
    raw = str(gpu.get("cuda") or "")
    try:
        major, minor = (int(x) for x in (raw.split(".") + ["0"])[:2])
    except ValueError:
        major, minor = 0, 0
    version = major + minor / 10.0
    channels: list[str] = []
    for threshold, channel in ((13.0, "cu130"), (12.8, "cu128"),
                               (12.6, "cu126"), (12.4, "cu124"), (12.1, "cu121")):
        if version >= threshold:
            channels.append(channel)
    if not channels:
        # Driver present but the version string was not recognised. Modern cards
        # are far more likely here than decade-old ones, so start at cu128 and
        # walk down instead of assuming an old runtime.
        channels = ["cu128", "cu126", "cu124", "cu121"]
    # Blackwell (RTX 50xx) needs 12.8+; a driver that reports 12.8 but ships a
    # 50-series card is still fine, so the newest channel stays first.
    return channels + ["cpu"]


# ---------------------------------------------------------------- install
def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def pick_mirror(timeout: float = 4.0) -> str:
    """Return the first reachable mirror from :data:`MIRRORS`."""
    for _name, url in MIRRORS:
        try:
            with _opener().open(url + "/", timeout=timeout) as response:
                if response.status < 500:
                    return url
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return MIRRORS[-1][1]


def _pip(args: list[str], *, quiet: bool = False) -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pip", "install",
           "--disable-pip-version-check", "--no-input"]
    if quiet:
        cmd.append("--quiet")
    cmd += args
    env = dict(os.environ)
    # pip must not inherit a proxy that only allows a whitelist of hosts.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=1800,
                                text=True, encoding="utf-8", errors="replace",
                                env=env, cwd=str(APP_ROOT))
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def install_torch(gpu: dict[str, object], *, quiet: bool = True) -> tuple[bool, str]:
    """Install torch/torchaudio from the best CUDA channel available."""
    for channel in torch_channels(gpu):
        if channel == "cpu":
            args = ["torch", "torchaudio"]
            label = "CPU 版（未检测到可用 NVIDIA 驱动）"
        else:
            args = ["torch", "torchaudio", "--index-url", f"{PYTORCH_INDEX}/{channel}"]
            label = channel
        code, log = _pip(args, quiet=quiet)
        if code == 0:
            return True, label
        print(f"    ! {label} 装不上，换下一个通道", flush=True)
    return False, "failed"


def install(items: Iterable[Dependency], *, mirror: Optional[str] = None,
            quiet: bool = True) -> tuple[bool, str]:
    specs = [d.spec for d in items if d.module not in ("torch", "torchaudio")]
    if not specs:
        return True, ""
    index = mirror or pick_mirror()
    code, log = _pip(["-i", index] + specs, quiet=quiet)
    if code != 0 and index != MIRRORS[-1][1]:
        code, log = _pip(["-i", MIRRORS[-1][1]] + specs, quiet=quiet)
    return code == 0, log


def provision(tier: str = "all", *, gpu_mode: str = "auto",
              quiet: bool = True) -> dict[str, object]:
    """Install everything missing for ``tier`` and return a report."""
    report: dict[str, object] = {"tier": tier, "installed": [], "failed": [], "notes": []}
    todo = missing(tier)
    if not todo:
        report["notes"].append("该层级依赖已齐全")
        return report

    gpu = nvidia_info() if gpu_mode != "off" else {}
    if gpu:
        report["notes"].append(f"检测到 GPU: {gpu.get('name')} (驱动 {gpu.get('driver')}, CUDA {gpu.get('cuda', '?')})")
    elif gpu_mode != "off":
        report["notes"].append("未检测到 NVIDIA GPU，torch 将安装 CPU 版")

    names = ", ".join(d.module for d in todo)
    print(f"  待安装: {names}", flush=True)

    torch_items = [d for d in todo if d.module in ("torch", "torchaudio")]
    if torch_items:
        ok, label = install_torch(gpu, quiet=quiet)
        (report["installed"] if ok else report["failed"]).append(f"torch({label})")
        if ok:
            report["notes"].append(f"torch 通道: {label}")

    rest = [d for d in todo if d.module not in ("torch", "torchaudio")]
    if rest:
        ok, log = install(rest, quiet=quiet)
        if ok:
            report["installed"].append(", ".join(d.module for d in rest))
        else:
            report["failed"].append(", ".join(d.module for d in rest))
            report["notes"].append(log.strip().splitlines()[-1] if log.strip() else "pip 失败")

    # Re-verify instead of trusting pip's exit code.
    still = missing(tier)
    report["still_missing"] = [d.module for d in still]
    return report


# ------------------------------------------------------------------ report
def report() -> dict[str, object]:
    gpu = nvidia_info()
    layers = {}
    for tier in ("core", "voice", "tts"):
        gone = [d.module for d in missing(tier)]
        layers[tier] = {
            "missing": gone,
            "ready": not gone,
            "size_mb": sum(d.size_mb for d in MANIFEST if d.module in gone),
        }
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "gpu": gpu,
        "layers": layers,
        "torch": _torch_state(),
        "ready_for_text": layers["core"]["ready"],
        "ready_for_voice": layers["core"]["ready"] and layers["voice"]["ready"],
    }


def _torch_state() -> dict[str, object]:
    if not present("torch"):
        return {"installed": False}
    try:
        import torch  # noqa: PLC0415 - only when already installed

        return {
            "installed": True,
            "version": torch.__version__,
            "cuda": bool(torch.cuda.is_available()),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "",
        }
    except Exception as exc:  # noqa: BLE001 - a broken torch is still "installed"
        return {"installed": True, "broken": f"{type(exc).__name__}: {exc}"}


def print_report(data: Optional[dict[str, object]] = None) -> None:
    data = data or report()
    print("=" * 58)
    print(f"  Python {data['python']}")
    gpu = data.get("gpu") or {}
    if gpu:
        print(f"  GPU    {gpu.get('name')} | 驱动 {gpu.get('driver')} | CUDA {gpu.get('cuda', '?')}")
    else:
        print("  GPU    未检测到 NVIDIA 设备")
    torch_state = data.get("torch") or {}
    if torch_state.get("installed"):
        stamp = "CUDA" if torch_state.get("cuda") else "CPU"
        print(f"  torch  {torch_state.get('version')} ({stamp})"
              + (f" | {torch_state.get('device')}" if torch_state.get("device") else ""))
    else:
        print("  torch  未安装")
    for tier, label in (("core", "界面必需"), ("voice", "语音识别"), ("tts", "语音合成")):
        layer = data["layers"][tier]
        if layer["ready"]:
            print(f"  [OK] {label}")
        else:
            print(f"  [--] {label} 缺少: {', '.join(layer['missing'])}"
                  f"（约 {layer['size_mb']} MB）")
    print("=" * 58)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="检查并安装本机缺失的依赖")
    parser.add_argument("--check", action="store_true", help="只报告")
    parser.add_argument("--install", action="store_true", help="安装缺失项")
    parser.add_argument("--tier", default="all", choices=TIERS)
    parser.add_argument("--gpu", default="auto", choices=("auto", "off"))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.install:
        started = time.time()
        result = provision(args.tier, gpu_mode=args.gpu, quiet=not args.verbose)
        if args.json:
            print(json.dumps(result, ensure_ascii=True))
        else:
            print(f"  已安装: {', '.join(result['installed']) or '无'}")
            if result["failed"]:
                print(f"  安装失败: {', '.join(result['failed'])}")
            for note in result["notes"]:
                print(f"  · {note}")
            print(f"  耗时 {int(time.time() - started)}s")
        return 0 if not result.get("still_missing") else 2

    data = report()
    if args.json:
        print(json.dumps(data, ensure_ascii=True))
    else:
        print_report(data)
    ok = bool(data["ready_for_text"])
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
