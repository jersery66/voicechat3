# Scale / Session Integration

状态：**FUTURE DESIGN / Phase 1 不接入**

## Normal active-scale path

普通量表回答不因 proactive offer 随意打断：

```text
ScaleAnswerInterpreter → ScaleRuntime.accept_answer/request_clarification
                         ↓
                    构建语言上下文
```

accepted answer 先 commit；后续 Center transition、LLM cancel、TTS interrupt 或
新 generation 都不能 rollback。

## Explicit user request

只有用户明确说“能休息一下吗”“我想看会儿东西”等，才允许 Policy 判断：

```text
ScaleRuntime.pause()
    ↓
Relaxation Center
    ↓ user exit
ScaleRuntime.resume()
```

恢复必须从 ScaleRuntime 当前真实状态推导 first unanswered item；UI 不能缓存旧
item index 并强制恢复。

## Transition table

| Current | Input | Result |
|---|---|---|
| no active scale | proactive offer approved | Offer Center; user may reject |
| no active scale | user requests rest | Policy may offer |
| active scale | ordinary turn | Continue current item |
| active scale | explicit rest request | Policy may pause, not automatic |
| paused scale | accept Center | Center/RUNNING; scale remains owner |
| Center | exit | RETURNING → chat |
| paused scale after exit | resume | ScaleRuntime first unanswered item |
| Center | session end | SessionEngine command handles lifecycle |

## Context recovery

进入 Center 前只保存只读 `pre_relaxation_context` reference/summary；退出后
Dialogue LLM 可以自然恢复最近主题，但不得重开量表、改变 session state 或宣称
疗效。
