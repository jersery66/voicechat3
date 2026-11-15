# Phase 3 Existing Content Integration Note

状态：**IMPLEMENTED / Phase 4 native leisure games added separately**

Phase 3 将三个已有 core relaxation 视频接入 Catalog metadata 和统一的
RelaxationRuntime lifecycle：

```text
breathing          → 呼吸训练.mp4
muscle_relaxation  → 肌肉放松.mp4
meditation         → 冥想训练.mp4
```

三个内容的技术 `category=VIDEO`，产品 `role=CORE_RELAXATION`，并要求
`requires_video=true`、`requires_audio=true`。左侧快捷入口和 Center 核心卡片
都进入同一个 `_start_core_relaxation` executor；legacy `VideoPlayTool.FILE_MAP`
只保留兼容 key，不再由 UI 维护 content mapping。

播放期间 RelaxationRuntime 为 `RUNNING`，成功/失败各只允许一次 complete/cancel，
随后返回聊天。SessionEngine 仍通过既有 lifecycle commands 拥有 session/media
状态，ScaleRuntime 仍拥有量表 pause/resume。

当前 `media_library/library_config.json` 没有真实本地 leisure video entries，故
Center 的“看看视频”继续 disabled/内容整理中。四个游戏已在 Phase 4 以 native
PySide6 实现，未接 legacy game engine。Agent/TurnPolicy recommendation 仍属于 Phase 5。
