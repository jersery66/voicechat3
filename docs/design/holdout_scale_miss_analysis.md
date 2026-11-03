# 留出集量表命中 0/5 的逐层归因分析

日期：2026-11-03 ｜ 基线：`ed2fd5c` ｜ 证据：`docs/evaluation/holdout-2026-11-02/`
（含首轮盲测 `holdout-tacit-zh.json`、标注修订后复跑 `holdout-tacit-zh-final.json`、
逐轮模型内部量 `scale_miss_diagnosis.json`）

所有材料为仓库内合成基准轨迹（`scripts/benchmarks/cases/`），不含真实参与者身份信息或
未经授权的访谈原文。

---

## 结论（先行）

**Tacit 量表命中 0/5 既不是校验器过严，也不是 TurnPolicy 约束，主要原因是模型自身的
决策质量：3 例是模型没有在窗口内提出建议（A），1 例是模型提议与自选目标自相矛盾被
校验器拒绝（B），1 例是有效邀请发出在窗口前一轮且同意流程未完成（D）。**
输入与评估方法经逐轮核查未发现影响结论的问题；本轮发现并修复 1 处指标归因缺陷
（把 3 轮"接受既有邀请"误计入"被拦截"），行为层无代码错误。

| 层面 | 判定 |
| --- | --- |
| 模型没有提出建议（A） | **主因，5 例中 3 例**（HOLD-02/05/10 部分） |
| 校验器拒绝（B） | 1 例主因（HOLD-06），根源仍是模型输出自相矛盾，校验器行为正确 |
| TurnPolicy 拒绝（C） | 0 例主因；所有拦截均符合设计（min-rounds、风险门） |
| 同意/执行未完成（D） | 1 例主因（HOLD-01），且邀请恰在窗口前一轮发出 |
| 输入/评估问题（E） | 0 例；逐轮核查未发现标注泄漏、状态错建或评分错误 |

---

## 一、Git 状态（任务一）

- `ed2fd5c` 父提交 `fbe8e8d`，**本身就是 `exp/agent-jev-decision` 的 HEAD**，无需整合。
- 提交包含 `conversation/interview_state.py`、`decision/request_builder.py`、
  两项修复（会话级风险门 `risk_in_session` + "完成量表"续答
  `force_resume_incomplete_scale`）及全部新增测试；测试套 1668 = 1628 + 40，
  其中四个相关测试文件收集 64 项（含此前已有的 timing 测试）。

## 二、五个失败案例的逐层归因（任务二）

判级窗口为 5 个 certain 量表窗口（HOLD-04/07 为 uncertain，单独计，不计入 0/5）。

| 案例 | 窗口 | 模型行为（关键轮概率） | 校验器 | 策略 | 归因 |
| --- | --- | --- | --- | --- | --- |
| HOLD-01 | 6–8 | R3 起每轮都提 offer_scale（0.40→0.81）；R5 (0.626, 选 PHQ-9) 形成邀请 `scale-5-1`，**早窗口 1 轮** | R3/R5 通过；R6–R9 `offer_already_pending` | R3 min-rounds 拦（合理）；R7/R9 用户应答判为模糊 → 澄清 | **D**：有效邀请已在窗口前发出，窗口内 pending 未被接受，同意流程未完成 |
| HOLD-02 | 12–13 | **13 轮从未提 offer_scale**；放松提议 0.65→0.98，R8 发出放松邀请、R11 接受完成；R12–13 放松已完成仍以 0.90/0.57 提放松 | `relaxation_already_done` 拒 | — | **A**：模型没有提出量表建议；放松完成后也不会切换到量表 |
| HOLD-05 | 10–11 | R4 提 offer_scale（0.567, PHQ-9）通过校验但早于 min-rounds 被拦；R5–R11 转向 continue/clarify（0.45–0.72），窗口内 R10=0.45 continue、R11=0.61 continue | R4 通过，其余弃权/通过 | R4 min-rounds 拦（合理） | **A**：模型没有在窗口内提出建议（R4 早期提议属正确拦截） |
| HOLD-06 | 9–10 | R3/5/6/8/9 五次提 offer_scale（0.36–0.70），但"选哪个量表"追问**每次都答"暂无量表"** | 5 次全部 `missing_scale_name` 拒 | — | **B**：模型提议与自选目标自相矛盾，校验器按"无量表名的提议不是提议"降级为 chat——防捏造设计，非过严 |
| HOLD-10 | 5–6, 9–10 | R4 提议通过校验但早于 floor 被拦（合理）；R5 提议 0.422 弃权；R6/8/9/10 提议但自选"暂无量表" | `below_abstain_threshold` 1 次 + `missing_scale_name` 4 次 | R4 min-rounds 拦 | **A+B 混合**：窗口 1 败于弃权，窗口 2 败于自相矛盾 |

补充观察（不改判归因）：

- **HOLD-01 的邀请不是"错过"而是"未闭环"**：R5 邀请合法（floor=5 轮），R6–R8 逐轮
  置信度 0.72–0.81、目标 PHQ-9，链路完全愿意继续；失败在参与者应答两轮被判
  `offer_answer_ambiguous`（同意解析刻意保守，属安全侧）。
- **HOLD-02 暴露的模型缺陷最有信息量**：gold 语义是"先放松、放松完成后再提量表"，
  模型完成放松后两轮仍以高置信度提放松（0.90）——它不理解"放松已完成"这一状态
  变化对干预选择的影响，尽管该状态明确写在 state block 里。
- **HOLD-06/10 的矛盾模式一致**：intervention 头选 offer_scale、scale_choice 头选
  "No scale for now"，两个读出没有相互约束。校验器把这个矛盾降级为 chat 是防御
  捏造的正确行为。

## 三、62 项提议的拦截分布（任务三）

62 项提议轮（offer_scale 29 / offer_relaxation 33）→ **53 被拦 + 6 新邀请成立**；
另有 3 轮是对既有邀请的接受处理（见下方"本轮修复"）。117 轮全量：

| 类别 | 次数 | 层 | 判定 |
| --- | --- | --- | --- |
| 模型输出异常（提议与自选目标矛盾） | 12 | 校验器 `missing_scale_name` | **模型决策问题**；校验器防捏造行为正确 |
| 置信度不足（argmax<0.55 弃权） | 37 全轮 / 8 在提议轮 | 校验器 `below_abstain_threshold` | 模型弃权；合理 |
| 重复建议（已有 pending 邀请） | 9 | 校验器 `offer_already_pending` | 合理拦截（防轰炸） |
| 任务正在进行（量表作答中） | 8 | 校验器 `state_conflict_active_scale` | 合理拦截（HOLD-08/09 测评中轨迹） |
| 放松已完成仍提议 | 2 | 校验器 `relaxation_already_done` | 合理拦截（暴露模型不懂状态） |
| 既往拒绝冷却 | 0 | — | 本轮未触发 |
| 用户意愿冲突 | 0 | — | 本轮未出现（同意解析保守属 HOLD-01 的 D 类） |
| 早于 min-rounds floor | 16 | 策略 `before_min_rounds` | 合理拦截（floor 设计） |
| 风险状态 | 1 提议轮 / 11 全轮 | 策略 `offer_blocked_risk` | 合理拦截（安全门） |

**未发现为提高命中率而放松的安全拦截**；校验器与策略的 17 类拒绝理由本轮全部
对应到明确的状态事实。模型问题与合理拦截之外，唯一缺陷是指标层（下节）。

### 本轮代码修复（指标归因，非行为）

`decision/timing_metrics.py`：接受处理轮（executed=recommend_relaxation /
start_scale）此前因带 `validate_reason="offer_already_pending"` 被计入
`validator_blocked`，导致报告"62 提议 56 次被拒"。实际**53 次被拒、6 次成立新邀请、
3 轮完成既有邀请**。已修复并补回归测试；文档同步改为 53/85.5%。

## 四、`risk_in_session` 持续性审查（任务四）

**当前行为（有留出集实证 + 代码定位）**：

1. **不会只阻断一轮**：置位后每一轮都进入 Priority 0 门
   （`conversation/turn_policy.py:97`），HOLD-07 R4 披露后 R4–R9 连续 6 轮
   `offer_blocked_risk`。
2. **不会自动解除**：`services/pipeline.py` 中 `_risk_disclosed` 只有置位点
   （R1625）与会话重置点（R523），会话内无任何解除路径；评估器侧同构
   （`decision/timing_eval.py` world.risk_disclosed）。
3. **作用范围比"主动提议"大**：门位于显式量表请求分支（R136 `user_scale_request`）
   **之前**，因此置位后**参与者自己主动要求填量表也会被转入 chat**。这是有意设计
   （风险对话中不该切换活动），但意味着一次披露 = 本会话内所有评估/放松通道关闭，
   包括用户发起的。

**受审计的状态转换方案（建议，本轮不实施）**：

```
risk_state: NONE ──detect_risk_marker──▶ DISCLOSED ──safety_resolved──▶ RESOLVED
                ▲                          │  （模型输出永远不能触发）
                └──────── session reset ───┘
```

- 只有 `DISCLOSED → RESOLVED` 一条解除边，触发器必须是**安全路径自身的完成事件**
  （危机干预流程按协议走完、记录处置结论），带四元组审计记录
  （`reason_code / actor / round / evidence_ref`）写入决策日志；
- 模型输出、置信度、对话流畅度等一切推断信号**不得**触碰该状态；
- `RESOLVED` 状态下普通任务恢复，但风险词检测仍逐轮独立生效（现状保留）；
- 披露 N 轮后仍无处置事件时，报告层应把"未处置的开放披露"作为会话摘要的显式字段
  呈现给人工审核，而不是由系统默默继续。

## 五、评估证据固定（任务五）

1. **原始结果与修订后结果均已入库留档**：`docs/evaluation/holdout-2026-11-02/`
   （首轮盲测、标注修订后复跑、replay 自检、规则基线、五案例逐轮诊断）。
   两次模型运行的 117 轮逐轮决策已验证零差异，修订只影响评分分母。
2. **留出集正式标记为已使用评估集**：案例文件头部与
   `timing_benchmark.py` 的 SPLIT_FILES 注释均改为 `USED EVALUATION SET`——
   Tacit 决策层已在每条轨迹上被观察过，任何后续比较不得再把它当全新留出集；
   仅用于回归与取证。
3. **下一轮新模型/新策略的正式比较必须使用新写的、写完即冻结的独立轨迹文件**，
   评分前不得在该文件上做过任何运行或标注修订。

## 附：本轮改动清单

- `decision/timing_metrics.py`：修复接受处理轮被误计入 `offer_blocked` 的归因缺陷。
- `tests/test_timing_metrics.py`：补回归测试（26 项全过）。
- `tests/test_session_safety_gate.py`：其内联加载器补 `#` 注释行跳过。
- `scripts/benchmarks/cases/interview_trajectories_holdout.jsonl`：加 USAGE 状态头。
- `scripts/benchmarks/timing_benchmark.py`：SPLIT_FILES 注释同步。
- `scripts/benchmarks/diagnose_scale_misses.py`（新增，诊断工具）：重跑指定轨迹并
  抓取四动作概率、scale_choice 追问应答、校验/策略/账本全链路，输出匿名化 JSON。
- `docs/design/interview_state_input_report.md`：56→53 及对应百分比同步。
- 未改动：提示词、阈值、标准答案、风险门、min-rounds、同意解析。
