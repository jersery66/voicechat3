# voicechat3 offline demo launcher (RTX 5090, Jev / Tacit inprocess).
#
# Flow:
#   1. Point VOICECHAT_RUNTIME_CONFIG at config/runtime_offline_5090.yaml.
#   2. Make sure the WSL llama-server dialogue service (:8000) is up -- start
#      it via the repo's engine script if needed (never kills foreign processes).
#   3. Check audio input/output devices exist.
#   4. Report missing relaxation media as a WARNING (non-fatal).
#   5. Run the full configuration preflight (scripts/check_config.py), which
#      validates endpoints, FunASR, VoxCPM, the voice prompt and the Tacit
#      weights (sha256).
#   6. Launch the GUI (main.py).
#
# No model downloads, no network APIs, no silent provider substitution: any
# check failure aborts with a clear message (the runtime itself also runs
# strict preflight).

param(
    [switch]$PreflightOnly,   # run all checks and exit without the GUI
    [switch]$SkipLlmStart     # do not try to start the dialogue service
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
# The launcher .bat starts the service in its own minimised window (start /min
# wsl.exe ...).  This script only WAITS for readiness; it never spawns or kills
# model processes.
if (-not $SkipLlmStart) {
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
} else {
    Write-Host "[1/5] dialogue service start skipped (-SkipLlmStart)"
}

# -- 2. audio devices --------------------------------------------------------
Write-Host "[2/5] audio devices..."
$audioCheck = @'
import sounddevice as sd
try:
    devices = sd.query_devices()
    inputs = [d for d in devices if d.get("max_input_channels", 0) > 0]
    outputs = [d for d in devices if d.get("max_output_channels", 0) > 0]
    default_in = sd.default.device[0]
    default_out = sd.default.device[1]
    print(f"  input devices: {len(inputs)} (default: {default_in})")
    print(f"  output devices: {len(outputs)} (default: {default_out})")
    if default_in < 0 or default_out < 0:
        print("FATAL: no default audio device set"); raise SystemExit(1)
    print("  audio OK")
except Exception as exc:
    print(f"FATAL: audio subsystem unavailable: {exc}"); raise SystemExit(1)
'@
$tmpAudio = Join-Path $env:TEMP "voicechat_audio_check.py"
Set-Content -Path $tmpAudio -Value $audioCheck -Encoding UTF8
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
Set-Content -Path $tmpMedia -Value $media -Encoding UTF8
Copy-Item -Path $tmpMedia -Destination (Join-Path $root "voicechat_media_check_tmp.py") -Force
& $python (Join-Path $root "voicechat_media_check_tmp.py")
$mediaExit = $LASTEXITCODE
Remove-Item (Join-Path $root "voicechat_media_check_tmp.py") -Force -ErrorAction SilentlyContinue
if ($mediaExit -ne 0) { exit 1 }

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
