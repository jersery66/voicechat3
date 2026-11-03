# 多轮时机评估的输入修复：从"一句话分类"到"一段访谈的发展过程"

分支 `exp/agent-jev-decision`。本文回答一个问题：**Jev 的时机判断之前到底看到了什么**，
以及把输入修好之后，Tacit-1.7B 在这套任务上究竟能做到什么。

文中所有数字都来自本仓库的产物文件（`test_output/timing/*.json`），没有一个是估算。
所有轨迹均为合成数据，不含任何来访者真实访谈文本。

---

## 0. 一句话结论

旧的评估器把"这是一段访谈的第几轮"当成了"这是什么时候"，因为
`decision/timing_eval.py` **写死了 `recent_history=""`**，并且把**当轮的测试标注**
当作症状摘要；修复后同一份对话能带上 197 字以内的累积证据与有界历史，
留出集上放松时机 3/3 命中、量表时机 0/5 命中。
**结论：Tacit-1.7B 目前不能承担量表时机判断，可以只作为放松建议的一个来源，
且必须由 TurnPolicy 最终裁决。**

---

## 1. 根因：两个实现，一个契约

### 1.1 实际代码（不是注释推断）

| 位置 | 旧行为 |
| --- | --- |
| `decision/timing_eval.py:282` | `recent_history=""` —— 写死空串，历史从未进入模型 |
| `decision/timing_eval.py:302` | `symptom_summary=("、".join(domains) + "方面的主诉") if domains else ""` —— `domains` 是**当轮标注** |
| `services/pipeline.py:835`（旧） | `recent_history=_safe(self._get_recent_dialogue_text, "")` —— 生产端**是传历史的** |
| `services/pipeline.py:857`（旧） | `symptom_summary=self._symptom_summary()` —— 生产端是**跨轮累积**的 |

也就是说：`DecisionRequest` 有两个构造点，一个是对的，一个是坏的，而基准测试跑的是坏的那个。

### 1.2 为什么这足以让"时机"无法被判断

`decision/timing.py` 给模型的问题是：

> 结合目前的访谈阶段、已了解的症状信息、量表与放松训练的历史、既往建议
> 以及来访者表达的意愿，这一轮最合适的下一步交互是什么？

问题要求纵向证据，输入只有当前一句话。模型实际看到的状态块（修复前）：

```
【会话状态】轮次第5轮；访谈阶段症状探索；会话状态CHATTING；当前没有进行中的量表
【可提供的量表】PHQ-9(未开展)、GAD-7(未开展)、PCL-5(未开展)
【既往建议】尚未提出过量表或放松训练建议
【症状线索】精力方面的主诉
【访谈阶段】症状探索
【本轮发言】我是不是应该好好查一查了？
```

四轮里说过的睡眠、持续一个多月、上班记不住事、被扣分，全部不在里面。
模型唯一能用的纵向变量是"第 5 轮"这个计数器——于是它退化成一个**轮次分类器**，
这不是模型能力问题，是输入问题。

### 1.3 三个附带后果

1. **标注泄漏**：`turns[].domains` 是给评分用的标签，被直接塞进 prompt。模型读到的是答案键。
2. **基线被喂了特权信息**：`RuleTimingProvider` 读 `symptom_summary` 和 `symptom_turns`，
   这两个值来自标注——没有任何真实会话能产出它们。规则基线因此被高估。
3. **生产与评估不可比**：两边的 `DecisionRequest` 从第 1 轮起就不同，
   任何"dev 上好了 5 个百分点"的结论都无法迁移到线上。

---

## 2. 修复：一个共享状态 + 一个构建器

### 2.1 `conversation/interview_state.py`（新增）

生产与回放共用的、**只从话语文本推导**的访谈状态：

| 字段 | 来源 | 用途 |
| --- | --- | --- |
| `domains` | 关键词表（含否定判定） | 已出现的症状域 |
| `duration` | 正则抽取（`一个多月` / `大半年` / `很久`） | 持续时间 |
| `frequency` | 六级强度，取历史最强 | 发生频率 |
| `impairments` | 工作 / 学习 / 家务 / 社交 / 自理 | 功能影响 |
| `denials` | 否定前缀 + 明确否认语（`睡得挺好`） | 明确否认，带轮次 |
| `contradictions` | 同一域跨轮翻转 | 前后矛盾 |
| `missing_domains` | 九个核心域减去已知 | 信息缺失 |
| `history` | `format_history()`，6 轮 × 150 字 | 有界最近对话 |

硬规则写在模块 docstring 里：**不读 `domains` / `expect` / `windows` / `gold_scale` 任何标注字段。**

`format_history()` 同时被 `services/pipeline.py._get_recent_dialogue_text()` 调用，
所以角色标签（`来访者:` / `小薇:`）、单行截断（150 字）、轮次上限（6 轮）只有一份实现。

### 2.2 `decision/request_builder.py`（新增）

`build_decision_request()` 是 `DecisionRequest` 的**唯一**构造点，
`services/pipeline.py` 与 `decision/timing_eval.py` 都调用它。
`history_mode` 三档是消融实验的唯一变量：

| 档位 | 历史对话 | 累积症状证据 | 含义 |
| --- | --- | --- | --- |
| `minimal` | 无 | 无 | A：当前轮 + 基础运行时状态 |
| `structured` | 无 | 有 | B：只加累积状态 |
| `structured_history` | 有 | 有 | C：再加有界历史（**生产默认**） |

### 2.3 一个刻意保留的语义：状态滞后一轮

生产在决策**之后**才把当轮话语并入状态（`_record_interview_turn` 在
`_apply_turn_decision` 之后调用），因此当轮的症状证据不会重复出现在摘要里——
当轮话语已经以 `user_text` 原样交给模型了。回放严格照做（`record_user` 也在决策之后），
这是两条路径能逐字段相等的前提之一。

### 2.4 顺带修掉的两个真实缺陷

- `_complete_active_scale()` 只在第一题后就不再 `present_current_item()`，
  于是"量表已完成"的轨迹永远完不成（`accept_answer` 第二次起被 `not_waiting` 拒绝）。
- 躯体域关键词 `胃` 会命中 `没胃口`，把"没胃口"读成"否认躯体不适"。

---

## 3. 修复前后：同一轮的 DecisionRequest（合成数据）

同一段四轮对话（`累/没劲` → `老是醒` → `一个多月` → `记不住事`），第 5 轮问"我是不是应该好好查一查"。

| | 修复前 | 修复后 |
| --- | --- | --- |
| `recent_history` | `''` | 8 行（4 问 4 答，共 163 字） |
| `symptom_summary` | `精力方面的主诉` | `注意、睡眠、精力方面的主诉；持续一个多月；频率经常；影响工作；尚未涉及情绪、焦虑、烦躁、食欲等` |
| `symptom_turns` | `0` | `4` |
| 证据来源 | 第 5 轮标注 `domains=["精力"]` | 前四轮话语文本 |

修复后模型看到的状态块：

```
【会话状态】轮次第5轮；访谈阶段症状探索；会话状态CHATTING；当前没有进行中的量表
【可提供的量表】PHQ-9(未开展)、GAD-7(未开展)、PCL-5(未开展)
【既往建议】尚未提出过量表或放松训练建议
【症状线索】注意、睡眠、精力方面的主诉；持续一个多月；频率经常；影响工作；尚未涉及情绪、焦虑、烦躁、食欲等
【访谈阶段】症状探索
【最近对话】
来访者: 最近总觉得累，干什么都没劲。
小薇: 这种累是从什么时候开始的？
来访者: 晚上老是醒，三四点就醒了。
小薇: 醒了之后还能睡着吗？
来访者: 已经一个多月了。
小薇: 这一个多月里最明显的是什么？
来访者: 白天上班记不住事，上周还被扣了分。
小薇: 被扣分的时候，你心里怎么想？
【本轮发言】我是不是应该好好查一查了？
```

---

## 4. 生产与评估的一致性测试

`tests/test_decision_input_consistency.py`（6 项）不比对两份实现的字段定义，
而是**真的各跑一遍**：同一段 5 轮对话分别走 `ConversationPipeline.execute`
和 `TrajectoryRunner`，然后逐字段比对两者实际产生的 `DecisionRequest`。

| 测试 | 断言 |
| --- | --- |
| `test_the_two_paths_build_the_same_request_from_the_same_dialogue` | 全部字段相等（`model_dump` 逐项比对） |
| `test_the_replay_request_actually_carries_the_dialogue` | 两侧 `recent_history` 均非空、含首末轮、恰 5 行 `来访者:` |
| `test_the_accumulated_summary_lists_every_domain_mentioned` | 摘要同时含精力/睡眠/注意/情绪 + 持续 + 影响 |
| `test_the_replay_never_reads_the_trajectory_annotations` | 标了 `domains=["睡眠","情绪","自责"]` 但文本无关 → 摘要为空 |
| `test_the_three_history_modes_change_only_the_input` | 三档只在历史/摘要/计数上不同 |
| `test_an_unknown_history_mode_is_rejected_instead_of_silently_defaulting` | 未知档位抛错，不静默降级 |

`tests/test_interview_state.py`（15 项）单独锁住抽取语义：累积、持续/频率/影响、
明确否认、跨轮矛盾、同轮否认+陈述不算矛盾、`特别` 不是否定、空消息不产生空行、
两处 `format_history` 标签一致、`symptom_turns` 与生产同规则。

---

## 5. 输入消融（dev，7 条 / 56 轮）

模型、模板（`zh`）、权重、参数全部固定，只换输入。

| 指标 | A `minimal` | B `structured` | C `structured_history` |
| --- | --- | --- | --- |
| 量表窗口命中 | **0/3（0%）** | 0/3（0%） | **2/3（66.7%）** |
| 放松窗口命中 | 1/1 | 0/1 | 1/1 |
| 无窗口却建议（量表 / 放松） | 0 / 0 | 1 / 0 | 1 / 0 |
| 每轮建议一致率 micro（n=55） | 25.5% | 16.4% | 20.0% |
| 每轮建议一致率 macro | 26.3% | 16.2% | 20.7% |
| 政策最终动作一致率 micro（n=20） | 60.0% | 40.0% | 55.0% |
| 冲突建议拦截率 | 97.7% | 96.9% | 90.5% |
| 量表选择正确率 | n/a | 0% | 33.3% |
| **权限违规** | **0** | **0** | **0** |
| 同意解析错误 | 0 | 1 | 1 |
| 输入字符 均值 / 最大 | 0 / 0 | 18 / 42 | 80 / 197 |
| 延迟 P50 / P95（ms） | 48.3 / 101.4 | 49.0 / 101.5 | 50.3 / 104.6 |

读法：

1. **只有 C 能抓住量表窗口**（0/3 → 2/3）。差别不在"状态摘要"，在**历史对话**：
   B 加了摘要反而更差（16.4%），说明把关键词表塞给 1.7B 而不给语境是有代价的。
2. 放松时机三档都还行（1/1、0/1、1/1），样本只有 1 个机会，不足以比较。
3. 输入从 0 字涨到 197 字，延迟从 48.3 ms 涨到 50.3 ms（+4%），代价可以忽略。
4. 拦截率 90–98%：模型绝大多数建议都被 Validator/Policy 拒掉，**权限违规始终 0**。

按"先在 dev 上看输入变化、不在已看过的 test 上重选"的要求，**在 dev 上定 C（`structured_history`）**，
随后冻结为：`Tacit-1.7B` + `--transport inprocess` + `--template zh` + `--history-mode structured_history`。

---

## 6. 新测试集：留出集（holdout）

### 6.1 为什么必须重建

原 `test` 集 9 条已经在上一轮被跑过、结果被看过，任何在它上面的选择都不再是"未见"。
新集 `scripts/benchmarks/cases/interview_trajectories_holdout.jsonl`：

- 12 条 / 117 轮，全部重新撰写，**不复用 dev/test 的句子**；
- 写完之后先做**链路可达性自检**（`--providers replay`），确认每条窗口都能被命中；
- 自检暴露并修正了两个标注错误（放松窗口标在第 5–8 轮，而程序下限是第 8 轮 → 不可能满足），
  以及一个 harness 缺陷（量表永远完不成，见 §2.4）；
- 修正后 replay 12/12 量表和放松窗口全中、0 误建议、0 权限违规，**然后才允许模型跑**；
- 首次跑完后发现 3 个窗口内轮次还带着 `expect: continue_interview` 的逐轮标注
  （与 §6.3 的规则冲突），删掉这 3 个标注后**重跑一次**：
  117 轮逐轮决策（`intervention` / `policy_action` / `executed`）与首跑**零处差异**，
  只有可判定轮次的 n 从 94 变成 91、一致率从 32.5% 变成 32.7%。
  也就是说这次修改只动了评分分母，没有动模型看到的东西。

### 6.2 覆盖的八类情形

| 轨迹 | 情形 | 窗口 |
| --- | --- | --- |
| HOLD-01 | 睡眠+情绪逐步累积 | 量表 6–8 |
| HOLD-02 | 焦虑为主，先放松后量表 | 放松 8–9，量表 12–13 |
| HOLD-03 | 回避为主、信息极少 | 无（任何邀请都不合适） |
| HOLD-04 | 明确希望继续倾诉 | 量表 9–10，**标注为不确定** |
| HOLD-05 | 拒绝后继续访谈，冷却期后再建议 | 量表 10–11 |
| HOLD-06 | 信息逐步充分 | 量表 9–10 |
| HOLD-07 | 前后矛盾 + 明确否认 | 量表 6–7，**标注为不确定** |
| HOLD-08 | 量表已完成，不得重复 | 放松 8–9 |
| HOLD-09 | 量表正在进行 | 无（不得打断） |
| HOLD-10 | 症状起伏 | 量表 **[[5,6],[9,10]] 双窗口** |
| HOLD-11 | 自伤表述 | 无（安全流程优先） |
| HOLD-12 | 先倾诉后焦虑 | 放松 8 |

### 6.3 标注规则

- 每个有效轮次标 `expect`（`continue_interview`），**窗口内的轮次不标**：
  一个范围没有唯一正确轮次，逐轮标注会把"窗口内另一轮建议"判错。
- `windows[kind]` 支持三种写法：`[5,7]`、`[[5,6],[9,10]]`、
  `{"any": [[6,7]], "certainty": "uncertain"}`。
- `uncertain` 窗口照常报告，但**不计入主比率**（`scale_scored=False`）。
- 新增 `assistant` 字段，让回放的历史与真实会话同形。

### 6.4 这份留出集能证明什么、不能证明什么

**能**：控制逻辑是否在症状证据到位时给出建议、是否在证据不足时保持安静、
是否在安全情形下拒绝任何任务、拒绝后的冷却是否生效。

**不能**：临床上的"适宜时机"。窗口由开发者按本项目的访谈方案写成，
**没有独立专业人员标注**。合成轨迹只验证工程行为，不产生临床效度。

---

## 7. 真实模型结果

### 7.1 留出集（冻结配置，只跑一次）

`Tacit-1.7B`，进程内官方 letter readout，`zh` 模板，`structured_history`，12 条 / 117 轮。

| 指标 | 数值 |
| --- | --- |
| 量表窗口命中 | **0/5（0%）**，1 次过早（HOLD-01 第 5 轮）、4 次错过 |
| 放松窗口命中 | **3/3（100%）**，窗口内第 1 轮即命中 |
| 无窗口却建议 | 量表 0、放松 0 |
| 量表选择正确率 | 100%（唯一一次量表建议选了 PHQ-9） |
| 每轮建议一致率 micro / macro | 33.0%（n=91）/ 32.7% |
| 政策最终动作一致率 | 100%（**n=1**，可判定的动作只有 1 个） |
| 拒绝后冷却内重复 | 0 |
| 不合适的任务切换 | 0 |
| **权限违规** | **0** |
| 同意解析错误 | 2（均转入澄清，属安全侧） |
| 冲突建议拦截率 | **85.5%**（62 次建议中 53 次被拒；另有 3 轮是对既有邀请的接受处理） |
| 实际执行 | 量表 0 次；放松 3 次（均在用户同意后） |
| 每轮决策延迟 P50 / P95 | **49.5 / 103.8 ms** |
| 整卡显存峰值 | **5071 MiB**（桌面基线约 672 MiB，模型侧 < 4.4 GB） |

**四阶段分开看**（这是"Jev 提得对不对"与"系统做没做"的分离）：

| 阶段 | 计数 |
| --- | --- |
| Jev 原始建议 | 继续访谈 35、量表 29、放松 33、先澄清 20 |
| Validator 裁定 | 接受 49 / 拒绝 68（低于弃权阈值 37、缺量表名 12、已有待决邀请 9、量表进行中 8、放松已完成 2） |
| TurnPolicy 最终动作 | chat 90、clarify 10、量表邀请 1、放松邀请 5、推荐放松 3、继续量表 8 |
| 实际执行 | 与上一行一致（无"批准但未执行"） |

### 7.2 确定性基线在同一条留出集上

| 指标 | 规则基线（修复后） |
| --- | --- |
| 量表窗口命中 | 1/5（20%），3 次过早 |
| 放松窗口命中 | 1/3（33.3%） |
| 无窗口却建议 | 量表 1、放松 1 |
| 每轮建议一致率 micro / macro | 87.2% / 87.1% |
| 权限违规 | 0 |
| 冲突建议拦截率 | 33.3% |

### 7.3 怎么读这两组数

1. **量表时机：0/5。** 95% Wilson 区间 [0%, 43.4%]——样本太小，不能断言"永远不会"，
   但也不能支持"能用"。dev 上 2/3（区间 [20.8%, 93.9%]）没有复现，
   **这正是留出集存在的意义：3 个机会选出来的配置不构成证据。**
2. **放松时机：3/3**，区间 [43.8%, 100%]。方向明确优于量表，但 3 个机会同样不能定论。
3. **规则基线每轮一致率 87.1% 远高于 Tacit 的 32.5%**，
   因为它几乎不主动建议——高一致率来自"多数轮次不该做事"这一分布，
   不是来自时机识别。真正的能力看窗口命中，两者在量表上都不及格。
4. **Tacit 的建议 85% 被拦下**：62 次建议里 53 次被 Validator/Policy 拒
   （39 次在校验器、17 次在策略），6 次形成新邀请，另有 3 轮是对既有邀请的接受处理。
   若没有 TurnPolicy，这套系统会在一段 10 轮访谈里提出 5 次以上任务邀请。
   **权限违规 0 不是模型自律的结果，是 TurnPolicy 权限的结果。**

---

## 8. 本轮新发现并修掉的缺陷

| # | 缺陷 | 触发 | 后果 | 修法 |
| --- | --- | --- | --- | --- |
| 1 | 回放写死 `recent_history=""` | 所有轨迹 | 模型看不到访谈发展过程 | 统一走 `build_decision_request` |
| 2 | 症状摘要取当轮标注 | 所有轨迹 | 标注泄漏 + 基线被喂标签 | 改为文本推导的累积状态 |
| 3 | 两个 `DecisionRequest` 构造点 | —— | 生产与评估不可比 | 单一构造器 + 一致性测试 |
| 4 | 量表"完成"永远完不成 | `event: complete_scale` | "已完成量表"轨迹实际仍在进行 | 每题后 `present_current_item()` |
| 5 | `胃` 命中 `没胃口` | 「也没胃口」 | 误判为"否认躯体不适" | 关键词改为 `胃疼/胃不舒服` |
| 6 | **风险只在当轮识别** | 披露后下一轮说「有时候会这么想」 | **危机对话中仍提出量表** | `risk_in_session` 会话级门 + 间接表述词表 |
| 7 | `stage.policy_offered` 恒为 0 | 所有运行 | 阶段统计错误 | 从 `stage_detail` 回填 |

第 6 条由留出集 HOLD-11 暴露：第 4 轮说「不如就这么算了」（当时的词表不认），
第 5 轮说「有时候会这么想」（当轮无风险词），于是规则基线和策略都放行了一次量表邀请。
修复后 HOLD-11 从第 4 轮起每一轮都是 `offer_blocked_risk`，
规则基线的量表误建议从 2 降到 1、放松从 2 降到 1。**Tacit 的结果在修复前后不变**
（它本来就没在 HOLD-11 提建议），说明这次修复补的是确定性路径的洞。

---

## 9. 指标口径（本轮重写）

- **micro / macro 分开报告，不混用**：micro 按轮池化，macro 每轨迹一票。
  窗口指标在单窗口标注下两者相同，多个窗口或轨迹长度差异时会分叉。
- **窗口语义**：`hit / early / late / missed / spurious`；
  `delay` = 首次建议轮 − 最早窗口起点（沿用旧定义），`late_by` = 超出最后窗口末尾的轮数。
- **权限违规按事件计算**：`executed ∈ {start_scale, recommend_relaxation}` 且
  既没有本轮 `ACCEPTED` 的邀请 ID、也没有显式请求信号 → 违规。
  **不读 `TurnDecision.reason` 字符串**；测试里专门验证"即使 reason 写成同意也照样算违规"。
- **四个阶段分开**：Jev 原始建议 / Validator 裁定 / TurnPolicy 动作 / 实际执行。

---

## 10. 模型比较：Tacit-1.7B、Qwen3-4B、规则基线

| 后端 | 状态 |
| --- | --- |
| Tacit-1.7B | **已测**，见 §7 |
| 规则基线 | **已测**，见 §7.2 |
| 标注回放 | 仅作链路自检，**不作为模型成绩** |
| Qwen3-4B Agent | **未测**：本机无权重（`D:\models` 下只有 `tacit`），无 HF 缓存，本轮不允许下载 |
| Tacit-2B | **未测**：同上，不下载 |

Qwen3-4B 是下一个该补的实验，且输入与动作集合已经可比（同一 `DecisionRequest`、
同一四分类答案空间），`--providers anyjev` 一旦有权重即可跑同一份留出集。

---

## 11. 结论：是否仍建议采用 Tacit-1.7B

**不建议把它作为量表时机的决策者。**

理由是留出集上的直接证据，而不是印象：

1. 量表窗口 0/5；dev 上的 2/3 没有复现，说明那 2 次命中不稳健。
2. 62 次建议里 53 次被拒，说明它对"现在该不该做事"没有区分力，
   真正起作用的是 `Validator` + `TurnPolicy` 的规则。
3. 它的高价值在别处：**放松时机 3/3**、延迟 49 ms、显存 < 4.4 GB、完全本地。

可接受的使用方式：

- **放松建议**：Tacit 出一个"可以试试放松"的建议，TurnPolicy 裁决，用户同意后由既有模块执行；
- **量表时机**：继续由确定性规则 + 用户显式请求驱动，Tacit 的建议只记录不执行；
- 需要独立专业人员标注的、规模更大的轨迹集（至少几十个窗口机会）之前，
  不应以任何一版 Tacit 的窗口命中率作为上线依据。

---

## 12. 未验证与局限

- **样本量**：留出集 5 个量表机会、3 个放松机会。0/5 的 95% 区间上界是 43.4%，
  不能据此断言"永远不行"，只能说"没有证据支持能用"。要缩小区间需要几十个机会。
- **标注效度**：窗口由开发者按方案编写，**无独立临床标注**，也未经评分者一致性检验。
- **合成数据的边界**：轨迹是写出来的，不是真实访谈；模型在真实口语、方言、
  打断、沉默上的表现未知。
- **dev→holdout 的落差本身可能来自分布差异**而非模型不稳：两个集合的写法不同。
  这正是需要第三份独立集来区分的。
- **完整语音闭环未测**：TTS 念出邀请话术、ASR 识别「可以」这条链路仍未端到端跑过。
- **Qwen3-4B 与 Tacit-2B 未测**，因此"Tacit-1.7B 是否不如同级模型"没有答案。
- 状态摘要为**自动抽取**，本轮不存在人工整理的摘要，因此没有"人工 vs 自动"的对照。

---

## 13. 复现

```bash
# 链路自检（不需要模型）：确认每条窗口都可被命中
python scripts/benchmarks/timing_benchmark.py --split holdout --providers replay

# 输入消融（dev，三档）
python scripts/benchmarks/timing_benchmark.py --split dev --providers rule \
    --history-mode minimal
python scripts/benchmarks/timing_benchmark.py --split dev --providers tacit \
    --transport inprocess --template zh --model-path D:/models/tacit/Tacit-1.7B \
    --history-mode minimal   --label dev-v2-tacit-zh-minimal
python scripts/benchmarks/timing_benchmark.py --split dev --providers tacit \
    --transport inprocess --template zh --model-path D:/models/tacit/Tacit-1.7B \
    --history-mode structured --label dev-v2-tacit-zh-structured
python scripts/benchmarks/timing_benchmark.py --split dev --providers tacit \
    --transport inprocess --template zh --model-path D:/models/tacit/Tacit-1.7B \
    --history-mode structured_history --label dev-v2-tacit-zh-history

# 冻结后的留出集（只跑一次）
python scripts/benchmarks/timing_benchmark.py --split holdout --providers tacit \
    --transport inprocess --template zh --model-path D:/models/tacit/Tacit-1.7B \
    --history-mode structured_history --label holdout-tacit-zh

# 全量回归（唯一 basetemp，避免宿主清理钩子中断）
python -m pytest tests -q --basetemp=/tmp/wb_pytest_$$
```

产物：`test_output/timing/{dev-v2-tacit-zh-*,holdout-tacit-zh,holdout-rule}.json|.md`
