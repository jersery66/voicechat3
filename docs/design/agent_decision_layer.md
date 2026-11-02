# 结构化决策层（Jev-style Decision Layer）

> 状态：实验分支 `exp/agent-jev-decision`。默认关闭，`mode: legacy` 时行为与 RC1 逐字节一致。

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
   │                    五问：intent / emotion / action / clarify / complex_review
   ▼
DecisionValidator ──►  接受 / 弃权（永不改写动作语义）
   │
   ▼
AgentObservation     （仍然不可执行）
   │
   ▼
TurnPolicy.decide()  ──►  TurnDecision（唯一可执行产物）
   │
   ▼
SessionEngine / ScaleRuntime
```

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

## 4. 五类结构化观察

| 问题 | 标签空间 | 下游含义 |
| --- | --- | --- |
| intent | chat / symptom / emotion / help_seeking / scale / relaxation / game / end / unclear | 映射到既有 4 值 `counseling/entertainment/chitchat/relaxation` |
| emotion | calm / sad / anxious / angry / irritable / positive / uncertain | 映射到报告用的情绪字符串 |
| action | 5 个 `RouterAction` | **仅建议**，仍需 TurnPolicy |
| clarify | no / yes | 建议追问澄清 |
| complex_review | no / yes | 建议转入更深入评估（只记录） |

## 5. 读出（readout）机制

`decision/engine/readout.py` 实现两种读法，配置 `mode` 选择：

- `next_token`（默认首选）：prompt 以「答案：」结尾，取 next-token 分布，只在各选项标签的 token 上做**受限 softmax**。
  这是最贴近「读分布」的形态，一次前向。
- `candidate`：把每个标签当作续写，分别求 log P(label | prompt)，再 softmax。标签不是单 token 时自动回落。
- `auto`：优先 `next_token`，遇到多 token 标签按题回落 `candidate`（记录在 `notes`）。

**选项顺序偏差**：`rotations > 1` 时对选项列表做循环置换，把概率映射回原标签后平均。
`tests/test_decision_readout.py::test_rotations_average_over_presentation_order` 用一个「永远偏好第一个选项」的
假 scorer 证明了偏差被抵消。

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
