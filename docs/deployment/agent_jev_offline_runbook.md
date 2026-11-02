# 决策服务离线部署手册（Tacit-1.7B / 2B · Windows + WSL2）

> 目标环境：**完全离线的访谈机**（无外网、无代理、无 HuggingFace 访问）。
> 主模型 Qwen3.8-27B + llama.cpp 不动；决策服务是**独立进程**，只经 `127.0.0.1` 与主应用通信。

## 0. 一句话结论

离线部署只有四件事：**权重进目录、manifest 落盘、依赖进独立 venv、断网跑一遍验收**。
四件事里任何一件没做，服务都拒绝启动（这是设计，不是故障）。

## 1. 拓扑

**Windows 上没有 vLLM 原生 wheel**，所以官方 `python -m anyjev.serve` 网关
（它的上游必须是 `vllm serve`）在 Windows 上不可用。由此在 Windows 上只有两条路：

```
(A) 主路径：应用进程内加载官方 Tacit（transformers，bf16，单进程）
     python -c / GUI
       └─ decision.providers.tacit  transport=inprocess
            └─ anyjev.Tacit.from_pretrained(<本地权重目录>)
                 └─ Tacit-1.7B/ (config.json / tokenizer* / model.safetensors)

(B) 只有 WSL2/Linux 才有：vllm serve + 官方网关
     vllm serve <权重目录> --host 127.0.0.1 --port 8000
     python -m anyjev.serve --model <权重目录> --upstream http://127.0.0.1:8000/v1 --port 8100
       应用 ──HTTP 127.0.0.1:8100──▶ 网关 ──▶ vLLM
```

本项目的 `python -m decision.service` **不是** Tacit 服务：它跑的是自建 readout
（读选项字符串 token），与官方语义不同，只用于对照实验。它会默认拒绝 Tacit 检查点，
必须显式 `--allow-non-tacit-readout` 才启动，并在 `/health` 中声明
`readout_semantics`。

- 应用本身**不会** import torch：走 (A) 时由 `decision/providers/tacit.py` 在首次决策时懒加载，
  服务进程与主进程同生共死；走 (B) 时应用只发 HTTP。
- 决策端点只允许回环；`decision.allow_remote_endpoint` 默认 false（网关无鉴权）。
- 主模型、ASR、TTS 的启停不受影响（`manage_model_servers: false`）。

## 2. 离线安装包制作清单（在**联网机**上做）

### 2.1 权重

```bash
# 优先 1.7B，2B 作为升级候选；两者目录结构相同
huggingface-cli download morriszjm/Tacit-1.7B --local-dir Tacit-1.7B
# huggingface-cli download <Tacit-2B repo> --local-dir Tacit-2B
```

HuggingFace 直连不通时（国内常见）用镜像；镜像同样返回官方 LFS 对象，哈希一致：

```bash
HF_ENDPOINT=https://hf-mirror.com huggingface-cli download \
    morriszjm/Tacit-1.7B --local-dir Tacit-1.7B
# 实测（2026-11-02）：3.44 GB 单分片，1 分 47 秒
```

目录必须包含：

```
config.json
tokenizer.json（或 tokenizer_config.json / tokenizer.model / spiece.model 之一）
chat_template.jinja          # 官方 prompt 通过 apply_chat_template 渲染，缺了会退回纯文本
*.safetensors（分片时还必须带上 *.index.json 与**全部分片**）
```

### 2.2 完整性清单（哈希必须来自来源，不是目标机）

```bash
# 在下载机上：sha256 取自 HuggingFace 发布的 LFS oid（来源侧），
# 同时逐文件比对本地字节；对不上直接失败。
python scripts/decision/tacit_model_check.py \
    --model-path Tacit-1.7B --from-hub morriszjm/Tacit-1.7B \
    --hub-endpoint https://hf-mirror.com --model-id morriszjm/Tacit-1.7B
```

生成 `Tacit-1.7B/tacit_manifest.json`，其中：

- `source: hub:morriszjm/Tacit-1.7B@main`、`hub_revision`；
- 每个文件带 `sha256_source`：`hub_lfs_oid`（来源发布）或缺失（本机计算，
  非 LFS 的小文件，已如实标注）。

**与权重一起拷走。** 拷到离线机后只跑校验、**不要**再 `--write` 重新生成——
否则 manifest 变成"目标机自证"，失去来源校验的意义。

```bash
python scripts/decision/tacit_model_check.py --model-path Tacit-1.7B   # 离线机，只校验
```

> 为什么必须哈希而不是只看大小：同尺寸的比特翻转不会改变大小，`verify` 会以
> `sha256_mismatch` 拒绝；截断则先被 `size_mismatch` 拦下。

### 2.3 Python 依赖（独立 venv，不污染主 `.venv`）

一次下载、一个索引参数即可，`anyjev` 也在这份清单里（官方路径是主路径）：

```bash
python -m pip download -r deployment/offline/requirements-decision.lock.txt \
    -d wheelhouse --only-binary=:all: \
    --extra-index-url https://download.pytorch.org/whl/cu130 \
    --platform win_amd64 --python-version 3.13 --implementation cp --abi cp313
python scripts/decision/wheelhouse.py seal --dir wheelhouse
```

必须带 `--extra-index-url .../cu130`：PyPI 上的 `torch==2.11.0` 与本项目实测的
`2.11.0+cu130` **不是同一个 wheel**，不带这个索引会拿到别的 CUDA 变体。

在离线机：

```bash
python scripts/decision/wheelhouse.py verify --dir wheelhouse
python -m venv <bundle>/venv-decision
<bundle>/venv-decision/Scripts/python -m pip install --no-index --find-links wheelhouse \
    -r deployment/offline/requirements-decision.lock.txt
```

`--no-index` 是关键：即使机器有网，也不会去 PyPI 拿别的版本。


### 2.4 拷贝清单

```
bundle/
  Tacit-1.7B/            # 权重 + tacit_manifest.json
  wheelhouse/            # wheels + wheelhouse_manifest.json
  venv-decision/         # 独立解释器
  voicechat3/            # 本仓库
```

## 3. 配置

`config/runtime.yaml`：

```yaml
decision:
  mode: jev                    # legacy | shadow | jev
  backend: tacit               # legacy_qwen | anyjev_decider | tacit | fake
  transport: inprocess         # 服务进程内加载权重（与 server 二选一）
  model_path: /models/Tacit-1.7B   # 绝对路径，或相对本 yaml 所在目录
  model_dtype: bfloat16        # 5090 原生支持；显存紧时改 float16
  endpoint: http://127.0.0.1:8100  # transport: server 时必填
  abstain_threshold: 0.55
  escalation_mode: legacy      # 回退到 Qwen Agent
  allow_remote_endpoint: false
  allow_model_download: false
  journal_path: null
  clinical_review:
    enabled: false
```

说明：

- `model_path` **不要求目录存在**（否则换机器就起不来）；存在性由启动预检和服务进程强制检查。
- `mode: jev` + `backend: tacit` + `transport: inprocess` 时，`model_path` 与 `model` 至少给一个。
- `transport: inprocess` 时 `endpoint` 不参与；`transport: server` 指**官方 anyjev 网关**
  （`python -m anyjev.serve`，需要 vLLM 上游，Windows 不可用，见 §1）。
- 未知 section / key / provider、越界值一律硬失败（`RuntimeConfigError`）。

## 4. 启停与异常恢复

```bash
S=scripts/engines/tacit/decision_service.sh

$S start   --name tacit-1.7b --model-path /models/Tacit-1.7B --python <bundle>/venv-decision/bin/python
$S status  --name tacit-1.7b
$S health  --name tacit-1.7b
$S restart --name tacit-1.7b --model-path /models/Tacit-1.7B
$S recover --name tacit-1.7b --model-path /models/Tacit-1.7B
$S stop    --name tacit-1.7b
```

所有权契约（与 llama.cpp 脚本同一思路）：**pid 文件 + meta + 活进程命令行三者一致**才允许动作。

| 状态 | 含义 | 处理 |
| --- | --- | --- |
| `NO_PID_FILE` | 干净未启动 | 直接 `start` |
| `STALE_PID` | pid 文件指向死进程 | `recover`（会先清槽） |
| `OWNERSHIP_MISMATCH` | 活进程不是本槽的服务 | **不许直接 stop**，需 `--force` |
| `SERVICE_UNHEALTHY` | 进程在但 `/health` 不是 ok | `recover` |

`recover` 会 stop → start，最多 3 次，退避 2s/4s/6s；遇到参数错误或权重不合格立即返回（重试无意义）。

启动失败会打印日志尾部，并清理 pid/meta（不留假状态）。

Windows 说明：控制台进程没有 SIGTERM，`_terminate` 在 Windows 直接用 `taskkill /F`，
POSIX 先 SIGTERM 再 SIGKILL——「先礼后兵」在 Windows 上只会白等超时。

## 5. 断网验收（必做）

### 5.1 真模型端到端（推荐先跑这个）

```bash
<bundle>/venv-decision/Scripts/python scripts/decision/acceptance_tacit.py \
    --model-path D:/models/tacit/Tacit-1.7B
```

六段检查，全部用真权重、无 mock（只有 legacy agent 用计数器替身）：

1. 权重与**来源 manifest** 一致；
2. 官方 `Tacit.from_pretrained` 回一轮决策；
3. 本项目 `TacitDecisionProvider` 回同一轮（走官方 `decide_batch`）；
4. legacy / shadow / jev 三模式切换；
5. 模型提出 `end_session` 时被 validator 拒绝、`TurnPolicy` 仍作最终决定；
6. 决策端点不可达时回退到 legacy，而不是抛错。

产物：`test_output/acceptance/tacit_1.7b_acceptance.json`。
退出码：`0` 通过 / `3` 权重或 manifest 不可用 / `4` provider 不可用。

**本机实测（2026-11-02，RTX 5090，Tacit-1.7B bf16）**：

| 项 | 实测值 |
|---|---|
| 完整性 | ok，11 文件，3297.0 MiB，11 个文件全部哈希校验 |
| 官方单轮（5 题批） | `recommend_relaxation` 0.672，`route=one_forward`，5 题概率和均 = 1.0 |
| 三模式 | legacy→`chat`（agent 直通）、shadow→`chat`（权威不变）、jev→`recommend_relaxation`（模型） |
| 权限边界 | 2 个真实回合模型给 `end_session` 0.975 / 0.959，均被 `end_requires_deterministic_signal` 拒绝并回退，`TurnPolicy` 结果 `chat` |
| 故障回退 | 端点 `127.0.0.1:9` → `DecisionTransportError` → 回退 legacy，`action=chat`，agent 调用 1 次 |
| 断网守卫 | 违约 0 次，仅 2 次回环连接 |

### 5.2 服务层断网巡检（自建 readout，只作对照）

```bash
python scripts/decision/offline_decision_check.py \
    --model-path /models/Tacit-1.7B \
    --cases scripts/benchmarks/cases/decision_cases_synthetic.jsonl
```

> 注意：它校验的是**自建 readout 服务**。该服务默认拒绝 Tacit 检查点，
> 需要 `--allow-non-tacit-readout` 才会启动，且结果不能当作 Tacit 结果。

它做什么：

1. 装 **运行时断网守卫**（`deployment/net_guard.py`）：patch `socket.connect / connect_ex /
   create_connection / getaddrinfo`，并清空代理环境变量、stub 掉 `urllib.request.getproxies`
   （Windows 的代理还来自注册表，不清这一层 `urlopen` 会经本地代理把请求转出去）。
2. 校验权重完整性，失败即退出码 3，并打印离线安装包清单。
3. 起服务（或 `--endpoint` 接已有的），等 `/health`。
4. 跑完用例集，经 `DecisionValidator` 统计接受/弃权。
5. 采 `/v1/stats`（进程内 torch 峰值显存）+ nvidia-smi 采样的**整卡**峰值。
6. 写产物 `test_output/offline_decision/offline_decision_<stamp>.json`，含 `"real_inference": true`。

退出码：`0` 通过 / `3` 权重不可用 / `4` 服务不可用 / `5` 断网违约。

> **没有权重时脚本不会伪造结果**：它直接退出 3 并打印清单。
> 产物里的 `real_inference` 只有在真的完成了至少一次决策后才为 `true`。

## 6. 官方 Tacit vs 自建 readout 对照 / 1.7B vs 2B 基准

```bash
# 同一权重、同一 24 条用例，两条路线各跑一遍（推荐先跑这个）
<bundle>/venv-decision/Scripts/python scripts/benchmarks/tacit_official_compare.py \
    --model-path /models/Tacit-1.7B --order-rounds 4
```

```bash
# 跨权重（1.7B vs 2B）对照走服务层
python scripts/benchmarks/decision_benchmark.py --model-path /models/Tacit-1.7B \
    --label Tacit-1.7B --order-rounds 2 --out test_output/benchmarks
python scripts/benchmarks/decision_benchmark.py --model-path /models/Tacit-2B \
    --label Tacit-2B  --order-rounds 2 --out test_output/benchmarks
python scripts/benchmarks/decision_benchmark.py --compare \
    test_output/benchmarks/tacit-1.7b.json test_output/benchmarks/tacit-2b.json
```

**本机实测：官方 vs 自建 readout（Tacit-1.7B，24 条中文合成用例）**

| 题 | 官方 acc / F1 / flip | 自建 acc / F1 / flip |
|---|---|---|
| intent | 0.5833 / 0.6134 / 0.2083 | 0.1667 / 0.1296 / 0.1458 |
| emotion | 0.5417 / 0.4400 / 0.2604 | 0.0833 / 0.0220 / 0.0104 |
| action | **0.7083 / 0.7391 / 0.0729** | 0.4167 / 0.2870 / 0.4792 |
| clarify | 0.9167 / 0.4783 / 0.0000 | 0.0833 / 0.0769 / 0.0417 |

延迟（5 题一次批调用）P50 23.0 ms vs 108.8 ms；进程内推理峰值 3399.5 MiB vs 4230.7 MiB。

> 这是"必须用官方路径"的最直接证据：同一份权重，只换读出方式，action 准确率从 0.708 掉到 0.417，
> 选项顺序翻转率从 0.073 涨到 0.479。

指标：

| 指标 | 为什么需要 |
| --- | --- |
| accuracy | 基线，但标签分布不均时会骗人 |
| macro-F1 | 小类（如 `end_session`）是否被牺牲 |
| 混淆矩阵 | 错在哪里比错多少重要 |
| Brier | 概率是否可信（不只是答案对不对） |
| ECE | 高置信度错误最危险 |
| 延迟 P50/P95/P99 | 语音交互的实时预算 |
| order-flip | 选项顺序敏感性 |

**用例集是合成模板句**（`scripts/benchmarks/cases/decision_cases_synthetic.jsonl`，24 条，
覆盖 5 个动作与 9 个意图、含 2 条需要澄清的含糊输入），不含任何真实来访者数据。
真实数据要另建用例集，且不得进仓库。

### 6.1 多轮时机基准（本轮的主要验收）

单句准确率回答不了「第几轮该提量表」。时机基准在**连续轨迹**上跑，
每个 provider 共用同一状态、同一 `TurnPolicy`、同一 `DecisionValidator`、同一 `ScaleRuntime`。

```bash
# 不需要模型，先确认链路与守卫：dev 集
python scripts/benchmarks/timing_benchmark.py --split dev

# 只看链路自检（回放标注；若它都拿不到窗口，坏的是链路不是模型）
python scripts/benchmarks/timing_benchmark.py --split dev --providers replay --verbose
```

```bash
# 真实模型：进程内加载官方 Tacit 权重（必须用官方 letter readout，
# 本地 decision/service 会主动拒绝 Tacit 检查点）
python scripts/benchmarks/timing_benchmark.py --split dev \
    --providers tacit --transport inprocess --template zh \
    --model-path D:/models/tacit/Tacit-1.7B

# 配置在 dev 上定好之后，再在未见过的 test 集上跑一次
python scripts/benchmarks/timing_benchmark.py --split test \
    --providers tacit --transport inprocess --template zh \
    --model-path D:/models/tacit/Tacit-1.7B --label test-tacit-zh
```

`--template` 必须在 dev 上选：英文是官方训练用语，中文在本机 dev 上明显更好
（放松命中 0% → 100%，政策一致 32.5% → 49.2%）。**在 test 上换模板再报分等于作废。**

产物：`test_output/timing/<split>-<provider>.json|.md`，含逐轨迹的窗口命中表与逐轮明细。

**先 dev 定配置，再 test 报分。** 同一批数据上调参又报成绩的数字不能算结果。

轨迹集共 16 条（dev 7 / test 9，129 轮）。`replay` 是链路自检，`rule` 是确定性下界；
两者都不是模型成绩。真实 Tacit-1.7B 需要本地权重（官方 `Tacit-1.7B`）与
`decision/service` 在 8099 上运行，缺一不可——没有它就不要在报告里写第三行。

邀请的生命周期见 `docs/design/agent_decision_layer.md` §10.5：未答复的邀请**没有轮数超时**，
只有「连续多轮既未答复也未指涉」才会 `EXPIRED`。调 `topic_drift_grace_rounds` 前先读那一节。

基准的三种数字必须区分开：

| provider | 能不能当模型成绩 |
| --- | --- |
| `rule` | 不能，是确定性基线（下界参考） |
| `replay` | 不能，是链路自检（上界参考：它照着标注答） |
| `tacit` / `anyjev` | 能，但必须与上面两个同时报告 |

`rule` 与 `replay` 的作用是把模型夹在中间：低于 `rule` 说明模型不如规则；高于 `replay`
说明测试集或统计有问题。

## 7. 显存预算（RTX 5090 32GB）

| 组件 | 实测 / 估算 |
| --- | --- |
| Qwen3.8-27B Q4_K_S（llama.cpp，主模型） | ~16–17 GB（**估算**，本轮未与决策同驻测量） |
| Fun-ASR | ~1–2 GB（**估算**） |
| VoxCPM / CosyVoice TTS | ~1–2 GB（**估算**） |
| Tacit-1.7B bf16（官方路径） | **实测**：加载峰值 3281.8 MiB，推理峰值 3399.5 MiB，reserved 峰值 3446.0 MiB |
| Tacit-1.7B bf16（自建 readout） | **实测**：推理峰值 4230.7 MiB，reserved 峰值 5692.0 MiB（candidate 批更大） |
| Tacit-2B bf16 | 约 +0.5 GB（**估算**，未下载，未测量） |

整卡口径（nvidia-smi 每 200 ms 采样，含驱动与其他进程）：官方路径峰值 **4750 MiB**，
自建 readout 6996 MiB。两者都不含主模型——本轮主模型没有启动。

结论：1.7B 与主模型同时驻留可行（3.4 + 17 + 2 + 2 ≈ 24.4 GB / 32 GB）；
2B 也可行但余量更小，需在 `float16` 下复测。
**以实测为准**：`tacit_official_compare.py` 与 `offline_decision_check.py` 都会同时给出
进程内 torch 峰值与整卡峰值。


## 8. 安全回退

| 故障 | 表现 | 回退 |
| --- | --- | --- |
| 权重缺失/损坏 | 服务拒绝启动（退出 3） | `mode: legacy` 或 `escalation_mode: legacy` |
| 服务未起/端口不通 | provider 报传输错误 | coordinator 按 `escalation_mode` 回退 |
| 决策概率低于阈值 | validator 弃权 | 同上，回到 Qwen Agent |
| 概率非法（NaN/越界/argmax 不一致） | validator 拒绝 | 同上 |
| 模型提出 `end_session` | validator **恒拒**（`end_requires_deterministic_signal`） | 回退；结束只能由确定性会话状态触发 |
| 概率合法但读出方式不对（自建 readout 配 Tacit 权重） | 服务启动即拒绝 | 改用 `decision.providers.tacit` |
| journal 写失败 | 只记一行日志 | 不影响回合 |

三种回退路径都有测试固定（`tests/test_decision_modes.py`），
且 `TurnPolicy` 始终是唯一执行者——决策层挂了，权限链不断。

## 9. 常见问题

**Q1：`manifest_missing` 怎么办？**
用 `--from-hub <repo>` 在**下载机**上生成，它的 sha256 取自来源发布的 LFS oid；
然后只把 manifest 拷到离线机做 `verify`。不要在离线机上 `--write` 重新生成——
那是目标机自证，查不出传输途中被替换的文件。

**Q2：`sha256_mismatch` 但大小一样？**
拷贝过程比特损坏或存盘介质问题。重新拷贝，不要 `--no-hashes` 糊过去。

**Q3：`python_version_drift`？**
wheelhouse 是在 3.13 上封的，换了 3.12 就重封或加 `--allow-python-drift`（不推荐）。

**Q4：`OWNERSHIP_MISMATCH` 出现时能直接 stop 吗？**
不能。先 `status --json` 看 pid，确认是不是自己的进程；确认后才 `--force`。

**Q5：为什么 `decision.service` 起不来，报 "looks like a Tacit checkpoint"？**
因为服务跑的是自建 readout（读选项字符串 token），不是官方字母读出，配 Tacit 权重会得到
"概率合法但语义错"的结果。Tacit 请用 `decision.providers.tacit`（transport=inprocess）。
确要拿它做对照实验，加 `--allow-non-tacit-readout`，并且不要把结果写成 Tacit 成绩。

**Q6：Windows 上能跑官方网关 `python -m anyjev.serve` 吗？**
不能。网关的上游必须是 `vllm serve`，而 vLLM 没有 Windows 原生 wheel。
Windows 上走进程内 transformers；要用网关就上 WSL2/Linux。

**Q7：为什么日志里没有来访者原话？**
这是硬约束：`DecisionJournal` 有字段 deny-list，服务访问日志只记方法/路径/状态码。
