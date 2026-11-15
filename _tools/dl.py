#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用分段并发下载：python dl.py <url> <dest> [总字节数] [分片数]
用于 GitHub release 这类单连接很慢的源；分片级续传，完成校验字节数后拼接。"""
import os
import shutil
import subprocess
import sys
import time

CURL = ["curl", "-sS", "-L", "--retry", "6", "--retry-delay", "2", "--retry-all-errors",
        "--connect-timeout", "30"]


def head_size(url):
    p = subprocess.run(["curl", "-sS", "-L", "-I", url], capture_output=True, text=True)
    for line in reversed(p.stdout.splitlines()):
        if line.lower().startswith("content-length:"):
            return int(line.split(":")[1].strip())
    return 0


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    url, dest = sys.argv[1], sys.argv[2]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else head_size(url)
    parts = int(sys.argv[4]) if len(sys.argv) > 4 else 8
    if not size:
        print("拿不到总大小，请显式传入")
        return 2
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    chunk = (size + parts - 1) // parts
    jobs = []
    for i in range(parts):
        s = i * chunk
        e = min(s + chunk, size) - 1
        jobs.append((i, s, e, f"{dest}.p{i:02d}"))

    t0 = time.time()
    running = []
    queue = [j for j in jobs if not (os.path.exists(j[3]) and os.path.getsize(j[3]) == j[2] - j[1] + 1)]
    while queue or running:
        while queue and len(running) < parts:
            i, s, e, part = queue.pop(0)
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have > e - s + 1:
                os.remove(part); have = 0
            fh = open(part, "ab") if have else open(part, "wb")
            running.append((i, s, e, part,
                            subprocess.Popen(CURL + ["-r", f"{s+have}-{e}", url], stdout=fh,
                                             stderr=subprocess.DEVNULL), fh))
        for r in list(running):
            if r[4].poll() is not None:
                r[5].close()
                running.remove(r)
        done = sum(min(os.path.getsize(p), e - s + 1) for i, s, e, p in jobs if os.path.exists(p))
        print(f"\r{done/1e6:8.1f}/{size/1e6:.1f} MB  {done/max(time.time()-t0,1)/1e6:5.1f} MB/s",
              end="", flush=True)
        time.sleep(1)
    print()
    for i, s, e, part in jobs:
        if os.path.getsize(part) != e - s + 1:
            print(f"分片 {i} 大小不符，重跑可续传")
            return 1
    with open(dest, "wb") as out:
        for i, s, e, part in jobs:
            with open(part, "rb") as f:
                shutil.copyfileobj(f, out, 8 << 20)
    for *_, part in jobs:
        os.remove(part)
    got = os.path.getsize(dest)
    print(f"完成 {dest}  {got/1e6:.1f} MB  {time.time()-t0:.0f}s  {'OK' if got == size else '大小不符'}")
    return 0 if got == size else 1


if __name__ == "__main__":
    sys.exit(main())
