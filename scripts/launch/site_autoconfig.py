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
import re
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


def find_voice_prompt(base: Path) -> Path | None:
    """The prompt audio is a file, and sites name its folder inconsistently.

    Deployed layouts seen so far: models/voice_prompt/s1.mp3 and
    models/voicechat3/tts/voice_prompt/s1.mp3, plus a bare s1.mp3 under tts/.
    Rather than guess the folder, look for the file itself, three levels deep.
    """
    lowered = Path(str(base))
    if not lowered.is_dir():
        return None
    for depth, directory in _walk_limited(lowered, max_depth=3):
        for name in ("s1.mp3", "s1.wav", "s1.flac"):
            candidate = Path(directory) / name
            if candidate.is_file():
                return candidate
    return None


def _walk_limited(root: Path, max_depth: int):
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        yield depth, current
        if depth >= max_depth:
            continue
        try:
            children = [p for p in current.iterdir() if p.is_dir()]
        except OSError:
            continue
        for child in children:
            if child.name.startswith("."):
                continue
            stack.append((child, depth + 1))


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
            if key == "voice_prompt":
                prompt = find_voice_prompt(base)
                if prompt is not None:
                    found[key] = str(prompt)
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


LOOPBACK_MARKERS = ("stereo mix", "立体声混音", "loopback", "cable output",
                    "wave out mix", "线路输入")


def audio_report() -> dict[str, object]:
    """What Windows actually offers: input list, output list, and the defaults.

    Install has to answer this on the spot. A machine can have no microphone at
    all (only a monitor with speakers), or a default *input* that is really a
    loopback of the speakers — both record silence while the UI looks healthy.
    """
    report: dict[str, object] = {"inputs": [], "outputs": [], "default_in": None,
                                 "default_out": None, "error": ""}
    try:
        import sounddevice as sd

        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        return report

    inputs: list[tuple[int, str, bool]] = []
    outputs: list[tuple[int, str]] = []
    for index, device in enumerate(devices):
        name = str(device.get("name", f"设备 {index}"))
        if int(device.get("max_input_channels", 0)) > 0:
            low = name.lower()
            inputs.append((index, name, any(m in low for m in LOOPBACK_MARKERS)))
        if int(device.get("max_output_channels", 0)) > 0:
            outputs.append((index, name))

    report["inputs"] = inputs
    report["outputs"] = outputs
    for kind, key in (("input", "default_in"), ("output", "default_out")):
        try:
            device = sd.query_devices(kind=kind)
            report[key] = (int(device["index"]), str(device["name"]))
        except Exception:  # noqa: BLE001 - "no default device" is a valid state
            report[key] = None
    return report


def real_microphones(inputs: list[tuple[int, str, bool]]) -> list[tuple[int, str, bool]]:
    return [item for item in inputs if not item[2]]


def probe_peak(index: int, seconds: float = 0.4, rate: int = 16000) -> float:
    """Peak level captured from one endpoint; -1.0 when it cannot be opened.

    The app picks a device by *name* (a real microphone before a loopback), which
    is a good guess but not proof — a disabled or dead microphone still has a
    plausible name. Measuring for a fraction of a second tells us which endpoints
    actually deliver signal, and that answer is worth storing.
    """
    try:
        import numpy as np
        import sounddevice as sd

        frames = int(rate * seconds)
        captured = sd.rec(frames, samplerate=rate, channels=1, dtype="float32",
                          device=index, blocking=True)
        data = np.asarray(captured, dtype=np.float32)
        if data.size == 0:
            return 0.0
        if not np.all(np.isfinite(data)):
            return -1.0
        peak = float(np.max(np.abs(data)))
        # A device that never delivers frames hands back an uninitialised buffer;
        # those values are astronomical, not audio. Audio stays within ±1 or so.
        if peak > 4.0:
            return -1.0
        return peak
    except Exception:  # noqa: BLE001 - busy/disabled/permission denied
        return -1.0


MIC_NAME_HINTS = ("麦克风", "microphone", "话筒", "阵列")
GENERIC_ALIASES = ("sound mapper", "主声音捕获", "primary sound")


def looks_like_microphone(name: str) -> bool:
    low = str(name).lower()
    if any(marker in low for marker in LOOPBACK_MARKERS):
        return False
    if any(alias in low for alias in GENERIC_ALIASES):
        return False  # legacy alias, not a physical endpoint
    if any(hint in low for hint in MIC_NAME_HINTS):
        return True
    # "Mic" is only meaningful as its own word — "Microsoft" contains the letters.
    import re as _re

    return _re.search(r"(?<![a-z])mic(?![a-z])", low) is not None


def choose_input_device(inputs: list[tuple[int, str, bool]]) -> tuple[int, str, float] | None:
    """Pick the most plausible working microphone.

    Name first, then measured level: a room can be silent while someone installs
    software, so "it opened and its name says microphone" already beats the
    system default, which is very often a virtual cable.
    """
    scored: list[tuple[int, float, int, str]] = []
    for index, name, _loopback in real_microphones(inputs)[:6]:
        peak = probe_peak(index)
        if peak < 0:  # cannot open: busy, disabled, or no permission
            continue
        scored.append((1 if looks_like_microphone(name) else 0, peak, index, name))
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    named, peak, index, name = scored[0]
    return index, name, peak


SITE_CONFIG = ROOT / "site-config.cmd"
CONFIG_MARKER = "REM ---- settings below this line take effect ----"


def write_device_choice(index: int) -> Path:
    """Persist the chosen device in site-config.cmd, keeping any other settings."""
    values: dict[str, str] = {}
    text = ""
    if SITE_CONFIG.is_file():
        try:
            text = SITE_CONFIG.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for line in text.splitlines():
            match = re.match(r'\s*set\s+"?([A-Za-z_][A-Za-z0-9_]*)=([^"\r\n]*)"?\s*$',
                             line, re.IGNORECASE)
            if match:
                values[match.group(1).upper()] = match.group(2).strip()
    values["VOICECHAT_INPUT_DEVICE"] = str(index)

    head = (text.split(CONFIG_MARKER, 1)[0] + CONFIG_MARKER) if CONFIG_MARKER in text else (
        "@echo off\r\n"
        "REM Site settings written by the installer and the in-app settings dialog.\r\n"
        f"{CONFIG_MARKER}")
    body = [f'set "{name}={value}"' for name, value in values.items() if value]
    SITE_CONFIG.write_text(head + "\r\n" + "\r\n".join(body) + "\r\n",
                           encoding="utf-8", newline="")
    return SITE_CONFIG


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
    parser.add_argument("--no-audio", action="store_true", help="跳过录音/播放设备检查")
    parser.add_argument("--shortcut-dir", help=argparse.SUPPRESS)  # 测试用
    args = parser.parse_args(argv)

    emit("=" * 62)
    emit(f"程序目录 : {ROOT}")
    emit(f"Python   : {sys.executable}")
    emit("")

    emit("【1/5】检查 Python 环境")
    env_ok = True
    for ok, label in check_python():
        emit(f"  [{'OK ' if ok else '!! '}] {label}")
        env_ok = env_ok and ok
    emit("")

    emit("【2/5】自动查找模型")
    models = detect_models()
    labels = {"funasr": "语音识别 Fun-ASR", "voxcpm": "语音合成 VoxCPM2",
              "voice_prompt": "音色提示", "tacit": "决策 Tacit（可选）"}
    for key, label in labels.items():
        path = models.get(key)
        emit(f"  [{'OK ' if path else ('-- ' if key == 'tacit' else '!! ')}] {label}: {path or '未找到'}")
    missing_required = [k for k in ("funasr", "voxcpm") if k not in models]
    emit("")

    audio: dict[str, object] = {"inputs": [], "outputs": [], "default_in": None,
                                "default_out": None, "error": ""}
    if args.no_audio:
        emit("【3/5】录音 / 播放设备（已跳过）")
        emit("")
    else:
        emit("【3/5】录音 / 播放设备")
        audio = audio_report()
        if audio["error"]:
            emit(f"  [!! ] 无法枚举音频设备：{audio['error']}")
        else:
            inputs = audio["inputs"]  # type: ignore[assignment]
            outputs = audio["outputs"]  # type: ignore[assignment]
            real = real_microphones(inputs)  # type: ignore[arg-type]
            emit(f"  [{'OK ' if real else '!! '}] 可用麦克风：{len(real)} 个"
                 f"（另有 loopback {len(inputs) - len(real)} 个）")  # type: ignore[arg-type]
            for index, name, loopback in inputs[:8]:  # type: ignore[misc]
                mark = "  <- loopback，不要选" if loopback else ""
                emit(f"        [{index}] {name}{mark}")
            default_in = audio["default_in"]
            if default_in:
                index, name = default_in  # type: ignore[misc]
                is_loop = any(m in str(name).lower() for m in LOOPBACK_MARKERS)
                emit(f"  [{'!! ' if is_loop else 'OK '}] 默认录音设备：[{index}] {name}"
                     + ("  <- loopback！会让对话录到一片安静" if is_loop else ""))
            else:
                emit("  [!! ] 系统没有默认录音设备")
            default_out = audio["default_out"]
            if default_out:
                index, name = default_out  # type: ignore[misc]
                emit(f"  [OK ] 默认播放设备：[{index}] {name}")
            else:
                emit("  [!! ] 系统没有默认播放设备（小薇会没有声音）")

            fixed = os.environ.get("VOICECHAT_INPUT_DEVICE", "").strip()
            if fixed:
                emit(f"  [-- ] 录音设备已被固定为：{fixed}（不再试测）")
            elif real:
                emit("       试测每个麦克风（各 0.4 秒），看哪个真能收到声音 …")
                best = choose_input_device(inputs)  # type: ignore[arg-type]
                if best is None:
                    emit("  [!! ] 所有麦克风都打不开（被别的程序占用，或没有权限）")
                elif best[2] >= 0.003:
                    if args.check:
                        emit(f"  [OK ] 能收到声音的是：[{best[0]}] {best[1]}"
                             f"（峰值 {best[2]:.3f}）—— 加 --install 才会写进配置")
                    else:
                        path = write_device_choice(best[0])
                        emit(f"  [OK ] 已自动选用：[{best[0]}] {best[1]}（峰值 {best[2]:.3f}）")
                        emit(f"         已写入 {path.name}，以后录音就用这个")
                        audio["chosen"] = (best[0], best[1], best[2])
                elif looks_like_microphone(best[1]):
                    if args.check:
                        emit(f"  [OK ] 最像麦克风且能打开：[{best[0]}] {best[1]}"
                             f"（当前静音）—— 加 --install 才会写进配置")
                    else:
                        path = write_device_choice(best[0])
                        emit(f"  [OK ] 已自动选用：[{best[0]}] {best[1]}（静音环境，设备可正常打开）")
                        emit(f"         已写入 {path.name}；第一次录音前请对着话筒说句话确认")
                        audio["chosen"] = (best[0], best[1], best[2])
                else:
                    emit(f"  [-- ] 没测到声音（最高峰值 {best[2]:.4f}），保持自动选择")
        emit("")

    emit("【4/5】检查模型服务")
    port_open = probe_service(8000)
    emit(f"  [{'OK ' if port_open else '-- '}] 8000 端口"
         + ("（已就绪）" if port_open else "（现在没开，启动脚本会自动拉起）"))
    emit(f"  {'[OK ]' if probe_service(8001) else '[-- ]'} 8001 端口（Agent，可选）")
    emit("")

    if args.check:
        emit("检查完成（未写入任何文件）。")
        return 0 if env_ok and not missing_required else 2

    emit("【5/5】写入配置并创建快捷方式")
    if missing_required:
        emit(f"  [!! ] 缺少必需权重：{', '.join(missing_required)}")
        emit("       请确认 models 目录与本程序目录同级，或用 site-config.cmd 手工指定。")

    env_file = write_generated_env(models)
    emit(f"  [OK ] 已写入 {env_file.name}（启动时自动加载）")

    ok, detail = create_shortcut(ROOT, desktop=Path(args.shortcut_dir) if args.shortcut_dir else None)
    emit(f"  [{'OK ' if ok else '!! '}] 桌面快捷方式：{detail}")

    audio_lines: list[str] = ["", "录音 / 播放设备："]
    if args.no_audio:
        audio_lines.append("  （本次跳过）")
    elif audio.get("error"):
        audio_lines.append(f"  !! 枚举失败：{audio['error']}")
    else:
        inputs = audio["inputs"]  # type: ignore[assignment]
        real = real_microphones(inputs)  # type: ignore[arg-type]
        audio_lines.append(f"  可用麦克风 {len(real)} 个，loopback {len(inputs) - len(real)} 个")  # type: ignore[arg-type]
        for index, name, loopback in inputs[:10]:  # type: ignore[misc]
            audio_lines.append(f"    [{index}] {name}" + ("   <- loopback" if loopback else ""))
        for key, label in (("default_in", "默认录音"), ("default_out", "默认播放")):
            value = audio.get(key)
            audio_lines.append(f"  {label}：" + (f"[{value[0]}] {value[1]}" if value else "无"))
        chosen = audio.get("chosen")
        if chosen:
            audio_lines.append(f"  自动选用：[{chosen[0]}] {chosen[1]}（峰值 {chosen[2]:.3f}）")

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
        *audio_lines,
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
