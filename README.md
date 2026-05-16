# 心医生 (Heart Doctor) — 专业 AI 心理咨询语音系统

> **科技传递温暖，专业重构新生。**

专为强制隔离戒毒所打造的全场景 AI 心理咨询语音系统。集成动机访谈 (MI)、实时情绪识别、RAG 专家知识库及多媒体康复干预，通过自然语音交互与来访者建立深层情感连接，协助缓解戒断焦虑。

---

## 核心特性

- **MI 动机访谈架构**: 深度集成 OARS（开放式提问、肯定、反映、摘要）技术，严禁说教，通过"以此攻彼"和"双面反映"协助来访者处理矛盾心态。
- **实时情绪与状态监控**: AI 实时识别情绪波动、防御强度及"变革话语"，后台生成动态评估报告。
- **RAG 专家知识库**: 内置临床心理学数据，检测到特定症状（幻觉、自杀倾向、失眠等）时自动调取专业干预方案。
- **语音交互**:
  - STT: FunASR SenseVoiceSmall 毫秒级转写，含毒品术语纠错。
  - TTS: CosyVoice3 流式合成 + 语音克隆，支持 `[breath]`、`[laughter]` 原生标记。
- **双模输入**: 支持语音和键盘文字两种输入方式。
- **交互式放松康复**: 根据对话逻辑自动推荐并播放呼吸、肌肉、冥想等放松训练视频。
- **自动化专业报告**: 会话结束时秒级生成符合临床标准的 PDF/JSON 报告，含风险评估、情绪变化轨迹及干预建议。
- **PySide6 UI**: 毛玻璃视觉风格，左右分栏布局。
- **模型自动检测**: 启动时自动识别 Ollama 可用模型及 FunASR/CosyVoice3 路径。

---

## 架构

### 对话流水线 (ConversationPipeline)

```
Microphone → STT(可选) → Intent+Emotion(并行3B调用) → RAG → LLM Streaming
  → ||| 分隔符解析 → Tag检测(END/REC) → TTS(可选) → 后处理路由
```

`services/pipeline.py` — 统一流水线，合并语音/文字两条路径为单一执行体，Qt 无关纯逻辑层。

### 会话状态机 (SessionOrchestrator)

```
IDLE → CHATTING → RELAXATION_RECOMMENDED → VIDEO_PLAYING → POST_RELAXATION
                   ↘ SESSION_ENDING → SESSION_ENDED → IDLE
```

`services/session_orchestrator.py` — 会话生命周期管理，状态转换验证，结束决策逻辑。

### Tools 工具层

`services/tools/` — 轻量 Tool 模式封装副作用操作：

| Tool | 职责 |
|------|------|
| `VideoPlayTool` | 全屏播放放松视频 |
| `RelaxationRecommendationTool` | 基于对话上下文推荐放松类型 |
| `ReportGenerationTool` | 生成访客反馈 + 研究员报告 + 保存 |

### 关键约定

- **`|||` 分隔符**: LLM 回复中左侧为临床分析（不展示/不播出），右侧为口语回复（播放 TTS）。
- **会话结束标记**: `[END_GOAL_ACHIEVED]`、`[END_TIME_LIMIT]`、`[END_SAFETY]` 等触发会话终止。
- **放松推荐标记**: `[REC_BREATHING]`、`[REC_MUSCLE]`、`[REC_MEDITATION]` 触发全屏放松视频。
- **CosyVoice 原生标记**: `[breath]`、`[laughter]` 嵌入文本由 CosyVoice3 处理，UI 展示时自动过滤。
- **流式管线**: LLM 逐块输出，TTS 预缓冲 5 块后开始播放。

---

## 项目结构

```text
voicechat/
├── main.py                          # 程序入口 (PySide6 Application)
├── config.py                        # 核心配置：System Prompt、模型路径、UI 参数
├── services/
│   ├── pipeline.py                  # 统一对话流水线 + 标签常量（单一数据源）
│   ├── session_orchestrator.py      # 会话状态机 + 结束决策
│   ├── llm_service.py               # Ollama 流式交互
│   ├── stt_service.py               # 实时录音 + FunASR 识别 + 毒品术语纠错
│   ├── tts_service_cosyvoice.py     # CosyVoice3 流式合成 + 语音克隆
│   ├── rag_service.py               # 意图路由 + 关键词知识检索
│   ├── agent_service.py             # 3B 模型意图分类 + 报告生成 (含自动 recheck)
│   ├── report_service.py            # 情绪追踪 + 会话生命周期 + Ollama 连接池
│   ├── report_generator.py          # PDF 报告生成
│   ├── video_service.py             # Pygame 全屏视频播放
│   ├── game_service.py              # 放松小游戏
│   ├── emotion_tracker.py           # 情绪趋势追踪
│   ├── scales.py                    # 心理量表管理
│   ├── stats_service.py             # 统计数据服务
│   ├── logger.py                    # 统一日志配置
│   ├── error_monitor.py             # WARNING+ 日志聚合 → logs/errors.jsonl
│   ├── metrics.py                   # 性能指标收集 (环形缓冲区 + @measure 装饰器)
│   ├── _ollama_pool.py              # 共享 ollama.Client 单例池
│   └── tools/
│       ├── __init__.py              # Tool Protocol 定义
│       ├── video_tool.py            # 放松视频播放 Tool
│       ├── relaxation_tool.py       # 放松类型推荐 Tool
│       └── report_tool.py           # 报告生成 Tool
├── ui/
│   ├── main_window.py               # 主窗口 (左右分栏，管道路由，队列处理)
│   ├── control_panel.py             # 左侧控制面板
│   ├── chat_panel.py                # 右侧对话面板
│   ├── loading_screen.py            # 加载画面
│   ├── dialogs.py                   # 弹窗 (会话结束/危机/继续或结束/警告)
│   ├── widgets.py                   # 自定义组件
│   ├── styles.py                    # QSS 样式表
│   ├── session_review.py            # 会话历史回顾
│   └── stats_panel.py               # 统计面板 (metrics + error log)
├── game/
│   ├── engine.py                    # Pygame 游戏主循环 (安全退出，可重复启动)
│   ├── config.py                    # 游戏常量
│   ├── clinical_tracker.py          # 临床数据追踪
│   ├── entities/                    # 游戏实体
│   └── systems/                     # 游戏子系统 (资源/风暴/营地/难度/背景)
├── data/
│   ├── data_manager.py              # 分层存储 (JSON I/O 抽象，WAV dtype 防御)
│   └── treatment_progress.py        # 治疗进度追踪
├── scripts/
│   └── check_config.py              # 启动前配置健康检查
├── tests/                           # 单元测试 (pytest)
│   ├── test_pipeline.py             # 标签检测 + 清理函数
│   ├── test_rag.py                  # 同义词扩展 + 评分
│   ├── test_data_manager.py         # 会话管理 + WAV 保存
│   └── test_report_service.py       # 报告解析 + 会话追踪
├── pytest.ini                       # pytest 配置
├── requirements-dev.txt             # 开发依赖 (pytest, pytest-mock)
├── knowledge_base/                  # 心理学知识库 (JSON)
├── media_library/                   # 放松训练视频素材
└── data/                            # 会话记录与 PDF 报告
```

---

## 快速启动

### 环境要求

- Windows + NVIDIA GPU (12GB+ VRAM 推荐)
- [Ollama](https://ollama.com/)

### 安装

```bash
git clone https://github.com/jersery66/voicechat3.git
cd voicechat3
pip install -r requirements.txt
ollama pull qwen2.5:72b
python main.py
```

启动时自动运行配置健康检查（Ollama 连通性、模型路径、知识库完整性），然后加载模型并显示进度。进入主界面后可选语音或文字输入开始对话。

### 开发与测试

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

### 外部依赖（不在 requirements.txt）

- **Ollama** 服务，默认 `http://localhost:11434`
- **CosyVoice3** 模型: `../CosyVoice/pretrained_models/Fun-CosyVoice3-0.5B-2512/`
- **FunASR** 模型: `../qwen/CosyVoice/pretrained_models/Fun-ASR-Nano-2512/`

路径可在 `config.py` 中配置。

---

## 配置

所有可调参数在 `config.py`:

- `OLLAMA_MODEL`, `OLLAMA_BASE_URL` — LLM 后端
- System Prompt (~140 行) — MI 咨询规则、OARS 约束、危机协议、CosyVoice 标签规范
- `MAX_CONVERSATION_TURNS`, `SESSION_TIME_LIMIT_MINUTES` — 会话边界
- `CRISIS_HOTLINES` — 紧急联系电话
- 音频参数: `SAMPLE_RATE`, `AUDIO_CHANNELS`, `TTS_*`

---

## 运维与监控

### 配置健康检查

启动前可手动运行：

```bash
python scripts/check_config.py
```

检查 Ollama 服务、FunASR/CosyVoice 模型路径、知识库完整性及数据目录可写性。

### 错误日志聚合

`services/error_monitor.py` 自动采集 WARNING+ 级别日志到 `logs/errors.jsonl`（JSON Lines 格式），并提供内存环形缓冲区供 UI 统计面板调用。

### 性能指标

`services/metrics.py` 提供 `@measure("llm.chat")` 装饰器和 `Metrics.timer()` 上下文管理器，记录关键路径耗时（STT、LLM 首 token、RAG 检索、TTS 合成、报告生成）。统计数据可通过 `ui/stats_panel.py` 查看。

---

*"每一次对话，都是一次心灵的重构。"*
