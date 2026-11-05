# voicechat3 offline demo launcher (RTX 5090, Jev / Tacit inprocess).
#
# Flow:
#   1. Point VOICECHAT_RUNTIME_CONFIG at config/runtime_offline_5090.yaml.
#   2. Wait for the WSL llama-server dialogue service (:8000) -- the .bat
#      starts it in its own minimised window; this script only waits.
#   3. Check that a real microphone exists (production device selection).
#   4. Report missing relaxation media as a WARNING (non-fatal).
#   5. Run the full configuration preflight (scripts/check_config.py).
#   6. Launch the GUI (main.py).
#
# No model downloads, no network APIs, no silent provider substitution: any
# check failure aborts with a clear message (the runtime itself also runs
# strict preflight).

param(
    [switch]$PreflightOnly,   # run all checks and exit without the GUI
    [switch]$SkipLlmStart     # alias kept for compatibility; waiting is cheap
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$env:VOICECHAT_RUNTIME_CONFIG = Join-Path $root "config\runtime_offline_5090.yaml"
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { $python = "python" }

Write-Host "=============================================================="
Write-Host " voicechat3 offline demo (Jev / Tacit inprocess, RTX 5090)"
Write-Host " runtime config: $env:VOICECHAT_RUNTIME_CONFIG"
Write-Host "=============================================================="

# -- 1. dialogue llama-server ------------------------------------------------
# The launcher .bat starts the service in its own minimised window
# (start /min wsl.exe ...).  This script only WAITS for readiness; it never
# spawns or kills model processes.
Write-Host "[1/5] waiting for dialogue service (WSL llama-server :8000)..."
Write-Host "      loading ~17GB on first start (up to 10 minutes)..."
$ready = $false
for ($i = 0; $i -lt 150; $i++) {
    try {
        $probe = Invoke-WebRequest -Uri "http://127.0.0.1:8000/v1/models" -TimeoutSec 3 -UseBasicParsing
        if ($probe.Content -match '"id"') { $ready = $true; break }
    } catch { }
    if ($i -gt 0 -and ($i % 5 -eq 0)) { Write-Host "      ...still loading ($($i * 4)s)" }
    Start-Sleep -Seconds 4
}
if (-not $ready) {
    Write-Host "FATAL: dialogue service did not become ready on :8000." -ForegroundColor Red
    Write-Host "       Make sure the 'voicechat3 dialogue' window is open, then check inside WSL:"
    Write-Host "       wsl -e bash -c 'ps aux | grep llama-server'"
    exit 1
}
Write-Host "      dialogue service ready on :8000"

# -- 2. audio input ----------------------------------------------------------
# Mirror the production device selection (services.stt_service.select_input_device):
# a missing Windows *default* is not fatal as long as a real microphone exists
# and can be opened explicitly.
Write-Host "[2/5] audio input..."
$audioCheck = @'
import sys

try:
    import sounddevice as sd
except Exception as exc:
    print(f"FATAL: audio subsystem unavailable: {exc}")
    sys.exit(1)

sys.path.insert(0, ".")
from services.stt_service import select_input_device, _LOOPBACK_DEVICE_MARKERS

devices = sd.query_devices()
index, reason = select_input_device(devices, default_index=sd.default.device[0])

def _probe(device_index, samplerate=16000, channels=1, seconds=0.4):
    """Callback-based probe in the production format; returns peak amplitude."""
    import numpy as np
    import time

    frames = []

    def cb(indata, n, ti, status):
        frames.append(indata.copy())

    with sd.InputStream(device=device_index, samplerate=samplerate,
                        channels=channels, dtype="float32",
                        blocksize=1024, callback=cb):
        time.sleep(seconds)
    audio = np.concatenate(frames)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return float(abs(audio).max())

def _name_of(i):
    try:
        return str(devices[i].get("name", ""))
    except Exception:
        return ""

def _is_loopback(i):
    return any(m in _name_of(i).lower() for m in _LOOPBACK_DEVICE_MARKERS)

if index is None or index < 0:
    print("FATAL: no microphone found by any audio API.")
    sys.exit(1)

print(f"  input: {reason}")
chosen = None
input_ids = [i for i, d in enumerate(devices) if d.get("max_input_channels", 0) > 0]
for candidate in [index] + [i for i in input_ids if i != index]:
    try:
        peak = _probe(candidate)
        chosen = candidate
        break
    except Exception as exc:
        print(f"  device {candidate} ({_name_of(candidate)}) not openable: {exc}")

if chosen is None:
    print("FATAL: no input device could be opened in the production format (16k/mono).")
    print("  如果『录制』设备已被禁用，请先在 mmsys.cpl -> 录制 中启用并设为默认，")
    print("  然后重新运行本脚本（内录/立体声混音也可以，启用方法相同）。")
    sys.exit(1)

if _is_loopback(chosen):
    print(f"  mode: 内录（{_name_of(chosen)}）——录音捕获系统播放的声音，不是麦克风。")
    print("  演示方法：GUI 点录音后，用任意播放器把参与者语音 wav 播放到扬声器。")
    print("  注意：录音期间不要播放其他系统声音（TTS 已由程序自动先停止）。")
elif chosen != index:
    print(f"  mode: fallback input device {_name_of(chosen)}")
else:
    print(f"  probe recorded ok (peak={peak:.4f})")
    if peak == 0.0:
        print("  WARN: 探测到输入打开但采集全静音——请检查：")
        print("    - 设置 -> 隐私和安全性 -> 麦克风 -> 允许桌面应用访问麦克风")
        print("    - 麦克风硬件开关/是否静音")
        print("  （不阻止启动，但语音输入将收不到声音。）")
print("  audio OK")
'@
$tmpAudio = Join-Path $env:TEMP "voicechat_audio_check.py"
[System.IO.File]::WriteAllText($tmpAudio, $audioCheck, [System.Text.UTF8Encoding]::new($false))
& $python $tmpAudio
if ($LASTEXITCODE -ne 0) { exit 1 }

# -- 3. relaxation media -----------------------------------------------------
Write-Host "[3/5] relaxation media..."
$media = @'
import sys
sys.path.insert(0, ".")
from pathlib import Path
try:
    from relaxation.catalog import build_default_catalog, RelaxationContentRole
except Exception as exc:
    print(f"  WARN: relaxation catalog not importable (non-fatal): {exc}")
    sys.exit(0)
root = Path.cwd() / "media_library" / "relaxation"
missing = []
for item in build_default_catalog().definitions:
    if item.role != RelaxationContentRole.CORE_RELAXATION:
        continue
    p = root / item.resource_path
    if not p.exists():
        missing.append(str(p))
if missing:
    print("  WARN: missing relaxation media (core training will degrade):")
    for m in missing:
        print(f"    - {m}")
else:
    print("  relaxation media OK")
'@
$tmpMedia = Join-Path $env:TEMP "voicechat_media_check.py"
[System.IO.File]::WriteAllText($tmpMedia, $media, [System.Text.UTF8Encoding]::new($false))
& $python $tmpMedia
if ($LASTEXITCODE -ne 0) { exit 1 }

# -- 4. full preflight -------------------------------------------------------
Write-Host "[4/5] configuration preflight (endpoints / FunASR / VoxCPM / Tacit)..."
& $python scripts\check_config.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "FATAL: preflight failed -- fix the [FAIL] items above. The GUI will not start." -ForegroundColor Red
    exit 1
}

# -- 5. GUI ------------------------------------------------------------------
if ($PreflightOnly) {
    Write-Host "[5/5] preflight-only: done."
    exit 0
}
Write-Host "[5/5] starting GUI..."
& $python main.py
exit $LASTEXITCODE
