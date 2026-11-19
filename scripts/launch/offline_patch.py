"""Apply the on-site fixes to an offline copy, by text anchor, with a backup.

The machine that needs this has no network, so the change has to travel on a USB
stick and be applied without a package manager, an editor session, or a git
pull. Every edit below is a small, exact replacement anchored on code that
existed before the fix — line numbers are not used, so an older copy that has
drifted by a few lines still patches correctly.

    python scripts\launch\offline_patch.py --dry-run     # 只检查能不能改，不写盘
    python scripts\launch\offline_patch.py               # 备份并应用
    python scripts\launch\offline_patch.py --revert      # 用备份还原

What it changes (and why):

1. ui/main_window.py — 录音启动失败时把原因显示出来。旧版只调 reset_recording()，
   按钮闪一下就复位，看起来像"点了没反应"。
2. ui/main_window.py — 窗口最小尺寸 1024x768 改 900x600。1366x768 的笔记本在
   125%/150% 缩放下可用区域比这个还小，窗口会顶出屏幕（"页面偏离"）。
3. ui/control_panel.py — 录音按钮禁用时给出悬停提示，避免被当成坏按钮。

It does not touch config, models, or the launcher. Safe to run twice: a file that
already contains a fix is skipped.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]

# (relative path, anchor text that must exist, replacement, human label)
EDITS: list[tuple[str, str, str, str]] = [
    (
        "ui/main_window.py",
        "        if not self.models_loaded:\n"
        "            self.control_panel.reset_recording()\n"
        "            return\n",
        "        if not self.models_loaded:\n"
        "            self.control_panel.reset_recording()\n"
        '            self.control_panel.set_status("模型仍在加载，请稍候再试")\n'
        "            return\n",
        "录音：模型未加载时给出提示",
    ),
    (
        "ui/main_window.py",
        "        except RecordingStartError:\n"
        '            logger.exception("Microphone recording startup failed")\n'
        '            self.processing_queue.put(("recording_start_failed", attempt_id))\n',
        "        except RecordingStartError as exc:\n"
        '            logger.exception("Microphone recording startup failed")\n'
        "            self._last_mic_error = str(exc)\n"
        '            self.processing_queue.put(("recording_start_failed", attempt_id))\n',
        "录音：记录麦克风启动失败原因",
    ),
    (
        "ui/main_window.py",
        "        except Exception:\n"
        "            # Keep the UI fail-safe even if a provider violates the explicit\n"
        "            # startup-error contract; the technical traceback remains in the\n"
        "            # log and the participant still receives a safe status.\n"
        '            logger.exception("Unexpected microphone recording startup failure")\n'
        '            self.processing_queue.put(("recording_start_failed", attempt_id))\n',
        "        except Exception as exc:\n"
        "            # Keep the UI fail-safe even if a provider violates the explicit\n"
        "            # startup-error contract; the technical traceback remains in the\n"
        "            # log and the participant still receives a safe status.\n"
        '            logger.exception("Unexpected microphone recording startup failure")\n'
        '            self._last_mic_error = f"{type(exc).__name__}: {exc}"\n'
        '            self.processing_queue.put(("recording_start_failed", attempt_id))\n',
        "录音：记录意外异常原因",
    ),
    (
        "ui/main_window.py",
        '        self.control_panel.set_status("麦克风启动失败，请检查麦克风连接后重试")\n',
        '        reason = getattr(self, "_last_mic_error", "") or "请检查麦克风连接后重试"\n'
        '        self.control_panel.set_status("麦克风启动失败：" + reason)\n',
        "录音：失败提示带上具体原因",
    ),
    (
        "ui/main_window.py",
        "        self.setMinimumSize(1024, 768)\n",
        "        self.setMinimumSize(900, 600)\n",
        "显示：最小窗口尺寸改为 900x600",
    ),
    (
        "ui/control_panel.py",
        "        self.record_button = RecordButton()\n"
        "        self.record_button.setEnabled(False)\n",
        "        self.record_button = RecordButton()\n"
        "        self.record_button.setEnabled(False)\n"
        '        self.record_button.setToolTip("请先填写并确认基本信息")\n',
        "显示：录音按钮禁用时给出提示",
    ),
]


def backup_path(path: Path) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_suffix(path.suffix + f".bak_{stamp}")


def apply_edits(root: Path, dry_run: bool) -> int:
    files: dict[Path, str] = {}
    planned: dict[Path, list[tuple[str, str, str]]] = {}
    missing: list[str] = []
    skipped: list[str] = []

    for rel, anchor, replacement, label in EDITS:
        path = root / rel
        if not path.is_file():
            missing.append(f"{rel}（文件不存在）")
            continue
        text = files.get(path) or path.read_text(encoding="utf-8")
        files[path] = text

        # 每个替换串都包含原锚点（都是"插入一行"或"整块扩写"），所以只要替换串
        # 已经在文件里，就说明这条已经应用过。
        if replacement in text:
            skipped.append(f"{label}（已经改过）")
            continue
        if anchor not in text:
            missing.append(f"{label}（找不到原文，可能版本不同）")
            continue
        files[path] = text.replace(anchor, replacement, 1)
        planned.setdefault(path, []).append((anchor, replacement, label))
        print(f"  [准备] {label}  ->  {rel}")

    for item in skipped:
        print(f"  [跳过] {item}")
    for item in missing:
        print(f"  [!!]   {item}")

    if not planned:
        print("\n没有需要改的地方。" if not missing else "\n没有可应用的改动。")
        return 0 if not missing else 2

    if dry_run:
        print(f"\n[dry-run] 将修改 {len(planned)} 个文件，未写盘。")
        return 0 if not missing else 2

    for path, text in files.items():
        backup = backup_path(path)
        shutil.copy2(path, backup)
        path.write_text(text, encoding="utf-8")
        print(f"  [已改] {path.relative_to(root)}  （备份 {backup.name}）")

    print("\n完成。请重启程序；若担心改坏，可用 --revert 还原。")
    return 0 if not missing else 2


def revert(root: Path) -> int:
    restored = 0
    for rel, _anchor, _replacement, _label in EDITS:
        path = root / rel
        candidates = sorted(path.parent.glob(path.name + ".bak_*"))
        if not candidates:
            continue
        newest = candidates[-1]
        shutil.copy2(newest, path)
        print(f"  [还原] {path.relative_to(root)}  <- {newest.name}")
        restored += 1
    if not restored:
        print("找不到备份文件。")
        return 1
    print("\n已还原。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线机器上的补丁应用器")
    parser.add_argument("--root", default=str(APP_ROOT), help="程序目录（默认按脚本位置推断）")
    parser.add_argument("--dry-run", action="store_true", help="只检查，不写文件")
    parser.add_argument("--revert", action="store_true", help="用备份还原")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not (root / "ui" / "main_window.py").is_file():
        print(f"这不是程序目录：{root}")
        return 1
    print(f"目标程序目录：{root}\n")
    if args.revert:
        return revert(root)
    return apply_edits(root, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
