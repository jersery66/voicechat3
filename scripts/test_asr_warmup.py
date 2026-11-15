"""ASR 预热与热词对比：确认首调开销与热词效果。"""
import os
import time

ROOT = r"D:\models\voicechat3\asr\Fun-ASR-Nano-2512"
VAD = r"D:\models\voicechat3\asr\fsmn-vad"
os.environ["MODELSCOPE_OFFLINE"] = "1"

import torch  # noqa: E402
from funasr import AutoModel  # noqa: E402

t0 = time.time()
model = AutoModel(model=ROOT, vad_model=VAD, vad_kwargs={"max_single_segment_time": 30000},
                  device="cuda:0", disable_update=True, disable_pbar=True, log_level="ERROR")
print(f"加载 {time.time() - t0:.1f}s  显存 {torch.cuda.memory_allocated() / 2**30:.2f} GiB")

zh = os.path.join(ROOT, "example", "zh.wav")
prev = None
for i in range(4):
    t = time.time()
    res = model.generate(input=[zh], cache={}, batch_size=1, language="中文", itn=True)
    el = time.time() - t
    txt = res[0]["text"]
    flag = "" if prev is None else (" 一致" if txt == prev else f" 变化->{txt}")
    prev = txt
    print(f"第{i + 1}次 {el:6.2f}s  {txt}{flag}")

t = time.time()
res = model.generate(input=[zh], cache={}, batch_size=1, language="中文", itn=True, hotwords=["开放时间"])
print(f"带热词 {time.time() - t:6.2f}s  {res[0]['text']}")
print(f"峰值显存 {torch.cuda.max_memory_allocated() / 2**30:.2f} GiB")
