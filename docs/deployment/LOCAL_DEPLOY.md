# 本机部署说明（自动适配）

对应两条并存的启动路径，互不影响：

| | 实验机（生产） | 本机（开发 / 联调） |
|---|---|---|
| 启动 | `Start-HeartDoctor.bat` | `Start-HeartDoctor-Local.bat` |
| 配置 | `config/runtime.yaml`（固定契约，不入库改动） | `config/runtime.local.yaml`（自动生成，已 gitignore） |
| 预检 | `strict_preflight: true`，失败即拒绝启动 | 按探测结果决定；降级而非崩溃 |
| 模型 | 手工起 llama.cpp，:8000 / :8001 | 自动探测；没有服务时尝试拉起 WSL 模型栈，再不行用离线桩 |
| 语音 | FunASR + VoxCPM2 | 本机已有真实权重，直接启用 |

本机启动器通过 `VOICECHAT_RUNTIME_CONFIG` 把应用指向自动生成的
`config/runtime.local.yaml`，**生产的 `config/runtime.yaml` 一个字节都不改**。

## 一、启动

```bat
Start-HeartDoctor-Local.bat              :: 自检 → 装依赖 → 探测/拉起模型 → 开界面
Start-HeartDoctor-Local.bat -CheckOnly   :: 只自检并生成配置
Start-HeartDoctor-Local.bat -NoRepair    :: 只报告，不动磁盘
Start-HeartDoctor-Local.bat -NoStub      :: 没模型服务也不启动离线桩
```

模型服务单独管理：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\launch\start_model_stack.ps1 -Status
powershell -ExecutionPolicy Bypass -File scripts\launch\start_model_stack.ps1          # 起 WSL llama.cpp 栈
powershell -ExecutionPolicy Bypass -File scripts\launch\start_model_stack.ps1 -Stop    # 停
```

## 二、自动适配做了什么

`scripts/launch/autoconfigure.py`（纯标准库，装依赖之前就能跑）依次判断：

1. **模型服务**：探测 `127.0.0.1:{8000,8001,8080,11434,8010}` 的 `GET /v1/models`，
   以及 `wsl hostname -I` 得到的 NAT 地址（本机 WSL 的 localhost 转发只对通配
   绑定生效，所以两种地址都试）。真实权重服务永远优先于离线桩。
2. **模型名**：以 `config/runtime.yaml` 里写的主模型名为首选，但只在服务端真的
   提供它时才采用；否则按 Qwen 优先挑一个实际存在的 id。
3. **Agent**：存在第二个真实服务才启用；否则 `agent.enabled: false`，应用走它
   自己文档化的关键词兜底路由。
4. **ASR / TTS 权重**：不再只认 `<repo>/models`，而是在各盘符一级子目录里找
   `models/`（本机权重在 `D:\戒毒所\models\`），找到就把**绝对路径**写进配置；
   找不到则留 `auto` 交由 `config.py` 探测。
5. **strict_preflight**：只有真实权重服务应答时才写 `true`；桩模式写 `false`，
   避免桩崩了把界面锁在门外。

`config.py` 的数据根目录也改成可移植解析：
`VOICECHAT_DATA_DIR` → `<程序目录>/runtime/data` → 仅当 `D:\program\voice_chat_data`
已存在时才沿用（保留老机器的历史会话）。

## 三、WSL 模型栈为什么要"保活"

模型服务是 WSL2 里的 Linux 二进制 `llama-server`。WSL2 在没有会话时会回收整个
发行版，`setsid`/`disown` 都留不住进程，服务会随会话一起消失。

所以 `start_model_stack.ps1` 起一个隐藏的 `wsl.exe` 会话并让它 `sleep 86400`，
会话活着服务就活着；`-Stop` 时把 llama-server 和保活进程一起收掉。
保活进程的 PID 记在 `logs\wsl_stack.pid`。

绑 `0.0.0.0` 而不是 `127.0.0.1`：本机 WSL 的 localhost 转发只对通配地址生效。
WSL 的 NAT 网段在物理局域网内不可路由，所以这不等于把模型暴露到网络上；
生产机请加 `-BindLoopback` 保持原来的 `127.0.0.1`。

## 四、本机实测结果

| 能力 | 状态 | 说明 |
|---|---|---|
| 界面 | 正常 | PySide6 offscreen 冒烟通过，截图见 `test_output\smoke_*.png` |
| 对话 27B | 正常 | WSL llama.cpp，`:8000`，Qwen3.8-27B-Q4_K_S |
| Agent 4B | 正常 | `:8001`，Qwen3-4B-Instruct-2507 |
| 语音识别 | 正常 | FunASR，7.4s 音频 → 1.7s，转写与 `data/S1.txt` 一致 |
| 语音合成 | 可用但慢 | VoxCPM2 在 **CPU** 上 3.8s 语音 ≈ 22s，warmup 约 2 分钟 |
| CUDA | 未启用 | 见下一节 |

## 五、把语音换成 GPU（可选，建议）

当前 venv 里是 **CPU 版 torch**（PyPI 镜像只有 CPU 轮子）。要跑满 5090：

```powershell
.venv\Scripts\python.exe -m pip install --force-reinstall torch torchaudio ^
    --index-url https://download.pytorch.org/whl/cu128
```

体积约 2.9 GB。本机经代理实测只有 ~0.8 MB/s（约 1 小时），直连会快很多。
装完后 `python scripts/launch/autoconfigure.py` 重新生成配置即可，代码无需改动。

## 六、降级矩阵

| 缺什么 | 自动行为 | 影响 |
|---|---|---|
| 任何 `/v1` 模型服务，且 WSL 栈起不来 | 启动离线桩（:8010） | 对话是规则模板，**只能联调界面与流程** |
| Agent 服务 | `agent.enabled: false` | 意图/情绪走关键词兜底 |
| torch / funasr | STT 关闭 | 只能文字输入 |
| VoxCPM2 权重或 voxcpm 包 | TTS 关闭 | 只有文字输出 |
| 放松视频 mp4 | WARN | 放松中心只报缺文件 |

## 七、离线桩服务

`scripts/launch/offline_stub_server.py` 是协议兼容的假模型服务：
实现 `GET /v1/models`、`POST /v1/chat/completions`、`POST /v1/completions`（含 SSE 流式），
回复来自规则表，模型 id 固定为 `voicechat-offline-stub`。

> 它不产生任何真实的语言理解或评估结果，**不得用于真实访谈**，
> 也不能把它的输出当作模型输出写进研究数据。

## 八、已知限制

- `scripts\` 下的一批 `test_asr*.py / test_tts.py / test_loop.py` 仍写着实验机的
  `D:\voicechat3`、`D:\models\voicechat3` 绝对路径，属单机诊断脚本，未纳入自动适配。
- 保活进程随启动它的控制台窗口存在；关掉所有相关终端后模型服务会停，
  下次运行 `Start-HeartDoctor-Local.bat` 会自动重新拉起。
