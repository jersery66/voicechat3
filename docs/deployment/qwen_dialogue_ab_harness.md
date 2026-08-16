# Qwen dialogue-model A/B harness

Implementation baseline: `59b9a0b1a1ab64cf75b8644f134de1a879130a26`.

This acceptance-only harness compares the frozen dialogue path on the two
explicit RTX PRO 6000 profiles without changing the application authority
chain or promoting a model.

```text
RouterProposal -> TurnPolicy -> exactly one TurnDecision
ScaleRuntime   -> questionnaire state
SessionEngine  -> session lifecycle
72B            -> participant-facing language realization only
```

The harness does not replace or re-run Router, TurnPolicy, ScaleRuntime,
SessionEngine, STT, TTS, or RAG. It sends a fixed synthetic scenario matrix to
the profile-built `VLLMOpenAIClient` and records descriptive output and timing
evidence.

## Profiles and run order

Only these explicit profiles are accepted:

| Role | Profile | Dialogue model |
| --- | --- | --- |
| Baseline | `rtxpro6000_96g` | `Qwen/Qwen2.5-72B-Instruct-AWQ` |
| Candidate | `rtxpro6000_96g_qwen38_candidate` | `Qwen/Qwen3.8-27B-FP8` |

Both profiles use the current `:8000` dialogue endpoint. Run them separately
against the same already-running workstation stack, recording one artifact for
each profile; do not run both models concurrently on the single-GPU contract.
The harness never starts, stops, restarts, or reconfigures vLLM services.

```powershell
.venv\Scripts\python.exe scripts\acceptance\qwen_dialogue_ab.py run `
  --profile rtxpro6000_96g

# Switch the already-running stack deliberately, then run the candidate.
.venv\Scripts\python.exe scripts\acceptance\qwen_dialogue_ab.py run `
  --profile rtxpro6000_96g_qwen38_candidate
```

The run command verifies the exact profile-owned model identity, reuses the
production factory and generation options, and checks participant-visible
outputs for `<think>`, reasoning/control markers, and legacy business tags. It
does not strip a leak and call the request successful.

## Paired comparison

After both runs complete, create a structural comparison artifact:

```powershell
.venv\Scripts\python.exe scripts\acceptance\qwen_dialogue_ab.py compare `
  --baseline test_output\qwen_dialogue_ab\<baseline>\run.json `
  --candidate test_output\qwen_dialogue_ab\<candidate>\run.json `
  --output test_output\qwen_dialogue_ab\comparison.json
```

The comparison requires the same scenario matrix and prompt hashes. It pairs
non-stream latency, streaming TTFT/total latency, output length, and visible
text for human review. No latency threshold, automatic quality score, winner,
or promotion decision is produced. The comparison always records
`promotion_status: NOT APPROVED` and `human_review_required: true`.

The empty review rubric covers Chinese naturalness, empathy/reflection, one
primary question, avoiding premature advice, scale-question semantics, ASR
ambiguity confirmation, resistance handling, interruption response, and
control-leak absence. Reviewers may mark a dimension not applicable for a
scenario; the harness never converts these observations into an automatic
winner.

## Evidence and privacy

Run artifacts are written only under the ignored
`test_output/qwen_dialogue_ab/` directory. Prompts are fixed synthetic Chinese
strings; no participant history, DataManager records, reports, audio, or
clinical scores are loaded or persisted. The system prompt is represented by a
hash in the artifact rather than copied into it.

The harness records the profile-owned `enable_thinking=False` contract for the
Qwen3.8 candidate and rejects visible thinking/control leakage. Full raw
server-field inspection remains the responsibility of the accepted Blackwell
live probe; this harness does not duplicate that probe or infer hidden
reasoning from a public client response.

## Status at implementation time

```text
Harness implementation: READY
Deterministic harness tests: TESTED
Real RTX PRO 6000 hardware: NOT RUN
Real Qwen2.5-72B run: NOT RUN
Real Qwen3.8-27B-FP8 run: NOT RUN
Human quality review: NOT RUN
Qwen3.8 production promotion: NOT APPROVED
```

This is the final deterministic comparison tool. After it is reviewed, stop
adding infrastructure and perform the paired runs on the real Windows + WSL2
RTX PRO 6000 Blackwell workstation.
