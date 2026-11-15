"""闭环自测：TTS 合成 -> ASR 回识，比对文本与时长，验证流式是否丢内容。"""
import os
import re

ROOT = r"D:\models\voicechat3\asr\Fun-ASR-Nano-2512"
VAD = r"D:\models\voicechat3\asr\fsmn-vad"
os.environ["MODELSCOPE_OFFLINE"] = "1"

import soundfile as sf  # noqa: E402
from funasr import AutoModel  # noqa: E402

EXPECT = "你今天心情怎么样最近睡得还好吗"


def norm(s):
    return re.sub(r"[^\u4e00-\u9fff a-zA-Z]", "", s).replace(" ", "")


model = AutoModel(model=ROOT, vad_model=VAD, vad_kwargs={"max_single_segment_time": 30000},
                  device="cuda:0", disable_update=True, disable_pbar=True, log_level="ERROR")

for name in ("tts_design", "tts_stream"):
    p = rf"D:\voicechat3\logs\{name}.wav"
    d, sr = sf.read(p)
    res = model.generate(input=[p], cache={}, batch_size=1, language="中文", itn=True)
    txt = res[0]["text"]
    got = norm(txt)
    missing = [c for c in EXPECT if c not in got]
    print(f"{name:<12} {len(d) / sr:5.2f}s {sr} Hz  识别: {txt}")
    print(f"{'':<12} 字数 {len(got)}/{len(EXPECT)}  缺字: {''.join(missing) or '无'}")
