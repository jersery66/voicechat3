# Relaxation Center V1 Implementation Roadmap

状态：**SOFTWARE CLOSED / PRE-HARDWARE**

## Phase 0 — Design re-freeze

完成本目录和 supersession record；只允许 Markdown，确认旧 Adaptive design 和
旧 Phase A 不是实现 source。

## Phase 1 — Runtime + Catalog

实现 immutable Relaxation contracts、轻量 RelaxationRuntime、deterministic
RelaxationCatalog 和 unit tests。不接 UI、Agent、TurnPolicy、ScaleRuntime、
SessionEngine、games、video、TTS、LLM 或 RAG。

## Phase 1.1 — Core / leisure hierarchy

明确 `CORE_RELAXATION` 与 `LEISURE` 的产品角色边界，保留 Catalog 元数据权威。

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

保留高层 `RECOMMEND_RELAXATION`，语义收束为邀请进入 Center/core relaxation，
而不是直接启动内容。Agent 不主动推荐 GAME、VIDEO 或具体 leisure content；
用户进入后自行选择。

## Phase 6 — Context/scale resume

验证聊天 → Center → content → return → 原上下文，以及 active scale explicit
pause → Center → ScaleRuntime resume first unanswered item。

## Phase 4.1 — Leisure lifecycle correction

独立 `PlayLeisureCommand` / `LeisureFinishedCommand`，SessionEngine 追踪 active
leisure media；完成后回 `CHATTING` 和 Games page，不进入 `POST_RELAXATION`。

## Phase 7 — Integration closure / software freeze

完成 provider failure、Center pause/resume、compound scale answer、return context、
report/privacy/licensing audit 和 deterministic software preflight。

Phase 7 之后 V1 软件闭环冻结为 **SOFTWARE CLOSED / PRE-HARDWARE**。真实
RTX PRO 6000、vLLM、STT/TTS、音频设备和 E2E 仍需另行实机验证。

## Post-V1 / Future Content

以下内容不属于 V1，不在本路线中实现：

- `zen_garden`
- `gentle_drift`
- additional leisure videos
- additional core exercises

只有 V1 软件闭环、真实硬件验证和实际用户体验评估完成后，才另行授权。
