"""Build a site release from a deployed snapshot: strip dev files, apply fixes.

The rehab centre runs a frozen snapshot (2026-11-06, exp/agent-jev-decision), not
this working tree. A site release therefore starts from *that* code, applies only
the fixes that matter on site, and adds the two things an offline machine needs:
a diagnosis command and a launcher that reads one editable config file.

    python scripts\\launch\\build_site_release.py --source <zip|dir> --out D:\\build\\site

Nothing here needs the network, and the result is a plain folder that can be
copied to a USB stick or zipped.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_ROOT = HERE.parents[1]

# Never shipped to a site: test-only, editor-only, or provenance for developers.
DEV_PATHS = (
    ".git", ".gitattributes", ".gitignore", ".github",
    "tests", "pytest.ini", "requirements-dev.txt",
    "AGENTS.md", "CLAUDE.md", "README_ENGINE.md",
)
DEV_GLOBS = ("debug_*.py", "*.bak_*", "*.pyc_check")

SITE_CONFIG_CMD = """@echo off
REM ============================================================
REM  Site settings. Edit the values you need, then save.
REM  Everything here is optional; the app works without it.
REM
REM  1) Microphone: if the app records the wrong device (or
REM     records silence), uncomment the line below and put the
REM     device number that Site-SelfTest.bat lists for the real
REM     microphone. Do NOT pick anything marked loopback/CABLE.
REM
REM     set VOICECHAT_INPUT_DEVICE=28
REM
REM  2) Interface scale on small screens (0.80 - 1.00):
REM
REM     set QT_SCALE_FACTOR=0.85
REM
REM  3) Pin the weights if auto-detection picks the wrong folder:
REM
REM     set FUNASR_MODEL_PATH=D:\\models\\funasr\\Fun-ASR-Nano-2512
REM     set VOXCPM_MODEL_PATH=D:\\models\\VOXCPM2
REM ============================================================

REM ---- settings below this line take effect ----

"""

SITE_README = """# 小微老师 · 戒毒所专用版

本版本基于这台机器上实际运行的代码（2026-11-06 快照）制作，只做了必要的修复，
没有更换界面框架、没有改动业务逻辑。程序位置不变：

    程序：D:\\戒毒所\\voicechat3
    权重：D:\\戒毒所\\models\\Fun-ASR-Nano-2512、models\\VoxCPM2、models\\voice_prompt
    主模型：llama-server :8000（Qwen3.8-27B-Q4_K_S）

## 一、这次修了什么

1. **点录音没反应**：以前麦克风启动失败会静默复位，看起来像按钮坏了。
   现在会在按钮下方用红字写出原因（模型未加载 / 麦克风打不开 / 具体报错）。
2. **页面显示不全**：窗口最小尺寸从 1024×768 改为 900×600，1366×768 的笔记本
   在 125%~150% 缩放下不会再顶出屏幕。
3. **语音识别入口更稳**：改为按文件路径加载 `model.py`，不再受同名模块影响。
4. **新增离线自检**：不需要麦克风、不需要联网，就能验证识别链路是否正常。

## 二、第一次装：三步

1. **备份**现有 `D:\\戒毒所\\voicechat3`（整个文件夹复制一份即可；至少保住 `config\\`
   和会话数据）。
2. 把本压缩包里的 `voicechat3` 文件夹**覆盖**过去（`.venv` 和 `models` 不删，继续用）。
3. **双击 `Install.bat`**。它会自动：
   - 查找 Fun-ASR / VoxCPM2 / 音色提示的实际位置，写进 `site-env.json`；
   - 检查 Python 依赖是否齐全；
   - 在桌面创建 **“小微老师”** 快捷方式（带图标）；
   - 把结果存成 `安装结果.txt`，可回传。

装完以后，**每次只要双击桌面上的“小微老师”**。

## 二之二、日常怎么启动

- **桌面快捷方式**（推荐）：等价于 `Site-Start.bat`，会自动加载上面探测到的路径。
- `Site-Start.bat`：手动启动入口，先读环境文件再调原来的启动脚本。
- `start_voicechat3_offline.bat`：完全原始的方式，参数（`-PreflightOnly` /
  `-SkipLlmStart`）照旧可用，但**不会**加载自动探测的路径。

## 三、出问题先做这一步

双击 **`Site-SelfTest.bat`**，等 1~2 分钟。它会在屏幕上打印诊断，并把同样的内容
存成 `logs\\selftest-日期-时间.txt`。把这个文件回传即可定位问题。

它会告诉你：
- 识别的权重路径解析到哪、文件在不在；
- 所有录音设备、系统默认设备、哪些是 loopback（录扬声器，不要选）；
- 用自带的 `data\\s1.flac` 转写一遍，和 `data\\S1.txt` 比对（不需要人说话）；
- 顺带验证一次语音合成。

## 四、麦克风选错了怎么办

自检输出里找到真正的"麦克风"那一行的方括号数字（例如 `[28] 麦克风 (Realtek...)`），
然后：

1. 用记事本打开 `site-config.cmd`；
2. 把 `REM  set VOICECHAT_INPUT_DEVICE=28` 这一行前面的 `REM ` 删掉，数字改成你的；
3. 保存；以后用 `Site-Start.bat` 启动即可。

想一次性固定（原入口也生效），在命令提示符里执行一次：

    setx VOICECHAT_INPUT_DEVICE 28

**不要**选名字里带 CABLE / 立体声混音 / loopback 的设备，那是录扬声器的。

## 五、页面太大或太小

用记事本打开 `site-config.cmd`，把这一行前面的 `REM ` 去掉：

    set QT_SCALE_FACTOR=0.85

1366×768 或系统缩放 150% 的机器用 0.85；1920×1080 保持默认（不设）。

## 六、想退回旧版

打补丁时自动留了备份，就在原文件旁边，名字形如：

    ui\\main_window.py.bak_20261123_103625
    ui\\control_panel.py.bak_20261123_103625
    services\\stt_service.py.bak_20261123_103625

把这些文件改名去掉 `.bak_...` 后缀覆盖回去，就回到打补丁前的状态。
也可以直接用原来那份压缩包覆盖整个程序目录。

## 七、不要删的东西

- `.venv\\`：Python 环境，删了要重装依赖（现场没网会很麻烦）
- `data\\s1.flac`、`data\\S1.txt`：自检用
- `config\\`：运行配置
"""


SITE_START_BAT = """@echo off
REM Start the app with the paths found at install time.
REM ASCII-only and no chcp: the original launcher is GBK and is called from here.
setlocal
cd /d "%~dp0"

set "PY=%CD%\\.venv\\Scripts\\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" scripts\\launch\\site_launch.py %*
exit /b %ERRORLEVEL%
"""

SITE_INSTALL_BAT = """@echo off
REM One-time setup after unzipping: find the models and create a desktop shortcut.
REM ASCII-only and CRLF: cmd.exe misparses UTF-8 Chinese in .bat files.
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

set "PY=%CD%\\.venv\\Scripts\\python.exe"
if not exist "%PY%" set "PY=python"

echo ============================================================
echo  XiaoWei - site setup
echo  Folder : %CD%
echo  Python : %PY%
echo ============================================================
echo.

"%PY%" scripts\\launch\\site_autoconfig.py --install
if errorlevel 1 (
  echo.
  echo Setup reported a problem. Read the output above.
  echo It is also saved to the install report file in this folder.
  pause
  exit /b 1
)

echo.
echo You can start the program from the desktop shortcut from now on.
pause
exit /b 0
"""

SITE_SELFTEST_BAT = """@echo off
REM Offline diagnosis: no microphone and no network needed.
REM Writes a report under logs\\ as well as printing it.
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

set "PY=%CD%\\.venv\\Scripts\\python.exe"
if not exist "%PY%" set "PY=python"
if exist "%~dp0site-config.cmd" call "%~dp0site-config.cmd"

if not exist "logs" mkdir "logs"
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set "STAMP=%%I"
set "STAMP=%STAMP:~0,8%-%STAMP:~8,6%"
set "REPORT=logs\\selftest-%STAMP%.txt"

echo Running diagnosis, please wait 1-2 minutes...
echo.
"%PY%" scripts\\launch\\offline_selftest.py --tts --report "%REPORT%"
echo.
echo Send %REPORT% to support if anything failed.
pause
"""


def load_patch_module():
    spec = importlib.util.spec_from_file_location("_site_patch", HERE / "offline_patch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def materialise(source: Path, out: Path) -> Path:
    """Copy or extract the snapshot so that ``out/voicechat3`` exists."""
    out.mkdir(parents=True, exist_ok=True)
    target = out / "voicechat3"
    if target.exists():
        shutil.rmtree(target)
    if source.is_dir():
        shutil.copytree(source, target)
        return target
    with zipfile.ZipFile(source) as archive:
        archive.extractall(out)
    if not target.is_dir():
        # A zip whose root is not "voicechat3": take the single top folder.
        tops = [p for p in out.iterdir() if p.is_dir()]
        if len(tops) == 1:
            tops[0].rename(target)
        else:
            raise SystemExit(f"无法在 {source} 里找到唯一的程序目录")
    return target


def strip_dev_files(root: Path) -> list[str]:
    removed: list[str] = []
    for rel in DEV_PATHS:
        target = root / rel
        if target.is_dir():
            shutil.rmtree(target)
            removed.append(rel + "/")
        elif target.exists():
            target.unlink()
            removed.append(rel)
    for pattern in DEV_GLOBS:
        for path in list(root.rglob(pattern)):
            if path.is_file():
                path.unlink()
                removed.append(str(path.relative_to(root)))
    return removed


def write_site_files(root: Path, patch_module) -> None:
    (root / "site-config.cmd").write_text(SITE_CONFIG_CMD.replace("\n", "\r\n"),
                                          encoding="ascii", newline="")
    (root / "Site-Start.bat").write_text(SITE_START_BAT.replace("\n", "\r\n"),
                                         encoding="ascii", newline="")
    (root / "Site-SelfTest.bat").write_text(SITE_SELFTEST_BAT.replace("\n", "\r\n"),
                                            encoding="ascii", newline="")
    (root / "Install.bat").write_text(SITE_INSTALL_BAT.replace("\n", "\r\n"),
                                      encoding="ascii", newline="")
    for name in ("offline_selftest.py", "offline_patch.py", "site_autoconfig.py", "site_launch.py"):
        shutil.copy2(HERE / name, root / "scripts" / "launch" / name)
    (root / "README-戒治所.md").write_text(SITE_README, encoding="utf-8")
    checks = "\n".join(f"  - {label}" for _f, _a, _r, label in patch_module.EDITS)
    (root / "SITE-BUILD.txt").write_text(
        "戒毒所专用版 / Site build\n"
        "=========================\n"
        "基于现场运行的快照（exp/agent-jev-decision, 2026-11-06）\n"
        "构建时间见下方\n\n"
        "已包含的修复：\n"
        f"{checks}\n"
        "  - 录音：按文件路径加载 FunASR 入口，避免 model 模块名被占用\n"
        "  - 显示：最小窗口 900x600，小屏不再溢出\n"
        "  - 新增：离线诊断（Site-SelfTest.bat，不需要麦克风）\n"
        "  - 新增：site-config.cmd（麦克风编号 / 界面缩放 / 权重路径）\n",
        encoding="utf-8",
    )
    import datetime

    with (root / "SITE-BUILD.txt").open("a", encoding="utf-8") as handle:
        handle.write(f"\n构建时间：{datetime.datetime.now():%Y-%m-%d %H:%M}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建现场版（去开发文件 + 打补丁 + 加站点文件）")
    parser.add_argument("--source", required=True, help="现场快照的 zip 或目录")
    parser.add_argument("--out", required=True, help="输出目录（会创建其中的 voicechat3）")
    parser.add_argument("--zip", action="store_true", help="构建完再打一个 zip 包")
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        print(f"找不到源：{source}")
        return 1

    root = materialise(source, Path(args.out).expanduser().resolve())
    print(f"程序目录：{root}")

    removed = strip_dev_files(root)
    print(f"移除开发文件 {len(removed)} 项：{', '.join(removed[:8])}" + (" …" if len(removed) > 8 else ""))

    patch_module = load_patch_module()
    code = patch_module.apply_edits(root, dry_run=False)
    if code != 0:
        print("补丁未能全部应用，请检查上面的输出。")
        return code

    write_site_files(root, patch_module)
    print("已写入 site-config.cmd / Site-Start.bat / Site-SelfTest.bat / SITE-BUILD.txt")

    if args.zip:
        archive = root.parent / f"{root.name}-site-{__import__('datetime').date.today():%Y%m%d}"
        shutil.make_archive(str(archive), "zip", root.parent, root.name)
        print(f"打包完成：{archive}.zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
