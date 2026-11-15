# -*- coding: utf-8 -*-
"""完整语音闭环：TTS 合成 -> 播到 CABLE -> STTService 录音 -> ASR 转录。

这条链路跑通，说明除了真人麦克风之外的每一环都是可用的；
如果 ASR 转不出原句，问题就落在 CABLE 的时钟上，而不是程序里。
"""
import sys, os, time, threading, warnings
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\voicechat3")

import numpy as np
import sounddevice as sd
import config
from services.tts_service_voxcpm import TTSService
from services.stt_service import STTService, SAMPLE_RATE

TEXT = "你好，我是小薇老师。今天我们先做几次深呼吸。"
devices = sd.query_devices()

def pick_out(part):
    best = None
    for i, d in enumerate(devices):
        if part.lower() not in str(d["name"]).lower() or d["max_output_channels"] <= 0:
            continue
        try:
            api = str(sd.query_hostapis(int(d["hostapi"]))["name"]).lower()
        except Exception:
            api = ""
        if "wasapi" in api:
            return i
        if best is None:
            best = i
    return best

OUT = pick_out("CABLE Input")
print("play device:", OUT, devices[OUT]["name"] if OUT is not None else None)

print("--- 1. TTS ---")
tts = TTSService()
t0 = time.time()
assert tts.load_model(), "TTS load failed"
print("tts loaded %.1fs" % (time.time() - t0))
t1 = time.time()
speech = np.asarray(tts.generate(TEXT), dtype=np.float32).reshape(-1, 1)
sr = int(config.TTS_SAMPLE_RATE)
print("synthesised %d samples = %.2fs in %.2fs (rtf %.2f)" % (
    speech.shape[0], speech.shape[0] / sr, time.time() - t1, (time.time() - t1) / (speech.shape[0] / sr)))
wav = r"D:\voicechat3\logs\loop_tts.wav"
import soundfile as sf
sf.write(wav, speech, sr)
print("saved:", wav)

print("--- 2. load ASR ---")
stt = STTService()
t2 = time.time()
stt.load_model()
print("asr loaded %.1fs" % (time.time() - t2))

print("--- 3. play into CABLE + record ---")
stop = threading.Event()

def player():
    try:
        with sd.OutputStream(device=OUT, samplerate=sr, channels=1,
                             dtype="float32", blocksize=1024) as o:
            o.write(np.zeros((int(sr * 0.3), 1), dtype=np.float32))
            o.write((speech * 0.45).astype(np.float32))     # 只播一遍，留足余量避免削波
            # 播完后继续喂静音，让录音端不因设备空闲而断流
            silence = np.zeros((int(sr * 0.2), 1), dtype=np.float32)
            while not stop.is_set():
                o.write(silence)
    except Exception as e:
        print("player error:", e)

th = threading.Thread(target=player, daemon=True)
th.start()
time.sleep(0.8)
assert stt.start_recording(), "recording failed"
time.sleep(speech.shape[0] / sr + 2.2)
rec = stt.stop_recording()
stop.set()
th.join(timeout=5)

n = int(rec.size)
print("recorded %d samples = %.2fs @16k, peak %.3f" % (
    n, n / SAMPLE_RATE, float(np.abs(rec).max()) if n else 0.0))
sf.write(r"D:\voicechat3\logs\loop_rec.wav", rec, SAMPLE_RATE)

print("--- 4. ASR ---")
t3 = time.time()
text = stt.transcribe(rec)
print("asr %.2fs" % (time.time() - t3))
print("expected:", TEXT)
print("got     :", text)
print("VERDICT:", "ROUNDTRIP OK" if text and len(text) > 6 else "ASR EMPTY/WEAK")
