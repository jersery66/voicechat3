# 决策服务离线部署手册（Tacit-1.7B / 2B · Windows + WSL2）

> 目标环境：**完全离线的访谈机**（无外网、无代理、无 HuggingFace 访问）。
> 主模型 Qwen3.8-27B + llama.cpp 不动；决策服务是**独立进程**，只经 `127.0.0.1` 与主应用通信。

## 0. 一句话结论

离线部署只有四件事：**权重进目录、manifest 落盘、依赖进独立 venv、断网跑一遍验收**。
四件事里任何一件没做，服务都拒绝启动（这是设计，不是故障）。

## 1. 拓扑

```
应用进程 (python -m ... / GUI)
   │  仅 HTTP，127.0.0.1
   ▼
决策服务进程 (python -m decision.service)
   │  transformers + 本地权重目录
   ▼
models/Tacit-1.7B  (config.json / tokenizer* / *.safetensors)
```

- 应用**不会** import torch。`decision/engine/` 只有在服务进程里才加载 transformers。
- 服务只监听回环；`--allow-remote` 需要显式给出，`DecisionService` 默认拒绝非回环 host。
- 主模型、ASR、TTS 的启停不受影响（`manage_model_servers: false`）。

## 2. 离线安装包制作清单（在**联网机**上做）

### 2.1 权重

```bash
# 优先 1.7B，2B 作为升级候选；两者目录结构相同
huggingface-cli download morriszjm/Tacit-1.7B --local-dir Tacit-1.7B
# huggingface-cli download <Tacit-2B repo> --local-dir Tacit-2B
```

目录必须包含：

```
config.json
tokenizer.json（或 tokenizer_config.json / tokenizer.model / spiece.model 之一）
*.safetensors（分片时还必须带上 *.index.json 与**全部分片**）
```

### 2.2 完整性清单

```bash
python scripts/decision/tacit_model_check.py \
    --model-path Tacit-1.7B --write --model-id morriszjm/Tacit-1.7B
```

生成 `Tacit-1.7B/tacit_manifest.json`（逐文件 size + sha256）。**与权重一起拷走**。

> 为什么必须哈希而不是只看大小：同尺寸的比特翻转不会改变大小，`verify` 会以
> `sha256_mismatch` 拒绝；截断则先被 `size_mismatch` 拦下。

### 2.3 Python 依赖（独立 venv，不污染主 `.venv`）

```bash
pip download -r deployment/offline/requirements-decision.lock.txt -d wheelhouse --only-binary=:all:
# torch 的 CUDA 轮子要指定索引，否则默认拿 CPU 版：
pip download torch==2.11.0+cu130 --index-url https://download.pytorch.org/whl/cu130 -d wheelhouse
python scripts/decision/wheelhouse.py seal --dir wheelhouse
```

在离线机：

```bash
python scripts/decision/wheelhouse.py verify --dir wheelhouse
python -m venv <bundle>/venv-decision
<bundle>/venv-decision/Scripts/pip install --no-index --find-links wheelhouse \
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

```bash
python scripts/decision/offline_decision_check.py \
    --model-path /models/Tacit-1.7B \
    --cases scripts/benchmarks/cases/decision_cases_synthetic.jsonl
```

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

## 6. 1.7B vs 2B 基准

```bash
python scripts/benchmarks/decision_benchmark.py --model-path /models/Tacit-1.7B \
    --label Tacit-1.7B --order-rounds 2 --out test_output/benchmarks
python scripts/benchmarks/decision_benchmark.py --model-path /models/Tacit-2B \
    --label Tacit-2B  --order-rounds 2 --out test_output/benchmarks
python scripts/benchmarks/decision_benchmark.py --compare \
    test_output/benchmarks/tacit-1.7b.json test_output/benchmarks/tacit-2b.json
```

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

## 7. 显存预算（RTX 5090 32GB）

| 组件 | 估算 |
| --- | --- |
| Qwen3.8-27B Q4_K_S（llama.cpp，主模型） | ~16–17 GB |
| Fun-ASR | ~1–2 GB |
| VoxCPM / CosyVoice TTS | ~1–2 GB |
| Tacit-1.7B bf16 | ~3.4 GB 权重 + KV/激活，实测看 `/v1/stats` |
| Tacit-2B bf16 | 约 +0.6 GB |

结论：1.7B 与主模型同时驻留可行；2B 也可行但余量更小，需在 `float16` 下复测。
**以实测为准**：`offline_decision_check.py` 会同时给出进程内 torch 峰值与整卡峰值。

## 8. 安全回退

| 故障 | 表现 | 回退 |
| --- | --- | --- |
| 权重缺失/损坏 | 服务拒绝启动（退出 3） | `mode: legacy` 或 `escalation_mode: legacy` |
| 服务未起/端口不通 | provider 报传输错误 | coordinator 按 `escalation_mode` 回退 |
| 决策概率低于阈值 | validator 弃权 | 同上，回到 Qwen Agent |
| 概率非法（NaN/越界/argmax 不一致） | validator 拒绝 | 同上 |
| journal 写失败 | 只记一行日志 | 不影响回合 |

三种回退路径都有测试固定（`tests/test_decision_modes.py`），
且 `TurnPolicy` 始终是唯一执行者——决策层挂了，权限链不断。

## 9. 常见问题

**Q1：`manifest_missing` 怎么办？**
在可信副本上 `--write` 一次，再 `verify`。不要在来源不明的副本上 `--write`——那等于给未知文件盖章。

**Q2：`sha256_mismatch` 但大小一样？**
拷贝过程比特损坏或存盘介质问题。重新拷贝，不要 `--no-hashes` 糊过去。

**Q3：`python_version_drift`？**
wheelhouse 是在 3.13 上封的，换了 3.12 就重封或加 `--allow-python-drift`（不推荐）。

**Q4：`OWNERSHIP_MISMATCH` 出现时能直接 stop 吗？**
不能。先 `status --json` 看 pid，确认是不是自己的进程；确认后才 `--force`。

**Q5：为什么日志里没有来访者原话？**
这是硬约束：`DecisionJournal` 有字段 deny-list，服务访问日志只记方法/路径/状态码。
