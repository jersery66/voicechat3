# 数据、报告与隐私设计

状态：**FUTURE DESIGN / 不修改日志或 DataManager**

## ActivitySessionRecord

```yaml
activity_session_id: string
session_id: string
subject_id: string | pseudonymous reference
activity_id: string
category: string
offered_at: timestamp
accepted: boolean | null
declined_reason: string | null
started_at: timestamp | null
completed_at: timestamp | null
cancelled_at: timestamp | null
completion_status: OFFERED | DECLINED | ACTIVE | COMPLETED | CANCELLED | ERROR
responses: map
pre_rating: number | null
post_rating: number | null
derived_features: map
evidence_status: SUPPORTIVE_SKILL_PRACTICE | EXPERIMENTAL
```

`responses` 只保存活动需要的事实。`derived_features` 只能是非诊断性的观察
特征，例如拒绝表达是否包含退出策略；不能包含复吸风险、成瘾严重度、意志力或
治疗成功分数。

## Data / Report authority

```text
ActivityRuntime → committed ActivitySessionRecord
ScaleRuntime → committed scale facts
SessionEngine → committed lifecycle facts
Delivered history → actually delivered assistant text
                         ↓
                  DataManager / ReportService
```

Data/Report 是 read/sink，不得重新解释活动回答、改变 ActivityRuntime、推进量表、
结束会话或改变 `needs_rag`。报告可以写“完成了拒绝情境练习”，不能写“拒绝能力
良好”，除非未来有独立验证过的测量工具。

## 隐私与日志

真人使用前，普通 DEBUG/WARNING 日志不应默认记录：

- raw participant transcript；
- raw LLM output 或 hidden reasoning；
- complete activity free-text responses；
- 原始音频、临床评分全文或 RAG 原文。

Activity 数据通过正式 DataManager/报告 artifact 保存，不能让普通日志成为第二
份心理数据仓库。诊断模式如果未来需要，必须显式 opt-in、最小化记录并单独审计。

## 取消与隐私

取消的活动仍可保留 `CANCELLED` 事实，但不应把未确认的草稿 response 当作完成
结果。stale generation、未交付文本和 hidden reasoning 不进入活动记录或报告。

## 非诊断性声明

所有报告输出必须区分：

```text
activity completed / cancelled
user-selected strategies
observed response features
```

不得自动生成疾病诊断、复吸概率、治疗成功率、人格特征或“配合度”标签。
