# 05 — Real baseline/candidate A/B

Run baseline first. The live probe summary is a mandatory input and must be a
real artifact from the same corrected commit.

```powershell
.venv\Scripts\python.exe scripts\acceptance\qwen_dialogue_ab.py run `
  --profile rtxpro6000_96g `
  --live-probe-summary <baseline-acceptance_summary.json>
```

Check `status=PASS`, the exact model/profile, commit parity, scenario matrix
hash, prompt hashes, leakage fields, and measured performance fields. Keep
`promotion_status=NOT APPROVED`.

Then stop only the owned baseline dialogue service, verify `:8000` and VRAM
release, start the explicit candidate profile, run its independent Phase 5,
and run the candidate arm:

```powershell
.venv\Scripts\python.exe scripts\acceptance\qwen_dialogue_ab.py run `
  --profile rtxpro6000_96g_qwen38_candidate `
  --live-probe-summary <candidate-acceptance_summary.json>
```

Compare only after both arms pass:

```powershell
.venv\Scripts\python.exe scripts\acceptance\qwen_dialogue_ab.py compare `
  --baseline <baseline-run.json> `
  --candidate <candidate-run.json> `
  --output <comparison.json>
```

`NOT_COMPARABLE` is a stop condition. Blind packets contain no model identity;
human reviewers, not an automatic winner, decide promotion.
