# 🎙️ 心医生 (Heart Doctor) - 专业 AI 心理咨询助手

> **科技传递温暖，专业重构新生。**

**心医生** 是一款专为特殊戒治环境（如强制隔离戒毒所）打造的 **全场景 AI 心理咨询语音系统**。它不仅是一个对话机器人，更是一个集成了动机访谈 (MI)、情绪识别、RAG 专家知识库以及多媒体康复干预的综合性心理支持平台。

通过先进的语音交互技术与深度心理学逻辑，心医生致力于在私密、安全的环境下，与来访者建立深层次的情感连接，协助缓解戒断焦虑，降低防御心理。

---

## ✨ 核心特性

- **🤝 动机访谈 (MI) 架构**：深度集成 **OARS**（开放式提问、肯定、反映、摘要）技术。严禁说教，通过"以此攻彼"和"双面反映"协助来访者处理矛盾心态。
- **👁️ 实时情绪与状态监控**：AI 会实时识别来访者的情绪波动、防御强度及"变革话语"，并在后台生成动态评估报告。
- **📚 RAG 专家知识库**：内置数万条临床心理学数据。当检测到特定症状（如幻觉、自杀倾向、失眠）时，系统自动调取专业干预方案。
- **🎙️ 极致语音交互**：
  - **STT**: 集成 FunASR 毫秒级转写。
  - **TTS**: 搭载 CosyVoice3，支持语音克隆与流式合成，可通过 `[breath]`、`[laughter]` 等原生标记增强表现力。
- **⌨️ 双模输入**：支持语音录入和键盘文字输入两种交互方式，灵活适配不同场景。
- **🌊 交互式放松康复**：系统会根据对话逻辑，适时推荐并自动播放呼吸、肌肉、冥想等放松训练视频。
- **📊 自动化专业审计报告**：会话结束秒级生成符合临床标准的 PDF/JSON 报告，包含风险评警、情绪变化轨迹及干预建议。
- **🎨 现代化 PySide6 UI**：基于 PySide6 (Qt) 实现的 **毛玻璃 (Frosted Glass)** 视觉特效，左右分栏布局，营造宁静、解压的咨询氛围。
- **🔍 模型自动检测**：启动时自动识别 Ollama 可用模型、FunASR 及 CosyVoice3 模型路径，零配置即可运行。

## 🛠️ 技术底座

- **核心大脑**: [Ollama](https://ollama.com/) (Qwen2.5:72b / gemma4 / deepseek-v3 等，可在 `config.py` 切换)
- **语音引擎**:
  - 识别: [FunASR](https://github.com/alibaba-damo-academy/FunASR) (SenseVoiceSmall)
  - 合成: [CosyVoice3](https://github.com/FunAudioLLM/CosyVoice) (Fun-CosyVoice3-0.5B)
- **前端架构**: Python + PySide6 (Qt for Python)
- **知识检索**: 基于 jieba 分词 + 同义词扩展的加权关键词检索系统 (Local RAG)
- **数据管理**: 基于会话 ID 的全流程录音与日志持久化

## 📁 项目结构

```text
voicechat/
├── main.py                  # 程序主入口 (PySide6 Application)
├── config.py                # 核心配置：System Prompt、模型路径、UI 参数、自动检测
├── services/
│   ├── llm_service.py       # Ollama 流式交互封装
│   ├── stt_service.py       # 实时录音与 FunASR 识别
│   ├── tts_service_cosyvoice.py  # CosyVoice3 流式语音合成 + 语音克隆
│   ├── rag_service.py       # 意图路由与专家知识检索
│   ├── report_service.py    # 情绪追踪与会话生命周期管理
│   ├── report_generator.py  # PDF 报告生成
│   ├── video_service.py     # Pygame 全屏视频播放
│   └── game_service.py      # 放松小游戏
├── ui/
│   ├── main_window.py       # 主窗口 (左右分栏布局)
│   ├── control_panel.py     # 左侧控制面板 (用户信息、录音、放松训练)
│   ├── chat_panel.py        # 右侧对话面板 (气泡消息、文字输入)
│   ├── loading_screen.py    # 加载画面
│   ├── dialogs.py           # 弹窗对话框
│   ├── widgets.py           # 自定义组件 (毛玻璃面板、动画按钮)
│   └── styles.py            # QSS 样式表
├── knowledge_base/          # 心理学临床数据集 (JSON)
├── media_library/           # 放松训练多媒体素材
└── data/                    # 结构化会话历史与 PDF 报告
```

## 🚀 快速启动

### 1. 硬件准备
- 建议配备 NVIDIA GPU (12G+ 显存) 以获得最佳语音实时响应体验
- 安装 [Ollama](https://ollama.com/)

### 2. 环境安装
```bash
# 克隆项目
git clone https://github.com/jersery66/voicechat3.git
cd voicechat3

# 安装依赖
pip install -r requirements.txt
```

### 3. 模型准备
确保 Ollama 中已拉取对应模型（系统启动时会自动检测可用模型）：
```bash
ollama pull qwen2.5:72b  # 推荐模型
# 或者使用轻量模型：
# ollama pull gemma4:e2b
```

### 4. 运行
```bash
python main.py
```

启动后系统将自动检测模型路径并显示加载进度，进入主界面后可选择语音或文字输入开始对话。

## 💡 咨询室规则 (Prompt Engineering)

系统在 `config.py` 中定义了极高的咨询伦理准则：
- **[RED_WARNING]**：自动识别自杀/脱逃风险。
- **[OARS_ONLY]**：强制限制回复长度与句式，模拟自然聊天。
- **[EMOTION_TAGS]**：自动在回复中插入 `[breath]`、`[laughter]` 等 CosyVoice 原生标记以增强共情。

---

*"每一次对话，都是一次心灵的重构。" —— 心医生团队*
