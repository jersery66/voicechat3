# 首批六个支持活动规格

状态：**FUTURE DESIGN / 只设计六个，不实现**

所有活动都遵守：推荐不等于开始、必须 opt-in、可退出、无羞辱反馈、无诊断或
疗效结论、LLM 不能改变步骤/结果、ActivityRuntime 是唯一活动状态写入者。

## 1. 诱因侦探 — `trigger_detective`

| 项目 | 设计 |
|---|---|
| 目标 | 识别人、地点、时间、情绪、身体状态、想法和事件等可能诱因 |
| 非目标 | 不生成复吸概率、不判断危险等级、不诊断 |
| Agent trigger | `trigger_awareness >= 0.65`，stage 为 EXPLORATION/RECOVERY |
| 排除 | HIGH load、active scale 普通回答、已完成/cooldown |
| load/stage | LOW/MODERATE；EXPLORATION/RECOVERY |
| 主动推荐 | 可，必须先邀请 |
| opt-in | 必须 |
| 时长 | 约 5 分钟 |
| UI | 分类卡片 + 用户自由补充 |
| Runtime steps | 选择触发类别 → 选择具体线索 → 补充情境 → 确认 |
| LLM 权限 | 自然追问、反映和总结用户原话；不命名风险级别 |
| 退出 | 任一步 `CANCELLED`，回聊天 |
| 数据 | `identified_triggers[]`, `primary_trigger`, `context_notes` |
| 完成 | 用户确认至少一个线索或主动退出 |
| 禁止结论 | 复吸概率、人格标签、治疗效果 |
| 示例 | “以前跟那些朋友出去就容易……” → “更像是人物、地点，还是当时的情绪？” |
| Scale/Session | 不改 ScaleRuntime；SessionEngine 只处理活动生命周期 |

## 2. 拒绝挑战 — `refusal_rehearsal`

| 项目 | 设计 |
|---|---|
| 目标 | 语音角色扮演：明确拒绝、退出高风险情境、寻求支持 |
| 非目标 | 不是对错考试，不产生意志力或人格评分 |
| Agent trigger | `refusal_skill >= 0.70`，stage EXPLORATION/RECOVERY |
| 排除 | HIGH load、即时稳定需要高、用户不接受语音角色扮演 |
| load/stage | LOW/MODERATE；EXPLORATION/RECOVERY |
| 主动推荐 | 可 |
| opt-in | 必须 |
| 时长 | 约 5 分钟 |
| UI | 情境卡 + 语音输入 + 退出按钮 |
| Runtime steps | Level 1 普通邀请 → Level 2 关系压力 → Level 3 持续劝说 |
| LLM 权限 | 扮演熟人、自然反馈、MI 反思；不能升级 level 或宣布通过 |
| 退出 | 任意时刻取消并回聊天 |
| 数据 | `scenario_id`, `level`, `responses[]`, `observed_response_features` |
| 完成 | Runtime 收到最后一步回答并写入完成事件 |
| 允许特征 | `clear_refusal`, `ambiguous_opening`, `exit_strategy`, `support_seeking` |
| 禁止结论 | `willpower_score`, `relapse_probability`, `personality_score` |
| 示例 | “朋友叫我的话不好意思拒绝。” → 用户选择是否练习一句拒绝表达 |
| Scale/Session | 不改量表分；SessionEngine 只处理 start/finish/cancel |

## 3. 我的应对工具箱 — `coping_toolbox`

| 项目 | 设计 |
|---|---|
| 目标 | 让用户选择本人愿意使用的替代应对策略 |
| 非目标 | 不替用户决定唯一正确策略，不保证效果 |
| Agent trigger | `coping_skill >= 0.65`，stage EXPLORATION/RECOVERY |
| 排除 | HIGH load 时只允许极简版本；active scale 普通回答不打断 |
| load/stage | LOW/MODERATE；EXPLORATION/RECOVERY |
| 主动推荐 | 可 |
| opt-in | 必须 |
| 时长 | 约 5 分钟 |
| UI | 策略卡片、多选、用户自定义 |
| Runtime steps | 选择困扰 → 浏览策略 → 选择/删除 → 保存个人计划 |
| LLM 权限 | 解释卡片和反映理由；不能指定唯一计划 |
| 退出 | 取消不保存未确认选择 |
| 数据 | `coping_plan: {distress:[], sleep:[], craving:[]}` |
| 完成 | 用户确认至少一个自选策略 |
| 禁止结论 | “最佳策略”、治疗成功、依从性评分 |
| 示例 | “烦的时候不知道干嘛。” → 用户从散步、离开环境、联系支持等卡片选择 |
| Scale/Session | 不改 ScaleRuntime；SessionEngine 只管理生命周期 |

## 4. 岔路口 — `crossroads`

| 项目 | 设计 |
|---|---|
| 目标 | 练习高风险情境中的选择、后果反思和应对计划 |
| 非目标 | 不是分数考试，不把选项量化成正确率 |
| Agent trigger | `recovery_planning >= 0.65`，stage RECOVERY |
| 排除 | HIGH load、无用户兴趣、active scale 普通回答 |
| load/stage | LOW/MODERATE；RECOVERY |
| 主动推荐 | 可 |
| opt-in | 必须 |
| 时长 | 约 7 分钟 |
| UI | 情境卡、路径选择、反思文本 |
| Runtime steps | 展示情境 → 用户选路径 → consequence reflection → alternative consideration |
| LLM 权限 | 自然描述情境、追问反思；不能给 A/B/C 评分或替用户选 |
| 退出 | 任何步骤退出回聊天 |
| 数据 | `scenario_id`, `choice`, `consequence_reflection`, `alternative` |
| 完成 | 用户完成一次选择与反思，或取消 |
| 禁止结论 | “正确选择”、复吸概率、人格/治疗评价 |
| 示例 | 旧朋友联系 → 用户自己讨论各选择可能带来的结果 |
| Scale/Session | 不触碰量表分；结束由 SessionEngine command 处理 |

## 5. 十分钟缓冲 — `ten_minute_buffer`

| 项目 | 设计 |
|---|---|
| 目标 | 强烈渴求/冲动时提供低认知负荷的短暂缓冲 |
| 非目标 | 不声称治疗有效，不把前后 rating 当疗效指标 |
| Agent trigger | `craving_coping >= 0.65` 或 `immediate_stabilization >= 0.65` |
| 排除 | 用户不接受、资源缺失、已有 activity active |
| load/stage | HIGH/MODERATE；STABILIZATION 优先 |
| 主动推荐 | 可，但必须邀请 |
| opt-in | 必须 |
| 时长 | 最多约 10 分钟，可提前退出 |
| UI | pre-rating、呼吸/grounding、简短视觉注意任务、post-rating |
| Runtime steps | `pre_rating 0–10 → brief breathing → grounding → visual task → post_rating` |
| LLM 权限 | 低负担引导语言；不能解释疗效或判定成功/失败 |
| 退出 | 任何时刻回聊天；记录 `CANCELLED` |
| 数据 | `pre_rating`, `post_rating`, `completed_steps`, `cancel_reason` |
| 完成 | Runtime 完成步骤或用户主动退出 |
| 禁止结论 | “治疗有效”、失败、风险下降 |
| 示例 | `8 → 6` 只能描述“当前主观强度有所变化”；`8 → 9` 不得说失败 |
| Scale/Session | 可由 Policy 决定 pause scale；resume 由 ScaleRuntime 推导 first unanswered item |

## 6. 改变天平 — `change_balance`

| 项目 | 设计 |
|---|---|
| 目标 | 探索继续原有生活方式与做出改变各自得到/失去什么 |
| 非目标 | 不替用户作决定，不总结“因此你应该戒毒” |
| Agent trigger | `change_motivation >= 0.65` + LOW/MODERATE + RECOVERY |
| 排除 | HIGH load、尚未建立关系、用户不愿讨论改变 |
| load/stage | LOW/MODERATE；RECOVERY |
| 主动推荐 | 可 |
| opt-in | 必须 |
| 时长 | 约 7 分钟 |
| UI | 两列卡片/自由输入，全部内容来自用户 |
| Runtime steps | 选择主题 → 记录继续/改变两侧 → 标记个人重要价值 → 反思 |
| LLM 权限 | MI 风格反映和开放问题；不能给建议或结论 |
| 退出 | 取消不生成完整 balance result |
| 数据 | `change_balance_entries`, `personally_salient_values` |
| 完成 | 用户确认至少一项两侧内容或主动退出 |
| 禁止结论 | 成功率、复吸风险、动机分数、治疗建议 |
| 示例 | “你刚才放进去最多的是家人、工作和自由，哪一样现在最重要？” |
| Scale/Session | 不改量表；SessionEngine 只管理活动开始/结束 |

所有六个模块的 `evidence_status` 初始只能是 supportive skill practice 或
experimental，不能宣传为已验证治疗。
