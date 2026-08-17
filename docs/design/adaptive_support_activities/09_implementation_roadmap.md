# 实施路线图

状态：**FUTURE ROADMAP / 当前不实施**

所有阶段开始前必须完成真实 frozen runtime 的 RTX PRO 6000 验收。每阶段独立
提交、focused tests、full regression、clean worktree；不得移动既有 tag。

## Phase A — Contracts + Catalog

范围：AgentObservation V2 schema、Activity candidate、ActivityCatalog validation。

不接 UI、LLM 新字段或真实活动。验收：schema/range、unknown field、catalog
eligibility 和 no-runtime-mutation tests。

## Phase B — ActivityRuntime

范围：纯 deterministic single-writer runtime、snapshot、commands、events、失败
恢复和 six-activity state contracts。验收：step/cancel/complete/duplicate start。

## Phase C — TurnPolicy contract

范围：高层 `RECOMMEND_SUPPORT_ACTIVITY`、recommend/start separation、load/stage
矩阵、cooldown、active-scale conflict。不得让 Agent 获得执行权。

## Phase D — 最小活动

只接入 `trigger_detective` 做一条 production-chain mock integration。若发现
当前 runtime authority 冲突，停止并单独记录 CURRENT CONSTRAINT，不顺手重构。

## Phase E — 技能练习

接入 `refusal_rehearsal` 和 `coping_toolbox`。重点测试用户 opt-in、语音输入、
退出、结果事实和 delivery cancellation。

## Phase F — 其他三项

依次接入 `crossroads`、`ten_minute_buffer`、`change_balance`，每个活动单独
catalog entry、runtime steps、UI contract、data schema 和 tests。

## Phase G — UI/Data polish

统一活动 UI、DataManager/ReportService read/sink、隐私日志和 artifact provenance。
不得加入自动评分或疗效结论。

## Phase H — Human usability validation

在获得机构批准、隐私和伦理边界后，使用研究者操作的合成/批准数据做可用性验证。
不得把可用性结果写成临床疗效或复吸率结论。

## Rollback boundary

任一阶段失败可整体禁用 activities，回到现有 `CHAT`/scale/relaxation runtime。
ActivityRuntime 取消不能回滚已经提交的 ScaleRuntime answer，也不能改变
SessionEngine lifecycle。每阶段不得跨阶段修改 Prompt、model profile、STT/TTS、
Delivery 或当前 TurnPolicy 之外的 authority。
