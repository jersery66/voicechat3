# 测试与验收计划

状态：**FUTURE DESIGN / 不添加当前测试**

未来每个阶段都必须保留当前 `1011 passed / 0 failed` 回归基线，并新增 focused
tests。真实硬件测试必须显式 gate，不能进入普通 pytest 造成无 GPU 全红。

## A. AgentObservation V2

- 枚举 range validation：stage/load/engagement；
- continuous fields `[0,1]`；
- unknown/extra field compatibility；
- malformed Agent response → deterministic fallback；
- fallback 不生成复杂 activity candidate；
- observation 不写 ScaleRuntime/SessionEngine。

## B. TurnPolicy

- Agent 不能直接 start activity；
- recommendation 需要 Policy approval；
- start 需要 explicit user acceptance；
- explicit end priority；
- active scale priority；
- HIGH load restrictions；
- stage/load/readiness matrix；
- cooldown、declined suppression、max per session；
- recommendation/start separation。

## C. ActivityRuntime

- single-writer invariant；
- immutable snapshot；
- duplicate start rejected；
- step progression only by Runtime；
- cancel/complete cannot accidentally resume；
- asset/provider error recovery；
- session end while active；
- ActivityRuntime never writes scale/session state。

## D. Scale integration

- activity never changes scale score；
- accepted answer persists if activity starts afterward；
- pause preserves domain state；
- resume derives first unanswered item；
- stale UI item cannot force restoration；
- ordinary active-scale answer cannot launch complex activity。

## E. SessionEngine

- activity/media does not create a second lifecycle writer；
- end while active is deferred/handled by existing command contract；
- media completion and activity cancellation events are idempotent；
- new session has no previous ActivityRuntime state。

## F. Delivery

- stale generation cannot continue after activity transition；
- interruption cancels old output；
- TTS cancellation and sentence ordering remain unchanged；
- blocked activity-related language follows the existing Guard fallback contract。

## G. Data

- completed/cancelled ActivitySessionRecord roundtrip；
- no invented activity score；
- report reads committed facts only；
- draft free text is not promoted to completed result；
- no hidden reasoning or stale output persisted。

## H. Permissions

- LLM text tags cannot start activity；
- legacy tags cannot trigger activity；
- EmotionTracker cannot trigger activity；
- UI cannot bypass opt-in；
- ActivityRuntime cannot change `needs_rag`。

## I. UX

- reject → continue chat；
- accept → start exactly once；
- exit → safe return；
- no forced repeated recommendation；
- no shame, ranking, punishment or clinical verdict language。

## Evidence classes

```text
UNIT / CONTRACT / MOCK INTEGRATION / LOCAL INTEGRATION / HARDWARE ACCEPTANCE
```

Fixture activity tests are `SIMULATED`; real device/model evidence is `MEASURED` only
on the target workstation. No test result may promote Qwen3.8 or an activity.
