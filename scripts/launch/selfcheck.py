"""Self-check for a deployed copy: services, config, launcher, voice, GUI.

This is the checklist used to accept a workstation. It is deliberately
end-to-end: it starts the model stack if needed, stops it and lets autostart
bring it back, transcribes a known clip, synthesises a sentence, and opens the
GUI offscreen for one real turn. Unit tests live in ``tests/``; this covers the
parts that only fail on a real machine.

    python scripts/launch/selfcheck.py              # everything (~6 min)
    python scripts/launch/selfcheck.py --quick      # skip voice + GUI (~1 min)
    python scripts/launch/selfcheck.py --json       # machine-readable result

Exit code is 0 only when every check passes. Paths are derived from this file,
so the same script works from any checkout.

Note for Windows/pytest users: ``tests/`` must be run with ``--basetemp`` outside
the repository on machines with a bulk-delete guard, otherwise pytest's tmp
cleanup trips the guard and the exit code is misleading.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = Path(sys.executable)
sys.path.insert(0, str(ROOT))

RESULTS: list[dict[str, object]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def child(args: list[str], *, timeout: int = 600, env_extra: dict | None = None,
          cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("VOICECHAT_RUNTIME_CONFIG", None)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT)})
    env.update(env_extra or {})
    return subprocess.run(args, capture_output=True, timeout=timeout, cwd=str(cwd or ROOT),
                          text=True, encoding="utf-8", errors="replace", env=env)


def local_config() -> str:
    return str(ROOT / "config" / "runtime.local.yaml")


# ------------------------------------------------------------------ checks
def check_compile() -> None:
    targets = ["main.py", "config.py", "deployment/autostart.py",
               "deployment/dependencies.py", "deployment/__init__.py",
               "scripts/launch/autoconfigure.py", "scripts/check_config.py"]
    bad = [t for t in targets if child([str(PY), "-m", "py_compile", t], timeout=120).returncode]
    check("模块编译", not bad, ", ".join(bad) if bad else f"{len(targets)} 个文件")


def _status() -> dict:
    """Probe without recording a result (used by the recovery sequence)."""
    r = child([str(PY), "-m", "deployment.autostart", "--status", "--json"], timeout=120)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {}


def check_services() -> dict:
    state = _status()
    check("模型服务就绪", state.get("ready") is True,
          f"8000={state.get('dialogue', {}).get('models')} "
          f"8001={state.get('agent', {}).get('models')}")
    return state


def check_weights() -> None:
    r = child([str(PY), "-m", "deployment.autostart", "--models", "--json"], timeout=180)
    try:
        missing = json.loads(r.stdout.strip().splitlines()[-1]).get("missing")
    except (ValueError, IndexError):
        missing = ["<无法解析>"]
    check("语音权重齐全", missing == [], str(missing))


def check_detection() -> None:
    r = child([str(PY), "scripts/launch/autoconfigure.py", "--check", "--json"], timeout=180)
    try:
        state = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        check("自动探测", False, (r.stderr or "").strip()[-100:])
        return
    chosen = state.get("chosen") or {}
    check("探测到模型服务", bool(chosen), f"{chosen.get('base_url')} / {state.get('dialogue_model')}")
    check("Agent 已识别", bool((state.get("agent") or {}).get("model")),
          str((state.get("agent") or {}).get("model")))
    voice = state.get("voice") or {}
    check("权重路径已写入配置", bool(voice.get("funasr")) and bool(voice.get("voxcpm")),
          f"{voice.get('funasr')} | {voice.get('voxcpm')}")


def check_health() -> None:
    r = child([str(PY), "-c",
               "from scripts.check_config import run_check; raise SystemExit(0 if run_check() else 1)"],
              timeout=300, env_extra={"VOICECHAT_RUNTIME_CONFIG": local_config()})
    check("配置体检", r.returncode == 0,
          "All checks passed." if r.returncode == 0 else (r.stdout or "").strip()[-140:])


def check_dependencies() -> None:
    r = child([str(PY), "-m", "deployment.dependencies", "--check"], timeout=180)
    check("依赖体检", r.returncode == 0,
          (r.stdout or "").strip().splitlines()[-2] if r.stdout else "")


def check_settings() -> None:
    """The settings file must round-trip, including a hand-typed scale."""
    import tempfile

    from services.user_settings import UserSettings

    probe = Path(tempfile.gettempdir()) / "vc_selfcheck_settings.json"
    probe.unlink(missing_ok=True)
    saved = UserSettings(probe).load()
    saved.set("ui.scale", 0.9)
    ok = saved.save()
    back = UserSettings(probe).load()
    probe.unlink(missing_ok=True)
    check("设置可保存可读回", ok and abs(back.scale - 0.9) < 1e-6, f"scale={back.scale}")


def check_layout() -> None:
    """No screen tier may push the chat column off the window."""
    probe = """
import sys
sys.path.insert(0, '.')
import main, config
config.ENABLE_TTS = False
config.ENABLE_STT = False
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
app = QApplication([])
w = MainWindow(); w.show()
bad = []
for width, height in ((1024, 700), (1280, 800), (1366, 730), (1920, 1080)):
    w.resize(width, height); app.processEvents()
    m = w._main_layout.contentsMargins()
    need = w.control_panel.width() + w.chat_panel.width() + m.left() + m.right()
    need += w._main_layout.spacing()
    if need > width:
        bad.append(str(width) + ':' + str(need))
print('LAYOUT', 'OK' if not bad else '; '.join(bad))
"""
    r = child([str(PY), "-c", probe], timeout=300,
              env_extra={"QT_QPA_PLATFORM": "offscreen"})
    out = (r.stdout or "") + (r.stderr or "")
    line = next((l for l in out.splitlines() if l.startswith("LAYOUT")), "")
    ok = "LAYOUT OK" in line
    check("小屏布局不溢出", ok, line[7:].strip() or out.strip()[-120:])


def check_launcher() -> None:
    script = ROOT / "scripts" / "launch" / "bootstrap_local.ps1"
    if not script.is_file():
        return
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                        "-File", str(script), "-CheckOnly"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900, cwd=str(ROOT))
    out = (r.stdout or "") + (r.stderr or "")
    match = re.search(r"通过\s*(\d+)\s*项[，,]\s*警告\s*(\d+)\s*项[，,]\s*失败\s*(\d+)\s*项", out)
    if not match:
        check("启动器自检", False, "没有找到自检汇总")
        return
    passed, warns, fails = (int(x) for x in match.groups())
    check("启动器自检", fails == 0 and warns == 0, f"通过 {passed} / 警告 {warns} / 失败 {fails}")


def check_recovery() -> None:
    if not _status().get("ready"):
        return
    child([str(PY), "-m", "deployment.autostart", "--stop-services"], timeout=180)
    time.sleep(3)
    stopped = not _status().get("ready")          # 停下来的状态不记入检查项
    started = time.time()
    from deployment.autostart import ensure   # 必须在进程内：保活会话是调用者的子进程
    ensure(wait_seconds=300)
    back = _status().get("ready") is True
    check("服务自愈（停止→自动拉起）", stopped and back,
          f"停止={'成功' if stopped else '失败'} 恢复={'成功' if back else '失败'}"
          f" 用时 {int(time.time() - started)}s")


def check_asr() -> None:
    code = (
        "import json,re,time,numpy as np,miniaudio,soxr,config\n"
        "from services.stt_service import get_stt_service\n"
        "s=get_stt_service(); s.load_model()\n"
        "d=miniaudio.decode_file('data/s1.flac',output_format=miniaudio.SampleFormat.FLOAT32)\n"
        "a=np.array(d.samples,dtype=np.float32).reshape(-1,d.nchannels).mean(axis=1)\n"
        "a=soxr.resample(a,d.sample_rate,config.SAMPLE_RATE) if d.sample_rate!=config.SAMPLE_RATE else a\n"
        "s.transcribe(a)\n"
        "t=time.time(); text=s.transcribe(a); dt=time.time()-t\n"
        "bare=lambda v: re.sub(r'[\\s，。、！？；：,.!?;:]','',v)\n"
        "exp=open('data/S1.txt',encoding='utf-8').read()\n"
        "print('RESULT',json.dumps({'secs':round(dt,2),'match':bare(text)==bare(exp),"
        "'text':text.strip()[:44]},ensure_ascii=True))\n"
    )
    r = child([str(PY), "-c", code], timeout=600,
              env_extra={"VOICECHAT_RUNTIME_CONFIG": local_config()})
    line = next((l for l in (r.stdout or "").splitlines() if l.startswith("RESULT")), "")
    if not line:
        check("语音识别", False, (r.stderr or "").strip()[-140:])
        return
    data = json.loads(line.split("RESULT", 1)[1])
    check("语音识别（与 data/S1.txt 一致）", data["match"], f"{data['secs']}s | {data['text']}")


def check_tts() -> None:
    code = (
        "import json,time,numpy as np\n"
        "from services.tts_service import get_tts_service\n"
        "s=get_tts_service(); s.load_model(use_streaming=False)\n"
        "s.generate('你好。')\n"
        "t=time.time(); audio=s.generate('你好，我是小薇。今天想聊什么都可以。'); dt=time.time()-t\n"
        "sr=int(getattr(s,'sample_rate',48000) or 48000)\n"
        "dur=len(np.asarray(audio).squeeze())/sr\n"
        "print('RESULT',json.dumps({'secs':round(dt,2),'audio_secs':round(dur,2),"
        "'rtf':round(dt/max(dur,1e-6),2)},ensure_ascii=True))\n"
    )
    r = child([str(PY), "-c", code], timeout=900,
              env_extra={"VOICECHAT_RUNTIME_CONFIG": local_config()})
    line = next((l for l in (r.stdout or "").splitlines() if l.startswith("RESULT")), "")
    if not line:
        check("语音合成", False, (r.stderr or "").strip()[-140:])
        return
    data = json.loads(line.split("RESULT", 1)[1])
    check("语音合成（RTF<1 表示快于实时）", data["rtf"] < 1.0,
          f"{data['secs']}s 生成 {data['audio_secs']}s, RTF={data['rtf']}")


def check_gui() -> None:
    """Open the real window offscreen and run one turn through the pipeline."""
    code = (
        "import os,sys,time\n"
        "sys.path.insert(0,os.getcwd())\n"
        "os.environ.setdefault('QT_QPA_PLATFORM','offscreen')\n"
        "import main, config\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from PySide6.QtCore import QTimer\n"
        "from services.logger import setup_logging\n"
        "setup_logging()\n"
        "from ui.main_window import MainWindow\n"
        "START=time.time(); app=QApplication(sys.argv)\n"
        "w=MainWindow(); w.resize(1280,800); w.show()\n"
        "state={'sent':False}\n"
        "def poll():\n"
        "    msgs=w.chat_panel._messages\n"
        "    if getattr(w,'models_loaded',False) and not state['sent']:\n"
        "        state['sent']=True\n"
        "        w._on_confirm_user({'user_id':'SELFCHECK','name':'自检','age':'30',"
        "'gender':'男','marital_status':'未婚','addiction_type':'海洛因','drug_years':'1'})\n"
        "        QTimer.singleShot(8000, lambda: w._on_text_submitted('我这几天总是睡不着，白天也没精神。'))\n"
        "    ai=[m for m in msgs if m.get('type')=='ai' and str(m.get('text','')).strip()]\n"
        "    if state['sent'] and len(msgs)>=3 and ai:\n"
        "        print('SMOKE_OK', ai[-1]['text'][:60], flush=True); app.quit(); return\n"
        "    if time.time()-START>420:\n"
        "        print('SMOKE_TIMEOUT', len(msgs), flush=True); app.quit(); return\n"
        "    QTimer.singleShot(2000, poll)\n"
        "QTimer.singleShot(3000, poll)\n"
        "app.exec()\n"
    )
    started = time.time()
    r = child([str(PY), "-c", code], timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    ok = "SMOKE_OK" in out
    reply = ""
    for line in out.splitlines():
        if line.startswith("SMOKE_OK"):
            reply = line[len("SMOKE_OK"):].strip()
    check("界面冒烟（加载 + 一轮真实对话）", ok, f"{int(time.time()-started)}s | {reply or out.strip()[-100:]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="部署自检")
    parser.add_argument("--quick", action="store_true", help="跳过语音与界面测试")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--no-recovery", action="store_true", help="不停服务，避免打断正在用的人")
    args = parser.parse_args(argv)

    print(f"=== 部署自检 @ {time.strftime('%H:%M:%S')} · {ROOT} ===\n", flush=True)
    from deployment.autostart import ensure, start_watchdog

    ensure(wait_seconds=300)
    # 与应用同样的守护：检查期间服务若被回收（WSL 空闲回收、外部进程被杀），
    # 30 秒内自行恢复，而不是让某一条检查随机失败。
    start_watchdog()

    check_compile()
    check_services()
    check_weights()
    check_detection()
    ensure(wait_seconds=240)      # 服务可能在检查期间被 WSL 回收
    check_health()
    check_dependencies()
    check_settings()
    check_layout()
    ensure(wait_seconds=240)
    check_launcher()
    if not args.no_recovery:
        check_recovery()
    if not args.quick:
        check_asr()
        check_tts()
        check_gui()

    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n=== {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 ===")
    for item in failed:
        print(f"  失败: {item['check']} — {item['detail']}")
    if args.json:
        print(json.dumps({"results": RESULTS, "ok": not failed}, ensure_ascii=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
