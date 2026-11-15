"""ASR 启动预热验证：用 0.5s 静音吸收首调编译开销，保证第一次真实语音即低延迟。"""
import os
import time

ROOT = r"D:\models\voicechat3\asr\Fun-ASR-Nano-2512"
VAD = r"D:\models\voicechat3\asr\fsmn-vad"
WAV = os.path.join(ROOT, "example", "zh.wav")
os.environ["MODELSCOPE_OFFLINE"] = "1"

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
from funasr import AutoModel  # noqa: E402

t0 = time.time()
model = AutoModel(model=ROOT, vad_model=VAD, vad_kwargs={"max_single_segment_time": 30000},
                  device="cuda:0", disable_update=True, disable_pbar=True, log_level="ERROR")
print(f"加载 {time.time() - t0:.1f}s")

sil = r"D:\voicechat3\logs\_sil500.wav"
sf.write(sil, np.zeros(int(16000 * 0.5), dtype=np.float32), 16000)

t = time.time()
model.generate(input=[sil], cache={}, batch_size=1, language="中文", itn=True)
print(f"静音预热 {time.time() - t:.2f}s")

for i in range(2):
    t = time.time()
    r = model.generate(input=[WAV], cache={}, batch_size=1, language="中文", itn=True)
    print(f"预热后第{i + 1}次真实语音 {time.time() - t:.2f}s  {r[0]['text']}")
