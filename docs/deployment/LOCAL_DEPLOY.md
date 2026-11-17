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
Start-HeartDoctor-Local.bat -TextOnly    :: 只装界面依赖，跳过 torch/语音
Start-HeartDoctor-Local.bat -ProvisionModels :: 缺语音权重就自动下载（数 GB）
```

桌面上的「小微老师（本机）」入口指向同一个启动器，双击即可，不需要开终端。

**直接 `python main.py` 也行**：`main.py` 在最前面调用
`deployment/autostart.py::ensure()`，先探测机器、必要时拉起模型栈、生成运行配置，
然后才 import `config`。所以不需要先跑任何脚本，也不用设环境变量。

模型服务单独管理（内部同样走 autostart，不再有第二份实现）：

```powershell
python -m deployment.autostart --status          :: 看 :8000 / :8001
python -m deployment.autostart --start-services  :: 起 WSL llama.cpp 栈
python -m deployment.autostart --stop-services   :: 停
python -m deployment.autostart --write-config    :: 只探测并写配置
```

对应的 PowerShell 包装脚本仍是 `scripts\launch\start_model_stack.ps1`。

## 二、零配置启动：一次启动做四件事

`deployment/autostart.py` 在 `config.py` 被 import 之前运行（`config` 在 import 时
读环境变量，晚一步就失效），按顺序：

1. **尊重显式配置**：若 `VOICECHAT_RUNTIME_CONFIG` 已指向一个存在的文件，直接用它，
   绝不偷偷改写——固定契约的实验不能被"自动修复"破坏；
2. **探测并写配置**：否则调用 `scripts/launch/autoconfigure.py --json` 一次完成
   「探测 → 写 `config/runtime.local.yaml` → 回报结果」；
3. **服务不在就拉起来**：探测不到真实模型服务时启动 WSL 模型栈，等待就绪后重新探测；
4. **导出环境变量**：把 `VOICECHAT_RUNTIME_CONFIG` 指向生成的文件，后续 `config.py`
   自然读到。

全程 best-effort：任何一步失败只打印警告，绝不阻断界面启动。开关：

| 环境变量 | 作用 |
|---|---|
| `VOICECHAT_NO_AUTOSTART=1` | 完全关闭自动探测（诊断、跑测试用） |
| `VOICECHAT_ALLOW_STUB=1` | 没有真实模型时允许用离线联调桩 |
| `VOICECHAT_RUNTIME_CONFIG=...` | 显式钉住某份配置，autostart 不再介入 |

## 三、自动补齐：缺什么就装什么

换一台机器最烦的不是"配置"，而是"这次又缺了什么"。清单和安装逻辑收在
`deployment/dependencies.py`，启动器和应用都调它，不再各写一份。

依赖分三层，按能力而不是按大小划分：

| 层 | 内容 | 缺了会怎样 |
|---|---|---|
| `core` | PySide6 / numpy / PyYAML / sounddevice / openai / reportlab … | 界面打不开 |
| `voice` | torch / torchaudio / funasr / transformers / librosa | 只能文字输入 |
| `tts` | voxcpm | 只有文字输出 |

`python -m deployment.dependencies --check` 给一份体检表：

```
  Python 3.11.9
  GPU    NVIDIA GeForce RTX 5090 | 驱动 617.42 | CUDA 13.4
  torch  2.8.0+cu128 (CUDA) | NVIDIA GeForce RTX 5090
  [OK] 界面必需
  [OK] 语音识别
  [OK] 语音合成
```

`--install --tier core|voice|tts|all` 只补缺的那些（检测方式是
`importlib.util.find_spec`，装完会**重新校验**，不信 pip 的返回码）。

**torch 按显卡选轮子**：Windows 上 PyPI 的 torch 是纯 CPU 版，所以有 N 卡时改从
PyTorch 官方源装 CUDA 版，通道由驱动报告的 CUDA 版本决定（这台机器报 13.4 →
先试 cu130，失败退 cu128/cu126/cu124，最后才退 CPU）。CUDA 版本串两种写法都认：
老驱动是 `CUDA Version: 12.8`，600 系驱动是 `CUDA UMD Version: 13.4`。

**镜像自动选**：清华 → 阿里 → 官方，逐个探活，可用的第一个就用；pip 调用前会清掉
`HTTP_PROXY` 之类只放行白名单的环境代理。

**语音权重**：不在 git 里，也搜不到时，`--download-models` 从 ModelScope 拉
（`FunAudioLLM/Fun-ASR-Nano-2512` ≈2GB、`OpenBMB/VoxCPM2` ≈4.7GB，就是两个语音服务
自己用的 id）。默认不自动下 —— 几 GB 的流量应该在操作者知情时发生，
启动器加 `-ProvisionModels` 或设 `VOICECHAT_ALLOW_DOWNLOAD=1` 才下载。

实测：卸掉 `pydub` 后 `--check` 报 `缺少: pydub（约 1 MB）`，
`--install --tier core` 两秒补回。

**torch 优先复用本机已有安装**：这台线路到 download.pytorch.org 只有约
**0.7 MB/s**，一个 CUDA 轮子要 70 分钟以上。所以装之前先扫本机其它虚拟环境
（`*/.venv/Lib/site-packages` 两层深度，不做全盘遍历），找到**同 Python 版本**
（cp311 的轮子不能用 cp313 加载）且带 CUDA 的 torch，直接 robocopy 过来再验证
`torch.cuda.is_available()`；失败才回退下载。`--no-reuse-torch` 可强制下载。

复用要求 **torch 和 torchaudio 同时在供体里**：只有 torch 的供体会造出一个
"`import torch` 成功、但每个语音服务都挂在 `import torchaudio`" 的假成功环境。

### 全新设备实录

在 `D://voicechat3-fresh`（`git archive` 导出、无 `.venv`、无 `models/`、无
`config/runtime.local.yaml`）跑一键启动：

| 步骤 | 结果 |
|---|---|
| 建虚拟环境 | Python 3.11.9 |
| 界面依赖（20 个） | 88s |
| 语音依赖 | 132s（torch 复用本地 cu128，funasr/transformers/librosa 走镜像） |
| 语音合成依赖 | 约 40s |
| 服务探测 / 配置生成 | `:8000` / `:8001` 就绪 |
| 自检 | **9 项全通过，0 警告 0 失败，总耗时 5m13s** |
| 启动界面并完成一轮对话 | 65s（含 TTS 预热），27B 正常回复 |

对比：如果不用复用、纯下载 torch，同样是这一步但要多等一个多小时。

## 四、探测规则

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

## 五、WSL 模型栈为什么要"保活"

模型服务是 WSL2 里的 Linux 二进制 `llama-server`。WSL2 在没有会话时会回收整个
发行版，`setsid`/`disown` 都留不住进程，服务会随会话一起消失。

所以 `start_model_stack.ps1` 起一个隐藏的 `wsl.exe` 会话并让它 `sleep 86400`，
会话活着服务就活着；`-Stop` 时把 llama-server 和保活进程一起收掉。
保活进程的 PID 记在 `logs\wsl_stack.pid`。

绑 `0.0.0.0` 而不是 `127.0.0.1`：本机 WSL 的 localhost 转发只对通配地址生效。
WSL 的 NAT 网段在物理局域网内不可路由，所以这不等于把模型暴露到网络上；
生产机请加 `-BindLoopback` 保持原来的 `127.0.0.1`。

## 六、本机实测结果

| 能力 | 状态 | 说明 |
|---|---|---|
| 界面 | 正常 | PySide6 offscreen 冒烟通过，截图见 `test_output\smoke_*.png` |
| 对话 27B | 正常 | WSL llama.cpp，`:8000`，Qwen3.8-27B-Q4_K_S |
| Agent 4B | 正常 | `:8001`，Qwen3-4B-Instruct-2507 |
| 语音识别 | 正常 | GPU，7.4s 音频 → **0.46s**（首次调用含预热 1.4s） |
| 语音合成 | 正常 | GPU，一句 ≈ **1.0–1.3s**（首次调用 9.4s 预热），显存 5.1 GB |
| 启动耗时 | 59s | 含 STT/TTS 加载与预热（CPU 版需 5 分钟以上） |
| CUDA | 已启用 | torch 2.8.0+cu128，RTX 5090 32GB |

## 七、GPU 版 torch 的来源

本机不需要重新下载 2.9 GB 的 CUDA 轮子：`D:\speech_alignment_B\.venv` 里已有一套
Python 3.11.9 的 **torch 2.8.0+cu128 + torchaudio 2.8.0+cu128**，直接复制到本 venv 即可
（`torch`、`torchaudio`、`torio` 及三者 dist-info）。这套是从本机迁移来的，
所以文档里不写死路径；换机器时用下面的标准安装命令更稳妥：

```powershell
.venv\Scripts\python.exe -m pip install torch torchaudio ^
    --index-url https://download.pytorch.org/whl/cu128
```

装完后 `python scripts/launch/autoconfigure.py` 重新生成配置即可，代码无需改动。

## 八、降级矩阵

| 缺什么 | 自动行为 | 影响 |
|---|---|---|
| 任何 `/v1` 模型服务，且 WSL 栈起不来 | 启动离线桩（:8010） | 对话是规则模板，**只能联调界面与流程** |
| Agent 服务 | `agent.enabled: false` | 意图/情绪走关键词兜底 |
| torch / funasr | STT 关闭 | 只能文字输入 |
| VoxCPM2 权重或 voxcpm 包 | TTS 关闭 | 只有文字输出 |
| 放松视频 mp4 | WARN | 放松中心只报缺文件 |

## 九、离线桩服务

`scripts/launch/offline_stub_server.py` 是协议兼容的假模型服务：
实现 `GET /v1/models`、`POST /v1/chat/completions`、`POST /v1/completions`（含 SSE 流式），
回复来自规则表，模型 id 固定为 `voicechat-offline-stub`。

> 它不产生任何真实的语言理解或评估结果，**不得用于真实访谈**，
> 也不能把它的输出当作模型输出写进研究数据。

## 十、已知限制

- `scripts\` 下的一批 `test_asr*.py / test_tts.py / test_loop.py` 仍写着实验机的
  `D:\voicechat3`、`D:\models\voicechat3` 绝对路径，属单机诊断脚本，未纳入自动适配。
- 保活进程随启动它的控制台窗口存在；关掉所有相关终端后模型服务会停，
  下次运行 `Start-HeartDoctor-Local.bat` 会自动重新拉起。
- 权重自动下载依赖 ModelScope 可达；上游仓库名若变更需要改
  `deployment/autostart.py` 的 `WEIGHT_SOURCES`。离线机器请提前把权重放进
  `models/`（`Fun-ASR-Nano-2512/` 含 `model.pt`，`VoxCPM2/` 含 `config.json`）。
- 对话大模型（gguf）不在自动下载范围内：20GB 级权重、且不同机器可能用不同量化，
  目前靠本机已有服务或手动放置，`autostart` 只负责发现和拉起。
- 没有 NVIDIA 显卡时 `torch` 会装 CPU 版，语音仍可用但明显变慢（实测 CPU 合成
  22s/句 vs GPU 1.0–1.3s/句）。
