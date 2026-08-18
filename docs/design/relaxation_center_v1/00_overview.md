# Relaxation Center V1 — Design Re-freeze

状态：**RELAXATION CENTER V1 / IMPLEMENTATION DESIGN FROZEN**

## 基线与隔离

```text
implementation base: 766b2171ab9ab598a685c998bdaf0d8f2bf353a6
Adaptive design 301c8db: SUPERSEDED FOR IMPLEMENTATION
Old Phase A 45fe029: PRESERVED / NOT MERGED
Frozen runtime reference: f33acb57d2c1d5ece35aa946bc40206113a14d24
Adaptive Phase A ancestry included: NO
.workbuddy/memory restored: NO
```

`f33acb57` 是 runtime 行为锚点，不是新功能 checkout 起点。Relaxation Center
设计与实现不得改变 frozen runtime 行为，也不删除旧设计历史。

## 产品定位

Relaxation Center 是心理支持访谈中的短时、低负担、可自主选择的休息和情绪
缓冲空间，提供短暂休息、注意转换、身体放松、低压力互动和恢复聊天的机会。
它不是治疗模块、量表模块或娱乐平台，不宣称治疗成瘾、降低复吸率、提高戒断
成功率，也不测量意志力、认知能力或注意能力。

## 三条路径

```text
Normal Conversation
        ↓
Agent 判断是否适合提供一次休息机会
        ↓
TurnPolicy 最终决定是否邀请
        ↓ 用户选择 [放松一下] / [继续聊]
Relaxation Center
   ├── Exercises
   ├── Videos
   └── Games
        ↓ complete/cancel
恢复原聊天上下文
```

Structured Assessment 继续后台自然采集：

```text
自然聊天 → Agent/deterministic signal → TurnPolicy.START_SCALE
→ 自然提问 → ScaleAnswerInterpreter → ScaleRuntime
```

用户仍可拒答或暂停；量表不要求每次另弹“是否参加 PHQ-9”。

## Authority invariants

1. Agent 只判断是否适合 `OFFER_RELAXATION`，不选择具体内容。
2. TurnPolicy 是唯一业务裁决者。
3. User 唯一决定是否进入和选择内容。
4. RelaxationRuntime 是放松中心内部 lifecycle 的唯一 writer。
5. SessionEngine 继续拥有 session/media lifecycle。
6. ScaleRuntime 继续拥有量表状态。
7. Dialogue LLM 只生成邀请和恢复聊天措辞。
8. UI 只展示内容并提交明确 command。
9. 游戏/练习使用 deterministic local logic，不依赖 LLM。

## V1 内容

Exercises：`breathing`、`muscle_relaxation`、`meditation`，未来可选
`grounding`；Videos 使用当前已有资源；Games V1 为 `bubble_pop`、
`gentle_search`、`calm_puzzle`、`falling_leaves`。`zen_garden` 和
`gentle_drift` 仅为后续 roadmap。

```text
RTX PRO 6000 / WSL CUDA / vLLM / STT/TTS/E2E: NOT RUN
```
