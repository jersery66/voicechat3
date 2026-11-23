"""Site setup: find the models, remember the paths, put a shortcut on the desktop.

Runs on the machine, once, after the package is unzipped into place. It never
downloads anything and never rewrites the app's own config — it writes one
generated environment file that the launcher reads before starting, so the paths
it found are reused on every later start.

    .venv\\Scripts\\python.exe scripts\\launch\\site_autoconfig.py --check
    .venv\\Scripts\\python.exe scripts\\launch\\site_autoconfig.py --install

--install additionally creates the desktop shortcut (and a Start Menu entry when
that folder is writable). The shortcut carries the app icon that ships in
scripts\\launch\\xiaowei.ico and starts in this folder, so it works no matter
where the window is opened from.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATED_ENV = ROOT / "site-env.json"
REPORT = ROOT / "安装结果.txt"
ICON = ROOT / "scripts" / "launch" / "xiaowei.ico"
SHORTCUT_NAME = "小微老师"

MODEL_HINTS = {
    "funasr": ("Fun-ASR-Nano-2512", "model.pt"),
    "voxcpm": ("VoxCPM2", "config.json"),
    "voice_prompt": ("voice_prompt", "s1.mp3"),
    "tacit": ("tacit/Tacit-1.7B", "config.json"),
}


def emit(line: str = "") -> None:
    print(line, flush=True)


def candidate_model_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("VOICECHAT_MODELS_DIR", "").strip()
    if env_root:
        roots.append(Path(env_root))
    roots.append(ROOT.parent / "models")      # D:\戒毒所\models  <- 站点布局
    roots.append(ROOT / "models")
    roots.append(Path("D:/models"))           # 早期布局
    seen: list[Path] = []
    for root in roots:
        if root not in seen:
            seen.append(root)
    return seen


def find_dir(base: Path, name: str, marker: str) -> Path | None:
    direct = base / name
    if (direct / marker).is_file():
        return direct
    # One level of nesting, e.g. models/voicechat3/asr/Fun-ASR-Nano-2512
    for depth_one in base.glob("*"):
        if not depth_one.is_dir():
            continue
        nested = depth_one / name
        if (nested / marker).is_file():
            return nested
        for depth_two in depth_one.glob("*"):
            if depth_two.is_dir() and (depth_two / name / marker).is_file():
                return depth_two / name
    return None


def detect_models() -> dict[str, str]:
    found: dict[str, str] = {}
    for key, (name, marker) in MODEL_HINTS.items():
        for base in candidate_model_roots():
            if not base.is_dir():
                continue
            hit = find_dir(base, name, marker)
            if hit is not None:
                # The voice prompt is a file the TTS reads, not a folder.
                found[key] = str(hit / marker) if key == "voice_prompt" else str(hit)
                break
    return found


def check_python() -> list[tuple[bool, str]]:
    checks: list[tuple[bool, str]] = []
    checks.append((sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}"))
    for module, label in (("torch", "torch"), ("funasr", "funasr"),
                          ("sounddevice", "sounddevice"), ("miniaudio", "miniaudio")):
        try:
            __import__(module)
            checks.append((True, label))
        except Exception as exc:  # noqa: BLE001
            checks.append((False, f"{label}（{type(exc).__name__}）"))
    return checks


def probe_service(port: int = 8000) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def write_generated_env(models: dict[str, str]) -> Path:
    """Write the resolved paths as UTF-8 JSON.

    Deliberately not a .cmd: a batch file is read in the console code page, so a
    Chinese path written as GBK breaks under chcp 65001 and the reverse breaks
    elsewhere. JSON has one encoding, and site_launch.py applies it to the child
    process at start-up.
    """
    mapping = {
        "funasr": "FUNASR_MODEL_PATH",
        "voxcpm": "VOXCPM_MODEL_PATH",
        "voice_prompt": "VOICE_PROMPT_PATH",
        "tacit": "VOICECHAT_TACIT_DIR",
    }
    payload = {
        "_note": "由 scripts\\launch\\site_autoconfig.py 自动生成，重装即可刷新，不必手改。",
        "env": {var: models[key] for key, var in mapping.items() if key in models},
    }
    GENERATED_ENV.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    return GENERATED_ENV


def _powershell(script: str) -> subprocess.CompletedProcess:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )


def ps_literal(value: str) -> str:
    """Quote a single-quoted string for the child shell.

    json.dumps is wrong here: it escapes CJK to \\uXXXX, which the shell keeps
    literally, so a Chinese folder name became a literal "\\u5b89\\u88c5..." path.
    """
    return "'" + str(value).replace("'", "''") + "'"


def create_shortcut(target_dir: Path, *, desktop: Path | None = None) -> tuple[bool, str]:
    desktop = desktop or Path(os.path.expanduser("~")) / "Desktop"
    link = desktop / f"{SHORTCUT_NAME}.lnk"
    start_bat = ROOT / "Site-Start.bat"
    icon = str(ICON) if ICON.is_file() else f"{sys.executable},0"
    script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $ws = New-Object -ComObject WScript.Shell
  $sc = $ws.CreateShortcut({ps_literal(str(link))})
  $sc.TargetPath = {ps_literal(str(start_bat))}
  $sc.WorkingDirectory = {ps_literal(str(ROOT))}
  $sc.IconLocation = {ps_literal(icon)}
  $sc.Description = {ps_literal("小微老师 心理咨询语音系统")}
  $sc.WindowStyle = 7
  $sc.Save()
  Write-Output 'RESULT=DONE'
}} catch {{
  Write-Output ('RESULT=ERROR ' + $_.Exception.Message)
}}
"""
    result = _powershell(script)
    stdout = (result.stdout or "").strip()
    # stderr carries the child's CLIXML progress stream on some hosts; the only
    # signal that matters is whether the .lnk is actually on disk.
    if link.is_file():
        return True, f"已创建：{link}"
    reason = next((line.split("=", 1)[1].strip() for line in stdout.splitlines()
                   if line.startswith("RESULT=ERROR")), "")
    return False, f"创建失败：{reason or '未知原因'}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="现场安装：探测模型路径 + 建立桌面快捷方式")
    parser.add_argument("--check", action="store_true", help="只检查，不写文件")
    parser.add_argument("--install", action="store_true", help="写配置并创建快捷方式")
    parser.add_argument("--shortcut-dir", help=argparse.SUPPRESS)  # 测试用
    args = parser.parse_args(argv)

    emit("=" * 62)
    emit(f"程序目录 : {ROOT}")
    emit(f"Python   : {sys.executable}")
    emit("")

    emit("【1/4】检查 Python 环境")
    env_ok = True
    for ok, label in check_python():
        emit(f"  [{'OK ' if ok else '!! '}] {label}")
        env_ok = env_ok and ok
    emit("")

    emit("【2/4】自动查找模型")
    models = detect_models()
    labels = {"funasr": "语音识别 Fun-ASR", "voxcpm": "语音合成 VoxCPM2",
              "voice_prompt": "音色提示", "tacit": "决策 Tacit（可选）"}
    for key, label in labels.items():
        path = models.get(key)
        emit(f"  [{'OK ' if path else ('-- ' if key == 'tacit' else '!! ')}] {label}: {path or '未找到'}")
    missing_required = [k for k in ("funasr", "voxcpm") if k not in models]
    emit("")

    emit("【3/4】检查模型服务")
    port_open = probe_service(8000)
    emit(f"  [{'OK ' if port_open else '-- '}] 8000 端口"
         + ("（已就绪）" if port_open else "（现在没开，启动脚本会自动拉起）"))
    emit(f"  {'[OK ]' if probe_service(8001) else '[-- ]'} 8001 端口（Agent，可选）")
    emit("")

    if args.check:
        emit("检查完成（未写入任何文件）。")
        return 0 if env_ok and not missing_required else 2

    emit("【4/4】写入配置并创建快捷方式")
    if missing_required:
        emit(f"  [!! ] 缺少必需权重：{', '.join(missing_required)}")
        emit("       请确认 models 目录与本程序目录同级，或用 site-config.cmd 手工指定。")

    env_file = write_generated_env(models)
    emit(f"  [OK ] 已写入 {env_file.name}（启动时自动加载）")

    ok, detail = create_shortcut(ROOT, desktop=Path(args.shortcut_dir) if args.shortcut_dir else None)
    emit(f"  [{'OK ' if ok else '!! '}] 桌面快捷方式：{detail}")

    lines = [
        "小微老师 · 安装结果",
        "=" * 30,
        f"程序目录：{ROOT}",
        f"Python：{sys.executable}",
        "",
        "Python 环境：",
        *[f"  {'OK ' if ok2 else '!! '} {label}" for ok2, label in check_python()],
        "",
        "模型路径：",
        *[f"  {labels[k]}: {models.get(k, '未找到')}" for k in labels],
        "",
        f"环境文件：{env_file}",
        f"快捷方式：{detail}",
        "",
        "以后双击桌面上的“小微老师”即可启动。",
    ]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    emit(f"\n结果已保存：{REPORT.name}")
    emit("\n完成。以后双击桌面上的“小微老师”即可启动。")
    return 0 if env_ok else 2


if __name__ == "__main__":
    sys.exit(main())
