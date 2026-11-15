"""VoxCPM2 本地离线 TTS 实测：Voice Design + 流式，记录首声与 RTF。"""
import os
import time

MODEL_DIR = r"D:\models\voicechat3\tts\VoxCPM2"
OUT = r"D:\voicechat3\logs"
os.environ["MODELSCOPE_OFFLINE"] = "1"
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from voxcpm import VoxCPM  # noqa: E402

t0 = time.time()
model = VoxCPM.from_pretrained(MODEL_DIR, load_denoiser=False)
print(f"加载 {time.time() - t0:.1f}s  显存 {torch.cuda.memory_allocated() / 2**30:.2f} GiB")
sr = model.tts_model.sample_rate
print(f"采样率 {sr}")

TEXT = "(A young woman, gentle and warm voice, calm pace)你今天心情怎么样？最近睡得还好吗？"

# 一次非流式，供听感检查
t = time.time()
wav = model.generate(text=TEXT, cfg_value=2.0, inference_timesteps=10)
el = time.time() - t
dur = len(wav) / sr
p = os.path.join(OUT, "tts_design.wav")
sf.write(p, wav, sr)
print(f"非流式 用时 {el:.2f}s  音频 {dur:.2f}s  RTF {el / dur:.2f}  -> {p}")

# 流式：量首包延迟
t = time.time()
first = None
chunks = []
for ch in model.generate_streaming(text=TEXT, cfg_value=2.0, inference_timesteps=10):
    if first is None:
        first = time.time() - t
    chunks.append(ch)
wav2 = np.concatenate(chunks)
el = time.time() - t
dur2 = len(wav2) / sr
p2 = os.path.join(OUT, "tts_stream.wav")
sf.write(p2, wav2, sr)
print(f"流式 首包 {first * 1000:.0f} ms  总 {el:.2f}s  音频 {dur2:.2f}s  RTF {el / dur2:.2f}  块数 {len(chunks)}")
print(f"峰值显存 {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB")
