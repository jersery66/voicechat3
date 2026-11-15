# RelaxationRuntime 与 Authority

状态：**FUTURE DESIGN / Phase 1 实现范围**

## Runtime

这是轻量 runtime，不是旧 Adaptive Support Activities 的复杂 `ActivityRuntime`。
它只管理 Center 位置、当前 content、类型、content lifecycle、开始/完成/取消、
回到中心和回到聊天。

```text
INACTIVE → CENTER → RUNNING → CENTER → RETURNING → INACTIVE
                         └──── cancel ────┘
```

## Snapshot

```yaml
state: INACTIVE | CENTER | RUNNING | RETURNING
relaxation_session_id: string | null
selected_content_id: string | null
content_type: EXERCISE | VIDEO | GAME | null
started_at: timestamp | null
completed: boolean
cancelled: boolean
```

Snapshot immutable，不加入心理评分或疗效字段。

## Authority

```text
Agent → 是否 OFFER_RELAXATION
TurnPolicy → 是否邀请
User → 是否进入、选择什么内容
RelaxationRuntime → 中心内部 lifecycle
SessionEngine → session lifecycle
ScaleRuntime → scale state
Dialogue LLM → invitation / context recovery wording
UI → display + explicit command
Game/exercise → deterministic local logic
```

Runtime 不负责推荐、UI 或播放实现；小游戏不得访问 TurnPolicy、ScaleRuntime、
SessionEngine、RAG 或 LLM business authority。

## 非法状态与失败

`INACTIVE → start_content`、`RUNNING → enter_center`、未知/disabled content 和
重复 start 必须拒绝。资源缺失、provider failure、用户退出或 stale command 只
取消/关闭自己的 runtime，不结束 session、不改量表。
