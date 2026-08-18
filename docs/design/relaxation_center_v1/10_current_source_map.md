# Current Source Map

状态：**Phase 0 / Design-time change = NO for all production files**

| Current file/domain | Current ownership | Future relationship | Phase 0 change? | Future phase |
|---|---|---|---|---|
| `conversation/agent_observation.py` | Agent observation | high-level offer fact only | NO | 5 |
| `conversation/contracts.py` | Router/TurnDecision | retain `RECOMMEND_RELAXATION` | NO | 5 |
| `conversation/turn_policy.py` | sole per-turn authority | approves offer, not content | NO | 5 |
| `conversation/turn_signals.py` | deterministic facts | explicit rest/cooldown facts | NO | 5/6 |
| `conversation/input_semantics.py` | ambiguity observation | remains before Policy | NO | 6 |
| `conversation/delivery.py` | generation/cancel/order | reused on Center return | NO | 6 |
| `conversation/pre_delivery_guard.py` | output admission | Center wording guarded | NO | 5/6 |
| `assessment/answer_interpreter.py` | scale answer interpretation | accepted answer commits first | NO | 6 |
| `assessment/scale_runtime.py` | scale single writer | pause/resume owner | NO | 6 |
| `app/engine.py` | SessionEngine writer | session/media lifecycle | NO | 6 |
| `app/contracts.py` | lifecycle commands/events | future Center bridge | NO | 2/6 |
| `core/session_fsm.py` | session FSM | no second lifecycle owner | NO | 6 |
| `services/pipeline.py` | orchestration | offer/context adapter only | NO | 5/6 |
| `services/emotion_tracker.py` | style observation | no content authority | NO | 5 |
| `services/stt_service.py` | STT lifecycle | future optional voice input | NO | 6 |
| `services/llm_service.py` | wording realization | invitation/recovery wording | NO | 5/6 |
| `services/tools/relaxation_tool.py` | existing relaxation | Phase 3 adapter | NO | 3 |
| `services/tools/video_tool.py` | existing media | Phase 3 adapter | NO | 3 |
| `ui/main_window.py` | UI coordinator | Center command bridge | NO | 2/6 |
| `ui/control_panel.py` | existing buttons | Center entry only | NO | 2 |
| `ui/media_panel.py` | media selection | catalog rendering reference | NO | 2/3 |
| `data/` / report modules | read/sink | relaxation facts | NO | 6 |
| `config.py` | runtime config | future Center settings | NO | 2/5 |
| `deployment/profiles.py` | model authority | unchanged | NO | none |
| relevant authority/scale/session tests | regression contracts | Center isolation tests | NO | 5/6 |

Phase 1 应新增独立 `relaxation/` domain，不把 Runtime 逻辑塞进 MainWindow、
`services/pipeline.py` 或 `app/engine.py`。本表记录影响关系，不表示当前已有
Relaxation Center 功能。
