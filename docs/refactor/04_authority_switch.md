# 权威切换手册（影子模式 → SessionEngine 权威）

状态：**未就绪，默认关闭**。切换前置条件是部署机冒烟验证通过。

## 开关

| 环境变量 | 默认 | 含义 |
|---|---|---|
| `VOICECHAT_ENGINE_SHADOW` | `1`（开） | 引擎并行镜像生命周期决策，仅记日志 |
| `VOICECHAT_SESSION_ENGINE_AUTHORITATIVE` | `0`（关） | 引擎决策驱动会话（legacy 退居兜底） |

对应 `config.SESSION_ENGINE_SHADOW` / `config.SESSION_ENGINE_AUTHORITATIVE`。

## 前置条件（切换前必须全部满足）

1. 部署机按 `docs/refactor/02_smoke_checklist.md` 跑完全部场景，**应用行为与重构前零差异**；
2. `[EngineShadow]` 日志中引擎状态序列与 legacy 实际流程逐一比对一致（开始/结束/放松/视频/超时五类事件）；
3. 无 `[EngineShadow] submit failed` / `disabled due to init error` 记录；
4. 本机测试全绿（`pytest tests/`，当前基线 205 passed）。

## 切换步骤

1. 冻结功能变更，在部署副本上设置 `VOICECHAT_SESSION_ENGINE_AUTHORITATIVE=1`；
2. 跑一轮完整冒烟（同上清单），重点观察：结束流程单次性（不重复生成报告）、视频中途请求结束的延迟恢复、超时提示单次弹窗；
3. 通过后保持 7 天观察期，期间 logs 里对比引擎事件与实际 UI 行为；
4. 观察期通过后，移除 legacy 决策代码（MainWindow 中的重复状态标志 `_session_ending`、`_pending_end_after_video` 等），收敛到引擎单一事实源。

## 回滚

任何异常：设 `VOICECHAT_SESSION_ENGINE_AUTHORITATIVE=0` 立即回到 legacy 权威（影子模式不受影响）。无需改代码、无需重新部署。

## 尚未实现的切换部分（诚实声明）

截至本提交，`SESSION_ENGINE_AUTHORITATIVE` 只是配置占位：MainWindow 尚未实现读取该开关走引擎路径的分支。这是有意为之——在没有部署机验证的情况下接入权威路径违背"全程可运行"的硬约束。冒烟通过后按上述步骤 4 之前的接线工作单独成提交。
