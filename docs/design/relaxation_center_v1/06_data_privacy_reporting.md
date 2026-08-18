# Data / Privacy / Reporting

状态：**FUTURE DESIGN / Phase 1 仅注册 metadata**

## RelaxationSessionRecord

```yaml
session_id: string
relaxation_session_id: string
entry_source: USER_REQUEST | AGENT_OFFER
accepted: boolean
declined: boolean
content_type: EXERCISE | VIDEO | GAME
content_id: string
started_at: timestamp | null
ended_at: timestamp | null
duration_seconds: number | null
completed: boolean
cancelled: boolean
cancel_reason: string | null
```

只保存进入来源、选择、时间和 lifecycle 事实。`content_id` 不代表心理评分。

## Usage statistics

未来管理员可以查看 Center 进入次数、接受率、内容分布、平均停留时长，以及
`USER_REQUEST` 与 `AGENT_OFFER` 的比例。这些是产品使用数据，不是心理疗效或
临床测量。

## 禁止推导

不得根据游戏或练习生成焦虑/抑郁评分、成瘾风险、复吸风险、认知/注意能力、
游戏能力、意志力或治疗效果。

Data/Report 继续是 read/sink；报告可以写“用户选择了泡泡并在 120 秒后退出”，
不能写“用户焦虑下降”或“放松有效”，除非未来有独立验证的测量工具。

## 隐私

真人使用前，普通日志不得默认复制 raw transcript、raw LLM output、完整 activity
free text、hidden reasoning 或原始音频。正式数据通过 DataManager artifact 保存，
避免日志成为第二份心理数据仓库。
