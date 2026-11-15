"""ASR 延迟与音频长度的关系：判断是否存在按形状编译、需要多长度预热。"""
import os
import time

ROOT = r"D:\models\voicechat3\asr\Fun-ASR-Nano-2512"
VAD = r"D:\models\voicechat3\asr\fsmn-vad"
WAV = os.path.join(ROOT, "example", "zh.wav")
os.environ["MODELSCOPE_OFFLINE"] = "1"

import soundfile as sf  # noqa: E402
from funasr import AutoModel  # noqa: E402

d, sr = sf.read(WAV)

t0 = time.time()
model = AutoModel(model=ROOT, vad_model=VAD, vad_kwargs={"max_single_segment_time": 30000},
                  device="cuda:0", disable_update=True, disable_pbar=True, log_level="ERROR")
print(f"加载 {time.time() - t0:.1f}s  (音频总长 {len(d) / sr:.2f}s)")


def run(sec, tag, rep=2):
    n = int(16000 * sec)
    p = rf"D:\voicechat3\logs\_seg_{sec}.wav"
    sf.write(p, d[:n], 16000)
    for i in range(rep):
        t = time.time()
        r = model.generate(input=[p], cache={}, batch_size=1, language="中文", itn=True)
        print(f"{tag:<18} 第{i + 1}次 {time.time() - t:6.2f}s  {r[0]['text'][:40]}")


run(1.0, "1.0s 首调")
run(2.5, "2.5s 首调")
run(5.6, "5.6s 首调")
run(5.6, "5.6s 复调")
run(2.5, "2.5s 复调")
