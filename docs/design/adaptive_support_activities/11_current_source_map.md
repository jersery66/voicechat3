# Current Source Map

状态：**FUTURE DESIGN / 本次所有 production files 的 Design-time change = NO**

| Current file | Current ownership | Future relationship | Design-time change? | Future implementation phase |
|---|---|---|---|---|
| `conversation/contracts.py` | RouterProposal/TurnDecision/TurnSignals contracts | 未来新增 activity candidate/action 兼容 contract | NO | A/C |
| `conversation/agent_observation.py` | immutable AgentObservation | V2 observation fields保持兼容扩展 | NO | A |
| `conversation/turn_policy.py` | 单轮唯一业务裁决 | future activity priority/opt-in | NO | C |
| `conversation/turn_signals.py` | deterministic per-turn signals | explicit activity facts/cooldown facts | NO | C |
| `conversation/input_semantics.py` | 关键语义歧义 observation | 活动输入前仍是只读检查 | NO | C/D |
| `conversation/delivery.py` | generation identity/cancel/order/history | 活动切换时复用，不新增 authority | NO | D/E |
| `conversation/pre_delivery_guard.py` | UI/TTS 前 admission gate | 活动语言同样经过 Guard | NO | D/E |
| `assessment/answer_interpreter.py` | 自然语言 → 结构化量表候选 | 活动不得绕过量表解释边界 | NO | D/E |
| `assessment/scale_runtime.py` | 量表 single writer | pause/resume 仍由它拥有 | NO | C/D |
| `assessment/scale_policy.py` | 量表规则/策略 | 不与 activity policy 混写 | NO | D |
| `app/engine.py` | SessionEngine command/event writer | 与 ActivityRuntime 做 lifecycle 协作 | NO | B/E |
| `app/contracts.py` | lifecycle commands/events | future activity commands需兼容 command facade | NO | B/E |
| `core/session_fsm.py` | session/media FSM | 保持 SessionEngine ownership | NO | E |
| `services/pipeline.py` | 调用编排，不是 authority | future adapter only; no catalog if/else | NO | C/D |
| `services/emotion_tracker.py` | emotion trend/style observation | 不恢复具体 intervention 权限 | NO | A/C |
| `services/stt_service.py` | STT/VAD provider lifecycle | 未来活动语音输入复用或明确隔离 | NO | D/E |
| `services/llm_service.py` | Dialogue language realization | activity wording only | NO | D/E |
| `services/scales.py` | registered scale definitions | 与 activity catalog 分离 | NO | A/D |
| `services/report_service.py` | session/report helper | 只读取 activity facts | NO | G |
| `services/tools/relaxation_tool.py` | legacy/现有 relaxation helper | Phase 1 保持不变 | NO | F/G |
| `services/tools/video_tool.py` | media playback helper | ActivityRuntime 不直接拥有 media lifecycle | NO | G |
| `ui/main_window.py` | UI coordinator/command submitter | future activity offer/accept/exit UI | NO | G |
| `ui/control_panel.py` | user controls | 只提交 commands，不写 runtime | NO | G |
| `data/` / report modules | persistence/read-sink | ActivitySessionRecord artifact | NO | G |
| `config.py` | current runtime configuration | future catalog path/feature flag | NO | A/G |
| `deployment/profiles.py` | model/profile authority | activities不得改变 deployment topology | NO | 不改 |
| `tests/test_turn_authority.py` | current authority regression | add future activity permission tests | NO | C |
| `tests/test_scale_runtime*.py` | scale writer regression | add scale/activity isolation tests | NO | B/D |
| `tests/test_session_lifecycle*.py` | session writer regression | add activity lifecycle tests | NO | B/E |
| `tests/test_phase7_delivery_boundary.py` | delivery boundary regression | add stale output across activity transition | NO | D/E |
| `tests/integration/*` | mock production-chain contracts | future activity mock integration | NO | D/E |

当前 UI/media/game 模块只是 existing/legacy integration surface，不等同于已
存在的 Adaptive Support Activities。新增功能必须通过 ActivityCatalog 和
ActivityRuntime 设计，不应直接扩展现有按钮或 Pipeline 分支。
