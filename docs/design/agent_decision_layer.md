# 结构化决策层（Jev-style Decision Layer）

> 状态：实验分支 `exp/agent-jev-decision`。默认关闭，`mode: legacy` 时行为与 RC1 逐字节一致。
>
> **目标已重定位（见 §10）**：本层最重要的职责不是给单句打动作标签，而是**在多轮访谈中判断
> 何时继续访谈、何时建议量表、何时建议放松、何时先澄清**。§4 的五类观察仍保留，
> 但 Intent/Emotion/单句 Action 的准确率**不再是主要验收标准**。

## 1. 为什么要有这一层

原实现让 Qwen Agent 「写出」一个 JSON 观测，再解析它。这条链路有三个结构性弱点：

1. **解析失败即无事发生**——模型输出格式一错，观测就丢，错误被日志吞掉；
2. **没有置信度语义**——自报的 `confidence` 是模型自己写的数字，不是可校准的概率；
3. **不可对照**——想比较两个决策模型，只能比较两段自然语言。

Jev-style 决策换了一个读法：不问模型「你想做什么」，而是**读出模型在标签续写上的概率分布**。
这样输出天然是概率向量，可校准、可对照、可设阈值弃权。

## 2. 权限边界（本层最重要的约束）

```
用户发言
   │
   ▼
DecisionProvider  ──►  RawDecision（纯数据，不可执行）
   │                    intervention（主）/ emotion / intent / complex_review（辅）
   ▼
DecisionValidator ──►  接受 / 弃权（永不改写动作语义）
   │
   ▼
AgentObservation     （仍然不可执行；offer 字段表明「这是邀请，不是执行」）
   │
   ▼
TurnPolicy.decide()  ──►  TurnDecision（唯一可执行产物）
   │
   ▼
SessionEngine / ScaleRuntime
```

**五个阶段必须分开**，任何一层都不得跳步：

| 阶段 | 产物 | 谁负责 |
| --- | --- | --- |
| 1 建议 | `intervention`（四选一） | DecisionProvider（模型） |
| 2 审批 | 接受 / 弃权 + 时机闸门 | `DecisionValidator` |
| 3 邀请 | `TurnAction.OFFER_SCALE` / `OFFER_RELAXATION` | `TurnPolicy` |
| 4 同意或拒绝 | `offer_accepted` / `offer_declined` / 未答复 | 参与者（`OfferLedger` 记账） |
| 5 执行 | `START_SCALE` / `RECOMMEND_RELAXATION` | `TurnPolicy` + `ScaleRuntime` |

`OFFER_SCALE ≠ START_SCALE`，`OFFER_RELAXATION ≠ 已同意并开始训练`。

**决策层不拥有任何权限**：

| 能力 | 归属 |
| --- | --- |
| 决定下一步动作 | TurnPolicy（决策层只给「建议」） |
| 启动/继续/暂停量表 | TurnPolicy + ScaleRuntime |
| 结束会话 | TurnPolicy；`end_session` 在 validator 层恒被拒绝 |
| 诊断、风险排除、量表评分 | 不属于本层，永不进入 |
| 复杂评估 | `ClinicalReviewService` 接口，默认关闭，且强制人工复核 |

`RouterAction` 只有 5 个值（`chat` / `start_scale` / `recommend_relaxation` / `recommend_game` / `end_session`），
决策模型无法表达第 6 种动作，也无法携带量表分数。

## 3. 三种模式

| mode | 行为 | 状态写入 | 用途 |
| --- | --- | --- | --- |
| `legacy` | 完全走原 Qwen Agent；决策层直通 | 与原实现一致 | 线上默认 |
| `shadow` | Qwen 照常决策；另跑一个决策模型做**对照记录**，不影响任何结果 | 只写日志/JSONL 元数据 | 上线前评估 |
| `jev` | 决策模型产出观测，经 validator 后交给 TurnPolicy | 经 TurnPolicy 走原路径 | 实验 |

`shadow` 的失败被完全隔离：provider 报错只记一行日志，回合继续。
`jev` 的失败按 `escalation_mode` 回退：`legacy`（默认，回退到 Qwen Agent）或 `deterministic`（关键词兜底，不再调用模型）。

## 4. 结构化观察（主问题 + 辅助问题）

| 问题 | 标签空间 | 下游含义 |
| --- | --- | --- |
| **intervention（主）** | continue_interview / offer_scale / offer_relaxation / clarify_first | 决定本轮该做什么，见 §10 |
| emotion（辅） | calm / sad / anxious / angry / irritable / positive / uncertain | 映射到报告用的情绪字符串 |
| intent（辅） | chat / symptom / emotion / help_seeking / scale / relaxation / game / end / unclear | 映射到既有 4 值 `counseling/entertainment/chitchat/relaxation` |
| complex_review（辅） | no / yes | 建议转入更深入评估（只记录） |

历史五分类中的 `action`（5 个 `RouterAction`）**降级为 `intervention` 的路由投影**：
由 `intervention_to_router_action()` 算出，而不是由模型自报。
`clarify` 同理，由 `intervention == clarify_first` 推出。这样模型少一个可以自相矛盾的自由度。

## 5. 读出（readout）机制

> **先读这一条**：本节描述的自建 readout **不是**官方 Tacit 的推理语义，
> 不能宣称与 Tacit 等价，也不能把它的输出当作 Tacit 结果报告。
> 官方契约与本项目读法的逐项差异见 §5.1，实测差距见 §5.2。
> 部署主路径是官方 `anyjev.Tacit`（`decision/providers/tacit.py`），
> 自建 readout 保留为**可选实验方法**。

`decision/engine/readout.py` 实现两种读法，配置 `mode` 选择：

- `next_token`（默认首选）：prompt 以「答案：」结尾，取 next-token 分布，只在各选项标签的 token 上做**受限 softmax**。
  这是最贴近「读分布」的形态，一次前向。
- `candidate`：把每个标签当作续写，分别求 log P(label | prompt)，再 softmax。标签不是单 token 时自动回落。
- `auto`：优先 `next_token`，遇到多 token 标签按题回落 `candidate`（记录在 `notes`）。

注意 `next_token` 读的是**选项标签字符串本身**（如 `" chat"`、`"start_scale"`）的 token 概率，
而不是官方那样读**位置字母** A/B/C 的概率。两者是不同的估计量。

**选项顺序偏差**：`rotations > 1` 时对选项列表做循环置换，把概率映射回原标签后平均。
`tests/test_decision_readout.py::test_rotations_average_over_presentation_order` 用一个「永远偏好第一个选项」的
假 scorer 证明了偏差被抵消。但平均轮次并不等于官方 L0：L0 还会**除掉标签先验**
（batch prior 或 content-free prior），本项目没有这一步。

### 5.1 与官方 AnyJev 0.3.0 契约的差异（逐项核对 2026-11-02）

官方源码：`anyjev/readout.py`、`anyjev/tacit.py`、`anyjev/decider.py`；
Tacit-1.7B 自带 `tacit_decision.py` 与仓库实现一致。

| 项目 | 官方 Tacit `one_forward` | 本项目 `Readout` |
|---|---|---|
| prompt 渲染 | `apply_chat_template(add_generation_prompt=True, enable_thinking=False)`，带 system | 纯文本拼接，无 chat template、无 system |
| prompt 内容 | `State:` / `Question:` / `Options:` / `Answer with the letter only.` | `【会话状态】…` / `A. 选项` / `答案：` |
| 读出目标 | 位置标签 `A,B,C…`（choice）、`Yes/No`（yes_no）、`1..K`（score） | 选项字符串自身的 token |
| 概率 | 仅在 K 个标签 logits 上做受限 log-softmax | 同（受限 softmax），但标签集合不同 |
| 顺序处理 | 单次、按给定顺序（L0 才做置换边缘化） | `rotations` 次循环置换平均 |
| 标签先验 | Tacit 已由自蒸馏吸收；Decider L0 显式除掉 | 无 |
| 返回路由 | `one_forward` / `cot` | `label_token` / `candidate`（**刻意不再用** `one_forward`） |

由此产生三个必须承认的结论：

1. **普通 `AutoModelForCausalLM` 能加载 Tacit 权重**（`Qwen3ForCausalLM`，与本机实测一致），
   但「能加载 + 概率合法」不等于「决策方法正确」。
2. 在 Qwen3 词表上，本项目的五个问题里 `intent`/`emotion`/`action` 都存在多 token 标签
   （`help_seeking`、`irritable`、`start_scale`、`recommend_relaxation`、`recommend_game`、`end_session`），
   `auto` 模式会**整题回落到 `candidate`**——即 5 题里 3 题不是「读 next-token 分布」。
3. 因此 `DecisionService` 现在**默认拒绝**以自建 readout 服务 Tacit 检查点
   （`decision/integrity.py::tacit_checkpoint_markers`），
   必须显式加 `--allow-non-tacit-readout` 才能跑，并在 `/health` 里标注
   `readout_semantics: option_label_tokens (not official Tacit letter readout)`。

### 5.2 同一权重、同一 24 条中文用例的实测差距

`scripts/benchmarks/tacit_official_compare.py`，RTX 5090，bf16，两份产物在
`test_output/benchmarks/tacit_compare/`：

| action 题 | 官方 Tacit | 本项目 readout |
|---|---|---|
| accuracy | **0.7083** | 0.4167 |
| macro-F1 | **0.7391** | 0.2870 |
| Brier | **0.4045** | 0.7192 |
| ECE | **0.1616** | 0.2318 |
| 选项顺序翻转率 | **0.0729** | 0.4792 |
| 5 题一次批调用 P50 / P95 | **23.0 / 27.3 ms** | 108.8 / 115.8 ms |

顺序翻转率 6.6 倍差距，正是官方文档里「raw label logits 会随选项重排改答案」的现象；
本项目读法既没有 chat template，也没有除掉标签先验，落在这个陷阱里。


## 6. 校验与弃权

`DecisionValidator` 的稳定 reason（可用于统计）：

```
provider_error, missing_action, illegal_action, missing_probabilities,
non_finite_probability, negative_probability, probability_sum_invalid,
probability_out_of_range, inconsistent_argmax, below_abstain_threshold,
missing_scale_name, unknown_scale_name, state_conflict_active_scale,
state_conflict_session_ending, end_requires_deterministic_signal,
state_conflict_game_active
```

要点：

- **置信度取自分布**，不信任模型自报的 scalar（`_resolve_confidence`）。
- 概率和容差 1e-2 内会归一化，超出直接拒绝。
- `end_session` **恒拒**：结束会话必须有确定性信号，不能来自概率。
- `active_scale` 期间只允许 `chat`；`game_active` 时不允许再推荐游戏。
- 低于 `abstain_threshold`（默认 0.55）视为**弃权**，不是「弱投票」。

## 7. 环节之外：本层不做的事

- 不训练、不微调任何模型。
- 不修改量表分数、不产出诊断、不做风险排除。
- 不启动/停止任何模型服务（沿用 `manage_model_servers: false`）。
- 不记录访谈原文：日志与 journal 只写长度、标签、概率、延迟。`DecisionJournal` 有字段 deny-list，
  命中即抛 `DecisionJournalError`。

## 8. 代码地图

| 文件 | 职责 |
| --- | --- |
| `decision/contracts.py` | 标签词表、`DecisionRequest` / `RawDecision`、`build_observation` |
| `decision/validator.py` | 校验与弃权 |
| `decision/coordinator.py` | 三模式编排、shadow 对照、escalation |
| `decision/journal.py` | 元数据 JSONL（无原文） |
| `decision/clinical_review.py` | 复杂评估接口（默认关闭，强制人工复核） |
| `decision/providers/` | `legacy` / `anyjev` / `tacit` / `fake` 四种后端 |
| `decision/engine/` | L0 读出（纯逻辑 + transformers scorer） |
| `decision/service/` | 独立进程的本地 HTTP 决策服务 |
| `decision/integrity.py` | 权重完整性校验（manifest + sha256） |
| `deployment/net_guard.py` | 运行时断网守卫（真拦截，不是声明） |

## 9. 已知取舍

1. **`next_token` 对中文标签敏感**：标签若不能单 token 表达，会回落 `candidate`，成本上升（每题 N 次续写打分）。
   这是有意的失败方式——宁可慢，也不把「读不到的标签」当作某个默认值。
2. **`rotations` 是线性成本**：`rotations=k` 即 k 倍前向。基准里用 `--order-rounds` 单独测量，不默认开启。
3. **`AnyJev` 网关无鉴权**：`require_loopback` 硬拒绝非回环端点，`allow_remote_endpoint` 默认 false。
4. **断网守卫只能约束本进程**：子进程（决策服务）靠自身的 `HF_HUB_OFFLINE` / `local_files_only=True` 保证，
   验收脚本会读 `/health` 里的 `integrity` 复核。

## 10. 多轮干预时机决策

### 10.1 问题被重新定义

旧问题是：*这句话属于哪一类？* 新问题是：

> 结合目前访谈阶段、症状信息、量表与放松训练的历史、既往建议以及来访者表达的意愿，
> **这一轮最合适的下一步交互是什么？**

差别不是措辞。前者可以在 24 条合成单句上拿到很高的 accuracy，却完全无法回答
「访谈第 7 轮该不该提出量表」。后者只能在**连续轮次**上评价，而且必须允许「第 7 轮和第 9 轮
建议都算合适」。

### 10.2 四分类语义

| 标签 | 含义 | 允许的下游动作 |
| --- | --- | --- |
| `continue_interview` | 继续当前访谈 | `chat` / `continue_scale` |
| `offer_scale` | 邀请做一个**已注册**的量表 | `OFFER_SCALE`（邀请），同意后才 `START_SCALE` |
| `offer_relaxation` | 邀请做一次放松训练 | `OFFER_RELAXATION`（邀请），同意后才 `RECOMMEND_RELAXATION` |
| `clarify_first` | 信息不足或有歧义，先澄清 | `CLARIFY_INPUT` |

`CLARIFY_FIRST` 也是「否定误听」的兜底：ASR 把「不想」听成「想」时，政策宁可先问一句。

### 10.3 输入必须是可核实的多轮状态

`DecisionRequest` 新增的字段全部来自既有模块，**不允许模型凭空推断**：

| 输入 | 来源 | 模型不允许做什么 |
| --- | --- | --- |
| 轮次、阶段 | `DecisionRequest.current_round` / `interview_phase()` | 自行认定「已经到该做量表的阶段」 |
| 量表状态 | `ScaleRuntime`（`active_scale` / `completed_scales` / `administered_scales`） | 声称某个量表已完成 |
| 放松状态 | `relaxation_done` / `relaxation_active` | 声称已经做过训练 |
| 既往建议 | `OfferLedger` → `OfferState` | 声称来访者同意过或拒绝过 |
| 候选量表 | `rank_scale_candidates()` 基于注册表 | 生成注册表之外的量表名 |
| 症状线索 | `symptom_turns` / `symptom_summary`（领域计数） | 判断严重程度、下诊断 |
| 安全线索 | `risk_marker`（只做路由） | 用「我觉得没那么严重」排除风险 |

`symptom_summary` 只记录**谈到过哪些领域**（睡眠、情绪、焦虑……），不含分数、不含严重度。
小模型不做临床分级，这既是权限问题，也是可复现性问题。

### 10.4 问题与候选的构造

- 主问题只有一道（`intervention`），选项四个，**保持官方 Tacit 的位置字母读出机制**
  （A/B/C/D，不改成自建 readout —— 两者语义不等价，实测见 §5.2）。
- 量表选择是**条件子问题**：只有当 `intervention == offer_scale` 时才追加一次前向，
  选项来自 `scale_choice_option_texts()`，末尾固定有 `No scale for now`。
  没有这个兜底选项，模型在无合适量表时只能编一个名字——上一版 `scale_name=None` 的降级正是这条路。
- 候选排序 `rank_scale_candidates()`：未完成但已开展（resume）优先于未开展（fresh）；
  已完成、已拒绝的排除。**注册表之外的名字不可能出现**。
- 放松训练只判断**时机与意愿**，方式（呼吸/肌肉/冥想）交给既有模块；用户明确指定时才传 `intervention_type`。

### 10.5 邀请生命周期与账本

`conversation/offer_ledger.py` 是邀请记忆的唯一所有者。邀请是**显式状态机**，不是「几轮后自动作废」：

```
(无) --offer--> PENDING --问细节--> CLARIFYING --答复--> PENDING
                   |                    |
                   |--同意--> ACCEPTED <|
                   |--拒绝--> DECLINED        （进入冷却期）
                   |--话题持续漂移--> EXPIRED  （需要证据，见下）
                   |--会话结束/风险--> CANCELLED
                   |--被新请求替换--> CANCELLED(superseded)
```

每条记录都带 `offer_id`、类型、目标量表、提出时会话状态、当前状态、创建轮次、
最近交互轮次、结束轮次与原因（`resolution_note`）、来源（`policy` / `explicit_request`）。

**没有「未答复超时」。** 轮数本身永远不能证明来访者不愿参与——真实访谈中隔两三轮才回答很常见。
`EXPIRED` 只有一条路径：`expire_on_topic_drift()` 要求**连续 `topic_drift_grace_rounds`（默认 3）轮**
既没有答复、也没有任何指涉（「那个量表」「刚才说的」都算指涉）；任一轮 engaged 就重置计数。

`CLARIFYING` 是不可省略的状态：「可以先说一下是什么吗？」既不是同意也不是拒绝，
把它读成任何一种都会产生错误行为（前者伪造同意，后者关闭一扇正在被推开的门）。

结构性保证：

- `TurnPolicy._apply_turn_decision()` 的 `OFFER_*` 分支**只记账、不改量表/放松状态**；
- `TurnDecision` 契约禁止 `OFFER_SCALE` 携带 `answered_item` / `accepted_score` / `end_reason`；
- 未答复的邀请**拥有本轮**：新提议只能得到 `offer_already_pending`；
- 关闭后的邀请不能被后来的「好」唤醒——`snapshot.pending_offer` 为空时不评估任何答复；
- 会话结束或风险信号会 `cancel_all`，邀请不跨边界存活。

### 10.6 闸门是地板，不是触发器

`MIN_ROUNDS_BEFORE_SCALE=5`、`MIN_ROUNDS_FOR_RELAXATION=8` 的含义是「不早于」，不是「到了就该做」。
测试集里的窗口（如 `scale: [6, 8]`）全部 ≥ 对应地板；把第 5 轮当成应触发的依据属于误读。

另外，**用户主动请求优先于所有地板**：来访者说「我想做个量表」，系统不应把「你想做吗」反问回去。

### 10.7 指标体系（按时间窗口，不按单点）

`decision/timing_metrics.py`：

| 指标 | 定义 |
| --- | --- |
| 合适时机识别率 | 首次建议落在窗口内 |
| 过早建议率 / 过晚率 | 首次建议早于 / 晚于窗口 |
| 漏建议率 | 窗口内没有任何建议 |
| 不当建议数（spurious） | 没有窗口却提出了建议 |
| 首次建议延迟 | 仅在窗口内命中时计 0；早/晚为正负偏移轮数 |
| 拒绝后冷却期内重复率 | 拒绝后的 `cooldown` 轮内又提同类建议 |
| 不合适任务切换 | 量表进行中、或来访者正在倾诉时提出建议 |
| 量表选择正确率 | 首次建议的量表 == 标注量表 |
| 同意后执行成功率 | `accepted` 轮的 `active_scale` 确实等于被接受的量表 |
| 冲突建议拦截率 | 提议为 offer_* 但未成为邀请（含 validator 拒绝）的比例 |
| 每轮建议一致率 / 政策一致率 | 分别对标 Jev 建议与 TurnPolicy 最终动作 |

Accuracy / Macro-F1 只作为辅助，且只在单句基准里报告。

### 10.8 三基线、两个 split

`scripts/benchmarks/timing_benchmark.py`，四种 provider，**同一历史状态、同一 `TurnPolicy`**：

| provider | 角色 | 是否需要模型 |
| --- | --- | --- |
| `rule` | 确定性状态规则基线（`decision/providers/rule.py`） | 否 |
| `replay` | 回放标注 —— **链路自检，不是模型结果** | 否 |
| `tacit` | 官方 Tacit-1.7B 网关 | 是 |
| `anyjev` | Qwen Agent 后端 | 是 |

`replay` 的作用只有一个：如果「告诉它正确答案」都拿不到窗口，坏的是链路而不是模型。
所有报告里它都必须标注为 harness self-check。

数据分 `dev`（6 条）/ `test`（8 条），共 14 类情境。**先在 dev 上定模板与阈值，再在 test 上报分**；
同批调参又报成绩的数字不算结果。

### 10.9 本轮已验证 / 未验证

已验证（可复现，`pytest` 或脚本）：

- 链路自检：replay 在两个 split 上命中全部窗口，`turn_agreement` 与 `policy_agreement` 均为 100%；
- 守卫：量表进行中不产生邀请、危机轮不产生邀请、明确结束恒为 `END_SESSION`、
  冷却期内不重复邀请、`risk_requires_safety_path` 实际拦截；
- 契约：`scale_name` 不再可能为 `None`（`OFFER_SCALE` 与 `START_SCALE` 均要求注册表内的名字）；
- 信号：`"现在我特别烦，能不能做个放松训练？"` 能被识别为请求（此前被单字「别」误判为否定）；
  `"我知道可以放松，但我现在更想把这些说出来"` **不再**被读成同意量表；
- 全量回归：1594 passed / 1 failed；失败项是既有的决策服务端口偶发（`WinError 10053`），
  与本轮改动无关，单独复跑可通过。

未验证（必须如实标注）：

- 真实 Tacit-1.7B / Qwen 在 16 条轨迹上的时机准确率 —— 本机没有权重/服务，**没有数字**；
- 窗口标注本身的专业性 —— 由开发者按研究方案定义，**未经独立临床标注**，因此不能作为
  「临床正确时机」报告，只能作为控制逻辑的一致性检查；
- 合成轨迹无法替代真实访谈中的时机判断。

### 10.10 代码地图（增补）

| 文件 | 职责 |
| --- | --- |
| `decision/timing.py` | 四分类词表、访谈阶段、`OfferState`、候选排序、理由常量 |
| `decision/timing_eval.py` | 用生产对象逐轮回放轨迹（`DecisionRequest` → validator → policy → ledger → runtime） |
| `decision/timing_metrics.py` | 窗口化时机指标与聚合 |
| `decision/providers/rule.py` | 确定性时机基线 |
| `decision/providers/scripted.py` | 标注回放（链路自检，明确不是模型） |
| `conversation/offer_ledger.py` | 邀请账本 |
| `scripts/benchmarks/timing_benchmark.py` | 多基线对照 CLI |
| `scripts/benchmarks/cases/interview_trajectories_{dev,test}.jsonl` | 14 类多轮轨迹 |

### 10.11 已知缺口（不隐藏）

1. **答复识别仍有关键词边界**：句首 8 字定答复、`_DEFER_PHRASES` 定延后，
   覆盖了本轮点名的十句话，但「嗯，我想想……好吧，那就做」这类迟疑仍会落到澄清
   （邀请保持待决，不会误启动——方向安全，代价是多问一句）。
2. **结束语表仍是关键词法**：`core/scoring.py` 已补入「到这儿吧 / 就到这里 / 今天就这样」等口语收尾，
   但「我不想做了」单独出现时仍不判结束（它也可能是拒绝量表）。这类歧义应由对话本身澄清。
3. **话题漂移的「engaged」判定来自词表**：`mentions_offer()` + `detect_offer_question()`。
   它能区分「那个量表要多久」和「我在想换工作」，但无法理解更远的指代（「就你说的那事」）。
   漂移过期宁可偏晚——多保留一轮的代价小于丢掉一次真实同意。
4. **窗口标注来自开发者**：若要与临床标准对齐，需要独立标注者给「合适建议窗口」，
   并计算标注者间一致性。
5. **真实模型未跑**：`rule` / `replay` 都是链路基线与自检，Tacit-1.7B 与 Qwen 的时机数字尚不存在。
