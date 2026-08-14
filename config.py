# Voice Chat Application Configuration

import os
import fnmatch as _fnmatch

# Application root (this file's directory)
APP_ROOT = os.path.dirname(os.path.abspath(__file__))

# Parent directory (for sibling model folders)
PROGRAM_ROOT = os.path.dirname(os.path.dirname(APP_ROOT))

# _BASE_DIR alias for backward compatibility
_BASE_DIR = PROGRAM_ROOT

# Offline deployment roots.  In an offline bundle the app usually lives at
# <bundle>/app and large model assets live at <bundle>/models.  Developers can
# override this with VOICECHAT_MODELS_DIR without editing config.py.
OFFLINE_MODELS_ROOT = os.environ.get(
    "VOICECHAT_MODELS_DIR",
    os.path.join(PROGRAM_ROOT, "models"),
)
APP_MODELS_ROOT = os.path.join(APP_ROOT, "models")

# Ollama host/model can be pinned by offline launch scripts.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")


# ============== Auto-detect Models ==============
def _find_dir(base, *candidates):
    """Return the first existing directory from candidates under base."""
    for c in candidates:
        p = os.path.join(base, c) if not os.path.isabs(c) else c
        if os.path.isdir(p):
            return p
    return None


def _iter_dirs_bounded(base, max_depth=4, max_dirs=20000):
    """Yield directory paths under base up to max_depth levels.

    Never follows symlinks/junctions (avoids infinite recursion on cyclic
    links) and stops after scanning max_dirs directories (bounds import time
    on very large trees such as PROGRAM_ROOT).

    Semantic differences vs the old recursive glob (documented on purpose):
      - directories deeper than max_depth are not searched;
      - trees behind symlinks/junctions are not descended into (a matching
        link directory itself is still yielded and marker-checked).
    """
    base = os.path.abspath(base)
    base_depth = base.rstrip(os.sep).count(os.sep)
    scanned = 0
    for root, dirs, _files in os.walk(base, followlinks=False):
        depth = root.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= max_depth:
            dirs[:] = []
            continue
        for d in list(dirs):
            scanned += 1
            if scanned > max_dirs:
                # Loud on purpose: silently giving up here used to make
                # model detection nondeterministic and hard to debug.
                print(f"[config] model search truncated: more than {max_dirs} "
                      f"directories under {base}")
                return
            yield os.path.join(root, d)


def _find_model_dir(base, name_patterns, marker_names, max_depth=4):
    """Depth-bounded search for a model directory.

    Returns the first directory under base whose basename matches any of
    name_patterns (fnmatch semantics) and which contains at least one of
    marker_names. Returns None on any error — import must never crash.
    """
    if not base or not os.path.isdir(base):
        return None
    try:
        for d in _iter_dirs_bounded(base, max_depth=max_depth):
            name = os.path.basename(d)
            if any(_fnmatch.fnmatch(name, pat) for pat in name_patterns):
                if any(os.path.exists(os.path.join(d, m)) for m in marker_names):
                    return d
    except OSError:
        pass
    return None


def _detect_funasr():
    """Search for FunASR model in common locations."""
    env_path = os.environ.get("FUNASR_MODEL_PATH")
    if env_path and os.path.isdir(env_path):
        return env_path

    direct_candidates = [
        os.path.join(APP_MODELS_ROOT, "funasr"),
        os.path.join(APP_MODELS_ROOT, "funasr", "Fun-ASR-Nano-2512"),
        os.path.join(OFFLINE_MODELS_ROOT, "funasr"),
        os.path.join(OFFLINE_MODELS_ROOT, "funasr", "Fun-ASR-Nano-2512"),
        os.path.join(PROGRAM_ROOT, "Fun-ASR-Nano-2512"),
        os.path.join(PROGRAM_ROOT, "CosyVoice", "pretrained_models", "Fun-ASR-Nano-2512"),
        os.path.join(PROGRAM_ROOT, "QWEN", "CosyVoice", "pretrained_models", "Fun-ASR-Nano-2512"),
    ]
    for candidate in direct_candidates:
        if os.path.isdir(candidate) and (os.path.exists(os.path.join(candidate, "model.pt")) or os.path.exists(os.path.join(candidate, "model.py"))):
            return candidate

    search_bases = [
        APP_MODELS_ROOT,
        OFFLINE_MODELS_ROOT,
        os.path.join(PROGRAM_ROOT, "CosyVoice"),
        os.path.join(PROGRAM_ROOT, "QWEN", "CosyVoice"),
    ]
    name_patterns = ["Fun-ASR-Nano-2512", "FunASR*", "funasr*"]
    for base in search_bases:
        found = _find_model_dir(base, name_patterns, ("model.pt", "model.py"), max_depth=4)
        if found:
            return found
    return None


def _detect_cosyvoice():
    """Search for CosyVoice model directory."""
    env_base = os.environ.get("COSYVOICE_BASE_DIR")
    env_model = os.environ.get("COSYVOICE_MODEL_PATH")
    if env_base and os.path.isdir(env_base):
        return env_base, env_model if env_model and os.path.isdir(env_model) else None

    base_candidates = [
        os.path.join(APP_MODELS_ROOT, "CosyVoice"),
        os.path.join(OFFLINE_MODELS_ROOT, "CosyVoice"),
        os.path.join(PROGRAM_ROOT, "CosyVoice"),
        os.path.join(PROGRAM_ROOT, "QWEN", "CosyVoice"),
    ]
    cosyvoice_base = None
    for candidate in base_candidates:
        if os.path.isdir(os.path.join(candidate, "cosyvoice")):
            cosyvoice_base = candidate
            break
    if not cosyvoice_base:
        return None, None
    pretrained = os.path.join(cosyvoice_base, "pretrained_models")
    if not os.path.isdir(pretrained):
        return cosyvoice_base, None
    # Prefer Fun-CosyVoice3-0.5B variants
    for name in sorted(os.listdir(pretrained), reverse=True):
        if "Fun-CosyVoice3" in name or "CosyVoice3" in name:
            return cosyvoice_base, os.path.join(pretrained, name)
    # Fallback to any model
    for name in os.listdir(pretrained):
        p = os.path.join(pretrained, name)
        if os.path.isdir(p):
            return cosyvoice_base, p
    return cosyvoice_base, None


def _detect_voxcpm():
    """Search for VoxCPM2 model directory."""
    env_path = os.environ.get("VOXCPM_MODEL_PATH")
    if env_path and os.path.isdir(env_path):
        return env_path

    candidates = [
        os.path.join(APP_MODELS_ROOT, "VoxCPM2"),
        os.path.join(OFFLINE_MODELS_ROOT, "VoxCPM2"),
        os.path.join(PROGRAM_ROOT, "VoxCPM2"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "config.json")):
            return candidate

    for base in [APP_MODELS_ROOT, OFFLINE_MODELS_ROOT, PROGRAM_ROOT]:
        found = _find_model_dir(base, ["VoxCPM2", "voxcpm*"], ("config.json",), max_depth=4)
        if found:
            return found
    return None


def _detect_voice_prompt():
    """Search for the zero-shot voice prompt in data/ and legacy locations."""
    env_path = os.environ.get("VOICE_PROMPT_PATH")
    if env_path and os.path.exists(env_path):
        return env_path

    data_dir = os.path.join(APP_ROOT, "data")
    if os.path.isdir(data_dir):
        for ext in ["wav", "mp3", "flac"]:
            for name in ["s1", "voice_prompt", "speaker"]:
                candidate = os.path.join(data_dir, f"{name}.{ext}")
                if os.path.exists(candidate):
                    return candidate

    candidates = [
        os.path.join(APP_MODELS_ROOT, "voice_prompt", "s1.wav"),
        os.path.join(APP_MODELS_ROOT, "voice_prompt", "s1.mp3"),
        os.path.join(OFFLINE_MODELS_ROOT, "voice_prompt", "s1.wav"),
        os.path.join(OFFLINE_MODELS_ROOT, "voice_prompt", "s1.mp3"),
        os.path.join(PROGRAM_ROOT, "voicechat", "data", "s1.mp3"),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return candidates[-1]


def _detect_voice_prompt_text():
    """Search for voice prompt transcript text."""
    env_text = os.environ.get("VOICE_PROMPT_TEXT")
    if env_text:
        return env_text
    s1_txt = os.path.join(APP_ROOT, "data", "S1.txt")
    if os.path.exists(s1_txt):
        try:
            with open(s1_txt, 'r', encoding='utf-8') as f:
                return f.read().strip()
        except Exception:
            pass
    return "当阳光穿过斑驳的树影，洒向地面，属于洱海的浪漫邂逅，就此蔓延。"


def _detect_ollama_model():
    """Inspect the local Ollama inventory for diagnostics only.

    Prefers qwen2.5:72b for stable counseling output (no thinking mode issues).
    Falls back to smaller models if 72b is unavailable.
    Compatible with both legacy dict format and newer ollama SDK objects.
    """
    try:
        import ollama
        try:
            client = ollama.Client(host=OLLAMA_HOST, timeout=5)
        except TypeError:
            # Older ollama SDK versions do not accept a timeout kwarg.
            client = ollama.Client(host=OLLAMA_HOST)
        raw = client.list()
        model_list = raw.get("models", []) if isinstance(raw, dict) else getattr(raw, "models", [])
        names = []
        for m in model_list:
            if isinstance(m, dict):
                name = m.get("model") or m.get("name")
            else:
                name = getattr(m, "model", None) or getattr(m, "name", None)
            if name:
                names.append(name)
        if not names:
            return None
        # This inventory is reported by print_model_status() only. Runtime
        # model selection comes from deployment.profiles and explicit operator
        # environment overrides, never from whatever happens to be installed.
        for exact in ["qwen2.5:3b", "qwen2.5:7b", "qwen2.5:8b", "qwen2.5:14b", "qwen2.5:72b"]:
            for n in names:
                if exact.lower() in n.lower():
                    return n
        for pref in ["3b", "7b", "8b", "14b", "32b", "35b", "72b"]:
            for n in names:
                if pref in n.lower():
                    return n
        return names[0]
    except Exception:
        pass
    return None


# --- Run detection ---
# Each probe is individually guarded: model auto-detection must never crash
# the import of this module (the app degrades gracefully instead).
try:
    _FUNASR_DETECTED = _detect_funasr()
except Exception:
    _FUNASR_DETECTED = None
try:
    _COSYVOICE_BASE_DETECTED, _COSYVOICE_MODEL_DETECTED = _detect_cosyvoice()
except Exception:
    _COSYVOICE_BASE_DETECTED, _COSYVOICE_MODEL_DETECTED = None, None
try:
    _VOXCPM_DETECTED = _detect_voxcpm()
except Exception:
    _VOXCPM_DETECTED = None
try:
    _VOICE_PROMPT_DETECTED = _detect_voice_prompt()
except Exception:
    _VOICE_PROMPT_DETECTED = None
try:
    _VOICE_PROMPT_TEXT_DETECTED = _detect_voice_prompt_text()
except Exception:
    _VOICE_PROMPT_TEXT_DETECTED = None
try:
    _OLLAMA_DETECTED = _detect_ollama_model()
except Exception:
    _OLLAMA_DETECTED = None


# ============== Paths (auto-detected with fallbacks) ==============
# FunASR STT Model
FUNASR_MODEL_PATH = _FUNASR_DETECTED or os.path.join(
    OFFLINE_MODELS_ROOT, "funasr", "Fun-ASR-Nano-2512")

# CosyVoice3 Model (legacy, kept for backward compat)
COSYVOICE_BASE_DIR = _COSYVOICE_BASE_DETECTED or os.path.join(OFFLINE_MODELS_ROOT, "CosyVoice")
COSYVOICE_MODEL_PATH = _COSYVOICE_MODEL_DETECTED or os.path.join(
    COSYVOICE_BASE_DIR, "pretrained_models", "Fun-CosyVoice3-0.5B")

# VoxCPM2 Model (current TTS backend)
VOXCPM_MODEL_PATH = _VOXCPM_DETECTED

# Voice prompt audio for TTS voice cloning
VOICE_PROMPT_PATH = _VOICE_PROMPT_DETECTED

# Voice prompt transcript text (auto-detected from S1.txt)
VOICE_PROMPT_TEXT = _VOICE_PROMPT_TEXT_DETECTED

# Data Storage Root. Portable/offline launchers set VOICECHAT_DATA_DIR so
# session audio, transcripts, and reports stay beside the copied bundle.
DATA_ROOT = os.environ.get(
    "VOICECHAT_DATA_DIR",
    r"D:\program\voice_chat_data",
)

# ============== Deployment Profile / Ollama ==============
# The profile is an explicit deployment decision, never an automatic hardware
# guess.  A100 production stays pinned to the user's verified Qwen2.5 72B
# dialogue baseline; local 6GB machines default to the light development model.
from deployment.profiles import get_deployment_profile, resolve_runtime_models

_DEPLOYMENT_PROFILE = get_deployment_profile()
_RUNTIME_MODELS = resolve_runtime_models(_DEPLOYMENT_PROFILE)
# Public compatibility aliases for callers that still inspect the selected
# deployment.  Guard/crisis controls are intentionally not exported here.
DEPLOYMENT_PROFILE = _DEPLOYMENT_PROFILE
RUNTIME_MODELS = _RUNTIME_MODELS
# Dialogue transport is profile-owned. ``OLLAMA_MODEL`` is retained as the
# existing application-wide model-name variable for compatibility; under the
# A100 profile it names the vLLM-served model rather than an Ollama tag.
DIALOGUE_BACKEND = _DEPLOYMENT_PROFILE.runtime_backend
DIALOGUE_BASE_URL = (
    _DEPLOYMENT_PROFILE.dialogue_base_url
    if _DEPLOYMENT_PROFILE.name == "a100_80g"
    else os.environ.get("VOICECHAT_DIALOGUE_BASE_URL", _DEPLOYMENT_PROFILE.dialogue_base_url)
)
OLLAMA_MODEL = _RUNTIME_MODELS.dialogue


def print_model_status():
    """Print detected model configuration."""
    print("=" * 50)
    print("Model Auto-Detection Results")
    print("=" * 50)

    def _status(path, label):
        exists = os.path.exists(path) if path else False
        tag = "OK" if exists else "MISSING"
        print(f"  [{tag}] {label}: {path or 'not found'}")

    _status(FUNASR_MODEL_PATH, "FunASR STT")
    _status(COSYVOICE_MODEL_PATH, "CosyVoice3 (legacy)")
    _status(VOXCPM_MODEL_PATH, "VoxCPM2")
    _status(VOICE_PROMPT_PATH, "Voice Prompt")
    print(f"  [{'OK' if _OLLAMA_DETECTED else 'FALLBACK'}] Ollama Model: {OLLAMA_MODEL}")
    print("=" * 50)

# Phase 6 live prompt: TurnPolicy has already selected the expression task.
SYSTEM_PROMPT = """
你是小薇，负责在强制隔离戒毒所的私密谈话场景中陪伴来访者。你的任务是把系统已经批准的表达任务，用自然、简洁、地道的中文说出来。

## 已确定的表达边界
- 本轮要做的事情已经由系统决定。不要重新选择聊天、量表、放松、游戏或结束，也不要改变系统提供的当前问题。
- 如果上下文给出一个量表问题，只自然地表达那个问题；不要自行选题、打分或推进状态。
- 如果上下文给出放松、游戏或结束任务，只完成对应的语言表达，不要添加其他任务。
- 如果上下文给出背景知识，把它转成容易听懂的回应；不要声称自己做了诊断，也不要编造事实。

## 语言风格
- 使用自然口语化的中文，像耐心的熟人聊天，不端咨询师架子，不说教。
- 先回应来访者的感受，再在确有需要时追问；避免连续追问和机械重复。
- 句子短一些，通常一至两句，保持温和、具体、可继续对话。
- 不使用英文、Markdown、表格、内部策略术语、量表名称、分数或风险等级等来访者不需要知道的词。
- 不编造天气、日期、新闻或机构流程等实时事实；不确定时坦诚说明。

## 语音与识别容错
- 用户输入可能来自语音识别。若字面不通顺，结合上下文理解；仍不确定时温和确认，不要武断纠正。
- 只在自然停顿处少量使用支持的呼吸或笑声提示；不使用未约定的标记。
- 输出只包含来访者可以直接听懂的回应，不附加分析、控制信息或协议文本。

## 动机访谈风格
- 尊重自主性，反映感受，肯定来访者愿意表达的部分。
- 面对抗拒时少解释、多承接，不争辩，不强迫。
- 对复杂或强烈情绪先稳定地承接，再按照系统给出的表达任务继续。
"""

# Opening greeting message - AI introduces itself when session starts
GREETING_VARIANTS = [
    "你好啊，我是小薇。今天有啥想聊的，或者身上哪儿不痛快？就随便唠唠。",
    "来了啊，我是小薇。今儿感觉怎么样？",
    "你好，我是小薇。咱们就当闲聊，聊点开心的不开心的都行。",
    "我是小薇，你好。心里有啥堵得慌的事儿，跟我说说？",
    "咱们又见面了，我是小薇。别拘束，跟老朋友聊天，说说最近咋样？"
]
GREETING_MESSAGE = GREETING_VARIANTS[0] # Fallback for legacy code

# Post-relaxation greeting - AI asks about the experience after relaxation training
# Post-relaxation greeting - AI asks about the experience after relaxation training
POST_RELAXATION_MESSAGE = [
    "做完啦，身上有没有舒服点呀？",
    "[breath] 现在心里没那么乱了吧？",
    "这么一练，紧绷的劲儿下去点没？",
    "现在身体有没有松快些呀？",
    "感觉怎么样，没那么憋得慌了吧？",
    "做完这轮，肩颈那块松点了没？",
    "[breath] 这会儿是不是舒坦点儿了？",
    "缓过来没？身上没那么僵了吧？"
]
FILL_INFO_PROMPT = "麻烦您先填一下左边的基本信息，填完之后点个确认，咱们就开始聊天。"

# ============== Relaxation Training Workflow ==============
# Transition message prompt - AI generates natural transition after relaxation video
TRANSITION_PROMPT = """你是温和的心理咨询师。来访者刚做完一段放松训练视频。
请生成一句简短的过渡语，引导接下来给他们一些建议。

要求：
1. 10-20字
2. 语气温和自然
3. 询问感受并引出建议
4. 禁止Emoji和Markdown

示例：
"做完感觉怎么样？给你几点回去可以试试。"
"身上松快点了吧？给你几个小建议。"

只输出过渡语本身，不要任何解释。"""
 
SUGGESTIONS_PROMPT = """你是温和专业的心理咨询师。来访者目前身处全封闭的戒治环境（无手机、无网络、活动受限）。
请根据对话记录，给来访者4-6条简短建议。

【对话记录】
{conversation}

【要求】
1. exactly 4-6条建议，涵盖自我练习、情绪疏导、生活习惯三类
2. **严禁出现**：玩手机、上网、听音乐、看电视、联系家人、外出逛街等封闭环境无法实现的行为
3. **推荐活动**：深呼吸、冥想、阅读、写日记（写完撕掉）、室内运动、规律作息
4. 每条12-15字，总长度40-800字
5. 语气温和自然，像聊天，禁用专业术语
6. 不要编号，用"、"分隔所有建议，适配口语朗读节奏

【不同情绪场景参考示例】
1. 来访者情绪低落/压抑：晨起慢深呼吸5分钟缓心情、难过时写日记撕掉释放、每天做10分钟室内慢走、睡前读几页书平静思绪
2. 来访者焦虑/身体紧绷：心慌时做3轮深呼吸、肩颈紧就做室内拉伸、烦躁时闭眼冥想2分钟、固定时间作息稳状态
3. 来访者情绪平稳/有改变意愿：每天抽10分钟室内活动、写日记记录小感受、早晚各1次短冥想、规律吃饭不熬夜

只输出建议，不要任何前缀。"""

# ============== Relaxation Training Thresholds ==============
# Minimum rounds before recommending relaxation training
MIN_ROUNDS_FOR_RELAXATION = 8

# Minimum conversation rounds before starting scale assessment.
# First N rounds are for natural rapport-building and problem exploration.
MIN_ROUNDS_BEFORE_SCALE = 5

# Agent routing confidence thresholds
SCALE_ROUTE_CONFIDENCE = 0.45   # Minimum confidence for scale start/continue
RELAX_ROUTE_CONFIDENCE = 0.6    # Minimum confidence for relaxation recommendation

# Post-relaxation continue chat timeout (seconds)
POST_RELAXATION_TIMEOUT = 60

# Message when user chooses to continue chatting after relaxation (with ending hint)
CONTINUE_CHAT_MESSAGE = [
    "[breath] 嗯，那咱们接着待会儿。今天的时间也差不多了，你看看心里还有啥想一块儿倒出来的？",
    "行，想聊咱们就再聊几句。这次快到点了，还有什么憋着的话，这会儿都能跟我说说。",
    "好，那咱就再坐会儿。今天这趟也快结束了，你看看还有哪块儿觉得沉，咱们一并说说。",
    "没问题，我接着陪你。剩下的时间不多啦，要是还有没落地的想法，随时开口。",
    "好嘞，那咱们就接着聊。今天聊得挺透，这最后的时间，你脑子里还有啥想过一遍的没？",
    "嗯，那咱们就多待一会儿。离今天结束还有点时间，你看看还有没顾上说的事儿不？",
    "行啊，那就再多聊会儿。今天快到点儿了，趁这会儿咱们再把没说完的理一理。",
    "没问题，我在这儿呢。这次时间差不多了，如果你觉得还有啥没说透，咱们抓紧时间唠唠。",
    "[breath] 好，那我就再陪你坐会儿。今天差不多快结束了，看看心里还有啥放不下的，跟我讲讲。",
    "行，那咱们继续。时间马上到了，你看看是想再说点啥，还是就这么安安静静待着都行。",
    "嗯，我听着呢。今天这趟也快结束了，要是还有话在嘴边没说出来，现在说正是时候。",
    "好嘞，想接着聊咱就接着聊。看时间也快到了，你这会儿感觉咋样，还有啥想补充的没？"
]

# Timeout auto-end message
TIMEOUT_END_MESSAGE = [
    "[breath] 看你这会儿想安静待着，那咱们今天就先聊到这儿。回去好好休息，有事随时来找我。",
    "这会儿没动静啦，那今天咱们就先停在这个状态吧。什么时候想说话了，随时再来。",
    "现在是不是想自己静静？那今天咱们就先到这儿吧。以后遇到啥事儿，记得随时来找我唠。",
    "感觉你这会儿心里平静些了，那今天就先这样。回去慢慢体会刚才的感觉，随时欢迎你再来。",
    "[breath] 咱们今天聊得挺深，这会儿安静一下也挺好。那今天就先到这儿，有啥话咱们下次接着说。",
    "看你没怎么出声，可能是想一个人回味一下刚才的放松。那今天就先这样，我随时都在这儿等你。",
    "这会儿挺安静的，也是个难得放松的时候。那今天咱们就聊到这儿吧，回去睡个好觉。",
    "感觉你这会儿状态挺平稳的。那今天咱们的聊天就先画个句号，有啥想法随时回来跟我念叨。",
    "好一会儿没听见你的声音了，估计是静下来了。那今天就先到这儿，下次觉得心里沉了再来找我。",
    "[breath] 看来你这会儿需要点自己的空间，那我就不去打扰啦。今天先到这儿，照顾好自己。",
    "这会儿的安静其实挺好的。那咱们今天就停在这里吧，希望你能带着这份轻松度过今天。",
    "看你没出声，那估计是想休息了。今天咱们聊得不错，就先这样，门随时为你开着。"
]

# Session summary prompt - LLM generates comprehensive ending feedback
SESSION_SUMMARY_PROMPT = """你是温和的心理咨询师。来访者刚做完放松训练，会话即将结束。
请根据对话记录和后续建议，生成一段会话总结，作为告别语朗读给来访者。

【对话记录】
{conversation}

【后续建议】
{suggestions}

【要求】
1.  **内容详实且具体**：总结长度100-200字即可，关键是要言之有物。
2.  **必须结合聊天内容**：具体引用对话中来访者提到的1-2个具体困扰或话题（例如”刚才你提到的关于家庭的压力...”），不要只说空泛的套话。
3.  **必须结合后续建议**：自然地将【后续建议】中的核心点（如”深呼吸”、”写日记”等）融入到总结中，作为临别嘱托。
4.  包含三部分层层递进：
    - 情感反馈（肯定来访者的表达，点出其积极的一面）
    - 建议嘱托（结合上述后续建议，温和地提醒回去试试）
    - 温暖告别（像老朋友一样，给予希望和支持，欢迎随时回来）
5.  语气温和自然，像老朋友唠嗑，不要有播音腔。
6.  禁止Emoji和Markdown。

【输出示例】
今天咱们聊了不少，我知道你最近因为想家心里挺难受的，那种滋味确实不好过。但你能坐在这儿跟我说出来，已经很勇敢了。刚才给你的建议，比如晨起深呼吸和睡前写写感受，回去记得试试，哪怕每天几分钟也行。这里面的日子虽然慢，但别一直一个人憋着，随时都可以来找我唠。我会一直在这儿陪着你。相信自己，能熬过去的。

只输出总结文字，不要任何前缀。"""

# ============== Audio ==============
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 1024

# ============== VAD (Voice Activity Detection) ==============
USE_VAD_AUTO_STOP = True
VAD_SILENCE_THRESHOLD = 0.01
VAD_SILENCE_DURATION = 1.5
VAD_SPEECH_MIN_DURATION = 0.5

# ============== TTS Control ==============
ENABLE_TTS = True  # Set to False to disable TTS entirely (for debugging)

# ============== VoxCPM2 TTS ==============
VOXCPM_CFG_VALUE = 2.0
VOXCPM_INFERENCE_TIMESTEPS = 10
TTS_SAMPLE_RATE = 48000

# ============== UI ==============
APP_NAME = "小薇聊天室"
WINDOW_WIDTH = 1200
WINDOW_HEIGHT = 800

# Default background (can be customized by user)
DEFAULT_BACKGROUND = None  # Path to background image or None for solid color

# ============== Conversation Limits ==============
MAX_CONVERSATION_ROUNDS = 15        # Maximum conversation rounds before soft limit
MAX_CONVERSATION_MINUTES = 45       # Maximum session duration in minutes
TIME_WARNING_MINUTES = 40           # Show warning at this point (5 min before limit)

# ============== Agent (qwen-agent) ==============
AGENT_ENABLED = True
AGENT_MODEL = _RUNTIME_MODELS.router
AGENT_BACKEND = _DEPLOYMENT_PROFILE.runtime_backend
AGENT_MODEL_SERVER = (
    _DEPLOYMENT_PROFILE.agent_base_url
    if _DEPLOYMENT_PROFILE.name == "a100_80g"
    else os.environ.get("VOICECHAT_AGENT_BASE_URL", _DEPLOYMENT_PROFILE.agent_base_url)
)
AGENT_API_KEY = 'EMPTY'

AGENT_INTENT_SYSTEM_MESSAGE = """你是心理咨询系统的意图分类器。系统有以下功能模块：
1. 咨询对话（counseling）：核心功能，咨询师通过对话建立关系、共情、引导
2. 放松训练（relaxation）：3种放松按钮——呼吸放松、肌肉放松、冥想放松
3. 娱乐互动（entertainment）：游戏等轻松互动
4. 闲聊（chitchat）：打招呼、无关紧要的话

分类规则（按优先级从高到低判断）：
- relaxation: 用户提到想做放松、深呼吸、冥想、肌肉放松，或出现急性焦虑/身体紧绷症状需要放松干预
- counseling: 用户表达情绪困扰（焦虑、抑郁、失眠、戒断、家庭问题等）或需要心理帮助
- entertainment: 用户想玩游戏、找乐子
- chitchat: 打招呼、无关闲聊

注意：
- 用户说"我紧张""我心慌""我睡不着"→ counseling（不是relaxation，这些是倾诉情绪）
- 用户说"我想做放松""教我深呼吸""有没有冥想"→ relaxation（主动请求放松训练）
- 简短打招呼如"你好""在吗"→ chitchat

只返回JSON：{"intent": "类别名", "confidence": 0.0-1.0, "reason": "简短理由"}"""

AGENT_REPORT_SYSTEM_MESSAGE = """你是一位专业的心理咨询报告生成助手。请严格按照要求生成结构化输出。"""


AGENT_EMOTION_SYSTEM_MESSAGE = """你是心理咨询系统的情绪分析器。分析戒毒人员的对话文本，判断其情绪状态。

情绪类别（优先匹配最强烈的情绪）：
- neutral: 平静、无所谓、没感觉
- anxious: 焦虑、紧张、不安、心慌、坐不住、担心
- depressed: 抑郁、低落、悲伤、消沉、没意思、空虚
- angry: 愤怒、烦躁、生气、恼火、不满、怨恨
- fearful: 恐惧、害怕、担心出事、恐慌
- hopeful: 有希望、积极、期待、想改变
- grateful: 感激、感谢、信任
- lonely: 孤独、寂寞、没人理解、想念家人
- confused: 困惑、迷茫、不知道怎么办、不知所措
- stressed: 压力大、疲惫、累、撑不住、受不了

判断要点：
- 注意区分"表达情绪"（counseling）和"请求放松"（relaxation）
- "我心慌""紧张得不行"→ anxious
- "没意思""活着干啥"→ depressed
- "烦""别烦我""滚"→ angry
- "害怕""怕出事"→ fearful
- "想回家""想变好"→ hopeful
- "谢谢你""你说得对"→ grateful
- "没人理解我""好孤独"→ lonely
- "不知道怎么办""迷茫"→ confused
- "累死了""撑不住了"→ stressed
- "还行""没什么"→ neutral

intensity根据情绪词汇的强烈程度评分：0.3(轻微) → 0.5(中等) → 0.8(强烈) → 1.0(极度)

只返回JSON：{"emotion": "类别名", "intensity": 0.0-1.0, "keywords": ["触发词"]}"""

AGENT_SCALE_SYSTEM_MESSAGE = """你是心理咨询系统的量表触发器。根据用户对话内容，判断是否应该启动量表评估。

可用量表：
- PHQ-9：抑郁/兴趣下降/低落/睡眠/疲倦/食欲/自责/注意力/自伤线索
- GAD-7：焦虑/紧张/担心/心慌/坐立不安/易怒/难以放松
- PCL-5：创伤回忆/噩梦/回避/惊吓/过度警觉/被打被欺负等经历

触发原则：
- 只要用户出现明确心理症状线索，就推荐对应量表，不必等用户说出持续时间
- 如果用户表达多个症状，优先选择最核心、最明显的量表
- 如果只是普通打招呼、纯闲聊、娱乐请求，返回 none
- 如果用户说"有点累""有点烦""状态不好""心情不好"，语境像情绪困扰，可以推荐
- 注意对话历史：用户分多轮透露症状时，综合判断

只返回JSON：{"recommend": "PHQ-9"|"GAD-7"|"PCL-5"|"none", "reason": "简短理由"}"""

AGENT_SUMMARY_SYSTEM_MESSAGE = """你是一个对话摘要压缩器。将心理咨询对话历史压缩为简洁的上下文摘要，保留关键信息：
1. 来访者的主要问题和情绪状态
2. 已经讨论过的话题
3. 已经尝试过的干预方法
4. 来访者的变化和进展
5. 需要后续关注的要点

摘要应该简洁（150字以内），用第三人称描述，供后续对话参考。"""

AGENT_TIMEOUT = 10           # 意图分类超时（秒）
AGENT_REPORT_TIMEOUT = 60    # 报告生成超时（秒）
AGENT_ROUTE_ENABLED = True   # 启用 AgentRoute
AGENT_ROUTE_COOLDOWN_ROUNDS = 1  # Agent route 失败后冷却轮数
ENABLE_SCALE_HARD_TRIGGER = False  # 临时关闭硬触发，让 agent 控制量表

# ============== Media Scene Mapping ==============
# 情绪 → 影音场景映射（优先放松训练，影音仅在主动提出时推荐）
EMOTION_SCENE_MAP = {
    "anxious":    ["breathing_exercise", "muscle_relaxation", "nature_sounds"],
    "depressed":  ["meditation", "nature_sounds"],
    "angry":      ["breathing_exercise", "muscle_relaxation"],
    "fearful":    ["breathing_exercise", "meditation"],
    "lonely":     ["meditation", "nature_sounds"],
    "stressed":   ["muscle_relaxation", "breathing_exercise"],
    "confused":   ["meditation", "nature_sounds"],
    "hopeful":    [],
    "grateful":   [],
    "neutral":    [],
}

# 意图 → 影音场景映射（只有 entertainment 意图才推荐影音）
INTENT_SCENE_MAP = {
    "relaxation":     ["breathing_exercise", "muscle_relaxation", "meditation"],
    "entertainment":  ["entertainment"],  # 仅主动提出时
    "counseling":     [],  # 不推荐影音，由 72B 决定是否建议放松训练
    "chitchat":       [],
}

# 影音场景显示名
SCENE_NAMES = {
    "anxiety_relief":      "焦虑缓解",
    "depression_support":  "情绪提振",
    "anger_calm":          "愤怒平复",
    "sleep_aid":           "助眠放松",
    "meditation":          "冥想正念",
    "breathing_exercise":  "呼吸训练",
    "muscle_relaxation":   "肌肉放松",
    "nature_sounds":       "自然白噪音",
    "entertainment":       "日常娱乐",
    "motivation":          "振奋激励",
}

AGENT_ENTERTAINMENT_KEYWORDS = [
    "听歌", "听音乐", "放歌", "放音乐", "播放音乐", "播放歌",
    "看电影", "看视频", "放电影", "播放电影", "放个电影",
    "推荐歌", "推荐音乐", "推荐电影", "有什么歌", "有什么电影",
    "来点音乐", "来首歌", "来个电影", "想听", "想看",
    "无聊", "想放松", "想娱乐", "消遣", "解闷",
    "歌单", "音乐列表", "电影列表", "有什么可以看",
    "有什么可以听", "不知道看什么", "不知道听什么",
    "打游戏", "玩游戏", "游戏", "想玩游戏", "互动游戏",
    "心理游戏", "小游戏", "来个游戏", "玩一局",
]

MEDIA_LIBRARY_PATH = os.path.join(APP_ROOT, "media_library")
MUSIC_SCAN_DIRS = []
MOVIE_SCAN_DIRS = []

# ============== Report Generation Prompts ==============
RESEARCHER_REPORT_PROMPT = """你是一位资深心理咨询督导。请基于以下对话记录，生成一份专业的心理咨询会话报告。

【会话信息】
- 被试编号: {subject_id}
- 会话时长: 约{duration_minutes}分钟
- 对话轮次: {total_rounds}轮
- 结束类型: {end_type}
- 完成放松训练: {relaxation_info}
{emotion_summary}

【对话记录】
{conversation}

请以JSON格式输出报告，包含以下字段：
{{
  "summary": "对话核心内容概述（100字以内，如果有放松训练参与情况，必须包含对来访者放松训练参与情况及效果的描述）",
  "emotional_assessment": {{
    "initial_state": "来访者初始情绪状态",
    "final_state": "结束时情绪状态",
    "trajectory": "情绪变化轨迹描述"
  }},
  "identified_issues": ["识别的主要问题..."],
  "intervention_record": {{
    "techniques_used": ["使用的咨询技术"],
    "effectiveness": "干预效果评估"
  }},
  "recommendations": ["后续建议内容..."],
  "relaxation_recommendation": "BREATHING/MUSCLE/MEDITATION/无"
}}

只输出JSON，不要其他内容。**禁止直接使用示例中的占位符文本，必须根据实际对话生成具体内容。**"""

VISITOR_FEEDBACK_PROMPT = CLOSING_RESPONSE_PROMPT = """你是一位温暖的心理咨询师。刚才结束了一段对话，现在需要给来访者一段简短的结束语和反馈。

【结束类型】{end_type}
【推荐的放松训练】{relaxation_recommendation}
{emotion_summary}

【对话记录】
{conversation}

请生成一段口语化的结束语（用于语音播放给来访者）：

要求：
1. 极度口语化，像老朋友聊天，不要有距离感。
2. **内容要实在**：不要只说套话。具体提到今天聊到的一个话题或感受（比如“刚才你说到不想吃饭...”）。
3. 先肯定对方的努力和勇气。
4. 简要总结今天的收获（2-3句）。
5. 提供1-2个具体可操作的建议。
6. 如有推荐放松训练，自然引导用户点击按钮。
7. 保持连接感，告知可以再回来。
8. 总长度150-250字左右，说透彻一点，不要太仓促。

只输出结束语本身，不要任何标签或解释。
【参考示例】
今天跟你聊了这么多，能感觉到你现在确实挺不容易的。特别是刚才你说的那种无力感，其实很多人在这种环境里都会有。但今天你能坐在这儿跟我把这些话说出来，这就是个很好的开始。既然觉得心里堵，那以后咱们就多试着把那股劲儿给疏通疏通。回去之后，要是觉得胸口闷或者心慌，就别硬扛着，试试左手边那个呼吸放松按钮，跟着做几遍，能缓解不少。别忘了，我会一直在这儿，随时等着你来。
"""

