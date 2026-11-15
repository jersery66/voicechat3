# AgentObservation V2 设计

状态：**FUTURE DESIGN / 不修改当前 Pydantic model**

## 兼容原则

当前 `AgentObservation` 字段保持兼容：

```text
proposal
intent
fallback_used
source
```

`proposal` 仍包含当前字段：`action`、`scale_name`、`intervention_type`、
`emotion`、`intensity`、`needs_rag`、`confidence`、`reason`。V2 的新字段是
只读 observation，不能写入任何 Runtime。

## 建议结构

```yaml
conversation_stage: RAPPORT | EXPLORATION | ASSESSMENT | STABILIZATION | RECOVERY
user_load: LOW | MODERATE | HIGH
engagement: LOW | MODERATE | HIGH
assessment_readiness:
  PHQ-9: 0.00..1.00
  GAD-7: 0.00..1.00
  PCL-5: 0.00..1.00
support_need:
  immediate_stabilization: 0.00..1.00
  craving_coping: 0.00..1.00
  trigger_awareness: 0.00..1.00
  refusal_skill: 0.00..1.00
  coping_skill: 0.00..1.00
  change_motivation: 0.00..1.00
  recovery_planning: 0.00..1.00
activity_readiness: 0.00..1.00
```

所有枚举必须拒绝未知值，所有连续值必须限制在 `[0.0, 1.0]`。缺失或不可信
字段使用 `None`/默认 observation，而不是猜测。

## Conversation stage

| Stage | 定义 | 典型线索 | 不应误判 | TurnPolicy 关系 |
|---|---|---|---|---|
| `RAPPORT` | 建立关系、低压力交流 | 寒暄、简短回应、试探系统 | 沉默不等于低 engagement | 不主动引入复杂活动 |
| `EXPLORATION` | 了解困扰、生活状态和背景 | 描述睡眠、压力、关系、诱因 | 单个症状不等于量表 readiness | 可候选轻量觉察活动 |
| `ASSESSMENT` | 信息足够，适合结构化采集 | 持续症状、明确时间频率 | readiness 不是疾病概率 | 量表优先 |
| `STABILIZATION` | 当前负荷较高，降低复杂度 | 强烈焦虑、冲动、失控感 | 高负荷不等于高风险诊断 | 只考虑短、低负荷活动 |
| `RECOVERY` | 讨论应对、改变目标和未来计划 | 价值、选择、支持网络、计划 | 一次积极表达不等于准备完成 | 可候选技能练习 |

## User load

`user_load` 只表示当前交互的认知/情绪负担，不表示疾病严重程度、危险程度
或人格特征。`HIGH` 时限制复杂、多步骤或高认知负担活动，优先考虑短时稳定
模块或继续聊天。

## Engagement

`engagement` 只表示当前参与对话/活动的投入程度。沉默、拒答、不愿做活动都
不能直接解释为“不配合”，也不能影响量表分数、报告标签或活动资格。

## Assessment readiness

`assessment_readiness[scale]` 表示自然对话是否出现足够线索，适合进一步结构化
采集某一量表维度。它不是诊断概率：

```text
assessment_readiness != probability_of_disorder
```

最终启动量表仍需 `TurnPolicy` 的轮次、完成状态、deterministic candidate 和
Router conflict 规则。

## Support need

| 字段 | 定义 |
|---|---|
| `immediate_stabilization` | 是否更需要降低交互负担和即时稳定 |
| `craving_coping` | 是否需要应对强烈渴求/冲动相关体验 |
| `trigger_awareness` | 是否适合识别人、地点、时间、情绪和事件线索 |
| `refusal_skill` | 是否存在拒绝高风险社交情境的技能需要 |
| `coping_skill` | 是否表现出不知道如何应对压力、失眠、烦躁等缺口 |
| `change_motivation` | 是否适合探索改变的利弊、价值和个人理由 |
| `recovery_planning` | 是否适合讨论未来高风险情境和恢复计划 |

这些分数只能用于 candidate ranking，不能直接启动活动。

## Activity readiness

`activity_readiness` 表示当前是否适合暂时从自然聊天切换到短互动模块，不能
解释为治疗依从性、治疗成功率或患者是否适合治疗。高负荷时即使某个 need 高，
也必须经过 load exclusion 和 opt-in。

## 示例

```json
{
  "conversation_stage": "RECOVERY",
  "user_load": "MODERATE",
  "engagement": "HIGH",
  "assessment_readiness": {"PHQ-9": 0.42, "GAD-7": 0.18, "PCL-5": 0.05},
  "support_need": {
    "immediate_stabilization": 0.22,
    "craving_coping": 0.38,
    "trigger_awareness": 0.41,
    "refusal_skill": 0.78,
    "coping_skill": 0.55,
    "change_motivation": 0.64,
    "recovery_planning": 0.71
  },
  "activity_readiness": 0.72
}
```

## Fallback

Agent unavailable、超时或 schema 无效时：

```text
deterministic local observation
    ↓
proposal = CHAT
activity candidate = none
    ↓
继续 TurnPolicy / 普通聊天
```

Fallback 不重试第二个 Agent，不主动推荐复杂活动，不改变量表或会话状态。
