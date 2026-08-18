# Test / Acceptance Plan

状态：**FUTURE DESIGN / Phase 1 focused only**

## Phase 1 runtime

- `INACTIVE → CENTER`；
- `CENTER → RUNNING`；
- `RUNNING → CENTER` on complete/cancel；
- `CENTER → RETURNING/INACTIVE`；
- `INACTIVE → start_content` rejected；
- duplicate start、unknown/disabled content rejected；
- immutable snapshot；
- completed content cannot accidentally resume；
- runtime 不 import/mutate ScaleRuntime、SessionEngine、TurnPolicy 或 LLM。

## Phase 1 catalog

- lookup/list/enabled filtering；
- duplicate id、unknown category；
- negative duration、max < recommended；
- missing display name；
- planned resource 明确为 planned，不能假装 available。

## Future integration

测试 Agent 只 offer、Policy 批准、用户 opt-in、UI command、不重复 proactive offer；
测试 active scale explicit pause/resume、accepted answer 先 commit、SessionEngine
lifecycle、Delivery stale generation、返回聊天上下文和四个游戏 deterministic。

## Evidence gate

Phase 1 fixture tests是软件证据。真实 RTX PRO 6000、GPU coexistence、音频设备、
TTFT 和 E2E 仍为 `NOT RUN`，不能被测试自动提升。
