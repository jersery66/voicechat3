#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分片补齐器：只补已有分片文件里缺的字节，多镜像轮换。
用法：python patch_parts.py <直链> <目标文件> <总大小> <分片数>
"""
import os
import subprocess
import sys
import time

MIRRORS = ["https://ghproxy.net/", "https://gh-proxy.com/", ""]


def main():
    raw, dest = sys.argv[1], sys.argv[2]
    size, parts = int(sys.argv[3]), int(sys.argv[4])
    chunk = (size + parts - 1) // parts
    for i in range(parts):
        s = i * chunk
        e = min(s + chunk, size) - 1
        part = f"{dest}.p{i:02d}"
        need = e - s + 1
        have = os.path.getsize(part) if os.path.exists(part) else 0
        if have == need:
            continue
        print(f"p{i:02d} 缺 {(need-have)/1e6:.1f} MB", flush=True)
        tries = 0
        while have < need and tries < 80:
            url = MIRRORS[tries % len(MIRRORS)] + raw
            with open(part, "ab") as fh:
                r = subprocess.run(["curl", "-sS", "-L", "--max-time", "90",
                                    "-r", f"{s+have}-{e}", url],
                                   stdout=fh, stderr=subprocess.DEVNULL)
            new = os.path.getsize(part)
            print(f"   {MIRRORS[tries % len(MIRRORS)] or 'direct':<26} rc={r.returncode} "
                  f"{have/1e6:.2f}->{new/1e6:.2f} MB", flush=True)
            have = new
            tries += 1
            if have < need:
                time.sleep(2)
        if have != need:
            print(f"p{i:02d} 补不齐，放弃", flush=True)
            return 1
    print("全部分片就绪", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
