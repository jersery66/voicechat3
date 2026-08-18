# Games V1 设计规格

状态：**FUTURE DESIGN / Phase 4 实现之前不写游戏代码**

以下游戏的 `content_type=GAME`、`content_role=LEISURE`，只能由进入 Center 的
用户选择，Agent 不主动指定某一个游戏。

统一规则：纯本地 deterministic、无 LLM、随时退出、建议 2–5 分钟、低刺激；
禁止积分、连击、排名、等级、金币、抽奖、奖励循环、Game Over、失败或羞辱反馈；
不得产生 attention/anxiety/willpower/relapse/addiction score。

## `bubble_pop` — 泡泡

缓慢出现泡泡，用户点击后轻微消散，约 2 分钟，可提前退出。不计分、不计连击、
不计速度，只允许轻微动画和极轻声音。

## `gentle_search` — 找一找

从简单低刺激图案中找不同元素，最多 5–8 个 trial，不计时。点错不显示红叉或
失败，只轻微淡出或继续提示。

## `calm_puzzle` — 轻拼图

自然景观、植物、天空或动物图片，4/6/9 块，不计时、不计分、不比较。完成只
显示“拼好了。”。

## `falling_leaves` — 接住落叶

叶子缓慢下落，用户移动接取区域；漏掉无惩罚，约 2 分钟。无生命、死亡、
Game Over、排行榜和高分，可有轻微粒子反馈。

## 开发顺序

```text
V1: bubble_pop → gentle_search → calm_puzzle
V1.1: falling_leaves
V1.2: zen_garden / gentle_drift (future)
```

游戏不接 Agent、LLM、ScaleRuntime 或 SessionEngine business state，必须从
Center 用户选择进入。
