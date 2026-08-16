# Qwen dialogue-model A/B harness

## Status

```text
HARNESS IMPLEMENTED
PHASE 5 PASS REQUIRED BEFORE EACH ARM
BASELINE REAL RUN NOT RUN
CANDIDATE REAL RUN NOT RUN
HUMAN REVIEW NOT RUN
PROMOTION NOT APPROVED
```

This acceptance-only harness compares the two explicit RTX PRO 6000 profiles
without changing the application authority chain or promoting a model:

```text
RouterProposal -> TurnPolicy -> exactly one TurnDecision
ScaleRuntime   -> questionnaire state
SessionEngine  -> session lifecycle
72B            -> participant-facing language realization only
```

The harness does not replace or re-run Router, TurnPolicy, ScaleRuntime,
SessionEngine, STT, TTS, or RAG. It sends fixed synthetic Chinese prompts to
the profile-built dialogue client and records descriptive output and timing.

## Required run sequence

Each arm must consume a matching **Phase 5 live-probe PASS artifact before any
dialogue request**. The harness validates the summary's overall status,
profile-owned dialogue model, hardware and server identity, Agent inference,
stream acceptance, and the three leakage flags. A failed, mismatched, or
missing summary stops the arm before the client is called.

The real deployment sequence is:

```text
baseline launcher
    -> Phase 5 PASS
    -> baseline A/B arm
    -> switch model externally
    -> candidate launcher
    -> Phase 5 PASS
    -> candidate A/B arm
    -> compare
    -> blind review A/B
    -> unblind
    -> human decision
```

Only these profiles are accepted:

| Role | Profile | Dialogue model |
| --- | --- | --- |
| Baseline | `rtxpro6000_96g` | `Qwen/Qwen2.5-72B-Instruct-AWQ` |
| Candidate | `rtxpro6000_96g_qwen38_candidate` | `Qwen/Qwen3.8-27B-FP8` |

The candidate remains explicit opt-in. Run the profiles separately against
the same already-running workstation stack; the harness never starts, stops,
restarts, or reconfigures vLLM services.

```powershell
.venv\Scripts\python.exe scripts\acceptance\qwen_dialogue_ab.py run `
  --profile rtxpro6000_96g `
  --live-probe-summary test_output\blackwell_acceptance\<baseline>\acceptance_summary.json

# Switch the already-running stack deliberately, then run the candidate.
.venv\Scripts\python.exe scripts\acceptance\qwen_dialogue_ab.py run `
  --profile rtxpro6000_96g_qwen38_candidate `
  --live-probe-summary test_output\blackwell_acceptance\<candidate>\acceptance_summary.json
```

## Matrix and repeatability

The synthetic matrix covers ordinary support, low mood, anxiety, insomnia,
loneliness/family separation, resistance and repeated refusal, direct advice,
institutional frustration, neutral small talk, gratitude, post-relaxation
outcomes, ASR ambiguity, scale timeframe/frequency/negation/core-symptom
semantics, refusal, one-primary-question discipline, premature/leading advice,
diagnosis and attribution avoidance, formulaic reassurance, closed-environment
fit, and long-context repetition. Each `ABScenario` records a category,
expected review constraints, and human-review dimensions. No LLM judge is
used for these metadata.

Every arm runs the complete matrix once. A fixed representative subset is then
streamed three additional times. Each repetition records its scenario ID,
repeat index, prompt hash, response text, TTFT, and total latency. Text is
never averaged, and non-stream and stream requests are not treated as the
same repetition.

## Comparability and performance

The comparison fails closed unless both arms are `PASS` and have matching:

- scenario matrix hash;
- system prompt hash;
- every per-scenario prompt hash and scenario ID set; and
- source git commit.

It returns only `READY_FOR_HUMAN_REVIEW`, `INCOMPLETE`, or
`NOT_COMPARABLE` (the latter two are represented as an `ABError`/non-zero CLI
result when the input cannot be compared). It never selects a winner and
always records `promotion_status: NOT APPROVED`.

Each run records descriptive stream TTFT and total-latency median/p95,
median output length, request failures, empty responses, and leakage counts.
Token throughput is recorded as exactly `NOT AVAILABLE` unless actual usage
metadata is provided by the client; no character-based estimate is used.

## Blind human-review packets

When `compare` is given an output path, it writes sibling artifacts:

```text
review_packet_A.csv
review_packet_B.csv
private_blind_map.json
```

Packets contain only a blind response ID, synthetic scenario context,
participant-facing response text, and blank human-review columns. They do not
contain profile/model names, baseline/candidate labels, latency, token, or
generation settings. A and B row orders use independent fixed randomization
seeds. The private map is kept out of reviewer packets and is the only place
that maps blind IDs back to profile/model/scenario identity.

The review columns cover Chinese naturalness, specificity, empathy calibration,
flow, resistance handling, closed-environment fit, one-primary-question,
premature/leading advice, overinterpretation, diagnostic language, motive or
personality attribution, formulaic empathy, repetition, scale semantics,
critical ambiguity clarification, and reviewer notes. Human review remains
human; no automatic score or LLM judge is introduced.

## Evidence and hardware boundary

Run artifacts are written only under the ignored
`test_output/qwen_dialogue_ab/` directory. Each run stores a reference and
SHA-256 for the consumed Phase 5 `acceptance_summary.json`, its probe commit,
profile, and dialogue model. A successful arm records
`hardware_validation: PHASE5_PASS_REFERENCED`; the paired A/B run itself is
still `real_ab_run_status: NOT RUN` until an operator runs it on the target
workstation.

The harness uses fixed synthetic prompts only. It does not load participant
history, DataManager records, reports, audio, or clinical scores. It does not
load STT/TTS providers and does not issue service lifecycle commands.

## Implementation baseline and validation state

Correction baseline: `508f3f96b022d93a2c121ef33f283496d693ab88`.

```text
Harness implementation: READY
Deterministic harness tests: TESTED
Real RTX PRO 6000 hardware: NOT RUN
Real Qwen2.5-72B run: NOT RUN
Real Qwen3.8-27B-FP8 run: NOT RUN
Human quality review: NOT RUN
Qwen3.8 production promotion: NOT APPROVED
```

After the paired runs and human review, stop adding deterministic
infrastructure. No Phase 8 or further model-promotion code is part of this
harness.
