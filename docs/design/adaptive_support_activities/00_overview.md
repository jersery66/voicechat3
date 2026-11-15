# Adaptive Support Activities — 设计总览

状态：**FUTURE DESIGN / DESIGN ONLY**

本目录描述下一阶段“自适应支持活动”框架。它不是当前生产功能，也不是
Correction C。当前 frozen runtime、部署包、模型 profile、真实硬件状态均不
因本设计改变。

## 设计基线

```text
design branch base:
  766b2171ab9ab598a685c998bdaf0d8f2bf353a6

runtime behavior baseline:
  pre-hardware-validation-ready-v2-20260817
  -> f33acb57d2c1d5ece35aa946bc40206113a14d24

privacy cleanup preserved:
  YES
.workbuddy/memory restored:
  NO
```

设计分支起点和 runtime 行为基线是两个不同概念。所有未来实现必须先完成
真实 RTX PRO 6000 验收，再按本目录分阶段实施。

## 目标

设计一个可配置、可退出、可审计的支持活动框架，用于短时的技能练习、自我
觉察和支持性互动，例如诱因识别、拒绝情境练习和应对工具选择。

活动推荐链为：

```text
AgentObservation V2
    ↓ 只读 support need / readiness observation
Activity candidate selection
    ↓
RouterProposal
    ↓
TurnPolicy
    ↓
TurnDecision.RECOMMEND_SUPPORT_ACTIVITY
    ↓
用户明确接受
    ↓
ActivityRuntime.start(activity_id)
```

推荐不等于开始。可感知活动始终 `opt_in_required=true`。

## 非目标

本设计不提供：

- 诊断、治疗、复吸风险预测或危机自动处置；
- `willpower_score`、`addiction_severity_score`、`treatment_success_score`；
- 自动把拒答解释为不配合；
- 让 Agent、LLM、UI 或 Delivery 直接修改业务状态；
- 当前生产 `.py` 修改、Prompt 修改、模型参数修改或 UI 实现；
- 任何真实硬件或活动疗效结论。

## 当前架构事实

当前系统已经存在以下边界：

```text
STT / text
    ↓
AgentObservation + RouterProposal
+ deterministic TurnSignals + TurnStateSnapshot
    ↓
TurnPolicy
    ↓ ONE immutable TurnDecision
ScaleRuntime / SessionEngine
    ↓
language context → Dialogue LLM → PreDeliveryGuard
    ↓
generation-scoped Delivery → UI / TTS / history
```

`ConversationPipeline` 是调用编排者，不是额外的业务 authority。
`RouterProposal` 和 `TurnSignals` 都是观察；`TurnPolicy` 是单轮唯一裁决者；
`ScaleRuntime` 和 `SessionEngine` 是不同的 single-writer state domain。

## 未来新增的唯一状态域

`ActivityRuntime` 只拥有支持活动内部状态：步骤、用户选择、取消、完成和
活动结果。它不能拥有量表答案、会话结束、RAG、Dialogue LLM 或 UI 生命周期。

## 两类流程

### Structured Assessment

PHQ-9/GAD-7/PCL-5 继续作为后台结构化采集。用户可以拒答或暂停；系统不向
用户暴露量表名称、题号、分数或内部协议。量表不属于 Support Activity。

### Optional Support Activities

呼吸、grounding、视频、互动游戏和技能练习是用户可感知的可选功能。它们必须
先被推荐、再由用户接受，用户可以拒绝、中途退出并回到聊天。拒绝不得降低
“配合度”、影响量表分数或产生惩罚性反馈。

## 权限不变量

1. Agent 只能观察和提出候选。
2. TurnPolicy 仍是唯一业务裁决者。
3. `TurnDecision` 是唯一可执行单轮决定。
4. ScaleRuntime 仍是量表唯一写入者。
5. SessionEngine 仍是会话生命周期唯一写入者。
6. ActivityRuntime 只写活动内部状态。
7. Dialogue LLM 只做语言实现。
8. `TurnDecision.needs_rag` 仍是唯一生产 RAG gate。
9. Delivery 只负责 generation、取消、顺序和最终化。
10. Data/Report 只能读取事实并持久化，不反向改变状态。

## 证据边界

本目录所有内容均为 `FUTURE DESIGN`。当前状态仍为：

```text
RTX PRO 6000: NOT RUN
WSL CUDA: NOT RUN
Real vLLM: NOT RUN
Real Phase 5: NOT RUN
Real A/B: NOT RUN
Real STT/TTS/E2E: NOT RUN
```
