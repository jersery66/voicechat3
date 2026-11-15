# voicechat3 本机引擎（P2）

本机定位：**最终运行机**。模型在 `D:\models\voicechat3`（25.6 GB，30/30 sha256 已校验），引擎与 Python 环境在本目录。

## 目录

```
D:\voicechat3\
  .venv\              Python 3.13.14 + torch 2.11.0+cu130 + torchaudio + numpy
  bin\llama\          llama.cpp b12069 CUDA 13.4 预编译（llama-server.exe 等 53 个文件）
  scripts\
    run_llm.bat       :8000 主对话模型
    run_agent.bat     :8001 Agent 模型
    healthcheck.py    一键自检
  logs\               服务日志
  _tools\             下载/补片工具（dl.py、patch_parts.py）
```

## 启动

```
D:\voicechat3\scripts\run_llm.bat      :: :8000，Qwen3.8-27B Q4_K_S，ctx 16384
D:\voicechat3\scripts\run_agent.bat    :: :8001，Qwen3-4B-Instruct-2507 Q4_K_M，ctx 8192
D:\voicechat3\.venv\Scripts\python.exe D:\voicechat3\scripts\healthcheck.py
```

两个都是 OpenAI 兼容端点，`/v1/chat/completions`、`/v1/models`、`/health`、`/metrics` 可用。

## 实测数据（本机 RTX PRO 6000 Blackwell 96 GB）

| 项目 | 数值 |
|---|---|
| torch / CUDA | 2.11.0+cu130 / CUDA 13.0，sm_120 |
| fp16 matmul | 261–403 TFLOPS（张量核可用） |
| 27B 加载显存 | 15.23 GiB（全层上卡，-ngl 999） |
| 27B 生成速度 | 67.8 tok/s（llama-bench tg64） |
| 27B prompt 处理 | 约 1958 tok/s（pp256，样本少，参考值） |
| 27B 首字延迟 | 215–253 ms（流式，chat 端点） |
| 4B 短请求 | 53 ms 返回 6 tokens |

## 三个必须知道的点

1. **CUDA 运行时用的是 torch 自带的**。官方 `cudart-llama-bin-win-cuda-13.4-x64.zip`（423 MB）在 GitHub 上拉不下来，改从 `.venv\Lib\site-packages\torch\lib` 复制了 `cudart64_13.dll`、`cublas64_13.dll`、`cublasLt64_13.dll` 到 `bin\llama\`。torch 是 CUDA 13.0、llama 编于 13.4，同一大版本 ABI 兼容，实测设备识别与推理均正常。整目录搬走时这三件跟着走，不需要单独装 CUDA Toolkit。

2. **Qwen3.8 是推理模型，默认会先思考再说话**。实测同一个问题，默认配置 64 tokens 全花在 `reasoning_content` 上、`content` 为空。语音访谈必须关掉思考：
   ```json
   {"chat_template_kwargs": {"enable_thinking": false}}
   ```
   关闭后同一问题 0.78 s 出完整正文。服务端也可加 `--no-reasoning-preserve` 减少无谓 token。

3. **服务默认开 4 个 slot**（`n_slots = 4`，kv_unified）。做单路语音访谈时并发 1 就够，多余 slot 会按比例分走上下文；需要多路并发再用 `-np` 调。

## 2026-11-06 源码已同步进本目录

`D:\voicechat3` 现在是**项目根 + 引擎**合体（和演示机 `D:\戒毒所\voicechat3` 的布局一致）：

- 源码：远端 `exp/agent-jev-decision` @ `b98c5c8`，zip 快照 594 个文件，见同目录 `.snapshot.json`。
- 原始项目 README 覆盖了根 `README.md`；本文件改名为 `README_ENGINE.md`（本机专属文档，未纳入上游仓库）。
- `models` 是目录联接（junction）→ `D:\models\voicechat3`，让 `config.py` 的 `OFFLINE_MODELS_ROOT`
  直接命中 ASR/TTS 资产，不必改运行配置。
- 启动链路改成本机原生：`start_voicechat3_offline.bat` 不再调用 `wsl.exe`，改为
  `scripts\engines\llama_cpp\start_dialogue_only.bat`（新增，Windows 版 `start_dialogue_only.sh`，
  参数与之逐项对齐：`-c 8192 -ngl 99 -np 1 --alias Qwen3.8-27B-Q4_K_S --jinja
  --chat-template-kwargs {"enable_thinking":false}`）。

> 注意：`start_voicechat3_offline.bat` 与 `scripts\deployment\start_offline.ps1` 属**上游跟踪文件**，
> 本机为跑通改了两处 WSL 相关文本。将来若要从本机提交，先把这两处改回，或把改动作为
> "Windows 原生 llama-server 选项"提给上游。

## 环境与资产现状（2026-11-06 22:0x 更新）

- **Python 依赖已装齐**：按 `requirements.txt` + `requirements-core.txt` + `requirements-gpu.txt`
  装完，共 174 个包，`.venv` 5.1 GB；`pip check` 无破损依赖，22 个关键模块全部可导入。
  - 源用清华（pypi.org 直连 502）。**坑**：WorkBuddy 通过 `PYTHONPATH` 注入了
    `cli\vendor\shim\sitecustomize.py`，它 hook 了 `os.unlink` 的批量删除守卫，
    pip 覆盖安装既有包时会触发 `SystemExit: 1`。装包时要 `env -u PYTHONPATH`。
- **Tacit-1.7B 已就位并校验**：`D:\models\tacit\Tacit-1.7B`，来自 `morriszjm/Tacit-1.7B`
  （hf-mirror），`model.safetensors` 3,441,185,608 字节，sha256 与 HF LFS oid 一致；
  `tacit_manifest.json` 已用 `tacit_model_check.py --write` 生成（11 文件 / 3297 MiB）。
  下载用 8 连接分段（单连接只有 ~0.5–0.8 MB/s，聚合约 3 MB/s）。
- **预检全绿**：`scripts\check_config.py` 除两条 WARN（音色提示、放松训练视频）外全部 OK，
  含 Dialogue 别名 `Qwen3.8-27B-Q4_K_S`、FunASR、VoxCPM2、知识库、决策层完整性。
- **批处理换行符已修**：仓库里 6 个 `.bat` 全是裸 LF（上游在 WSL 里写的），cmd.exe 会把
  多行并成一行执行、刷一堆「不是内部或外部命令」。已全部转 CRLF。上游这条值得提个 PR。

## 还没做的

- **音色提示 `s1.mp3` 缺**：`config.py` 回退路径是 `D:\voicechat\data\s1.mp3`（不存在）。
  放到 `D:\voicechat3\data\s1.mp3` 即被自动识别（同目录已有 `S1.txt` 文本）。
- **放松训练视频缺**：`media_library\relaxation` 只有背景图，mp4/flac 被 .gitignore 排除，
  预检只 WARN，核心放松流程会降级。
- **本机音频设备不支持 16 kHz（真正卡住启动的一条）**：Realtek 三个输入（麦克风/线路输入/
  立体声混音）只接受 44100/48000，`SAMPLE_RATE = 16000` 直接开流会 `paInvalidDevice -9996`，
  `start_offline.ps1` 的 [2/5] 音频检查 FATAL；`services/stt_service.py` 采集处同样写死 16 kHz，
  没有采样率回退。两条路：系统声音设置里把输入设备默认格式改成 16 kHz，或改采集为
  设备原生率 + 重采样到 16 kHz。
- 主模型现在被 `start_dialogue_only.bat` 以 `-c 8192` 拉起（对齐演示机），
  和 `scripts\run_llm.bat` 的 `-c 16384` 不同；单路访谈够用，需要长上下文再调。
- :8000 旧实例（P2 起的 `qwen3.8-27b` 别名）已停，换成本配置要求的 `Qwen3.8-27B-Q4_K_S`；
  :8001 的 4B 实例仍在跑，jev + deterministic 模式下用不到它。
