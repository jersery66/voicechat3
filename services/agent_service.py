"""
Agent Service — 3B 模型意图分类 + 报告生成

使用 openai SDK 调用 Ollama 的 /v1/chat/completions 端点，
将 qwen2.5:3b-instruct 用于意图分类和报告生成，
释放 72B 模型专注心理咨询对话。

Fallback 策略：3B 模型故障时自动降级到关键词分类 / 72B 报告生成。
"""

import json
import re
import time
from typing import Optional, Dict, Any, Generator

from openai import OpenAI

from services.logger import get_logger
from config import (
    AGENT_MODEL,
    AGENT_MODEL_SERVER,
    AGENT_API_KEY,
    AGENT_INTENT_SYSTEM_MESSAGE,
    AGENT_REPORT_SYSTEM_MESSAGE,
    AGENT_RAG_ROUTING_SYSTEM_MESSAGE,
    AGENT_RELAXATION_SYSTEM_MESSAGE,
    AGENT_EMOTION_SYSTEM_MESSAGE,
    AGENT_SUMMARY_SYSTEM_MESSAGE,
    AGENT_CRISIS_SYSTEM_MESSAGE,
    AGENT_TIMEOUT,
    AGENT_REPORT_TIMEOUT,
    AGENT_ENTERTAINMENT_KEYWORDS,
    EMOTION_SCENE_MAP,
    INTENT_SCENE_MAP,
)

logger = get_logger(__name__)

# Crisis keywords for keyword fallback
_CRISIS_KEYWORDS = [
    "自杀", "不想活", "想死", "死了算了", "活不下去",
    "自残", "割腕", "跳楼", "结束生命", "轻生",
    "没有意义", "活着没意思", "不如死了",
]

# Relaxation keywords for keyword fallback
_RELAXATION_KEYWORDS = [
    "放松训练", "呼吸训练", "肌肉放松", "冥想",
    "深呼吸", "放松一下", "做个放松",
]

# Emotional phrases for RAG routing fallback
_EMOTIONAL_PHRASES = [
    "睡不着", "失眠", "做噩梦", "焦虑", "抑郁", "害怕", "恐惧",
    "生气", "愤怒", "伤心", "难过", "委屈", "孤独", "无助",
    "压力大", "紧张", "烦躁", "心慌", "头疼", "难受", "痛苦",
    "想哭", "崩溃", "绝望", "迷茫", "困惑", "瘾来了", "犯瘾",
    "想吸毒", "复吸", "渴求", "戒断", "家庭", "欺负", "创伤",
]


class AgentService:
    """3B 模型 Agent 服务，负责意图分类和报告生成。"""

    # Re-check availability at most once per RECHECK_INTERVAL seconds
    RECHECK_INTERVAL = 60.0

    def __init__(self):
        self.client = OpenAI(
            base_url=AGENT_MODEL_SERVER,
            api_key=AGENT_API_KEY,
            timeout=AGENT_TIMEOUT,
            max_retries=0,
        )
        self.model = AGENT_MODEL
        self._available: Optional[bool] = None
        self._last_check_ts: float = 0.0

    def is_available(self) -> bool:
        """检测 3B 模型是否可达。

        结果缓存 ``RECHECK_INTERVAL`` 秒；超期后会重新探测，避免临时故障
        后永久在 fallback 模式运行。
        """
        now = time.time()
        if (
            self._available is not None
            and (now - self._last_check_ts) < self.RECHECK_INTERVAL
        ):
            return self._available
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=5,
                timeout=AGENT_TIMEOUT,
            )
            self._available = resp.choices is not None and len(resp.choices) > 0
        except Exception as e:
            logger.debug(f"3B model not available: {e}")
            self._available = False
        self._last_check_ts = now
        return self._available

    # ==================== Internal helpers ====================

    def _call_json(
        self,
        system_message: str,
        user_text: str,
        *,
        max_tokens: int = 100,
        temperature: float = 0.1,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Call the 3B agent and parse a JSON response.

        Centralizes the request boilerplate for the various JSON-mode calls
        (intent, RAG routing, relaxation, emotion, crisis). Raises on failure
        so each public caller can decide how to fall back.
        """
        timeout = timeout or AGENT_TIMEOUT
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_text},
            ],
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
        choice = resp.choices[0] if resp.choices else None
        message = choice.message if choice else None
        content = (getattr(message, "content", None) or "").strip()
        finish_reason = getattr(choice, "finish_reason", None) if choice else None

        logger.warning(
            f"[AgentRaw] model={self.model} finish_reason={finish_reason} "
            f"content_len={len(content)} content_head={content[:300]!r}"
        )

        if not content:
            raise ValueError(f"empty response; finish_reason={finish_reason}")

        try:
            return json.loads(content)
        except Exception as e:
            logger.warning(f"[AgentRaw] JSON parse failed: {e}; raw={content[:1000]!r}")
            raise

    # ==================== Intent Classification ====================

    def classify_intent(self, user_text: str, timeout: float = None) -> Dict[str, Any]:
        """
        意图分类。返回:
        {"intent": "counseling|entertainment|crisis|chitchat|relaxation",
         "confidence": 0.0-1.0,
         "reason": "简短理由"}

        先用关键词快速判断，高置信度直接返回；
        关键词不确定时才调用3B模型。
        """
        keyword_result = self._keyword_classify(user_text)
        if keyword_result["confidence"] >= 0.85:
            return keyword_result

        timeout = timeout or AGENT_TIMEOUT
        try:
            result = self._call_json(
                AGENT_INTENT_SYSTEM_MESSAGE, user_text,
                max_tokens=100, temperature=0.1, timeout=timeout,
            )

            if "intent" not in result:
                raise ValueError(f"Missing 'intent' key in response: {result}")
            valid_intents = {"counseling", "entertainment", "crisis", "chitchat", "relaxation"}
            if result["intent"] not in valid_intents:
                result["intent"] = "counseling"
            result.setdefault("confidence", 0.5)
            result.setdefault("reason", "")
            return result

        except Exception as e:
            logger.debug(f"Intent classification failed: {e}")
            return keyword_result

    def _keyword_classify(self, text: str) -> Dict[str, Any]:
        """纯关键词 fallback 分类。"""
        text_lower = text.lower()

        # Crisis — highest priority
        for kw in _CRISIS_KEYWORDS:
            if kw in text_lower:
                return {"intent": "crisis", "confidence": 0.95, "reason": f"keyword: {kw}"}

        # Relaxation
        for kw in _RELAXATION_KEYWORDS:
            if kw in text_lower:
                return {"intent": "relaxation", "confidence": 0.9, "reason": f"keyword: {kw}"}

        # Entertainment — only trigger for request-like expressions
        _STATEMENT_PREFIXES = [
            "平时", "以前", "曾经", "过去", "老是", "总是",
            "一直", "以前喜欢", "以前爱", "习惯",
        ]
        is_statement = any(p in text_lower for p in _STATEMENT_PREFIXES)

        for kw in AGENT_ENTERTAINMENT_KEYWORDS:
            if kw in text_lower:
                if is_statement:
                    return {"intent": "counseling", "confidence": 0.7, "reason": f"keyword: {kw} (statement, not request)"}
                return {"intent": "entertainment", "confidence": 0.85, "reason": f"keyword: {kw}"}

        # Default: counseling
        return {"intent": "counseling", "confidence": 0.5, "reason": "default fallback"}

    # ==================== Crisis Risk Assessment ====================

    def assess_crisis_risk(self, text: str, timeout: float = None,
                           use_llm: bool = True) -> Dict[str, Any]:
        """
        Assess crisis risk level from user text.
        Returns {"risk_level": 0-10, "indicators": [...], "immediate_action": bool}
        Falls back to keyword scoring on failure.
        Set use_llm=False to skip the LLM call and use keyword scoring only.
        """
        if not use_llm:
            return self._keyword_crisis_risk(text)
        timeout = timeout or AGENT_TIMEOUT
        try:
            result = self._call_json(
                AGENT_CRISIS_SYSTEM_MESSAGE, text,
                max_tokens=100, temperature=0.1, timeout=timeout,
            )
            result.setdefault("risk_level", 0)
            result.setdefault("indicators", [])
            result.setdefault("immediate_action", result["risk_level"] >= 7)
            # Clamp risk_level to 0-10
            result["risk_level"] = max(0, min(10, int(result["risk_level"])))
            return result
        except Exception as e:
            logger.debug(f"Crisis risk assessment failed: {e}")
            return self._keyword_crisis_risk(text)

    def _keyword_crisis_risk(self, text: str) -> Dict[str, Any]:
        """Keyword-based crisis risk scoring fallback."""
        risk_level = 0
        indicators = []

        # Level 9-10: Direct suicide with method
        _critical = ["跳楼", "割腕", "上吊", "喝农药", "安眠药", "结束生命",
                      "马上去死", "现在就死", "不想活了马上"]
        for kw in _critical:
            if kw in text:
                risk_level = max(risk_level, 9)
                indicators.append(f"危急关键词: {kw}")

        # Level 7-8: Suicide ideation, self-harm
        _severe = ["自杀", "想死", "死了算了", "活不下去", "轻生", "自残",
                    "不想活", "活着没意思", "不如死了", "死了一了百了"]
        for kw in _severe:
            if kw in text:
                risk_level = max(risk_level, 7)
                indicators.append(f"严重关键词: {kw}")

        # Level 4-6: Violence, escape plans
        _moderate = ["杀了", "打死", "弄死", "报复", "逃跑", "逃出去",
                      "活够了", "撑不下去了", "没有意义"]
        for kw in _moderate:
            if kw in text:
                risk_level = max(risk_level, 5)
                indicators.append(f"中等关键词: {kw}")

        # Level 1-3: General distress
        _mild = ["绝望", "崩溃", "撑不住", "受不了了", "一点希望都没有",
                  "看不到希望", "走投无路"]
        for kw in _mild:
            if kw in text:
                risk_level = max(risk_level, 3)
                indicators.append(f"关注关键词: {kw}")

        return {
            "risk_level": risk_level,
            "indicators": indicators,
            "immediate_action": risk_level >= 7,
        }

    # ==================== Report Generation ====================

    def generate_report(self, prompt: str, system_msg: str = None, timeout: float = None) -> str:
        """
        非流式报告生成。返回完整文本。
        失败时抛出异常，由调用方 fallback 到 72B。
        """
        timeout = timeout or AGENT_REPORT_TIMEOUT
        system_msg = system_msg or AGENT_REPORT_SYSTEM_MESSAGE

        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            stream=False,
            timeout=timeout,
        )
        return resp.choices[0].message.content

    def generate_report_stream(self, prompt: str, system_msg: str = None, timeout: float = None) -> Generator[str, None, None]:
        """
        流式报告生成。逐 chunk yield。
        失败时抛出异常，由调用方 fallback 到 72B。
        """
        timeout = timeout or AGENT_REPORT_TIMEOUT
        system_msg = system_msg or AGENT_REPORT_SYSTEM_MESSAGE

        stream = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": prompt},
            ],
            stream=True,
            timeout=timeout,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    # ==================== RAG Intent Routing ====================

    def classify_rag_intent(self, user_text: str, timeout: float = None) -> bool:
        """
        判断用户输入是否需要检索知识库。
        返回 True 表示需要 RAG 检索。
        失败时 fallback 到关键词匹配。
        """
        timeout = timeout or AGENT_TIMEOUT
        try:
            result = self._call_json(
                AGENT_RAG_ROUTING_SYSTEM_MESSAGE, user_text,
                max_tokens=50, temperature=0.1, timeout=timeout,
            )
            need_rag = result.get("need_rag", False)
            reason = result.get("reason", "")
            logger.debug(f"RAG routing: {need_rag} ({reason})")
            return bool(need_rag)
        except Exception as e:
            logger.debug(f"RAG routing failed: {e}")
            return self._keyword_rag_routing(user_text)

    def _keyword_rag_routing(self, text: str) -> bool:
        """RAG 路由的关键词 fallback。"""
        for phrase in _EMOTIONAL_PHRASES:
            if phrase in text:
                return True
        return False

    # ==================== Relaxation Tag Inference ====================

    def infer_relaxation_tag(self, spoken_text: str, timeout: float = None) -> Optional[str]:
        """
        从AI回复文本中推断放松训练类型。
        返回 "[REC_BREATHING]" / "[REC_MUSCLE]" / "[REC_MEDITATION]" / "[REC_GAME]" / None
        失败时 fallback 到关键词匹配。
        """
        timeout = timeout or AGENT_TIMEOUT
        try:
            result = self._call_json(
                AGENT_RELAXATION_SYSTEM_MESSAGE, spoken_text,
                max_tokens=30, temperature=0.1, timeout=timeout,
            )
            tag_name = result.get("tag", "NONE")
            tag_map = {
                "BREATHING": "[REC_BREATHING]",
                "MUSCLE": "[REC_MUSCLE]",
                "MEDITATION": "[REC_MEDITATION]",
                "GAME": "[REC_GAME]",
            }
            tag = tag_map.get(tag_name)
            if tag:
                logger.debug(f"Relaxation tag inferred: {tag}")
            return tag
        except Exception as e:
            logger.debug(f"Relaxation inference failed: {e}")
            return self._keyword_relaxation_tag(spoken_text)

    def _keyword_relaxation_tag(self, text: str) -> Optional[str]:
        """放松标签的关键词 fallback。"""
        if "游戏" in text:
            return "[REC_GAME]"
        if "呼吸" in text:
            return "[REC_BREATHING]"
        if "肌肉" in text:
            return "[REC_MUSCLE]"
        if "冥想" in text:
            return "[REC_MEDITATION]"
        return None

    # ==================== Emotion Detection ====================

    def detect_emotion(self, text: str, timeout: float = None) -> Dict[str, Any]:
        """
        从文本中提取情绪状态。
        返回 {"emotion": "类别名", "intensity": 0.0-1.0, "keywords": ["触发词"]}
        先用关键词快速检测，匹配到高置信度情绪直接返回。
        """
        keyword_emotion = self._keyword_detect_emotion(text)
        if keyword_emotion.get("intensity", 0) >= 0.7:
            return keyword_emotion

        timeout = timeout or AGENT_TIMEOUT
        try:
            result = self._call_json(
                AGENT_EMOTION_SYSTEM_MESSAGE, text,
                max_tokens=50, temperature=0.1, timeout=timeout,
            )
            result.setdefault("emotion", "neutral")
            result.setdefault("intensity", 0.5)
            result.setdefault("keywords", [])
            return result
        except Exception as e:
            logger.debug(f"Emotion detection failed: {e}")
            return keyword_emotion

    def _keyword_detect_emotion(self, text: str) -> Dict[str, Any]:
        """关键词快速情绪检测 fallback。"""
        text_lower = text.lower()
        emotion_keywords = {
            "sad": ["难过", "伤心", "悲伤", "痛苦", "想哭", "委屈", "失落", "绝望"],
            "anxious": ["焦虑", "紧张", "害怕", "恐惧", "担心", "不安", "心慌", "烦躁"],
            "angry": ["生气", "愤怒", "烦", "恼火", "气愤", "不公平", "受够了"],
            "depressed": ["抑郁", "低落", "没意思", "无聊", "空虚", "绝望", "崩溃"],
            "happy": ["开心", "高兴", "快乐", "好", "不错", "满意", "感谢"],
            "lonely": ["孤独", "寂寞", "没人", "一个人", "无助", "没有朋友"],
        }
        for emotion, keywords in emotion_keywords.items():
            for kw in keywords:
                if kw in text_lower:
                    return {"emotion": emotion, "intensity": 0.75, "keywords": [kw]}
        return {"emotion": "neutral", "intensity": 0.0, "keywords": []}

    # ==================== Conversation Summary ====================

    def summarize_history(self, messages: list, timeout: float = None) -> str:
        """
        压缩对话历史为简洁摘要。
        messages: [{"role": "user"/"assistant", "content": "..."}]
        返回 150 字以内的摘要文本。
        失败时返回空字符串。
        """
        timeout = timeout or AGENT_REPORT_TIMEOUT
        # Format conversation for the prompt
        formatted = []
        for msg in messages[-20:]:  # Only last 20 turns to keep prompt short
            role = "来访者" if msg["role"] == "user" else "小薇"
            formatted.append(f"{role}: {msg['content'][:200]}")
        conversation_text = "\n".join(formatted)

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": AGENT_SUMMARY_SYSTEM_MESSAGE},
                    {"role": "user", "content": f"请压缩以下对话：\n\n{conversation_text}"},
                ],
                max_tokens=200,
                temperature=0.3,
                timeout=timeout,
            )
            summary = resp.choices[0].message.content.strip()
            logger.debug(f"History summarized ({len(summary)} chars)")
            return summary
        except Exception as e:
            logger.debug(f"History summarization failed: {e}")
            return ""

    # ==================== Dynamic Greeting Generation ====================

    def generate_greeting(self, timeout: float = None) -> str:
        """Generate a unique opening greeting."""
        timeout = timeout or 15.0
        prompt = (
            "你是小薇，一位温暖、亲切的心理咨询师。请生成一句简短的欢迎问候语，"
            "用于首次见面时对来访者说。要求：\n"
            "1. 口语化、有温度，像老朋友打招呼\n"
            "2. 不超过30个字\n"
            "3. 不要重复以下已有句式：'你好啊我是小薇'、'来了啊我是小薇'、'咱们又见面了'\n"
            "4. 可以提到聊天、放松、倾诉等，但不要提具体技术\n"
            "只输出问候语本身，不要输出任何其他内容。"
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
                timeout=timeout,
            )
            text = resp.choices[0].message.content.strip()
            if len(text) > 60:
                text = text[:60]
            return text
        except Exception as e:
            logger.debug(f"Greeting generation failed: {e}")
            return ""

    def generate_post_relaxation_greeting(self, relaxation_type: str = "", timeout: float = None) -> str:
        """Generate a unique post-relaxation greeting."""
        timeout = timeout or 15.0
        relax_name = {"breathing": "呼吸放松", "muscle": "肌肉放松", "meditation": "冥想"}.get(relaxation_type, "放松训练")
        prompt = (
            f"你是小薇。来访者刚完成了{relax_name}训练，"
            "请生成一句简短的关心问候，问问他们感觉怎么样。要求：\n"
            "1. 口语化、有温度，不超过25个字\n"
            "2. 可以用[breath]标记表示深呼吸\n"
            "3. 只输出问候语本身，不要输出其他内容。"
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=60,
                timeout=timeout,
            )
            text = resp.choices[0].message.content.strip()
            if len(text) > 50:
                text = text[:50]
            return text
        except Exception as e:
            logger.debug(f"Post-relaxation greeting generation failed: {e}")
            return ""

    def generate_fill_info_prompt(self, timeout: float = None) -> str:
        """Generate a unique fill-info prompt."""
        timeout = timeout or 15.0
        prompt = (
            "你是小薇。请生成一句简短的话，引导来访者填写基本信息。要求：\n"
            "1. 口语化、亲切，不超过30个字\n"
            "2. 提到'左边'或'基本信息'或'确认'\n"
            "3. 只输出这句话本身，不要输出其他内容。"
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=60,
                timeout=timeout,
            )
            text = resp.choices[0].message.content.strip()
            if len(text) > 50:
                text = text[:50]
            return text
        except Exception as e:
            logger.debug(f"Fill-info prompt generation failed: {e}")
            return ""

    def recommend_scene(self, emotion: str = "neutral", intent: str = "counseling") -> list:
        """
        根据情绪和意图推荐影音场景。
        返回场景 ID 列表（按优先级排序）。
        """
        scenes = []

        # 意图优先
        if intent in INTENT_SCENE_MAP:
            scenes.extend(INTENT_SCENE_MAP[intent])

        # 情绪补充
        if emotion in EMOTION_SCENE_MAP:
            for s in EMOTION_SCENE_MAP[emotion]:
                if s not in scenes:
                    scenes.append(s)

        # 去重保序
        seen = set()
        result = []
        for s in scenes:
            if s not in seen:
                seen.add(s)
                result.append(s)

        return result[:3]  # 最多返回 3 个场景

    # ==================== Media Library ====================

    def get_media_for_scene(self, scene: str, media_type: str = "music") -> list:
        """
        从影音库获取指定场景的媒体列表。
        scene: 场景 ID (如 "anxiety_relief")
        media_type: "music" 或 "videos"
        返回 [{"name": ..., "path": ..., "description": ...}, ...]
        """
        from pathlib import Path

        config_path = Path(__file__).parent.parent / "media_library" / "library_config.json"
        if not config_path.exists():
            return []

        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            scene_data = config.get("scenes", {}).get(scene, {})
            return scene_data.get(media_type, [])
        except Exception as e:
            logger.debug(f"Failed to load media for scene {scene}: {e}")
            return []

    def get_recommended_media(self, emotion: str = "neutral", intent: str = "counseling",
                               media_type: str = "music", limit: int = 5) -> list:
        """
        根据当前情绪和意图，推荐合适的影音内容。
        返回 [{"scene": "焦虑缓解", "name": "...", "path": "..."}, ...]
        """
        from config import SCENE_NAMES

        scenes = self.recommend_scene(emotion, intent)
        results = []

        for scene_id in scenes:
            media_list = self.get_media_for_scene(scene_id, media_type)
            for item in media_list:
                results.append({
                    "scene": SCENE_NAMES.get(scene_id, scene_id),
                    "scene_id": scene_id,
                    **item,
                })
            if len(results) >= limit:
                break

        return results[:limit]

    # ==================== Unified Conversation Routing ====================

    def _parse_json_loose(self, content: str) -> Dict[str, Any]:
        """Parse JSON from agent output, allowing ```json fences or extra prose."""
        text = (content or "").strip()

        # Strip markdown fences
        text = re.sub(r"^```(?:json)?", "", text.strip(), flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text.strip()).strip()

        # Try direct parse
        try:
            return json.loads(text)
        except Exception:
            pass

        # Try to find JSON object in prose
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            return json.loads(m.group(0))

        raise ValueError(f"No JSON object found in route output: {text[:500]!r}")

    def route_conversation_actions(
        self,
        user_text: str,
        recent_history: str = "",
        current_round: int = 0,
        active_scale: str = None,
        collected_scales: dict = None,
        relaxation_done: bool = False,
        timeout: float = None,
    ) -> dict:
        """Unified routing decision for scale/relaxation/crisis per turn.

        Uses direct API call WITHOUT response_format=json_object. We
        deliberately avoid json_object mode here (unlike the other `_call_json`
        routes) because small local models on Ollama frequently reject or
        mangle the `response_format` argument. The loose JSON parser
        (`_parse_json_loose`) handles undecorated JSON output robustly.
        """
        # Build context for the agent
        context_parts = []
        if recent_history:
            context_parts.append(f"最近对话：\n{recent_history}")
        context_parts.append(f"当前轮次：第{current_round}轮")
        if active_scale:
            context_parts.append(f"当前正在采样的量表：{active_scale}")
        if collected_scales:
            summary = []
            for scale_name, answers in collected_scales.items():
                summary.append(f"{scale_name}: 已采{len(answers)}题")
            context_parts.append(f"已采样量表：{', '.join(summary)}")
        if relaxation_done:
            context_parts.append("已完成放松训练")
        context_parts.append(f"用户本轮说：{user_text}")

        context = "\n".join(context_parts)

        system_prompt = """你是心理咨询系统的路由决策模块。只输出一个JSON对象，不要Markdown，不要解释，不要代码块。

字段必须完整，类型如下：
{
  "action": "chat|start_scale|continue_scale|recommend_relaxation|recommend_game|recommend_media|exit",
  "scale": null 或 "PHQ-9" 或 "GAD-7" 或 "PCL-5",
  "target_item": null 或 "Q1"-"Q9",
  "intervention_type": null 或 "breathing" 或 "muscle_relaxation" 或 "mindfulness" 或 "game" 或 "media",
  "urgency": 0-10,
  "risk_level": 0-10,
  "confidence": 0.0-1.0,
  "reason": "15字以内"
}

要求：只输出单行JSON，不要换行。不要输出题目文本。

规则：
- 普通聊天且症状不明确：action="chat"
- 连续2轮以上出现症状，或症状明确影响功能时：action="start_scale"
- 单次轻微信号（如"还好""不太开心"）不触发量表，继续观察
- 已采样但需继续时：action="continue_scale"
- 用户焦虑/紧张/失眠/疲惫且量表已部分完成：action="recommend_relaxation"
- 用户想玩/无聊时：action="recommend_game"
- 用户明确想退出时：action="exit"

症状→量表映射：
- 心情不好/低落/沮丧/没意思/没兴趣/难受/想哭→PHQ-9
- 睡不好/失眠/睡不着/早醒→PHQ-9
- 累/没力气/疲惫/没精神→PHQ-9
- 焦虑/紧张/担心/心慌→GAD-7
- 创伤/噩梦/闪回→PCL-5

注意：start_scale 时不需要指定 target_item，由系统自动决定。

示例1：用户连续表达低落（第3轮）
{"action":"start_scale","scale":"PHQ-9","target_item":null,"intervention_type":null,"urgency":2,"risk_level":0,"confidence":0.75,"reason":"持续低落情绪"}

示例2：用户单次说"睡不好"（第1轮）
{"action":"chat","scale":null,"target_item":null,"intervention_type":null,"urgency":1,"risk_level":0,"confidence":0.5,"reason":"单次睡眠提及"}

示例3：用户焦虑且已有PHQ-9在进行
{"action":"continue_scale","scale":"PHQ-9","target_item":null,"intervention_type":null,"urgency":2,"risk_level":0,"confidence":0.7,"reason":"继续采样"}"""

        timeout = timeout or AGENT_TIMEOUT
        try:
            # Direct call without response_format for Ollama compatibility
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": context},
                ],
                max_tokens=512,
                temperature=0.0,
                timeout=timeout,
            )
            choice = resp.choices[0] if resp.choices else None
            content = ((choice.message.content if choice and choice.message else "") or "").strip()
            finish_reason = getattr(choice, "finish_reason", None) if choice else None

            logger.warning(
                f"[AgentRouteRaw] finish_reason={finish_reason} "
                f"len={len(content)} head={content[:500]!r}"
            )

            if not content:
                raise ValueError(f"route empty response; finish_reason={finish_reason}")

            result = self._parse_json_loose(content)

            # Map new agent output format to old pipeline format
            raw_action = result.get("action") or result.get("scale_action") or "chat"

            # Convert action names
            if raw_action == "start_scale":
                scale_action = "start"
            elif raw_action == "continue_scale":
                scale_action = "continue"
            elif raw_action == "chat":
                scale_action = "none"
            else:
                scale_action = raw_action

            # Map item field
            item = result.get("target_item") or result.get("item")
            if isinstance(item, str) and item.startswith("Q"):
                try:
                    item = int(item[1:])
                except ValueError:
                    item = None

            # Build normalized result — derive booleans from action field
            intervention = result.get("intervention_type")
            if raw_action == "recommend_relaxation":
                intervention = {
                    "mindfulness": "meditation",
                    "meditation": "meditation",
                    "muscle": "muscle",
                    "muscle_relaxation": "muscle",
                    "progressive_muscle_relaxation": "muscle",
                    "breathing": "breathing",
                }.get(str(intervention or "").strip().lower(), "breathing")
            normalized = {
                "action": raw_action,
                "scale_action": scale_action,
                "scale": result.get("scale"),
                "target_item": result.get("target_item"),
                "item": item,
                "intervention_type": intervention,
                "probe_hint": result.get("probe_hint", ""),
                "recommend_relaxation": raw_action == "recommend_relaxation",
                "relaxation_type": intervention if raw_action == "recommend_relaxation" else None,
                "recommend_game": raw_action == "recommend_game",
                "game_type": intervention if raw_action == "recommend_game" else None,
                "recommend_media": raw_action == "recommend_media",
                "media_type": intervention if raw_action == "recommend_media" else None,
                "exit_intent": raw_action == "exit",
                "risk_level": result.get("risk_level", 0),
                "immediate_crisis": result.get("immediate_crisis", False),
                "confidence": result.get("confidence", 0.0),
                "reason": result.get("reason", ""),
            }
            return normalized
        except Exception as e:
            logger.warning(f"Route conversation actions failed: {e}")
            # Fallback: return no-action
            return {
                "scale_action": "none",
                "scale": None,
                "item": None,
                "probe_hint": "",
                "recommend_relaxation": False,
                "relaxation_type": None,
                "risk_level": 0,
                "immediate_crisis": False,
                "confidence": 0.0,
                "reason": f"agent fallback: {e}",
            }

    def validate_route_json(self) -> bool:
        """Test if agent can produce valid route JSON."""
        try:
            r = self.route_conversation_actions(
                user_text="我最近心情不好，睡不好",
                recent_history="",
                current_round=4,
                timeout=15.0,
            )
            ok = (
                isinstance(r, dict)
                and "scale_action" in r
                and "confidence" in r
                and r.get("confidence", 0) > 0
            )
            logger.warning(f"[AgentHealth] route_json_ok={ok}, result={r}")
            return ok
        except Exception as e:
            logger.warning(f"[AgentHealth] route_json_failed: {e}")
            return False


# Singleton
_agent_service = None


def get_agent_service() -> AgentService:
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service
