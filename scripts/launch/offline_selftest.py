"""Offline diagnosis: what the app sees, without needing a microphone.

Written for a closed-area machine with no network. It prints the resolved model
paths, the audio devices, whether the FunASR entry point is present, and then
transcribes the bundled ``data/s1.flac`` and compares it with ``data/S1.txt``.
That proves the speech-recognition chain end-to-end without anyone speaking.

    .venv\\Scripts\\python.exe scripts\\launch\\offline_selftest.py
    .venv\\Scripts\\python.exe scripts\\launch\\offline_selftest.py --no-asr   # 只看环境
    .venv\\Scripts\\python\\exe  ... --tts                                    # 再验合成

Exit code 0 means every check that ran passed. Nothing here writes to disk
(apart from the ASR model's own cache), starts services, or changes settings.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []
_LINES: list[str] = []


def emit(line: str = "", **_kwargs) -> None:
    """Print and remember, so --report can write the same text to a file."""
    print(line, flush=True)
    _LINES.append(line)


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(ok), detail))
    emit(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def note(text: str) -> None:
    emit(f"       {text}", flush=True)


def show_environment() -> None:
    emit(f"程序目录 : {ROOT}")
    emit(f"Python   : {sys.version.split()[0]} ({sys.executable})")
    try:
        import config

        for attr in ("FUNASR_MODEL_PATH", "ASR_MODEL_PATH", "VOXCPM_MODEL_PATH",
                     "TTS_MODEL_PATH", "VOICE_PROMPT_PATH", "VAD_MODEL_PATH"):
            value = getattr(config, attr, None)
            if value:
                exists = "存在" if os.path.exists(str(value)) else "不存在"
                note(f"{attr:18} = {value}  [{exists}]")
        note(f"SAMPLE_RATE        = {getattr(config, 'SAMPLE_RATE', '?')}")
        data_root = str(getattr(config, "DATA_ROOT", "") or "")
        if data_root:
            note(f"DATA_ROOT          = {data_root}")
            if any(ord(ch) > 127 for ch in data_root):
                # libsndfile / miniaudio open files by narrow path; a Chinese
                # folder breaks them. Live dialogue records from memory, so this
                # only affects features that re-open a saved file.
                note("注意：数据目录含中文，回放历史录音可能失败（不影响实时对话）")
    except Exception as exc:  # noqa: BLE001
        check("读取 config", False, f"{type(exc).__name__}: {exc}")
    emit()


def check_audio_devices() -> None:
    try:
        import sounddevice as sd
    except Exception as exc:  # noqa: BLE001
        check("音频设备", False, f"sounddevice 不可用：{exc}")
        return
    try:
        devices = sd.query_devices()
    except Exception as exc:  # noqa: BLE001
        check("音频设备", False, f"枚举失败：{exc}")
        return

    inputs = []
    for index, device in enumerate(devices):
        if int(device.get("max_input_channels", 0)) <= 0:
            continue
        name = str(device.get("name", ""))
        loop = any(m in name.lower() for m in
                   ("stereo mix", "立体声混音", "cable", "loopback", "wave out mix"))
        inputs.append((index, name, loop))

    if not inputs:
        check("录音设备", False, "没有任何输入设备（系统里没插麦克风？）")
        return
    check("录音设备", True, f"共 {len(inputs)} 个输入设备")
    for index, name, loop in inputs:
        note(f"[{index}] {name}" + ("   <- loopback，录扬声器，通常不要选" if loop else ""))
    try:
        default = sd.query_devices(kind="input")["index"]
        note(f"系统默认输入 = [{default}]")
    except Exception:
        note("系统默认输入 = 未设置")
    forced = os.environ.get("VOICECHAT_INPUT_DEVICE", "").strip()
    note(f"VOICECHAT_INPUT_DEVICE = {forced or '（未设置，自动选择）'}")
    emit()


def check_entry_point() -> None:
    """The 2026-11 deployment broke here: no usable model.py beside the weights."""
    try:
        import config
    except Exception:
        return
    path = getattr(config, "FUNASR_MODEL_PATH", None) or getattr(config, "ASR_MODEL_PATH", None)
    if not path:
        check("FunASR 权重路径", False, "config 里没有解析到 ASR 权重路径")
        return
    check("FunASR 权重路径", os.path.isdir(str(path)), str(path))
    entry = Path(str(path)) / "model.py"
    if not check("FunASR 入口 model.py", entry.is_file(), str(entry)):
        note("没有这个文件时，ASR 会退回 funasr.AutoModel 并报")
        note("'NoneType' object has no attribute 'from_pretrained'。")
        note("解决办法：用记事本新建 model.py，内容两行：")
        note("    from funasr.models.fun_asr_nano.model import FunASRNano")
        note('    __all__ = ["FunASRNano"]')
        return
    try:
        from services.stt_service import load_checkpoint_entry

        cls = load_checkpoint_entry(str(path))
    except ImportError:
        # Deployments before 2026-11-19 have no helper; load the entry the same
        # way the patched code does, so this diagnosis works before patching too.
        cls = None
        entry = Path(str(path)) / "model.py"
        if entry.is_file():
            try:
                import importlib.util

                spec = importlib.util.spec_from_file_location("_vc_selftest_entry", entry)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                cls = getattr(module, "FunASRNano", None)
            except Exception as exc:  # noqa: BLE001
                note(f"加载 model.py 失败：{type(exc).__name__}: {exc}")
    except Exception:
        cls = None
    check("FunASR 入口可加载", cls is not None,
          getattr(cls, "__name__", "加载失败（详见上方告警）"))


def check_asr() -> None:
    audio_file = ROOT / "data" / "s1.flac"
    text_file = ROOT / "data" / "S1.txt"
    if not audio_file.is_file():
        check("语音识别", False, f"缺少测试音频 {audio_file}")
        return
    try:
        import miniaudio
        import numpy as np

        import config
        from services.stt_service import get_stt_service

        # 按字节解码。机器装在 D:\戒毒所 这类中文目录下时，
        # soundfile/lib sndfile 会报 "psf_fseek() failed"，
        # miniaudio.decode_file 会报 DecodeError -7 —— 它们都按窄字符路径打开文件。
        raw = audio_file.read_bytes()
        try:
            decoded = miniaudio.decode(raw, output_format=miniaudio.SampleFormat.FLOAT32)
        except Exception:
            import io

            import soundfile as sf

            data, rate = sf.read(io.BytesIO(raw), dtype="float32")
            decoded = type("Decoded", (), {
                "samples": data.reshape(-1),
                "sample_rate": rate,
                "nchannels": 1 if data.ndim == 1 else data.shape[1],
            })()
        audio = np.array(decoded.samples, dtype=np.float32).reshape(-1, decoded.nchannels).mean(axis=1)
        target = int(getattr(config, "SAMPLE_RATE", 16000))
        if decoded.sample_rate != target:
            try:
                import soxr

                audio = soxr.resample(audio, decoded.sample_rate, target)
            except Exception:
                note(f"（采样率 {decoded.sample_rate} 与 {target} 不同，未重采样）")

        service = get_stt_service()
        service.load_model()
    except Exception as exc:  # noqa: BLE001
        check("语音识别", False, f"{type(exc).__name__}: {exc}")
        return

    if getattr(service, "model", None) is None:
        check("语音识别", False, "ASR 模型没有加载成功（见上方日志）")
        return
    try:
        text = str(service.transcribe(audio)).strip()
    except Exception as exc:  # noqa: BLE001
        check("语音识别", False, f"转写失败：{type(exc).__name__}: {exc}")
        return
    note(f"转写结果：{text}")
    if not text:
        check("语音识别", False, "转写为空")
        return
    if text_file.is_file():
        expected = text_file.read_text(encoding="utf-8")
        bare = lambda v: re.sub(r"[\s，。、！？；：,.!?;:]", "", v)  # noqa: E731
        ok = bare(text) == bare(expected)
        check("语音识别（与 data/S1.txt 一致）", ok,
              "完全一致" if ok else f"参考文本：{expected.strip()[:40]}")
    else:
        check("语音识别", True, "没有参考文本，仅验证能出字")


def check_tts() -> None:
    try:
        import numpy as np

        from services.tts_service import get_tts_service

        service = get_tts_service()
        service.load_model(use_streaming=False)
        audio = service.generate("你好，我是小薇。")
    except Exception as exc:  # noqa: BLE001
        check("语音合成", False, f"{type(exc).__name__}: {exc}")
        return
    samples = np.asarray(audio).squeeze()
    seconds = len(samples) / float(getattr(service, "sample_rate", 48000) or 48000)
    check("语音合成", len(samples) > 0 and seconds > 0.3, f"生成 {seconds:.2f}s 音频")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线自检（不需要麦克风）")
    parser.add_argument("--no-asr", action="store_true", help="跳过语音识别")
    parser.add_argument("--tts", action="store_true", help="额外验证语音合成")
    parser.add_argument("--report", help="把同样的内容写到这个文件（供现场回传）")
    args = parser.parse_args(argv)

    emit("=" * 60)
    show_environment()
    check_audio_devices()
    check_entry_point()
    if not args.no_asr:
        check_asr()
    if args.tts:
        check_tts()

    failed = [r for r in RESULTS if not r[1]]
    emit("=" * 60)
    emit(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    for name, _ok, detail in failed:
        emit(f"  失败：{name} — {detail}")

    if args.report:
        try:
            path = Path(args.report)
            path.parent.mkdir(parents=True, exist_ok=True)
            # utf-8-sig: Notepad on a Chinese Windows shows it without mojibake.
            path.write_text("\n".join(_LINES) + "\n", encoding="utf-8-sig")
            emit(f"报告已保存：{path}")
        except OSError as exc:
            emit(f"报告写入失败：{exc}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
