"""Record four seconds from the machine's microphone and transcribe it immediately.

Why a separate tool: the install-time ASR check proves the model works on a file
that ships with the program. It says nothing about whether *this* machine's
microphone delivers usable audio. When typing works but recording produces no
text, the split is either capture (silence, wrong rate, muted input) or the app's
own flow (VAD, model not loaded in-process). This tool answers the first half and
picks up the app log for the second.

    .venv\\Scripts\\python.exe scripts\\launch\\mic_test.py [--seconds 4]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LINES: list[str] = []


def say(text: str = "") -> None:
    print(text, flush=True)
    LINES.append(text)


def apply_site_env() -> None:
    env_file = ROOT / "site-env.json"
    if not env_file.is_file():
        return
    try:
        payload = json.loads(env_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for name, value in (payload.get("env") or {}).items():
        if value and not os.environ.get(str(name)):
            os.environ[str(name)] = str(value)


def pick_device(preferred: int | None) -> tuple[int, str]:
    import sounddevice as sd

    if preferred is not None:
        info = sd.query_devices(preferred)
        return preferred, str(info.get("name", ""))
    index = int(sd.default.device[0])
    info = sd.query_devices(index)
    return index, str(info.get("name", ""))


def record(seconds: float, device: int) -> tuple[object, int, int]:
    """Return (float32 mono array, sample rate, channels).

    Devices that only accept 44.1/48 kHz reject a 16 kHz stream, which is the
    single most common reason a working microphone still yields nothing: the open
    fails, the app falls through to another endpoint, and nobody sees the error.
    """
    import numpy as np
    import sounddevice as sd

    attempts: list[int] = []
    try:
        default_rate = int(sd.query_devices(device)["default_samplerate"])
        attempts.append(default_rate)
    except Exception:  # noqa: BLE001
        pass
    for rate in (16000, 44100, 48000):
        if rate not in attempts:
            attempts.append(rate)

    last_error: Exception | None = None
    for rate in attempts:
        for channels in (1, 2):
            try:
                frames = int(rate * seconds)
                captured = sd.rec(frames, samplerate=rate, channels=channels,
                                  dtype="float32", device=device, blocking=True)
                data = np.asarray(captured, dtype=np.float32)
                if data.ndim > 1:
                    data = data.mean(axis=1)
                say(f"  以 {rate} Hz / {channels} 声道录制成功")
                return data, rate, channels
            except Exception as exc:  # noqa: BLE001
                last_error = exc
    raise RuntimeError(f"无法打开该设备：{last_error}")


def write_wav(path: Path, data, rate: int) -> None:
    """Write via the stdlib: libsndfile cannot open a Chinese path on Windows."""
    import numpy as np

    pcm = np.clip(np.asarray(data, dtype=np.float32), -1.0, 1.0)
    pcm16 = (pcm * 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm16.tobytes())


def transcribe(data, rate: int) -> tuple[bool, str]:
    import numpy as np

    import config
    from services.stt_service import get_stt_service

    target = int(getattr(config, "SAMPLE_RATE", 16000))
    audio = np.asarray(data, dtype=np.float32)
    if rate != target:
        try:
            import soxr

            audio = soxr.resample(audio, rate, target)
            say(f"  重采样 {rate} → {target} Hz")
        except Exception as exc:  # noqa: BLE001
            say(f"  重采样失败（{type(exc).__name__}），按原采样率送入")
    service = get_stt_service()
    service.load_model()
    if getattr(service, "model", None) is None:
        return False, "STT 模型未加载"
    return True, str(service.transcribe(audio)).strip()


def tail_app_log() -> None:
    logs = sorted((ROOT / "logs").glob("voicechat_*.log"))
    if not logs:
        say("  （没有找到应用日志）")
        return
    newest = logs[-1]
    say(f"  最近的应用日志：{newest.name}")
    try:
        lines = newest.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        say(f"  读取失败：{exc}")
        return
    keep = [line for line in lines
            if any(key in line for key in ("Selected input device", "ASRDebug",
                                           "recording", "Recording", "VAD",
                                           "STT", "transcribe", "Error", "error"))]
    for line in keep[-14:]:
        say("    " + line[:160])
    if not keep:
        say("    （日志里没有与录音/识别相关的行）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="录四秒并立刻转写")
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--device", type=int, help="指定设备序号，默认用系统默认")
    parser.add_argument("--report", help="报告输出路径")
    parser.add_argument("--from-wav", help="不录音，直接转写这个音频文件（诊断用）")
    args = parser.parse_args(argv)

    apply_site_env()
    say("=" * 64)
    say("录音 + 转写 实测")
    say("=" * 64)

    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        say(f"[!!] sounddevice 不可用：{type(exc).__name__}: {exc}")
        return 2

    preferred = args.device
    env_device = os.environ.get("VOICECHAT_INPUT_DEVICE", "").strip()
    if preferred is None and env_device.isdigit():
        preferred = int(env_device)
        say(f"按 site-config.cmd 使用设备 [{preferred}]")
    try:
        device, name = pick_device(preferred)
    except Exception as exc:  # noqa: BLE001
        say(f"[!!] 选设备失败：{type(exc).__name__}: {exc}")
        return 2
    say(f"录音设备：[{device}] {name}")
    say(f"系统默认输入：{sd.default.device[0]}；默认输出：{sd.default.device[1]}")
    say()

    stamp = time.strftime("%Y%m%d-%H%M%S")

    if args.from_wav:
        source = Path(args.from_wav)
        say(f"跳过录音，直接转写：{source}")
        try:
            import miniaudio
            import numpy as np

            decoded = miniaudio.decode(source.read_bytes(),
                                       output_format=miniaudio.SampleFormat.FLOAT32)
            data = np.array(decoded.samples, dtype=np.float32)
            if decoded.nchannels > 1:
                data = data.reshape(-1, decoded.nchannels).mean(axis=1)
            rate = int(decoded.sample_rate)
        except Exception as exc:  # noqa: BLE001
            say(f"[!!] 读不出音频：{type(exc).__name__}: {exc}")
            _finish(args)
            return 1
        say(f"  采样率 {rate} Hz，样本数 {len(data)}，时长 {len(data) / rate:.2f} 秒")
        say()
        say("开始转写（模型加载约 30–60 秒）…")
        ok, text = transcribe(data, rate)
        say()
        say(f"转写结果：{text or '（空）'}")
        say("[结论] 这条路径只验证识别，不涉及麦克风" if ok else f"[结论] {text}")
        _finish(args)
        return 0

    say(f"请对着话筒连续说话 {args.seconds:.0f} 秒（从“3、2、1”后开始）…")
    for count in (3, 2, 1):
        say(f"  {count}")
        time.sleep(1)

    try:
        data, rate, channels = record(args.seconds, device)
    except Exception as exc:  # noqa: BLE001
        say(f"[!!] {exc}")
        say("     设备被占用或驱动异常：关掉其它会议/录音软件再试")
        _finish(args)
        return 1

    import numpy as np

    peak = float(np.max(np.abs(data))) if len(data) else 0.0
    rms = float(np.sqrt(np.mean(np.square(data)))) if len(data) else 0.0
    wav_path = ROOT / "logs" / f"mic-test-{stamp}.wav"
    try:
        write_wav(wav_path, data, rate)
        say(f"  已保存：{wav_path}")
    except Exception as exc:  # noqa: BLE001
        say(f"  保存 wav 失败：{exc}")
    say(f"  样本数 {len(data)}，时长 {len(data) / rate:.2f} 秒")
    say(f"  峰值 {peak:.4f}，RMS {rms:.4f}")

    if peak < 0.001:
        say()
        say("[结论] 没收到声音 —— 问题在采集，不在识别：")
        say("       ① 话筒上的静音开关/旋钮；② Windows 音量合成器里该设备是否为 0；")
        say("       ③ 设置 → 隐私 → 麦克风，允许桌面应用访问")
        _finish(args)
        return 1

    say()
    say("开始转写（模型加载约 30–60 秒）…")
    ok, text = transcribe(data, rate)
    say()
    if not ok:
        say(f"[结论] {text} —— 这是识别侧的问题，与话筒无关")
    elif text:
        say(f"转写结果：{text}")
        say("[结论] 采集与识别都正常 —— 说明是程序里的流程问题（VAD 或加载时序）")
    else:
        say("转写结果：（空）")
        say("[结论] 有声音但识别为空 —— 多半是采样率/声道不匹配，或声音太轻")
    _finish(args)
    return 0


def _finish(args) -> None:
    say()
    say("=" * 64)
    say("应用日志（最近 14 条相关记录）")
    say("=" * 64)
    tail_app_log()
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(LINES) + "\n", encoding="utf-8-sig")
        say(f"\n报告已保存：{path}")


if __name__ == "__main__":
    sys.exit(main())
