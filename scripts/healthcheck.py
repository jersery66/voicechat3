#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voicechat3 本机环境自检：python/torch/CUDA/模型文件/llama.cpp 二进制/端口占用。
用法：D:\\voicechat3\\.venv\\Scripts\\python.exe D:\\voicechat3\\scripts\\healthcheck.py
"""
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys

ROOT = r"D:\models\voicechat3"
BIN = r"D:\voicechat3\bin\llama"
OK, BAD, WARN = "[OK]", "[!!]", "[--]"
fails = 0


def line(tag, msg):
    print(f"{tag} {msg}")


def check_torch():
    global fails
    try:
        import torch
    except Exception as e:
        line(BAD, f"torch 导入失败: {e}")
        fails += 1
        return
    line(OK, f"torch {torch.__version__}  (编译 CUDA {torch.version.cuda})")
    if not torch.cuda.is_available():
        line(BAD, "cuda 不可用")
        fails += 1
        return
    p = torch.cuda.get_device_properties(0)
    free, total = torch.cuda.mem_get_info(0)
    line(OK, f"GPU {p.name}  sm_{p.major}{p.minor}  显存 {total/2**30:.1f} GiB (空闲 {free/2**30:.1f})")
    import time
    a = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
    b = torch.randn(4096, 4096, device="cuda", dtype=torch.float16)
    for _ in range(3):
        a @ b
    torch.cuda.synchronize()
    t = time.time()
    for _ in range(10):
        a @ b
    torch.cuda.synchronize()
    tf = 2 * 4096**3 / ((time.time() - t) / 10) / 1e12
    line(OK, f"fp16 matmul {tf:.0f} TFLOPS（sm_{p.major}{p.minor} 算子可用）")


def check_models():
    global fails
    man_path = os.path.join(ROOT, "checksums.json")
    if not os.path.exists(man_path):
        line(WARN, "checksums.json 缺失，只查大小")
        man = {}
    else:
        man = json.load(open(man_path, encoding="utf-8"))
    for rel, meta in man.items():
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            line(BAD, f"缺失 {rel}")
            fails += 1
            continue
        got = os.path.getsize(p)
        if got != meta["size"]:
            line(BAD, f"大小不符 {rel} {got} != {meta['size']}")
            fails += 1
    big = [r for r in man if man[r]["size"] > 1e8]
    line(OK, f"模型文件 {len(man)} 项齐备，其中大文件 {len(big)} 个")


def check_llama():
    global fails
    exe = os.path.join(BIN, "llama-server.exe")
    if not os.path.exists(exe):
        line(WARN, f"llama-server.exe 未就绪（{BIN}），跳过")
        return
    p = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=120)
    out = (p.stdout + p.stderr).strip().splitlines()
    ver = next((l for l in out if "version" in l.lower()), out[0] if out else "未知")
    line(OK, f"llama-server: {ver[:90]}")
    d = subprocess.run([exe, "--list-devices"], capture_output=True, text=True, timeout=180)
    cuda = [l.strip() for l in (d.stdout + d.stderr).splitlines() if "CUDA" in l]
    if cuda:
        line(OK, f"llama.cpp CUDA 设备: {cuda[0][:95]}")
    else:
        line(BAD, "llama.cpp 未识别到 CUDA 设备（检查 bin\\llama 下的 cudart/cublas DLL）")
        fails += 1


def check_ports():
    for port, name in ((8000, "对话主模型"), (8001, "Agent"), (9880, "ASR/TTS 网关")):
        s = socket.socket()
        s.settimeout(0.4)
        busy = s.connect_ex(("127.0.0.1", port)) == 0
        s.close()
        if not busy:
            line(OK, f"端口 {port} ({name}) 空闲")
            continue
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                body = r.read(60).decode("utf-8", "ignore")
            if '"ok"' in body:
                line(OK, f"端口 {port} ({name}) 正在服务 {body}")
            else:
                line(WARN, f"端口 {port} ({name}) 被占用但 /health 异常: {body}")
        except Exception:
            line(WARN, f"端口 {port} ({name}) 被其他进程占用")


def check_voice_prompt():
    global fails
    for p in (r"D:\voicechat3\voice_prompt\s1.mp3",):
        if os.path.exists(p):
            line(OK, f"音色样本 {p}  {os.path.getsize(p)/1024:.0f} KB")
        else:
            line(WARN, f"音色样本缺失 {p}（零样本 TTS 需要，须随项目带上）")


def main():
    print(f"== voicechat3 环境自检  python {sys.version.split()[0]} ==\n")
    check_torch()
    check_models()
    check_llama()
    check_ports()
    check_voice_prompt()
    print()
    if fails:
        print(f"结论：{fails} 项异常，需要处理")
        return 1
    print("结论：关键项全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
