# voicechat3 离线运行指南（RTX 5090 / Jev 模式）

本指南面向"打开程序、对着麦克风讲话、听到语音回答"的完整离线演示。全程不联网、
不下载模型、不使用任何在线 API。

## 0. 一次性前提（首次部署时检查）

| 项目 | 本机实际位置 | 状态 |
| --- | --- | --- |
| Tacit-1.7B 权重 | `D:\models\tacit\Tacit-1.7B`（11 文件，sha256 已校验） | ✓ |
| 主模型 GGUF（WSL 内） | `/root/models/Qwen3.8-27B-Q4_K_S.gguf` | ✓ |
| llama-server 可执行（WSL 内） | `/root/llama_cpp_python-0.3.36/vendor/llama.cpp/build/bin/llama-server` | ✓ |
| FunASR | `D:\戒毒所\models\Fun-ASR-Nano-2512` | ✓ |
| VoxCPM2 | `D:\戒毒所\models\VoxCPM2` | ✓ |
| 音色提示 | `D:\戒毒所\models\voice_prompt\s1.mp3` | ✓ |
| 放松训练视频 | `media_library\relaxation\呼吸训练.mp4`（本地 VoxCPM 真实合成） | ✓ |
| FSMN-VAD | ModelScope 本机缓存（无缓存时自动降级 RMS 端点检测，功能可用） | ✓ |

## 1. 启动（一条命令）

双击或命令行执行：

```
D:\戒毒所\voicechat3\start_voicechat3_offline.bat
```

它会依次：

1. 在一个**最小化窗口**里启动 WSL 内的主模型服务（llama-server :8000，
   Qwen3.8-27B-Q4_K_S，思考模式关闭）。首次加载约需 1–3 分钟；
   **该窗口请勿关闭**——关闭它主模型服务即停止，重新运行脚本会自动再拉起。
2. 等待 :8000 就绪（最长 10 分钟，超时给出明确报错）。
3. 检查音频输入/输出设备。
4. 报告放松媒体缺失情况（当前已齐备）。
5. 运行完整预检：对话端点、FunASR、VoxCPM、音色提示、Tacit 权重 sha256。
   **任何 FAIL 都会拒绝启动**（strict_preflight）。
6. 启动 GUI。窗口标题栏显示实际决策模式（本配置为 **［决策模式：Jev/tacit］**）。

常用变体：

- `start_voicechat3_offline.bat -PreflightOnly`：只做预检，不启动 GUI。
- `start_voicechat3_offline.bat -SkipLlmStart`：主模型服务已在跑时跳过第 1 步
  （脚本本身幂等，重复运行无害）。

## 2. 配置说明

- 离线演示配置：`config/runtime_offline_5090.yaml`
  （dialogue=llama.cpp :8000；decision=jev/tacit/inprocess；
  escalation=deterministic——Jev 不可用时回退为确定性 CHAT 并留痕，
  **不会静默把回退冒充 Jev**）。
- Legacy Agent（Qwen3-4B, :8001）在本配置下**不启动**，不为回退占用显存。
- 默认 `config/runtime.yaml` 保持 legacy 模式不变；`VOICECHAT_RUNTIME_CONFIG`
  环境变量由启动器自动指向离线配置。

## 3. 显存预算（实测）

| 组件 | 显存 |
| --- | --- |
| Qwen3.8-27B Q4_K_S（llama-server, -ngl 99, 8K ctx） | ~17 GB |
| Tacit-1.7B bf16（进程内） | ~4 GB |
| FunASR Nano | ~2–3 GB |
| VoxCPM2（流式合成） | ~5 GB |
| **全链路峰值（实测）** | **27.4 GB / 32 GB**（无 OOM） |

## 4. 演示操作路径

1. GUI 中确认被试信息，点击麦克风按钮开始录音，说"你好，我最近感觉压力有点大"。
2. 停止说话后 VAD 自动切段 → 转写 → 决策 → 回复语音播报；然后继续下一轮。
3. 量表：说"我想做个量表"（主动请求，立即生效）；或在访谈中被系统邀请后回答
   "好，可以"进入逐题问答，用"完全没有/有几天/一半以上时间/几乎每天"作答。
   说"先不做，我想继续聊"即拒绝并回到访谈。
4. 放松：说"我现在特别烦，能不能做个放松训练"，按引导进入放松中心；
   呼吸训练视频已就绪。
5. 结束：说"今天就先到这里吧"，系统生成告别语与报告并释放资源。

## 5. 故障排查

| 现象 | 处理 |
| --- | --- |
| 预检 FAIL：Dialogue | 确认最小化窗口在运行；`wsl -e bash -c "ps aux | grep llama-server"`；日志 `/root/llama_8000.log`（WSL 内） |
| Decision 校验失败 | 确认 `D:\models\tacit\Tacit-1.7B` 完整（脚本会做 sha256） |
| 无声音 | 检查默认播放设备；GUI 顶部状态会提示语音模块可用性 |
| 识别差 | FunASR Nano 对远场/嘈杂环境敏感；靠近麦克风、安静房间 |
| 端口被占 | :8000 被未知进程占用时启动器会拒绝，不会强杀——先手工处理 |
