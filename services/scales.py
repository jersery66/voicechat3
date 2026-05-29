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
            "反复、不自主地回忆 stressful 的事件",
            "反复做与 stressful 事件有关的噩梦",
            "尽量避免回忆或谈论 stressful 的事件",
            "尽量避免与 stressful 事件有关的外部提示（人、地点、活动等）",
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

    # Round by which every participant must have been offered at least one scale
    FORCE_SCALE_ROUND = 2

    # --- Keyword lists (shared by should_administer + recommend_scale_candidates) ---
    DEPRESSION_KW = [
        "难过", "伤心", "悲伤", "低落", "没意思", "绝望", "想哭",
        "抑郁", "失眠", "睡不着", "疲倦", "没活力", "心情不好",
        "不开心", "难受", "痛苦", "消沉", "颓废", "沮丧", "郁闷",
        "活着没意思", "不想活", "累", "没劲", "无聊", "空虚",
        "感觉不好", "感觉很差", "状态不好", "情绪不好",
    ]
    ANXIETY_KW = [
        "焦虑", "紧张", "害怕", "恐惧", "担心", "不安", "心慌",
        "烦躁", "烦躁不安", "压力", "慌", "坐立不安", "心烦",
        "睡不好", "做噩梦", "心跳快", "喘不过气",
    ]
    TRAUMA_KW = ["噩梦", "创伤", "应激", "闪回", "惊吓", "被打", "被欺负", "事故"]

    def recommend_scale_candidates(self, user_text: str,
                                    administered: set = None) -> List[str]:
        """Return ALL matching scale names (not just one). Caller queues them."""
        if not user_text:
            return []
        if administered is None:
            administered = set()

        text_lower = user_text.lower()
        candidates = []

        for kw in self.DEPRESSION_KW:
            if kw in text_lower and "PHQ-9" not in administered:
                candidates.append("PHQ-9")
                break
        for kw in self.ANXIETY_KW:
            if kw in text_lower and "GAD-7" not in administered:
                candidates.append("GAD-7")
                break
        for kw in self.TRAUMA_KW:
            if kw in text_lower and "PCL-5" not in administered:
                candidates.append("PCL-5")
                break

        return candidates

    def should_administer(self, emotion_tracker=None,
                          report_service=None,
                          user_text=None,
                          administered: set = None,
                          agent_service=None,
                          conversation_context: str = "") -> Optional[str]:
        """
        Determine if a scale should be administered based on session context.
        Returns scale name (e.g. "PHQ-9") or None.

        Priority: keywords > agent model > emotion tracker > force fallback.
        """
        import logging
        _log = logging.getLogger(__name__)

        if administered is None:
            administered = set()

        rounds = 0
        if report_service:
            rounds = report_service.get_round_count()
            if rounds < 1:
                _log.debug(f"Scale check skipped: rounds={rounds} < 1")
                return None

        _log.info(f"Scale check: rounds={rounds}, user_text={user_text!r}, "
                  f"agent={'yes' if agent_service else 'no'}, administered={administered}")

        # Debug trigger
        if user_text and "量表测试" in user_text:
            _log.warning("DEBUG scale trigger: 量表测试 -> PHQ-9")
            return "PHQ-9"

        # 1. Keyword matching first (fast, reliable for obvious signals)
        if user_text:
            text_lower = user_text.lower()
            for kw in self.DEPRESSION_KW:
                if kw in text_lower:
                    _log.info(f"Keyword match (depression): '{kw}' -> PHQ-9")
                    return "PHQ-9"
            for kw in self.ANXIETY_KW:
                if kw in text_lower:
                    _log.info(f"Keyword match (anxiety): '{kw}' -> GAD-7")
                    return "GAD-7"
            for kw in self.TRAUMA_KW:
                if kw in text_lower:
                    _log.info(f"Keyword match (trauma): '{kw}' -> PCL-5")
                    return "PCL-5"
            _log.info(f"No keyword match for: {user_text!r}")

        # 2. Agent model for nuanced cases (keywords didn't match)
        if user_text and agent_service and agent_service.is_available():
            _log.info("No keyword match, trying agent model...")
            agent_result = agent_service.recommend_scale(
                user_text, context=conversation_context)
            if agent_result:
                _log.info(f"Agent recommended: {agent_result}")
                return agent_result
            _log.info("Agent also returned no recommendation")

        # 3. Emotion tracker
        if emotion_tracker:
            data = emotion_tracker.get_emotion_data()
            dominant = data.get("dominant", "").lower()

            if dominant in ("depressed", "sad", "hopeless", "lonely"):
                return "PHQ-9"
            if dominant in ("traumatized", "fearful"):
                return "PCL-5"
            if dominant in ("anxious", "stressed", "nervous", "confused", "angry"):
                return "GAD-7"

            trend = emotion_tracker.get_trend()
            if trend in ("worsening", "volatile"):
                if dominant in ("anxious", "stressed", "nervous"):
                    return "GAD-7"
                if dominant in ("fearful", "traumatized"):
                    return "PCL-5"
                return "PHQ-9"

        # 4. Force PHQ-9 fallback — only if no keywords/agent/emotion matched
        if rounds >= self.FORCE_SCALE_ROUND and not administered:
            _log.info(f"Force PHQ-9: round {rounds} with no scales given")
            return "PHQ-9"

        return None

    def get_scale_guidance_for_prompt(self, scale_name: str) -> str:
        """Generate a forceful prompt that makes the LLM administer a scale THIS turn."""
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

【本轮强制任务】
你已经决定启动该量表。当前这一轮必须执行量表追问，不能只做普通共情或开放式追问。
回复结构：先简短反映用户情绪一句（不超过15字），然后立刻询问量表第1题。
禁止继续泛泛询问"发生了什么事""具体什么事情""为什么不开心"。

【提问要求】
每次只问1题，优先从Q1开始。问题必须自然口语化，但语义必须对应量表原题。
评分标准：{options_text}
题目：
{questions_text}

【记录规则】
用户回答了某题后，将答案映射为0-{len(scale['options'])-1}的分数。
必须以 [SCALE:{scale_name}:Q题号:S分数] 格式嵌入回复末尾。
本轮只是提问、用户尚未回答，则不要输出SCALE标签。

【示例】
用户说"我不开心好久了"：
|||这阵子一直不开心，确实挺耗人。[breath]我先问一个简单的，最近两周，你做事还有没有兴趣？"""


# Singleton
_scale_manager = None


def get_scale_manager() -> ScaleManager:
    global _scale_manager
    if _scale_manager is None:
        _scale_manager = ScaleManager()
    return _scale_manager
