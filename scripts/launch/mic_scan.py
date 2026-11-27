"""Open every capture endpoint for half a second and report who actually delivers.

The question this answers cannot be answered by looking at the device list: on
Windows the same microphone appears once per host API (MME, DirectSound, WASAPI,
WDM-KS), with the same name, and the WDM-KS copy is known to open successfully and
then deliver zero frames. A device that "exists" and a device that "captures" are
different things, and only opening it proves which one you have.

No speaking required: this measures for 0.6 s per endpoint and reports the frame
count and peak. Silence still delivers frames, so "0 frames" always means the
endpoint is not capturing at all.

    .venv\\Scripts\\python.exe scripts\\launch\\mic_scan.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LINES: list[str] = []
LOOPBACK_MARKERS = ("stereo mix", "立体声混音", "loopback", "cable output",
                    "wave out mix", "线路输入", "vb-audio", "voicemeeter",
                    "virtual audio")


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


def probe(index: int, seconds: float) -> tuple[str, int, float, str]:
    """Return (verdict, frames, peak, detail)."""
    import numpy as np
    import sounddevice as sd

    try:
        info = sd.query_devices(index)
        rate = int(info.get("default_samplerate") or 48000)
        channels = 1
        peaks: list[float] = []
        frames_seen = 0

        def callback(indata, frames, _time, status):
            nonlocal frames_seen
            frames_seen += int(frames)
            peaks.append(float(np.max(np.abs(indata))))

        with sd.InputStream(device=index, samplerate=rate, channels=channels,
                            dtype="float32", callback=callback):
            time.sleep(seconds)
        peak = max(peaks) if peaks else 0.0
        if frames_seen == 0:
            return "0 帧", 0, 0.0, f"{rate} Hz 打开成功但不送数据"
        return "OK", frames_seen, peak, f"{rate} Hz"
    except Exception as exc:  # noqa: BLE001 - busy / disabled / denied
        return "失败", 0, 0.0, f"{type(exc).__name__}: {exc}"


def hostapi_of(device) -> str:
    try:
        import sounddevice as sd

        return str(sd.query_hostapis(int(device.get("hostapi"))).get("name", ""))
    except Exception:  # noqa: BLE001
        return "?"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="扫描录音端点，看谁真能出数据")
    parser.add_argument("--seconds", type=float, default=0.6)
    parser.add_argument("--limit", type=int, default=8, help="最多试几个端点")
    parser.add_argument("--report", help="报告输出路径")
    args = parser.parse_args(argv)

    apply_site_env()

    say("=" * 68)
    say("录音端点扫描（每个试 %.1f 秒，不需要说话）" % args.seconds)
    say("=" * 68)

    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        say(f"[!!] sounddevice 不可用：{type(exc).__name__}: {exc}")
        return 2

    devices = sd.query_devices()
    default_in = sd.default.device[0]
    say(f"系统默认输入：{default_in}  {devices[default_in]['name'] if default_in >= 0 else ''}")
    say(f"系统默认输出：{sd.default.device[1]}")
    say()

    try:
        from services.stt_service import select_input_device

        chosen, why = select_input_device(devices, default_index=default_in)
        say(f"程序会选：{chosen}  {why}")
        say(f"           hostapi = {hostapi_of(devices[chosen]) if chosen is not None else '?'}")
    except Exception as exc:  # noqa: BLE001
        chosen = None
        say(f"（无法调用程序的选设备逻辑：{type(exc).__name__}: {exc}）")
    say()

    real = [(i, d) for i, d in enumerate(devices) if int(d.get("max_input_channels", 0)) > 0
            and not any(m in str(d.get("name", "")).lower() for m in LOOPBACK_MARKERS)]
    loop = [(i, d) for i, d in enumerate(devices) if int(d.get("max_input_channels", 0)) > 0
            and any(m in str(d.get("name", "")).lower() for m in LOOPBACK_MARKERS)]
    say(f"实体麦克风 {len(real)} 个，loopback {len(loop)} 个")
    say()
    say(f"{'序号':<6}{'hostapi':<22}{'结果':<8}{'帧数':>8}{'峰值':>8}  设备")
    say("-" * 68)

    results: list[tuple[int, str, str, int, float]] = []
    for index, device in (real + loop)[: args.limit]:
        verdict, frames, peak, detail = probe(index, args.seconds)
        api = hostapi_of(device)
        mark = " *" if index == chosen else ""
        say(f"{index:<6}{api:<22}{verdict:<8}{frames:>8}{peak:>8.4f}  "
            f"{str(device.get('name'))[:34]}{mark}")
        results.append((index, api, verdict, frames, peak))
    say("-" * 68)
    say("* = 程序当前会用的那一个")

    ok = [r for r in results if r[2] == "OK"]
    say()
    if any(r[0] == chosen and r[2] == "OK" for r in results):
        say("[结论] 程序选中的端点能采到数据 —— 采集这一环没问题，问题在后面的处理")
    elif ok:
        best = max(ok, key=lambda r: r[4])
        say(f"[结论] 程序选中的端点采不到，但 [{best[0]}] 可以（{best[1]}）")
        say(f"       把 site-config.cmd 改成 VOICECHAT_INPUT_DEVICE={best[0]} 即可")
    else:
        say("[结论] 一个端点都采不到数据 —— 麦克风被系统禁用/被独占，或驱动异常")
        say("       到「设置 → 系统 → 声音 → 输入」确认麦克风在响应，再重跑本工具")

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(LINES) + "\n", encoding="utf-8-sig")
        say(f"\n报告已保存：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
