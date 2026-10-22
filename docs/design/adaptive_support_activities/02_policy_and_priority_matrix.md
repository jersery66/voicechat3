# Activity Policy 与优先级矩阵

状态：**FUTURE DESIGN**

## 推荐 action 方案比较

### 方案 A：每个活动一个 RouterAction

例如 `RECOMMEND_TRIGGER_GAME`、`RECOMMEND_REFUSAL_GAME`。优点是短期直观；
缺点是 action vocabulary 膨胀、活动条件进入 Agent/Policy 分支、难以配置化。

### 方案 B：高层 action + catalog candidate（推荐）

保持一个高层动作：

```text
TurnDecision.action = RECOMMEND_SUPPORT_ACTIVITY
TurnDecision.activity_id = catalog-owned id
```

Agent 只识别 support need 并提出 candidate，ActivityCatalog 负责 eligibility
和展示/运行配置，TurnPolicy 批准 recommendation。该方案保持业务词汇稳定，
并避免把几十个活动写进 Pipeline `if/else`。

推荐方案 B。它仍需未来单独扩展 `TurnAction`/`TurnDecision` contract，当前不
修改 production model。

## Future priority

| 优先级 | 条件 | 结果 |
|---:|---|---|
| 1 | explicit end | `END_SESSION` |
| 2 | explicit user activity/relaxation request | Policy 决定 pause/offer |
| 3 | critical input ambiguity | `CLARIFY_INPUT` |
| 4 | active structured assessment | 先完成/澄清/暂停当前 item |
| 5 | immediate stabilization + HIGH load | 短稳定候选或继续聊天 |
| 6 | assessment start candidate | 量表规则先于普通活动 |
| 7 | optional support activity candidate | 只生成 invitation |
| 8 | ordinary chat | `CHAT` |

任何 activity candidate 都不能抢走 active scale 的 accepted answer commit。

## Recommendation 与 start 分离

```text
RECOMMEND_SUPPORT_ACTIVITY
    ↓ Dialogue LLM 生成邀请
    ↓ 用户明确接受
START_SUPPORT_ACTIVITY command
    ↓ ActivityRuntime.start(activity_id)
```

拒绝、关闭或继续聊天都不会产生负面标签；同一活动在 cooldown 期间不重复推荐。

## User load × activity

| Load | 允许优先考虑 | 默认排除 |
|---|---|---|
| `HIGH` | breathing、muscle、grounding、brief mindfulness、ten_minute_buffer | change_balance、complex crossroads、long refusal rehearsal、complex recovery planning |
| `MODERATE` | trigger_detective、coping_toolbox、refusal_rehearsal、short crossroads、stabilization | 多步骤高负担活动，除非用户明确接受 |
| `LOW` | change_balance、crossroads、recovery planning、deeper coping exercises | 仍受 stage、cooldown 和 opt-in 约束 |

## Conversation stage × activity

| Stage | 可候选 | 规则 |
|---|---|---|
| `RAPPORT` | 无复杂主动活动 | 先建立关系，除非用户明确请求 |
| `EXPLORATION` | trigger_detective、coping_toolbox、brief stabilization | 以了解和选择为主 |
| `ASSESSMENT` | 通常无复杂活动 | 量表优先，用户明确请求可由 Policy 决定暂停 |
| `STABILIZATION` | breathing、muscle、grounding、ten_minute_buffer | 短、低负荷、可立即退出 |
| `RECOVERY` | refusal_rehearsal、crossroads、change_balance、recovery planning、coping_toolbox | 必须满足 load/readiness |

## Active scale conflict matrix

| 情形 | Policy 结果 |
|---|---|
| active scale + ordinary activity candidate | 先完成当前回答；不弹复杂活动 |
| active scale + explicit relaxation/activity request | 可生成 `PAUSE_SCALE`，再邀请用户 opt-in |
| active scale + HIGH load / immediate stabilization | 先判断短稳定；必要时 pause → offer → accept → ActivityRuntime → `ScaleRuntime.resume()` |
| activity finished with paused scale | Runtime 自己计算 first unanswered item；UI 不保存旧题号 |
| activity declined | 保持 scale/chat；记录 declined fact，进入 cooldown |

## Cooldown 与频率

future session facts 可包括：

```text
declined_activity_ids
completed_activity_ids
last_activity_round
last_activity_id
```

建议同时配置 `max_proactive_activity_per_n_rounds`、`max_per_session`、
`cooldown_rounds`、同活动 session 上限和 declined cooldown。最终保存 owner
应由 ActivityRuntime 或 SessionEngine projection 明确设计，不能让 UI 私自保存。

## Activity candidate 选择

```text
AgentObservation V2
 + stage/load/engagement/readiness
 + support_need
 + active scale/session snapshot
    ↓
ActivityCatalog eligibility
    ↓
candidate ranking（不启动）
    ↓
RouterProposal
    ↓
TurnPolicy
```

`candidate ranking` 失败或信息不足时返回 `CHAT`，不得猜测或强制活动。
