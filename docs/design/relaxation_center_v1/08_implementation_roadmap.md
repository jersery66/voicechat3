# Relaxation Center V1 Implementation Roadmap

状态：**FUTURE DESIGN**

## Phase 0 — Design re-freeze

完成本目录和 supersession record；只允许 Markdown，确认旧 Adaptive design 和
旧 Phase A 不是实现 source。

## Phase 1 — Runtime + Catalog

实现 immutable Relaxation contracts、轻量 RelaxationRuntime、deterministic
RelaxationCatalog 和 unit tests。不接 UI、Agent、TurnPolicy、ScaleRuntime、
SessionEngine、games、video、TTS、LLM 或 RAG。

## Phase 2 — UI Shell

实现 Center 首页、分类、content cards、返回聊天和 explicit commands；暂不接
Agent recommendation 和新游戏。

## Phase 3 — Existing content

通过 Center 接入现有 breathing、muscle relaxation 和 videos，保持已有
SessionEngine/ScaleRuntime lifecycle。

## Phase 4 — Games V1

依次接入 `bubble_pop`、`gentle_search`、`calm_puzzle`、`falling_leaves`；每个
游戏独立 deterministic、可退出并有 focused tests。

## Phase 5 — Agent/TurnPolicy

保留高层 `RECOMMEND_RELAXATION`，语义收束为邀请进入 Center，而不是直接启动
内容。Agent 不选择 content；用户进入后自行选择。

## Phase 6 — Context/scale resume

验证聊天 → Center → content → return → 原上下文，以及 active scale explicit
pause → Center → ScaleRuntime resume first unanswered item。

## Phase 7 — Future content

`zen_garden`、`gentle_drift` 和更多视频/练习只在 V1 稳定、隐私和硬件验证后进入
新任务。每阶段都要求 focused tests、full regression、clean worktree。
