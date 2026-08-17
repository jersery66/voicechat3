# Shutdown and recovery runbook

This document describes safe operator recovery. It does not change lifecycle
code or add automatic recovery policy.

## Normal shutdown

1. Stop participant interaction and finish/cancel the active generation.
2. Close the Windows GUI normally.
3. Stop the owned dialogue service.
4. Stop the owned Agent service.
5. Run `-VerifyOnly` or the doctor and confirm PID metadata/ports.
6. Preserve logs and artifacts before cleanup.

The explicit stop script currently stops dialogue before Agent. Do not kill
unknown processes or delete another user's runtime directory.

## Recovery matrix

| Symptom | Check | Safe action | Do not do |
| --- | --- | --- | --- |
| GUI crash | doctor, launcher status, application log | inspect services; restart GUI only after exact readiness | kill all Python processes |
| Dialogue vLLM crash | WSL service log, PID status, `:8000` | preserve log; restart owned dialogue after identity check | start a second dialogue on the same port |
| Agent crash | Agent log, `:8001` identity | restart Agent first; re-run strict checks | continue with an unknown Agent |
| WSL terminated | `wsl --status`, `wsl -l -v` | restart WSL/operator session, then doctor | install a Linux display driver |
| Windows reboot | host/WSL checks | rerun the full startup sequence | assume old PIDs are valid |
| stale PID metadata | `-VerifyOnly`, WSL status | clean only the stale service slot, then restart | signal the stale numeric PID |
| `OWNERSHIP_MISMATCH` | metadata and `/proc` command line | stop and identify the process manually | force TERM/KILL |
| `UNKNOWN PORT OWNER` | TCP/HTTP/model identity | identify external owner manually | automatic kill or portproxy |
| wrong model identity | `/v1/models` | stop and correct profile/service selection | accept a text response as success |
| model startup timeout | service log, GPU snapshot | preserve evidence and diagnose | increase limits blindly |
| CUDA OOM | vLLM log and memory snapshots | stop owned service and record evidence | silently reduce model/profile |
| VoxCPM2 failure | application/TTS logs | record provider failure; continue only per existing behavior | claim real TTS acceptance |
| FunASR failure | STT logs/device status | record final-text failure and retry only by existing UI flow | guess transcript content |
| Phase 5 FAIL | acceptance summary | stop that A/B arm | edit the artifact manually |
| A/B `NOT_COMPARABLE` | hashes/commit/model identity | fix the comparison inputs and rerun | force a comparison |
