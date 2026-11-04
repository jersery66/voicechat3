# voicechat3 离线语音心理访谈——真实运行验收报告

日期：2026-11-04 ｜ 分支：`exp/agent-jev-decision` ｜ 基线：`6da3d6d` → 本次提交
硬件：RTX 5090 32GB，Windows 11 + WSL2（主模型服务）
配置：`config/runtime_offline_5090.yaml`（jev / tacit / inprocess / 确定性回退）

## 0. 结论

**软件链路（ASR→Jev→TurnPolicy→LLM→TTS→音频）已在真实模型上全链路验收通过
（6/6 场景，31 轮真实语音轮次）。** 显存峰值 27.4/32 GB，无 OOM。
物理麦克风/扬声器的最终人工验收（A–G 项中的"听到/说出"部分）提供了可执行的
工具与步骤，标记为**等待人工硬件验收**。

## 1. 本轮修复的真实缺陷

| # | 缺陷 | 修复 | 影响 |
| --- | --- | --- | --- |
| 1 | **VoxCPM 提示音泄漏**：`ref_continuation` 提示缓存下，合成音频以完整重放的提示句开头（约 2–8 秒）；非流式路径泄漏整段提示音，流式播放也漏前缀。真实使用中每句回复前都会先"朗读"提示词 | `TTSService.load_model` 改为 reference-only 提示缓存（`tts_service_voxcpm.py`） | 每轮 TTS；修复后合成音经真实 FunASR 回测逐字正确 |
| 2 | 放松训练媒体完全缺失（`media_library/relaxation/` 不存在，核心放松 100% 走降级分支） | `scripts/generate_relaxation_media.py`：本机真实 VoxCPM 合成呼吸引导语音（189s）+ PIL 背景 + ffmpeg 合成 `呼吸训练.mp4`（3.9MB，ffprobe 全解码验证） | 放松核心路径从不可用变为可用 |
| 3 | `check_ollama` 导入已删除的 `OLLAMA_MODEL`（切 ollama provider 时预检 ImportError） | 移除失效引用 | 条件触发的启动阻断 |
| 4 | 启动脚本残留 RTX 3060/Ollama/qwen2.5:3b 且 `immutable_contract` 下全部无效却仍拉起 Ollama serve | 新建 `start_voicechat3_offline.bat` + `scripts/deployment/start_offline.ps1`，旧脚本不动 | 启动可靠性 |
| 5 | WSL 后台进程随会话回收（llama-server 静默死亡，日志为空） | 启动契约改为**前台常驻**：bat 用 `start /min` 打开独立窗口运行 `start_dialogue_only.sh`（幂等，端口被占即退出，不强杀） | 主模型服务存活 |
| 6 | GUI 未显示实际决策模式 | 窗口标题显示［决策模式：Jev/tacit / Shadow / Legacy］ | 回退不冒充 Jev 的可见性 |

上一轮已提交的两阶段量表契约（`6da3d6d`）保留；本轮未改动任何决策算法、阈值或标准答案。

## 2. 自动化验收（真实模型，无 Mock 链路）

工具：`scripts/acceptance/offline_voice_e2e.py`（headless，镜像 GUI 的服务装配）。
**唯一的合成元素**：参与者的"声音"由真实 VoxCPM 以音色提示合成（作为真人替代），
每条 take 经真实 FunASR 回读校验（相似度 ≥0.6，最多 4 次），校验失败的 take 重新
合成并全部留痕；管线随后对保留下来的音频独立执行真实 ASR。
**场景 C/D 的量表提议为开发模式注入**（在证据与输出中明确标记
`dev_injection_test_entry`），注入之后的邀请话术、TTS、同意解析、OfferLedger、
ScaleRuntime、逐题解释与计分全部是真实链路。

最新运行（`test_output/offline_e2e_perf/20261104_194032/evidence.json`）：

| 场景 | 结果 | 真实证据 |
| --- | --- | --- |
| B 显式请求量表（语音"我想做个量表"） | **PASS** | `start_scale/user_scale_request`；PHQ-9 九题全部经语音作答并计分（tags 1–9 完整，`scale_completed=True`）；31 段用户 wav + 51 段回复 wav |
| A 连续 5 轮真实语音聊天 | **PASS** | ASR→Tacit 决策→TurnPolicy→27B 流式回复→VoxCPM 音频，逐轮留档 |
| C 邀请→同意→量表（注入提议） | **PASS** | 真实 OfferLedger 邀请 + 语音同意解析 + GAD-7 逐题完成 |
| D 邀请→拒绝→继续聊天（注入提议） | **PASS** | `offer_scale_declined`，未启动量表，访谈继续 |
| E 显式请求放松（"特别烦"未被误判否定） | **PASS** | `recommend_relaxation/user_relaxation_request` |
| F 语音结束会话 | **PASS** | `end_session/explicit_end`，告别语与资源释放 |

历史运行证据（含中间失败与修因）：`test_output/offline_e2e/`（保留全部 8 次迭代）。

### 自然触发的如实报告（不伪造 Jev 建议）

- **Jev 自然提出量表邀请：0 次**。与留出集诊断一致（Tacit 主动提议率低），
  本轮未通过调阈值伪造提升。
- **生产端确定性触发：真实发生 1 次**——症状线索累积后
  `start_scale(deterministic_scale_signal)` 自动启动 GAD-7 并逐题运转（19:13 运行）。
- **用户主动请求路径：真实可靠**（`user_scale_request`，不受主动建议最低轮数限制）。
- **放松：Jev 主动建议与用户请求路径均真实运转**（E 场景 + D 场景后的主动推荐）。

### 发现但未在本轮修改的问题（记录待办）

1. **显式请求的目标选择**：说"我想做一下焦虑量表"时，策略按候选表首位启动
   PHQ-9（抑郁）而非 GAD-7（焦虑）——显式请求路径缺少"目标确认"一步
   （证据：`test_output/offline_e2e/20261104_191359/`）。与两阶段契约的
   阶段二（目标解析）对接是下一步的自然位置。
2. FunASR Nano 对合成语音存在幻听/重复（真实麦克风音频通常更好，待人工验收确认）；
   LLM 对含噪转写表现出良好的容错（复述确认而非硬接）。
3. `enable_streaming_tts` 配置键无消费者（行为恰好一致，纯文档问题）。

## 3. 性能（RTX 5090 实测，31 轮）

| 指标 | 数值 |
| --- | --- |
| 显存峰值（全链路） | **27.4 GB / 32 GB**（无 OOM；LLM ~17G + Tacit ~4G + FunASR ~2.5G + VoxCPM ~5G） |
| LLM 首 token（TTFT） | 中位 **704 ms**（min 284 / max 1327） |
| 决策层（Tacit 进程内） | 中位 < 1 ms（含偶发 2.1 s 首轮加载摊销） |
| TurnPolicy | < 1 ms |
| 单轮墙钟（含合成用户语音+双向 ASR/TTS 留证） | 中位 **1.6 s**（min 0.7 / max 4.0） |

生产路径（麦克风输入、扬声器播放）的端到端延迟 = 上述 + 录音 VAD 等待 +
扬声器播放，待人工验收实测。冷启动（模型加载）与热态已区分：首轮 agent_ms
含 Tacit 加载，后续轮次 < 1 ms。

## 4. 真实硬件验收（等待人工执行）

按 `docs/deployment/offline_run_guide.md` 启动后逐项执行并记录：

| 项 | 内容 | 状态 |
| --- | --- | --- |
| A | 连续 5 轮真实麦克风语音聊天 | **等待人工硬件验收** |
| B | 一次完整语音量表问答（说"我想做个量表"） | **等待人工硬件验收** |
| C | 一次完整放松训练（呼吸训练视频） | **等待人工硬件验收** |
| D | 拒绝量表并继续聊天 | **等待人工硬件验收** |
| E | 拒绝/跳过放松并继续聊天 | **等待人工硬件验收** |
| F | 聊天→量表→聊天→放松→聊天组合会话 | 软件链路已验证；硬件版**等待人工验收** |
| G | 断网启动及完整运行 | 全部组件本地（无网络调用）；预检通过即等效，建议拔网线复跑一次 |

每项记录模板（填写到本报告附录或单独文件）：PASS/FAIL/BLOCKED、实际听见的内容、
GUI 标题显示的决策模式、异常与截图。

## 5. 离线与安全核查

- 无在线 API、无模型下载（`allow_model_download=false`，预检 sha256 校验）。
- 决策端点仅回环（`allow_remote_endpoint=false`）。
- 访谈原文不进调试日志（state block 设计如此）；研究事件按既有 EventJournal 通道。
- 量表/放松不经用户同意不启动；拒绝即时回到访谈；TurnPolicy 最终执行权未动。
- 本轮未将量表得分表述为诊断；放松不替代危机处置（风险门未改动）。

## 6. 测试与提交

- 全量自动化测试：1669 项通过（本轮新增/修改后复跑确认）。
- 提交：见本次 git log（本报告随代码提交，SHA 以 git 为准）。
