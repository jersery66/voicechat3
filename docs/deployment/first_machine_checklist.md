# First-machine deployment checklist

All boxes are intentionally unchecked. Complete this list only on the target
workstation and attach raw evidence/artifact paths for each acceptance item.

## Checkout and host

- [ ] Approved branch checked out
- [ ] Approved commit verified
- [ ] Working tree clean
- [ ] Windows edition/build recorded
- [ ] Exact GPU identity recorded
- [ ] Total/free VRAM recorded
- [ ] NVIDIA driver recorded

## WSL and software

- [ ] WSL2 installed
- [ ] Target distribution selected and recorded
- [ ] WSL GPU visible
- [ ] Windows Python environment created
- [ ] PyTorch CUDA availability recorded
- [ ] WSL vLLM executable recorded
- [ ] Dialogue baseline model cache present
- [ ] Dialogue candidate model cache present
- [ ] Agent model cache present
- [ ] FunASR model path present
- [ ] VoxCPM2 model path present
- [ ] Voice prompt path present if required
- [ ] Output/artifact paths writable

## Deployment contracts

- [ ] `.env.example` reviewed; no secrets committed
- [ ] Deployment doctor executed
- [ ] Deployment manifest generated
- [ ] Acceptance manifest generated
- [ ] Launcher `-VerifyOnly` executed
- [ ] Agent exact identity verified
- [ ] Dialogue exact identity verified
- [ ] Strict preflight passed
- [ ] GUI launched only after backend readiness

## Real acceptance

- [ ] Phase 5 baseline PASS artifact
- [ ] Baseline A/B arm complete
- [ ] Baseline artifacts saved
- [ ] Owned baseline dialogue stopped
- [ ] Port 8000 and VRAM release verified
- [ ] Candidate exact identity verified
- [ ] Phase 5 candidate PASS artifact
- [ ] Candidate A/B arm complete
- [ ] A/B comparison is `READY_FOR_HUMAN_REVIEW`
- [ ] Blind review packets checked
- [ ] Human review complete
- [ ] Real STT validation complete
- [ ] Real TTS validation complete
- [ ] Real STT → LLM → TTS E2E complete

Real hardware acceptance is not implied by checking the software boxes.
