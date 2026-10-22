# ActivityCatalog 设计

状态：**FUTURE DESIGN / 配置 schema，不实现**

## 目标

ActivityCatalog 是活动能力的配置入口。Eligibility、展示文本、步骤和结果
schema 不应散落在 `ConversationPipeline` 的大量 `if/else` 中。Catalog 是
描述性配置，不是业务 authority；最终 recommendation/start 仍由 TurnPolicy 和
ActivityRuntime 负责。

## Schema

```yaml
id: refusal_rehearsal
display_name: 拒绝挑战
category: recovery_skill
target_need: refusal_skill
min_need_score: 0.70
min_activity_readiness: 0.65
allowed_user_load: [LOW, MODERATE]
allowed_conversation_stages: [EXPLORATION, RECOVERY]
opt_in_required: true
proactive_allowed: true
max_per_session: 1
cooldown_rounds: 4
expected_duration_minutes: 5
can_interrupt_scale: false
resume_scale_after: true
uses_media: false
requires_voice_input: true
result_schema: refusal_rehearsal.v1
evidence_status: supportive_skill_practice
```

字段约束：

- `id` 必须稳定、唯一、snake_case；
- `target_need` 必须来自 AgentObservation V2 的 support need；
- min 分数在 `[0,1]`；
- duration、cooldown、session limit 必须为非负有限值；
- `opt_in_required` 对所有可感知活动必须为 `true`；
- `result_schema` 只能描述非诊断性活动事实；
- `evidence_status` 必须明确 supportive skill practice、experimental 或其他
  尚未验证状态，不能写成治疗效果。

## Eligibility 算法（未来）

```text
catalog entry exists
AND activity_readiness >= min_activity_readiness
AND target support_need >= min_need_score
AND stage allowed
AND user_load allowed
AND not active/completed/cooldown
AND active-scale conflict allows
AND session opt-in path available
    ↓
eligible candidate
```

任何条件缺失返回 `INELIGIBLE`，不自动降级成另一个活动。多个 candidate 的
排序是 observation ranking，不是直接执行。

## Catalog 示例

```yaml
- id: trigger_detective
  display_name: 诱因侦探
  category: awareness
  target_need: trigger_awareness
  min_need_score: 0.65
  min_activity_readiness: 0.60
  allowed_user_load: [LOW, MODERATE]
  allowed_conversation_stages: [EXPLORATION, RECOVERY]
  opt_in_required: true
  proactive_allowed: true
  max_per_session: 1
  cooldown_rounds: 4
  expected_duration_minutes: 5
  can_interrupt_scale: false
  resume_scale_after: true
  uses_media: false
  requires_voice_input: false
  result_schema: trigger_detective.v1
  evidence_status: supportive_skill_practice

- id: ten_minute_buffer
  display_name: 十分钟缓冲
  category: stabilization
  target_need: immediate_stabilization
  min_need_score: 0.65
  min_activity_readiness: 0.55
  allowed_user_load: [HIGH, MODERATE]
  allowed_conversation_stages: [EXPLORATION, ASSESSMENT, STABILIZATION, RECOVERY]
  opt_in_required: true
  proactive_allowed: true
  max_per_session: 1
  cooldown_rounds: 6
  expected_duration_minutes: 10
  can_interrupt_scale: true
  resume_scale_after: true
  uses_media: false
  requires_voice_input: false
  result_schema: ten_minute_buffer.v1
  evidence_status: supportive_skill_practice
```

## Validation

未来实现必须在启动时拒绝：重复 id、未知 need/stage/load、负 duration、
`opt_in_required=false` 的可感知活动、未声明 result schema、越界阈值和未知
activity category。Catalog validation 失败时不启动任何活动，并保留当前聊天/量表
状态。

## Extensibility

新增活动只添加 catalog entry、ActivityRuntime step adapter、UI contract 和测试；
不新增一个 RouterAction，不修改 `TurnPolicy` 的每个活动分支，不在 Pipeline 中
硬编码几十个条件。第一版只注册六个活动，第二批 roadmap 不得被误读为已实现。
