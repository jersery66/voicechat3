# Relaxation Center V1 Test / Acceptance Plan

状态：**IMPLEMENTED / V1 INTEGRATION CLOSURE**

本计划描述已经完成的软件合同；它不把软件测试提升为真实硬件或媒体验收。

## A. Catalog

- core IDs、四个 V1 game IDs、role/category 和 availability 一致；
- duplicate/unknown/invalid metadata fail closed；
- unavailable resources 只报告 `NOT VERIFIED`，不伪造播放成功。

## B. RelaxationRuntime

- `INACTIVE → CENTER → RUNNING → CENTER` complete/cancel；
- duplicate/stale completion 被忽略；
- `CENTER → RETURNING → INACTIVE` 只由明确返回聊天触发；
- snapshot immutable。

## C. Core Relaxation

`breathing`、`muscle_relaxation`、`meditation` 使用既有
`PlayRelaxationCommand` / `POST_RELAXATION` 路径；成功完成进入 Continue/End
选择，provider failure 回 `CHATTING`，不产生成功干预事实。

## D. Leisure

四个 native PySide6 games 通过 `PlayLeisureCommand` 进入 active media；完成/取消
通过 `LeisureFinishedCommand` 回 `CHATTING` 和 Games page，不进入
`POST_RELAXATION`，不调用 `record_relaxation()`。

## E. SessionEngine

- active media 期间阻止 pipeline/重复媒体；
- `playback_kind="leisure"` 与 core relaxation 分开；
- pending end 在媒体释放后安全恢复；
- provider failure 无 ContinueOrEndAsk。

## F. Scale pause/resume

- Center entry 统一暂停 active `ScaleRuntime`；
- 仅 `scale_paused_by_center=True` 的 context 允许返回时 resume；
- resume 永远使用 Runtime first unanswered item；
- compound valid answer 先 commit，再 pause next item；
- ambiguous answer 不评分；
- pre-existing paused scale 不被 Center 自动恢复。

## G. Agent / TurnPolicy

- Agent observe/propose only；
- proactive relaxation 只表示 opportunity，`intervention_type=None`；
- generic request 不默认 breathing；
- explicit core preference 只用于 Center highlight；
- proactive game 被拒绝，explicit game 只打开 Games page。

## H. UI

- Center 是用户选择边界；
- invitation 不自动启动内容；
- core/game/provider failure 的文案和返回路径不混淆；
- offscreen/headless UI regression 通过。

## I. Reporting / privacy

- core completed activity 使用 `type="relaxation"` / `CORE_RELAXATION`；
- leisure usage 使用 `type="leisure"` / `LEISURE`；
- 取消和 provider failure 不写 completed core fact；
- `RelaxationReturnContext` 仅 session-memory，不含 answers/score/clinical data；
- 不记录 game score、reaction time、attention/anxiety/willpower/relapse risk。

## J. Failure recovery

- stale/duplicate content completion；
- SessionEngine reject rollback；
- missing/provider-failed core media；
- pending end during core/leisure；
- paused scale survives until a legitimate resume command。

## K. Licensing

- Bubble Pop mechanics pinned to the documented MIT commit；
- MIT notice retained；
- PyJig、upstream assets、Phaser、WebView、pygame runtime 均未引入。

## L. Real hardware boundary

软件合同测试可以为 `PASS`；真实 RTX PRO 6000、WSL CUDA、vLLM、FunASR、VoxCPM2、
音频设备、实际视频播放和 E2E 仍为 `NOT RUN`，不得由本计划自动提升。
