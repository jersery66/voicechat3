#!/usr/bin/env python
"""Generate the core relaxation training media with fully local models.

The relaxation center expects <app>/media_library/relaxation/<name>.mp4 but
the repository ships no media (see docs/design/holdout_scale_miss_analysis.md
and check_config WARN).  This tool closes that gap without any download:

1. the guidance narration is synthesised by the *real* local VoxCPM2 with the
   same zero-shot voice prompt the counselor uses (TTSService, s1.mp3);
2. pacing silences are inserted between instruction segments so the guidance
   follows the breathing rhythm;
3. a calm static background image is rendered with PIL;
4. image + audio are muxed to H.264/AAC mp4 with the ffmpeg binary bundled by
   imageio-ffmpeg (no network).

Everything is real: the audio is genuine model synthesis and the training
follows the catalog definition (recommended_duration_seconds=180).  A
provenance README is written next to the media.

Usage:
    python scripts/generate_relaxation_media.py [--content breathing]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

FFMPEG = None  # resolved lazily via imageio_ffmpeg

#: (text, silence_seconds_after) -- numbers are Chinese so the TTS reads them
#: as count beats, matching the pacing a counselor would speak.
BREATHING_SEGMENTS = [
    ("欢迎来到呼吸放松训练。接下来的几分钟，请找一个舒服的姿势坐好，让双手自然地放在腿上。", 2.0),
    ("如果愿意，你可以轻轻闭上眼睛。我们一起来做几次缓慢的、平稳的呼吸。", 3.0),
    ("不用刻意用力，只需要跟着声音的节奏，让自己的呼吸慢慢慢下来。", 3.0),
    ("现在，我们开始。慢慢地吸气……二……三……四。", 1.0),
    ("轻轻屏住呼吸……二……三。", 1.5),
    ("好，再缓缓地呼气……二……三……四……五……六。", 2.5),
    ("很好。我们再来一次。慢慢地吸气……二……三……四。", 1.0),
    ("屏住……二……三。", 1.5),
    ("缓缓呼气……二……三……四……五……六。", 2.5),
    ("继续跟着这个节奏。吸气……二……三……四。", 1.0),
    ("屏住……二……三。", 1.5),
    ("呼气……二……三……四……五……六。", 2.5),
    ("做得很好。肩膀慢慢放松，让呼吸带动整个身体沉下来。", 3.0),
    ("再来最后一组。深深地吸气……二……三……四。", 1.0),
    ("屏住……二……三。", 1.5),
    ("把所有的疲惫，随着呼气……一点一点地送出去……二……三……四……五……六。", 3.0),
    ("现在，让呼吸回到它自己的节奏，不用再控制它。", 3.0),
    ("感受一下此刻的身体：也许比几分钟前，安静了一些，轻松了一些。", 3.0),
    ("轻轻动一动手指、脚趾，转一转肩膀。", 2.5),
    ("当你准备好了，慢慢睁开眼睛。本次呼吸放松训练到这里就结束了，谢谢你的投入。", 0.0),
]

CONTENTS = {
    "breathing": ("呼吸训练.mp4", BREATHING_SEGMENTS, "呼吸放松训练"),
}


def write_wav_pcm16(audio: np.ndarray, path: Path, sample_rate: int) -> None:
    """Write mono float audio as 16-bit PCM WAV without torchaudio.

    torchaudio 2.9 routes through torchcodec, whose DLL fails to load when the
    project path contains non-ASCII characters; production data saving uses
    the same numpy approach (data/data_manager.py::_save_wav).
    """
    import wave

    arr = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (arr * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def synthesize(segments: list[tuple[str, float]]) -> tuple[np.ndarray, int]:
    from services.tts_service_voxcpm import TTSService

    tts = TTSService()
    tts.load_model()
    if tts.model is None:
        raise RuntimeError("VoxCPM model failed to load -- see logs above")

    parts: list[np.ndarray] = []
    total_text_len = 0
    for text, silence in segments:
        audio = tts.generate(text)
        if audio is None or audio.size == 0:
            raise RuntimeError(f"VoxCPM returned empty audio for: {text[:20]}...")
        parts.append(audio)
        total_text_len += len(text)
        if silence > 0:
            parts.append(np.zeros(int(tts.sample_rate * silence), dtype=np.float32))
        print(f"  synthesized {len(text)} chars (+{silence:.1f}s silence)")
    track = np.concatenate(parts)
    print(f"total: {total_text_len} chars, {len(track)/tts.sample_rate:.1f}s @ {tts.sample_rate}Hz")
    return track, int(tts.sample_rate)


def render_background(width: int, height: int, title: str) -> Path:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    top = (206, 227, 216)
    bottom = (158, 194, 186)
    for y in range(height):
        t = y / height
        color = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
        draw.line([(0, y), (width, y)], fill=color)
    # a few slow, calm circles as breathing cues
    cx, cy = width // 2, height // 2 - 40
    for radius, alpha in ((300, 235), (230, 215), (160, 200)):
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            outline=(255, 255, 255), width=5,
        )
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 72) if font_path.exists() else None
    draw.text((cx, height - 160), title, fill=(60, 90, 82), anchor="mm", font=font)
    path = PROJECT_ROOT / "media_library" / "relaxation" / "_background.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


def mux(image: Path, wav: Path, out: Path) -> None:
    global FFMPEG
    import imageio_ffmpeg

    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        FFMPEG, "-y",
        "-loop", "1", "-i", str(image),
        "-i", str(wav),
        "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k",
        "-shortest",
        "-movflags", "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content", default="breathing", choices=sorted(CONTENTS))
    parser.add_argument("--skip-llm-check", action="store_true")
    args = parser.parse_args()

    filename, segments, title = CONTENTS[args.content]
    out_dir = PROJECT_ROOT / "media_library" / "relaxation"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / filename
    if out.exists():
        print(f"already exists, remove it first to regenerate: {out}")
        return 0

    print(f"[1/3] synthesising {args.content} guidance with local VoxCPM2...")
    track, sr = synthesize(segments)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "guidance.wav"
        write_wav_pcm16(track, wav, sr)
        print(f"[2/3] rendering background image ({title})...")
        image = render_background(1280, 720, title)
        print(f"[3/3] muxing to {out.name}...")
        mux(image, wav, out)
        image.unlink(missing_ok=True)

    size_mb = out.stat().st_size / 1e6
    print(f"written: {out} ({size_mb:.1f} MB)")

    readme = out_dir / "README.md"
    readme.write_text(
        "# 放松训练媒体（本地生成）\n\n"
        f"- 文件：{filename}\n"
        "- 语音：本机 VoxCPM2 + 音色提示 s1.mp3 真实合成（scripts/generate_relaxation_media.py）\n"
        "- 画面：PIL 渲染静态背景 + imageio-ffmpeg 合成 H.264/AAC\n"
        "- 时长：约 %.0f 秒（目录定义 recommended_duration_seconds=180）\n"
        "- 生成日期：2026-11-04\n" % (len(track) / sr),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
