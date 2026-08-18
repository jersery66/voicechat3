# Supersession Record

状态：**FUTURE DESIGN / 历史设计不删除**

`301c8db210600b2fb44505c3a2060b31e3b7619d` 的 Adaptive Support Activities 设计
标记为：

```text
SUPERSEDED FOR IMPLEMENTATION
```

原因：旧方案以 psychological-task activities 为中心，可能在普通访谈中插入
过强任务化流程。可复用原则仍包括 Agent 不越权、TurnPolicy authority、用户
opt-in、ScaleRuntime 隔离和非诊断数据边界。

`45fe029c391ab5296d6b2e9acf2dc0108adc8565` 的旧 Phase A：

```text
PRESERVED / DO NOT DELETE / DO NOT MERGE / DO NOT CHERRY-PICK
```

它的 `activity/` contracts/catalog/runtime 不属于 Relaxation Center V1 source
of truth。新实现使用独立 `relaxation/` domain，避免心理任务模型进入放松中心。

Frozen runtime `f33acb57...` 永久保留，不因新功能而移动或改写。
