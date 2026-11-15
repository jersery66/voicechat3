# Relaxation Center V1 — Software closure

状态：**SOFTWARE CLOSED / PRE-HARDWARE**

本文件是 V1 软件交接状态，不是硬件、模型、音频或真实用户验收声明。

## Implementation matrix

| Area | Status |
| --- | --- |
| Core `breathing` | AVAILABLE |
| Core `muscle_relaxation` | AVAILABLE |
| Core `meditation` | AVAILABLE |
| Leisure `bubble_pop` | AVAILABLE |
| Leisure `gentle_search` | AVAILABLE |
| Leisure `calm_puzzle` | AVAILABLE |
| Leisure `falling_leaves` | AVAILABLE |
| Leisure video content | NOT AVAILABLE / no validated local content |
| `zen_garden` | NOT IMPLEMENTED / Post-V1 |
| `gentle_drift` | NOT IMPLEMENTED / Post-V1 |
| Additional videos/exercises | NOT IMPLEMENTED / Post-V1 |

## Frozen authority matrix

| Owner | Authority |
| --- | --- |
| Agent | observe/propose only |
| TurnPolicy | one-turn business decision |
| User | content selection and opt-in |
| RelaxationCatalog | content metadata and availability |
| RelaxationRuntime | Center/content lifecycle |
| SessionEngine | session and active-media lifecycle |
| ScaleRuntime | questionnaire item, answer, pause, resume |
| UI | rendering and command bridge |
| Report/DataManager | sink/reader only |

No V1 path allows the Agent to choose a breathing exercise, game, video, scale
item, score, or session transition.  Proactive relaxation is an opportunity
offer; explicit game requests open the Games page; Center users select the
actual content.

## Software evidence

- Phase 3 core integration: deterministic tests PASS
- Phase 4 native games and attribution: deterministic tests PASS
- Phase 4.1 leisure lifecycle: deterministic tests PASS
- Phase 5 invitation policy: deterministic tests PASS
- Phase 6 scale pause/resume and return context: deterministic tests PASS
- Full repository regression at closure: recorded in the closure commit report
- Software preflight: reports metadata/import facts only

## Hardware boundary

The following remain **NOT RUN**:

- RTX PRO 6000 identity and VRAM validation
- Windows/WSL CUDA and PyTorch validation
- vLLM startup and real Agent/dialogue inference
- FunASR/FSMN-VAD and VoxCPM2 execution
- real audio devices and media playback
- full STT → LLM → TTS E2E
- long-session stability and human review

Future content is explicitly deferred until this software closure, real
hardware validation, and actual user-experience evaluation are complete.
