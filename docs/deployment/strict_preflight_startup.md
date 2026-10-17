# Strict deployment preflight startup

This change starts from
`6790495682fcd71249a7923380df3f77dae622e7` and makes the application entry
point honor the configured `strict_preflight` policy.

## Startup policy

- `strict_preflight=False`: a failed or exceptional diagnostic check prints a
  warning and development startup continues.
- `strict_preflight=True`: a failed or exceptional preflight prints a fatal
  operator message, returns status `2`, and does not construct `QApplication`
  or `MainWindow`.

The policy is read from the `runtime` section of the runtime configuration
(`deployment.runtime_config.RuntimeConfig`) and does not inspect hardware or
configuration file names.

The shipped `config/runtime.yaml` sets:

```yaml
runtime:
  strict_preflight: true
  immutable_contract: true
```

A development configuration can opt out by setting both flags to `false`.
The standalone `scripts/check_config.py` contract remains reusable and
continues to return a boolean; `main.py` owns the desktop application's
reaction to that result.

## Verification

The focused startup suite covers strict false/exception paths, successful
strict startup, development warning/exception paths, and a renamed synthetic
strict configuration:

```text
tests/test_strict_preflight_startup.py: passing
full regression: passing
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
