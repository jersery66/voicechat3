"""Apply the on-site fixes to an offline copy, by text anchor, with a backup.

The machine that needs this has no network, so the change has to travel on a USB
stick and be applied without a package manager or a git pull. Every edit below is
an exact text replacement anchored on code that existed before the fix — line
numbers are not used, so an older copy that has drifted still patches correctly.

    python scripts\\launch\\offline_patch.py --dry-run     # 只检查能不能改，不写盘
    python scripts\\launch\\offline_patch.py               # 备份并应用
    python scripts\\launch\\offline_patch.py --revert      # 用备份还原

A file is patched atomically: if one anchor in it does not match, that entire
file is left untouched rather than half-edited. After writing, each file is
compiled; a syntax failure restores it from the backup automatically.

What it changes (and why):

1. services/stt_service.py — load the FunASR entry point by file path instead of
   ``from model import FunASRNano``. ``model`` is a bare module name: once
   anything else has imported a module called ``model``, the checkpoint's entry
   point is skipped, ASR falls back to a path that does not load here, and the
   record button looks dead.
2. ui/main_window.py — show *why* a recording failed. The old code only called
   reset_recording(), so the button flickered and reverted with no message.
3. ui/main_window.py — minimum window size 1024x768 -> 900x600. A 1366x768
   laptop at 125 %/150 % scaling has less usable area than that, so the window
   extended past the screen ("页面偏离").
4. ui/control_panel.py — tooltip on the disabled record button.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]

# Anchors are plain triple-quoted text on purpose: escapes are how a patch tool
# silently ends up searching for "\n" instead of a newline.
EDITS: list[tuple[str, str, str, str]] = [
    (
        "services/stt_service.py",
        """            FunASRNano = None
            try:
                from model import FunASRNano  # checkpoint-local entry point
            except ImportError:
""",
        """            FunASRNano = None
            try:
                # 按文件路径加载，不用裸模块名：`model` 这个名字可能已被别的
                # 模块占用（那时 sys.path 插队无效），入口会被静默跳过，ASR
                # 退化到在这台机器上跑不通的路径，表现就是"点录音没反应"。
                import importlib.util as _ilu
                _entry = os.path.join(self.model_path, "model.py")
                _spec = _ilu.spec_from_file_location(
                    "_vc_local_funasr_entry", _entry)
                _mod = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)
                FunASRNano = getattr(_mod, "FunASRNano", None)
                if FunASRNano is None:
                    raise ImportError("model.py 里没有 FunASRNano")
            except Exception:
""",
        "录音：按路径加载 FunASR 入口（修 model 模块名被占用）",
    ),
    (
        "ui/main_window.py",
        """        if not self.models_loaded:
            self.control_panel.reset_recording()
            return
""",
        """        if not self.models_loaded:
            self.control_panel.reset_recording()
            self.control_panel.set_status("模型仍在加载，请稍候再试")
            return
""",
        "录音：模型未加载时给出提示",
    ),
    (
        "ui/main_window.py",
        """        except RecordingStartError:
            logger.exception("Microphone recording startup failed")
            self.processing_queue.put(("recording_start_failed", attempt_id))
""",
        """        except RecordingStartError as exc:
            logger.exception("Microphone recording startup failed")
            self._last_mic_error = str(exc)
            self.processing_queue.put(("recording_start_failed", attempt_id))
""",
        "录音：记录麦克风启动失败原因",
    ),
    (
        "ui/main_window.py",
        """        except Exception:
            # Keep the UI fail-safe even if a provider violates the explicit
            # startup-error contract; the technical traceback remains in the
            # log and the participant still receives a safe status.
            logger.exception("Unexpected microphone recording startup failure")
            self.processing_queue.put(("recording_start_failed", attempt_id))
""",
        """        except Exception as exc:
            # Keep the UI fail-safe even if a provider violates the explicit
            # startup-error contract; the technical traceback remains in the
            # log and the participant still receives a safe status.
            logger.exception("Unexpected microphone recording startup failure")
            self._last_mic_error = f"{type(exc).__name__}: {exc}"
            self.processing_queue.put(("recording_start_failed", attempt_id))
""",
        "录音：记录意外异常原因",
    ),
    (
        "ui/main_window.py",
        """        self.control_panel.set_status("麦克风启动失败，请检查麦克风连接后重试")
""",
        """        reason = getattr(self, "_last_mic_error", "") or "请检查麦克风连接后重试"
        self.control_panel.set_status("麦克风启动失败：" + reason)
""",
        "录音：失败提示带上具体原因",
    ),
    (
        "ui/main_window.py",
        """        self.setMinimumSize(1024, 768)
""",
        """        self.setMinimumSize(900, 600)
""",
        "显示：最小窗口尺寸改为 900x600",
    ),
    (
        "ui/control_panel.py",
        """        self.record_button = RecordButton()
        self.record_button.setEnabled(False)
""",
        """        self.record_button = RecordButton()
        self.record_button.setEnabled(False)
        self.record_button.setToolTip("请先填写并确认基本信息")
""",
        "显示：录音按钮禁用时给出提示",
    ),
]


# 新版代码里这些写法已经等效修好了，遇到就当成"无需再改"，避免误报失败。
ALREADY_APPLIED: dict[str, tuple[str, ...]] = {
    "services/stt_service.py": ("FunASRNano = load_checkpoint_entry(self.model_path)",),
}


def backup_path(path: Path) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = path.with_suffix(path.suffix + f".bak_{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_suffix(path.suffix + f".bak_{stamp}_{counter}")
        counter += 1
    return candidate


def _verify_syntax(path: Path) -> tuple[bool, str]:
    """Compile the file the way the app will; empty message means OK."""
    import py_compile

    probe = str(path) + ".pyc_check"
    try:
        py_compile.compile(str(path), doraise=True, cfile=probe)
        return True, ""
    except py_compile.PyCompileError as exc:
        return False, str(getattr(exc, "msg", exc)).strip()
    except OSError as exc:
        return False, str(exc)
    finally:
        try:
            Path(probe).unlink()
        except OSError:
            pass


def apply_edits(root: Path, dry_run: bool) -> int:
    by_file: dict[Path, list[tuple[str, str, str]]] = {}
    order: list[str] = []
    for rel, anchor, replacement, label in EDITS:
        if rel not in order:
            order.append(rel)
        by_file.setdefault(root / rel, []).append((anchor, replacement, label))

    failed = False
    touched: list[Path] = []

    for rel in order:
        path = root / rel
        if not path.is_file():
            print(f"  [!!]   {rel} 不存在")
            failed = True
            continue

        text = path.read_text(encoding="utf-8")
        pending: list[str] = []
        problems: list[str] = []

        for anchor, replacement, label in by_file[path]:
            markers = ALREADY_APPLIED.get(rel, ())
            if replacement in text or any(marker in text for marker in markers):
                print(f"  [跳过] {label}（已经改过）")
                continue
            if anchor not in text:
                problems.append(label)
                continue
            text = text.replace(anchor, replacement, 1)
            pending.append(label)

        if problems:
            print(f"  [不动] {rel}（{len(problems)} 处匹配不上，为避免改一半，这份文件整个跳过）")
            for label in problems:
                print(f"         未匹配：{label}")
            failed = True
            continue
        if not pending:
            continue

        for label in pending:
            print(f"  [准备] {label}  ->  {rel}")
        if dry_run:
            touched.append(path)
            continue

        backup = backup_path(path)
        shutil.copy2(path, backup)
        path.write_text(text, encoding="utf-8")

        ok, message = _verify_syntax(path)
        if not ok:
            shutil.copy2(backup, path)
            print(f"  [回滚] {rel} 改后语法错误，已自动还原：{message[:120]}")
            failed = True
            continue

        touched.append(path)
        print(f"  [已改] {rel}  （备份 {backup.name}，语法检查通过）")

    if dry_run:
        print(f"\n[dry-run] 将修改 {len(touched)} 个文件，未写盘。")
        return 0 if not failed else 2
    if not touched and not failed:
        print("\n没有需要改的地方（可能已经打过补丁）。")
        return 0
    if touched:
        print("\n完成。请重启程序；出问题可用 --revert 还原。")
    return 0 if not failed else 2


def revert(root: Path) -> int:
    restored = 0
    for rel in {rel for rel, _a, _r, _l in EDITS}:
        path = root / rel
        candidates = sorted(path.parent.glob(path.name + ".bak_*"))
        if not candidates:
            continue
        newest = candidates[-1]
        shutil.copy2(newest, path)
        print(f"  [还原] {rel}  <- {newest.name}")
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
