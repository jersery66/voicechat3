# 功能清单（重构回归基线）

本清单是架构重构期间的**回归基线**：重构的每一步结束后，以下全部功能必须保持可用。
验证方式分两类：**自动** = pytest 覆盖；**冒烟** = 需在部署机（GPU + Ollama + 麦克风）手工执行。

| # | 功能 | 实现位置 | 验证方式 | 冒烟要点 |
|---|------|----------|----------|----------|
| F01 | 语音输入：录音 + VAD 自动停录 | stt_service.py, main_window._run_pipeline | 冒烟 | 说话后自动停录并转写 |
| F02 | ASR 纠错（毒品术语/症状语境：进场→经常、又→有） | pipeline.correct_asr_text | 自动（需补测试）+ 冒烟 | 转写文本被正确纠正 |
| F03 | 文字输入（键盘发送） | chat_panel.ChatInput, main_window._on_text_submitted | 冒烟 | 文字能进入对话 |
| F04 | Agent 统一路由（chat/start_scale/continue_scale/recommend_relaxation/recommend_game/exit） | agent_service.route_conversation_actions, pipeline | 冒烟 | 3B 模型决定何时进入量表/放松；超时不阻塞主流程 |
| F05 | Agent 关键词兜底（模型不可用时） | agent_service._keyword_* | 冒烟 | 关闭 Ollama 3B 后对话仍可继续 |
| F06 | 危机/安全链路 | `safety/**` legacy source | 暂时脱离生产；Phase 1 boundary test 保证 main import graph 不可达 |
| F07 | RAG 知识检索（同义词扩展 + 加权评分 + 注入） | rag_service.py | 自动 test_rag.py | 提到失眠/戒断时注入知识 |
| F08 | RAG 截断策略（量表期 500 字 / 常规 1200 字） | pipeline 调用 rag_service | 自动（需补） | 量表问答期间上下文变短 |
| F09 | LLM 流式对话（Ollama, num_predict=1024） | llm_service.chat | 冒烟 | 流式气泡逐字显示 |
| F10 | `|||` 分隔解析（含反向格式 spoken|||analysis） | pipeline._stream_llm | 自动（需补） | 分析侧不展示不朗读 |
| F11 | 内部标签泄漏防护（_FORBIDDEN_INTERNAL_TERMS） | pipeline | 自动（需补） | 口播中不出现"高防御/PHQ-9"等术语 |
| F12 | 历史压缩摘要（超长对话） | llm_service._maybe_summarize | 冒烟 | 长对话不丢失关键信息 |
| F13 | 量表体系：PHQ-9 / GAD-7 / PCL-5 定义与计分 | scales.py, ScaleManager | 自动（部分） | 计分正确 |
| F14 | 症状信号累计评分（per-scale delta，阈值≥3 触发） | pipeline.score_symptom_signals | 自动（需补） | 多次提及症状后进入量表 |
| F15 | 对话中自然施测量表（隐藏探针，逐题推进） | pipeline + NATURAL_SCALE_QUESTIONS + SCALE_ITEM_CORES | 冒烟 | 量表不像问卷，逐题自然提问 |
| F16 | 量表中断恢复（放松/游戏后继续未完成题目） | pipeline.force_resume_incomplete_scale | 冒烟 | 放松回来后继续问下一题 |
| F17 | 量表作答解析（[SCALE:...] 标签 + 短回答推断 + 无分提示持久化） | pipeline.infer_scale_score_from_text, _score_short_scale_answer | 自动（需补） | "嗯/经常/没有"能被计分 |
| F18 | 情绪识别与趋势追踪（含干预提示） | agent_service.detect_emotion, emotion_tracker.py | 冒烟 | 连续负面情绪触发干预提示 |
| F19 | 放松训练推荐规则（每会话一次、量表等待期禁止、仅在题目间隙） | pipeline 放松规则 | 冒烟 | 同一会话不重复推荐 |
| F20 | 放松视频全屏播放（呼吸/肌肉/冥想三选一） | video_service.py, tools/video_tool.py, media_library | 冒烟 | 视频能播、Ctrl+Alt+Q 可退出 |
| F21 | 放松后跟进（感受询问 + 继续/结束选择 + 超时自动结束） | main_window POST_RELAXATION 流程, ContinueOrEndDialog | 冒烟 | 视频结束后 AI 问感受 |
| F22 | 游戏模块（pygame 小游戏 + 临床数据追踪） | game/, game_service.py | 冒烟 | 游戏能玩、数据落盘 |
| F23 | 游戏推荐流入 LLM 回复 | pipeline（68f04db） | 冒烟 | AI 自然提及游戏 |
| F24 | 娱乐/影音模块（音乐/电影推荐与播放） | media_panel.py, media_library, agent 场景推荐 | 冒烟 | 主动要求时可播放 |
| F25 | 会话生命周期：轮次上限 + 时间上限 + 40 分钟预警 | report_service.should_warn_time_limit | 自动 test_report_service.py | 到时提示且只提示一次 |
| F26 | 结束决策（4 状态 EndSessionDecisionDialog：量表完成度检查） | dialogs.EndSessionDecisionDialog, main_window._request_end_with_readiness_check | 冒烟 | 未完成量表时阻止直接结束 |
| F27 | 结束语生成（口语化告别，报告后播放） | report_service.generate_visitor_feedback | 冒烟 | 结束后听到告别语 |
| F28 | 研究者 JSON 报告（3B 生成，72B 回退） | report_service.generate_researcher_report | 冒烟 | JSON 结构完整 |
| F29 | PDF 报告（含量表评估结果表 + 背景图 + 中文字体） | report_generator.py | 冒烟 | PDF 中文正常、量表分数在列 |
| F30 | 数据存档：每轮 WAV + 文本 + metadata + 会话摘要 | data_manager.py | 自动 test_data_manager.py | 目录结构完整 |
| F31 | 治疗进度跨会话追踪 | treatment_progress.py | 冒烟（注意：主流程写入路径需复核） | 第二次会话能引用进展 |
| F32 | 会话历史回顾（对话回放/情绪曲线/录音播放） | session_review.py | 冒烟 | 能选中历史会话查看 |
| F33 | 统计面板（团体统计 + 运行监控 + PDF 导出） | stats_panel.py, stats_service.py, metrics.py, error_monitor.py | 冒烟 | 面板能打开、导出 PDF |
| F34 | 配置健康检查 | scripts/check_config.py | 自动（本次已验证可运行） | 启动前检查通过 |
| F35 | 模型自动检测（FunASR/CosyVoice/VoxCPM/语音克隆素材/Ollama） | config.py 探测函数 | 自动（导入不崩溃，已验证） | print_model_status 输出正确 |
| F36 | 离线部署支持（VOICECHAT_MODELS_DIR/VOICECHAT_DATA_DIR/OLLAMA_MODEL 环境变量） | config.py, offline_deploy/ | 冒烟 | 离线包可启动 |
| F37 | 量表完成后推荐放松而非直接结束 | pipeline（9d2a15d） | 冒烟 | 量表做完后流程正确 |
| F38 | 问候语/破冰（开场问候、问候快速路径） | config.GREETING_VARIANTS, pipeline greeting fast path | 冒烟 | 开场白自然 |
| F39 | 双模 TTS（VoxCPM2 主用，CosyVoice 遗留，播放锁防并发） | tts_service_voxcpm.py, tts_service_cosyvoice.py | 冒烟 | 语音播放不重叠 |
| F40 | 被试信息管理（填写/确认/修改/下一位被试准备） | control_panel.py, main_window._prepare_next_subject | 冒烟 | 换被试数据隔离 |

## 使用说明

1. 每个重构阶段的验收 = 本清单全部"自动"项通过 + 部署机冒烟抽查至少覆盖冒烟列标粗的关键链路（F04/F15/F16/F20/F21/F26/F29）。
2. 标注"需补测试"的项在 Phase 1 抽取对应模块时同步补齐单测——先有测试，再动代码。
3. 本清单只增不删：新功能加入时必须登记，否则视为回归缺陷。
