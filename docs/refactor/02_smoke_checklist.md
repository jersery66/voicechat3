# 部署机冒烟清单（Phase 2 影子模式验证）

目的：在真实运行环境验证 SessionEngine 影子模式——引擎与 legacy 流程并行接收同样的生命周期命令，**legacy 保持完全权威**，引擎决策只写日志。应用行为应与重构前完全一致；任何不一致都是引擎接线前必须修复的线索。

## 一、同步代码到部署副本

部署副本在 `D:\program\voicechat0.3\voicechat3`，与 E 盘 git 仓库分离。两种方式任选：

方式 A（部署副本可联网/可建 git）：在副本目录执行 `git fetch <E盘仓库路径或远端> && git checkout` 到最新提交 `4d9e67d`。
方式 B（离线）：从 E 盘仓库复制以下新增/变更项到副本对应位置：

- 新增目录：`core/`、`app/`
- 新增测试：`tests/test_core_*.py`、`tests/test_app_*.py`
- 变更文件：`config.py`、`services/pipeline.py`、`services/report_service.py`、`services/session_orchestrator.py`、`services/session_end_controller.py`、`ui/main_window.py`、`AGENTS.md`

同步后先在部署机跑一次 `python -m pytest tests/ -v`，应得 **183 passed**。

## 二、影子模式开关

- 默认**开启**，无需任何操作。
- 关闭：设置环境变量 `VOICECHAT_ENGINE_SHADOW=0` 后启动。
- 日志位置：`logs/voicechat_YYYYMMDD.log`，过滤关键字 `EngineShadow`。

## 三、冒烟场景与预期日志

每个场景先确认**应用行为与以前完全一致**（这是硬指标），再核对日志。

| # | 操作 | 预期行为（不变） | 预期 [EngineShadow] 日志 |
|---|------|------------------|--------------------------|
| 1 | 启动应用，填写被试信息并确认 | 正常开场问候 | `event state_changed: {"state": "CHATTING"}`（新会话开始） |
| 2 | 正常对话几轮（文字或语音） | 对话、量表触发、RAG 均与以前一致 | 无新增事件（对话轮次不经过引擎，属预期） |
| 3 | 点击任一放松按钮 | 全屏播放放松视频 | `event state_changed: {"state": "VIDEO_PLAYING"}` |
| 4 | 视频播完 | AI 询问感受，回到聊天 | `state_changed POST_RELAXATION` → `continue_or_end_ask` → `CHATTING` |
| 5 | 聊到会话自然结束（说"好多了"或点结束） | 告别语 + 报告 + PDF 流程不变 | `session_ending` 事件；或先出现一次 `relaxation_recommended forced`（结束前强制放松） |
| 6 | 结束流程中若触发强制放松，做完放松后 | 自动继续结束流程 | 视频结束后出现 `session_ending` |
| 7 | 在视频播放中点击结束会话 | 与以前一致（视频结束后走结束） | 视频结束前无 `session_ending`，结束后出现（延迟结束机制生效） |
| 8 | 退出程序（右上角/退出按钮） | 与以前一致 | QUIT 类结束不出现 `relaxation_recommended` |
| 9 | （可选）把会话拖到 40 分钟以上 | 出现一次时间提醒 | `session_warning` 只出现一次 |

**异常信号**（出现任一请记录并反馈）：

- 日志中出现 `[EngineShadow] submit failed` 或 `disabled due to init error`——接线本身有问题；
- 引擎状态变化序列与 legacy 明显不一致（例如 legacy 已结束、引擎还在 CHATTING）——命令转发漏点；
- 应用行为与重构前有任何差异——理论上影子模式不可能影响行为，若出现差异说明接线引入了副作用，请立即用 `VOICECHAT_ENGINE_SHADOW=0` 关闭并反馈。

## 四、验证通过后

影子日志确认一致后，下一步（Phase 2 stage 3）把权威从 legacy 切换到引擎，并做适配器接口化（LLM/STT/TTS/Video/Storage 的 Protocol 抽象），为 Phase 3 进程分离做准备。
