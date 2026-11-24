"""Find out why speech recognition will not load, and fix the common cause.

Text chat working while recording does not means the dialogue/model stack is fine
— recognition is a separate model with its own loader. This script inspects that
one path and nothing else:

    .venv\\Scripts\\python.exe scripts\\launch\\asr_doctor.py            # 只看
    .venv\\Scripts\\python.exe scripts\\launch\\asr_doctor.py --fix      # 缺入口就补上

The usual cause on a machine deployed with the older folder layout
(``D:\\models\\voicechat3\\asr\\Fun-ASR-Nano-2512``) is a missing ``model.py``
entry point beside the weights: the loader then falls back to
``funasr.AutoModel``, which fails on this checkpoint with
``'NoneType' object has no attribute 'from_pretrained'``. ``--fix`` writes the
four-line shim and re-tests immediately.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SHIM = (
    '"""Local shim: expose the packaged FunASRNano class to the app loader."""\n'
    "\n"
    "from funasr.models.fun_asr_nano.model import FunASRNano\n"
    "\n"
    '__all__ = ["FunASRNano"]\n'
)

LINES: list[str] = []


def say(text: str = "") -> None:
    print(text, flush=True)
    LINES.append(text)


def banner(text: str) -> None:
    say()
    say("=" * 64)
    say(text)
    say("=" * 64)


def resolve_model_dir() -> Path | None:
    try:
        import config
    except Exception as exc:  # noqa: BLE001
        say(f"读取 config 失败：{type(exc).__name__}: {exc}")
        return None
    for attr in ("FUNASR_MODEL_PATH", "ASR_MODEL_PATH"):
        value = getattr(config, attr, None)
        if value:
            return Path(str(value))
    env = os.environ.get("FUNASR_MODEL_PATH", "").strip()
    return Path(env) if env else None


def show_versions() -> None:
    say(f"Python      : {platform.python_version()}  ({sys.executable})")
    say(f"平台        : {platform.platform()}")
    for module in ("torch", "torchaudio", "funasr", "transformers", "sounddevice"):
        try:
            imported = __import__(module)
            version = getattr(imported, "__version__", "?")
            say(f"{module:12}: {version}")
        except Exception as exc:  # noqa: BLE001
            say(f"{module:12}: 导入失败 -> {type(exc).__name__}: {exc}")


def show_dir(model_dir: Path) -> None:
    say(f"识别模型目录: {model_dir}")
    if not model_dir.is_dir():
        say("  [!!] 这个目录不存在")
        return
    try:
        entries = sorted(model_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        say(f"  [!!] 无法列出目录：{exc}")
        return
    say(f"  共 {len(entries)} 项，前 14 项：")
    for entry in entries[:14]:
        size = entry.stat().st_size if entry.is_file() else 0
        kind = "dir " if entry.is_dir() else "file"
        say(f"    {kind} {entry.name}"
            + (f"   {size / 1048576:.1f} MB" if size > 1048576 else f"   {size} B" if size else ""))

    for name in ("model.pt", "model.py", "config.yaml", "configuration.json"):
        mark = "[OK ]" if (model_dir / name).exists() else "[-- ]"
        say(f"  {mark} {name}")


def try_import_entry(model_dir: Path) -> object | None:
    entry = model_dir / "model.py"
    if not entry.is_file():
        say("  [!!] 没有 model.py，跳过入口导入")
        return None
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_asr_doctor_entry", entry)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls = getattr(module, "FunASRNano", None)
        say(f"  [{'OK ' if cls else '!! '}] model.py 可加载，FunASRNano = {cls}")
        return cls
    except Exception:  # noqa: BLE001
        say("  [!!] model.py 加载失败，完整堆栈：")
        for line in traceback.format_exc().rstrip().splitlines():
            say("        " + line)
        return None


def try_funasr_automodel(model_dir: Path) -> None:
    say("  尝试 funasr.AutoModel（备选路径）…")
    try:
        from funasr import AutoModel

        AutoModel(model=str(model_dir), device="cpu", vad_model=None, punc_model=None,
                  disable_update=True, disable_pbar=True, disable_log=True)
        say("  [OK ] AutoModel 可以加载")
    except Exception:  # noqa: BLE001
        say("  [!!] AutoModel 也失败，完整堆栈：")
        for line in traceback.format_exc().rstrip().splitlines():
            say("        " + line)


def try_transcribe(model_dir: Path) -> bool:
    audio = ROOT / "data" / "s1.flac"
    if not audio.is_file():
        say(f"  缺少测试音频 {audio}，跳过转写")
        return False
    try:
        import miniaudio
        import numpy as np

        import config
        from services.stt_service import get_stt_service

        # Bytes, not path: libsndfile/miniaudio fail on a Chinese folder name.
        decoded = miniaudio.decode(audio.read_bytes(), output_format=miniaudio.SampleFormat.FLOAT32)
        audio_array = np.array(decoded.samples, dtype=np.float32)
        if decoded.nchannels > 1:
            audio_array = audio_array.reshape(-1, decoded.nchannels).mean(axis=1)
        target = int(getattr(config, "SAMPLE_RATE", 16000))
        if decoded.sample_rate != target:
            import soxr

            audio_array = soxr.resample(audio_array, decoded.sample_rate, target)

        service = get_stt_service()
        service.load_model()
        if getattr(service, "model", None) is None:
            say("  [!!] 服务里 STT 模型为空，加载失败（见上面日志）")
            return False
        text = str(service.transcribe(audio_array)).strip()
        say(f"  转写结果：{text or '（空）'}")
        return bool(text)
    except Exception:  # noqa: BLE001
        say("  [!!] 转写过程抛异常：")
        for line in traceback.format_exc().rstrip().splitlines():
            say("        " + line)
        return False


def ensure_shim(model_dir: Path, apply_fix: bool) -> bool:
    entry = model_dir / "model.py"
    if entry.is_file():
        return False
    if not apply_fix:
        say("  （加 --fix 或双击 Site-Fix-ASR.bat 可自动补上这个文件）")
        return False
    try:
        entry.write_text(SHIM, encoding="utf-8")
        say(f"  [已补] 写入 {entry}")
        return True
    except OSError as exc:
        say(f"  [!!] 写入失败：{exc}（目录只读？用管理员身份运行）")
        return False


def apply_site_env() -> None:
    """Honour the paths the installer found, so we inspect the same folder the app uses."""
    env_file = ROOT / "site-env.json"
    if not env_file.is_file():
        return
    try:
        import json

        payload = json.loads(env_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for name, value in (payload.get("env") or {}).items():
        if value:
            os.environ.setdefault(str(name), str(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="语音识别加载诊断与修复")
    parser.add_argument("--fix", action="store_true", help="缺少 model.py 时自动补上")
    parser.add_argument("--report", help="把输出写到这个文件")
    args = parser.parse_args(argv)

    apply_site_env()
    banner("语音识别（ASR）诊断")
    show_versions()

    model_dir = resolve_model_dir()
    if model_dir is None:
        say("\n[!!] 无法确定识别模型目录，检查 config 或设置 FUNASR_MODEL_PATH")
        return 2

    banner("一、目录与关键文件")
    show_dir(model_dir)
    fixed = ensure_shim(model_dir, args.fix)
    if fixed:
        say("  已补写入口文件，下面重新验证加载")

    banner("二、加载入口")
    entry_ok = try_import_entry(model_dir) is not None
    if not entry_ok:
        try_funasr_automodel(model_dir)

    banner("三、用 data/s1.flac 实际转写")
    transcribe_ok = try_transcribe(model_dir)

    banner("结论")
    if transcribe_ok:
        say("[PASS] 识别链路可用。请重启程序，再点录音说话。")
        code = 0
    elif entry_ok:
        say("[FAIL] 入口能加载但转写失败 —— 把本文件整份发回，重点看第二节的堆栈。")
        code = 1
    elif fixed:
        say("[待复验] 刚补了 model.py，仍失败或没有转写结果 —— 把本文件整份发回。")
        code = 1
    else:
        say("[FAIL] 识别模型没有加载起来。两种可能：")
        say("       ① 目录里没有 model.py（双击 Site-Fix-ASR.bat 可自动补）")
        say("       ② 依赖与 Python 版本不匹配（看第一节和第二节的堆栈）")
        code = 1

    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(LINES) + "\n", encoding="utf-8-sig")
        say(f"\n报告已保存：{path}")
    return code


if __name__ == "__main__":
    sys.exit(main())
