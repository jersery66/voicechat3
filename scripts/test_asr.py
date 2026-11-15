"""Fun-ASR 本地离线转写实测（不联网）。"""
import os
import sys
import time

ROOT = r"D:\models\voicechat3\asr\Fun-ASR-Nano-2512"
VAD = r"D:\models\voicechat3\asr\fsmn-vad"

os.environ["MODELSCOPE_OFFLINE"] = "1"
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from funasr import AutoModel  # noqa: E402

t0 = time.time()
model = AutoModel(
    model=ROOT,
    vad_model=VAD,
    vad_kwargs={"max_single_segment_time": 30000},
    device="cuda:0",
    disable_update=True,
    disable_pbar=True,
    log_level="ERROR",
)
print(f"模型加载耗时 {time.time() - t0:.1f} s")

for name, lang in (("zh", "中文"), ("en", "英文")):
    wav = os.path.join(ROOT, "example", f"{name}.wav")
    t = time.time()
    res = model.generate(input=[wav], cache={}, batch_size=1, language=lang, itn=True)
    el = time.time() - t
    txt = res[0]["text"]
    print(f"[{name}] {el:5.2f}s  {txt}")
