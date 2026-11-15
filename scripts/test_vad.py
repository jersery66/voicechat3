"""FSMN-VAD 首调开销实测：验证是否需要用短音频预热。"""
import os
import time

VAD = r"D:\models\voicechat3\asr\fsmn-vad"
WAV = r"D:\models\voicechat3\asr\Fun-ASR-Nano-2512\example\zh.wav"
os.environ["MODELSCOPE_OFFLINE"] = "1"

from funasr import AutoModel  # noqa: E402

t0 = time.time()
vad = AutoModel(model=VAD, device="cuda:0", disable_update=True, disable_pbar=True, log_level="ERROR")
print(f"加载 {time.time() - t0:.1f}s")

for i in range(3):
    t = time.time()
    res = vad.generate(input=WAV, cache={}, fs=16000)
    segs = res[0].get("value", [])
    print(f"第{i + 1}次 {time.time() - t:6.3f}s  段数 {len(segs)}  {segs[:2]}")

# 用 0.3s 静音预热，再看真实语音首调
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

sil = np.zeros(int(16000 * 0.3), dtype=np.float32)
p = r"D:\voicechat3\logs\_sil.wav"
sf.write(p, sil, 16000)
t = time.time()
vad.generate(input=p, cache={}, fs=16000)
print(f"静音预热 {time.time() - t:.3f}s")
t = time.time()
vad.generate(input=WAV, cache={}, fs=16000)
print(f"预热后真实语音 {time.time() - t:.3f}s")
