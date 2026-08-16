# Strict deployment preflight startup

This change starts from
`6790495682fcd71249a7923380df3f77dae622e7` and makes the application entry
point honor the profile-owned `strict_preflight` policy.

## Startup policy

- `strict_preflight=False`: a failed or exceptional diagnostic check prints a
  warning and development startup continues.
- `strict_preflight=True`: a failed or exceptional preflight prints a fatal
  operator message, returns status `2`, and does not construct `QApplication`
  or `MainWindow`.

The policy is read from `get_deployment_profile()` and does not inspect
hardware or profile-name substrings.

Current strict profiles:

```text
a100_80g
a100_80g_qwen38_candidate
rtxpro6000_96g
rtxpro6000_96g_qwen38_candidate
```

`dev_6g` and `dev_vllm_6g` remain warning-only profiles. The standalone
`scripts/check_config.py` contract remains reusable and continues to return a
boolean; `main.py` owns the desktop application's reaction to that result.

## Verification

The focused startup suite covers strict false/exception paths, successful
strict startup, development warning/exception paths, and a renamed synthetic
strict profile:

```text
tests/test_strict_preflight_startup.py: 9 passed
full regression: 771 passed / 0 failed
```

Real deployment validation remains intentionally unperformed:

```text
RTX hardware: NOT RUN
nvidia-smi: NOT RUN
WSL2: NOT RUN
vLLM: NOT RUN
Qwen2.5-72B: NOT RUN
Qwen3.8-27B-FP8: NOT RUN
```

WSL launchers, GPU probes, live model probes, VRAM budgeting, and A/B testing
remain separate follow-up work.
