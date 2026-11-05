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
from services.stt_service import select_input_device

devices = sd.query_devices()
index, reason = select_input_device(devices, default_index=sd.default.device[0])
if index is None or index < 0:
    print("FATAL: no usable microphone found (loopback-only machines cannot pick up speech).")
    sys.exit(1)

try:
    import numpy as np
    import time

    frames = []

    def _cb(indata, n, ti, status):
        frames.append(indata.copy())

    # Callback-based, like production recording (the WDM-KS backend rejects
    # the blocking-read API).
    with sd.InputStream(device=index, samplerate=16000, channels=1,
                        dtype="float32", blocksize=1024, callback=_cb):
        time.sleep(0.4)
    audio = np.concatenate(frames)
    peak = float(abs(audio).max())
    print(f"  input: {reason}")
    print(f"  probe recorded ok (peak={peak:.4f})")
    if peak == 0.0:
        print("  WARN: 探测到麦克风打开但采集全静音——请检查：")
        print("    - 设置 -> 隐私和安全性 -> 麦克风 -> 允许桌面应用访问麦克风")
        print("    - 麦克风硬件开关/是否静音")
        print("  （不阻止启动，但语音输入将收不到声音。）")
except Exception as exc:
    print(f"FATAL: cannot open microphone (device {index}): {type(exc).__name__}: {exc}")
    hostapis = {int(d["hostapi"]) for d in devices if d["max_input_channels"] > 0}
    api_names = {str(sd.query_hostapis(h)["name"]) for h in hostapis}
    if api_names == {"Windows WDM-KS"}:
        print()
        print("  诊断：MME/WASAPI 层看不到任何录音设备，麦克风只存在于内核层 (WDM-KS)。")
        print("  这几乎总是 Windows 声音设置里『录音设备被禁用/未设为默认』造成的。请：")
        print("    1. Win+R 运行 mmsys.cpl -> 『录制』选项卡")
        print("    2. 空白处右键 -> 勾选『显示禁用的设备』和『显示已断开的设备』")
        print("    3. 右键『麦克风』-> 启用，并『设为默认设备』")
        print("    4. 或：设置 -> 系统 -> 声音 -> 输入，选择麦克风并允许访问")
        print("  修好后重新运行本脚本即可。")
    sys.exit(1)
print("  audio OK")
'@
$tmpAudio = Join-Path $env:TEMP "voicechat_audio_check.py"
[System.IO.File]::WriteAllText($tmpAudio, $audioCheck, [System.Text.UTF8Encoding]::new($false))
& $python $tmpAudio
if ($LASTEXITCODE -ne 0) { exit 1 }

# -- 3. relaxation media -----------------------------------------------------
Write-Host "[3/5] relaxation media..."
$media = @'
from pathlib import Path
from relaxation.catalog import build_default_catalog, RelaxationContentRole
root = Path.cwd()
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
