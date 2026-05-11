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
    AGENT_TIMEOUT,
    AGENT_REPORT_TIMEOUT,
    AGENT_ENTERTAINMENT_KEYWORDS,
    EMOTION_SCENE_MAP,
    INTENT_SCENE_MAP,
)

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

    def __init__(self):
        self.client = OpenAI(
            base_url=AGENT_MODEL_SERVER,
            api_key=AGENT_API_KEY,
        )
        self.model = AGENT_MODEL
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        """检测 3B 模型是否可达。"""
        if self._available is not None:
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
            print(f"[AGENT] 3B model not available: {e}")
            self._available = False
        return self._available

    # ==================== Intent Classification ====================

    def classify_intent(self, user_text: str, timeout: float = None) -> Dict[str, Any]:
        """
        意图分类。返回:
        {"intent": "counseling|entertainment|crisis|chitchat|relaxation",
         "confidence": 0.0-1.0,
         "reason": "简短理由"}

        失败时自动 fallback 到关键词分类。
        """
        timeout = timeout or AGENT_TIMEOUT
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": AGENT_INTENT_SYSTEM_MESSAGE},
                    {"role": "user", "content": user_text},
                ],
                response_format={"type": "json_object"},
                max_tokens=100,
                temperature=0.1,
                timeout=timeout,
            )
            content = resp.choices[0].message.content.strip()
            result = json.loads(content)

            # Validate structure
            if "intent" not in result:
                raise ValueError(f"Missing 'intent' key in response: {content}")
            valid_intents = {"counseling", "entertainment", "crisis", "chitchat", "relaxation"}
            if result["intent"] not in valid_intents:
                result["intent"] = "counseling"
            result.setdefault("confidence", 0.5)
            result.setdefault("reason", "")
            return result

        except Exception as e:
            print(f"[AGENT] Intent classification failed: {e}")
            return self._keyword_classify(user_text)

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

        # Entertainment
        for kw in AGENT_ENTERTAINMENT_KEYWORDS:
            if kw in text_lower:
                return {"intent": "entertainment", "confidence": 0.85, "reason": f"keyword: {kw}"}

        # Default: counseling
        return {"intent": "counseling", "confidence": 0.5, "reason": "default fallback"}

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
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": AGENT_RAG_ROUTING_SYSTEM_MESSAGE},
                    {"role": "user", "content": user_text},
                ],
                response_format={"type": "json_object"},
                max_tokens=50,
                temperature=0.1,
                timeout=timeout,
            )
            content = resp.choices[0].message.content.strip()
            result = json.loads(content)
            need_rag = result.get("need_rag", False)
            reason = result.get("reason", "")
            print(f"[AGENT] RAG routing: {need_rag} ({reason})")
            return bool(need_rag)
        except Exception as e:
            print(f"[AGENT] RAG routing failed: {e}")
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
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": AGENT_RELAXATION_SYSTEM_MESSAGE},
                    {"role": "user", "content": spoken_text},
                ],
                response_format={"type": "json_object"},
                max_tokens=30,
                temperature=0.1,
                timeout=timeout,
            )
            content = resp.choices[0].message.content.strip()
            result = json.loads(content)
            tag_name = result.get("tag", "NONE")
            tag_map = {
                "BREATHING": "[REC_BREATHING]",
                "MUSCLE": "[REC_MUSCLE]",
                "MEDITATION": "[REC_MEDITATION]",
                "GAME": "[REC_GAME]",
            }
            tag = tag_map.get(tag_name)
            if tag:
                print(f"[AGENT] Relaxation tag inferred: {tag}")
            return tag
        except Exception as e:
            print(f"[AGENT] Relaxation inference failed: {e}")
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
        失败时返回 neutral。
        """
        timeout = timeout or AGENT_TIMEOUT
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": AGENT_EMOTION_SYSTEM_MESSAGE},
                    {"role": "user", "content": text},
                ],
                response_format={"type": "json_object"},
                max_tokens=50,
                temperature=0.1,
                timeout=timeout,
            )
            content = resp.choices[0].message.content.strip()
            result = json.loads(content)
            result.setdefault("emotion", "neutral")
            result.setdefault("intensity", 0.5)
            result.setdefault("keywords", [])
            return result
        except Exception as e:
            print(f"[AGENT] Emotion detection failed: {e}")
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
            role = "来访者" if msg["role"] == "user" else "心医生"
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
            print(f"[AGENT] History summarized ({len(summary)} chars)")
            return summary
        except Exception as e:
            print(f"[AGENT] History summarization failed: {e}")
            return ""

    # ==================== Scene Recommendation ====================

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
            print(f"[AGENT] Failed to load media for scene {scene}: {e}")
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


# Singleton
_agent_service = None


def get_agent_service() -> AgentService:
    global _agent_service
    if _agent_service is None:
        _agent_service = AgentService()
    return _agent_service
