# Scales - Standard psychological assessment scales (PHQ-9, GAD-7, PCL-5)

from typing import Optional, List, Dict, Any


SCALES: Dict[str, Dict[str, Any]] = {
    "PHQ-9": {
        "name": "患者健康问卷抑郁量表 (PHQ-9)",
        "description": "过去两周内，以下问题困扰您的频率",
        "instruction": "请根据过去两周的实际情况，选择最符合的选项",
        "options": [
            {"label": "完全不会", "score": 0},
            {"label": "好几天", "score": 1},
            {"label": "一半以上的天数", "score": 2},
            {"label": "几乎每天", "score": 3},
        ],
        "questions": [
            "做事时提不起劲或没有兴趣",
            "感到心情低落、沮丧或绝望",
            "入睡困难、睡不安稳或睡得太多",
            "感觉疲倦或没有活力",
            "食欲不振或吃得太多",
            "觉得自己很糟——或觉得自己很失败，或让自己或家人失望",
            "对事物专注有困难，例如阅读报纸或看电视时",
            "动作或说话速度缓慢到别人已经觉察？或正好相反——烦躁或坐立不安、动来动去的情况更胜于平常",
            "有不如死掉或用某种方式伤害自己的念头",
        ],
        "scoring": [
            (0, 4, "无抑郁症状"),
            (5, 9, "轻度抑郁"),
            (10, 14, "中度抑郁"),
            (15, 19, "中重度抑郁"),
            (20, 27, "重度抑郁"),
        ],
        "max_score": 27,
    },
    "GAD-7": {
        "name": "广泛性焦虑障碍量表 (GAD-7)",
        "description": "过去两周内，以下问题困扰您的频率",
        "instruction": "请根据过去两周的实际情况，选择最符合的选项",
        "options": [
            {"label": "完全不会", "score": 0},
            {"label": "好几天", "score": 1},
            {"label": "一半以上的天数", "score": 2},
            {"label": "几乎每天", "score": 3},
        ],
        "questions": [
            "感觉紧张、焦虑或急切",
            "不能够停止或控制担忧",
            "对各种各样的事情担忧过多",
            "很难放松下来",
            "由于不安而无法静坐",
            "变得容易烦恼或急躁",
            "感到似乎将有可怕的事情发生而害怕",
        ],
        "scoring": [
            (0, 4, "无焦虑症状"),
            (5, 9, "轻度焦虑"),
            (10, 14, "中度焦虑"),
            (15, 21, "重度焦虑"),
        ],
        "max_score": 21,
    },
    "PCL-5": {
        "name": "创伤后应激障碍筛查量表 (PCL-5 简版)",
        "description": "过去一个月内，以下问题困扰您的频率",
        "instruction": "请根据过去一个月的实际情况，选择最符合的选项",
        "options": [
            {"label": "完全没有", "score": 0},
            {"label": "有一点", "score": 1},
            {"label": "中等程度", "score": 2},
            {"label": "相当严重", "score": 3},
            {"label": "极度严重", "score": 4},
        ],
        "questions": [
            "反复、不自主地回忆那件压力很大的事",
            "反复做与那件事有关的噩梦",
            "尽量避免回忆或谈论那件事",
            "尽量避免与那件事有关的外部提示（人、地点、活动等）",
            '对自己、他人或世界有强烈的负面信念（如“我很坏”、“世界很危险”）',
            "持续地责备自己或他人",
            "过度警觉或易受惊吓",
            "难以集中注意力",
        ],
        "scoring": [
            (0, 7, "无明显创伤应激"),
            (8, 15, "轻度创伤应激，建议关注"),
            (16, 24, "中度创伤应激，建议进一步评估"),
            (25, 32, "重度创伤应激，可能存在PTSD"),
        ],
        "max_score": 32,
    },
}


class ScaleManager:
    """Manages standard psychological assessment scales."""

    # Unified keyword lists — single source of truth for both
    # recommend_scale_candidates() and should_administer().
    DEPRESSION_KW = [
        "难过", "伤心", "悲伤", "低落", "没意思", "绝望", "想哭",
        "抑郁", "失眠", "睡不着", "疲倦", "没活力", "心情不好",
        "不开心", "难受", "痛苦", "心里很累",
    ]
    ANXIETY_KW = [
        "焦虑", "紧张", "害怕", "恐惧", "担心", "不安", "心慌",
        "烦躁", "烦躁不安", "压力",
    ]
    TRAUMA_KW = [
        "噩梦", "创伤", "应激", "闪回", "惊吓",
    ]

    # Round by which every participant must have been offered at least one scale
    FORCE_SCALE_ROUND = 5

    def get_scale(self, scale_name: str) -> Optional[Dict[str, Any]]:
        """Return a copy of the scale definition (questions + options + metadata)."""
        return SCALES.get(scale_name)

    def get_scale_names(self) -> List[str]:
        """Return all available scale names."""
        return list(SCALES.keys())

    def score_scale(self, scale_name: str, answers: List[int]) -> Dict[str, Any]:
        """
        Score a completed scale.
        answers: list of integer scores (0-indexed option indices or raw scores).
        Returns {"total": int, "severity": str, "items": int, "max_score": int}
        """
        scale = SCALES.get(scale_name)
        if not scale:
            return {"total": 0, "severity": "未知量表", "items": 0, "max_score": 0, "error": f"Unknown scale: {scale_name}"}

        total = sum(answers)
        severity = "未分类"
        for low, high, label in scale["scoring"]:
            if low <= total <= high:
                severity = label
                break

        return {
            "total": total,
            "severity": severity,
            "items": len(answers),
            "max_score": scale["max_score"],
        }

    def recommend_scale_candidates(self, user_text: str,
                                   administered: set = None) -> List[str]:
        """Recommend one or more scales based on keyword analysis of user text.

        Returns a list of scale names (e.g. ["PHQ-9", "GAD-7"]) that match
        the user's language.  Empty list if no keywords match.
        Unlike should_administer(), this does NOT force a scale by round count
        or use emotion tracker — it's purely keyword-based.
        """
        if administered is None:
            administered = set()

        candidates = []
        if not user_text:
            return candidates

        text_lower = user_text.lower()

        if "PHQ-9" not in administered:
            for kw in self.DEPRESSION_KW:
                if kw in text_lower:
                    candidates.append("PHQ-9")
                    break
        if "GAD-7" not in administered:
            for kw in self.ANXIETY_KW:
                if kw in text_lower:
                    candidates.append("GAD-7")
                    break
        if "PCL-5" not in administered:
            for kw in self.TRAUMA_KW:
                if kw in text_lower:
                    candidates.append("PCL-5")
                    break

        return candidates

    def should_administer(self,
                          emotion_tracker=None,
                          report_service=None,
                          user_text=None,
                          administered: set = None,
                          agent_service=None,
                          conversation_context: str = "") -> Optional[str]:
        """
        Determine if a scale should be administered based on session context.
        Returns scale name (e.g. "PHQ-9") or None.

        Priority:
        1. Keyword matching (user_text + conversation_context)
        2. Agent model recommendation (if available)
        3. Emotion tracker dominant emotion / trend
        4. Forced PHQ-9 fallback after FORCE_SCALE_ROUND
        """
        if administered is None:
            administered = set()

        rounds = 0
        if report_service:
            rounds = report_service.get_round_count()
            if rounds < 1:
                return None

        # Combine current text and conversation context for keyword detection
        detect_text = user_text or ""
        if conversation_context:
            detect_text = conversation_context + "\n" + detect_text

        if detect_text:
            text_lower = detect_text.lower()

            for kw in self.DEPRESSION_KW:
                if kw in text_lower and "PHQ-9" not in administered:
                    return "PHQ-9"
            for kw in self.ANXIETY_KW:
                if kw in text_lower and "GAD-7" not in administered:
                    return "GAD-7"
            for kw in self.TRAUMA_KW:
                if kw in text_lower and "PCL-5" not in administered:
                    return "PCL-5"

        # Agent fallback for subtle cases keywords can't catch
        if user_text and agent_service and hasattr(agent_service, "is_available"):
            try:
                if agent_service.is_available():
                    agent_result = agent_service.recommend_scale(
                        user_text, context=conversation_context
                    )
                    if agent_result and agent_result not in administered:
                        return agent_result
            except Exception:
                pass

        if emotion_tracker:
            data = emotion_tracker.get_emotion_data()
            dominant = data.get("dominant", "").lower()

            if dominant in ("depressed", "sad", "hopeless", "lonely") and "PHQ-9" not in administered:
                return "PHQ-9"
            if dominant in ("traumatized", "fearful") and "PCL-5" not in administered:
                return "PCL-5"
            if dominant in ("anxious", "stressed", "nervous", "confused", "angry") and "GAD-7" not in administered:
                return "GAD-7"

            trend = emotion_tracker.get_trend()
            if trend in ("worsening", "volatile"):
                if dominant in ("anxious", "stressed", "nervous") and "GAD-7" not in administered:
                    return "GAD-7"
                if dominant in ("fearful", "traumatized") and "PCL-5" not in administered:
                    return "PCL-5"
                if "PHQ-9" not in administered:
                    return "PHQ-9"

        # Force a scale by FORCE_SCALE_ROUND if none administered yet
        if rounds >= self.FORCE_SCALE_ROUND and not administered:
            return "PHQ-9"

        return None

    def get_scale_guidance_for_prompt(self, scale_name: str) -> str:
        """Generate a natural-language prompt for the LLM to administer a scale."""
        scale = SCALES.get(scale_name)
        if not scale:
            return ""

        questions_text = "\n".join(
            f"  {i+1}. {q}" for i, q in enumerate(scale["questions"])
        )
        options_text = " / ".join(
            f"{opt['score']}-{opt['label']}" for opt in scale["options"]
        )

        return f"""【量表评估 - {scale['name']}】
请在接下来的对话中自然地询问以下问题。不要一次性抛出所有题目，每次只问1-2题，像平常聊天一样穿插在对话中。
评分标准：{options_text}
题目：
{questions_text}

询问方式示例：
- "最近两周，你有没有觉得做什么事都提不起劲？是完全没有，还是有那么几天？"
- "睡眠方面怎么样？入睡困难吗？"

记录规则：将用户的回答映射为0-{len(scale['options'])-1}的分数，以 [SCALE:{scale_name}:Q题号:S分数] 格式嵌入口语回复的末尾。
例如：用户回答了第1题的答案为"好几天"，则在你的回复末尾加上 [SCALE:PHQ-9:Q1:S1]"""


# Singleton
_scale_manager = None


def get_scale_manager() -> ScaleManager:
    global _scale_manager
    if _scale_manager is None:
        _scale_manager = ScaleManager()
    return _scale_manager
