# core.scoring — scale scoring & symptom signal detection (pure logic).
#
# Extracted from services/pipeline.py without behavior change.
# services/pipeline.py re-exports every name below for backward
# compatibility.

from typing import Optional


# Per-item positive symptom keywords for PHQ-9
# Used to detect "symptom confirmed but frequency missing" state
PHQ_POSITIVE_KEYWORDS_BY_ITEM = {
    1: ["没兴趣", "没意思", "提不起劲", "不想做", "做什么都没劲"],
    2: ["心情不好", "不开心", "低落", "沮丧", "难过", "绝望", "没希望"],
    3: ["睡不着", "失眠", "睡不好", "早醒", "睡太多", "入睡困难"],
    4: ["累", "没力气", "没劲", "疲惫", "乏力", "没活力"],
    5: ["吃不下", "没胃口", "吃太多", "食欲", "饭量"],
    6: ["觉得自己很糟", "失败", "让家人失望", "自责", "不够好"],
    7: ["注意力", "集中不了", "看不进去", "专注不了", "分心"],
    8: ["坐不住", "坐立不安", "烦躁", "急躁", "动作变慢", "说话变慢", "停不下来"],
    9: ["不想活", "伤害自己", "自杀", "自残", "死了算了"],
}

# Frequency words — if present with symptom, can score directly
FREQUENCY_WORDS = [
    "没有", "偶尔", "有时候", "有时", "几天", "一两天",
    "经常", "大多数", "多数时候", "一半以上", "超过一半",
    "每天", "天天", "几乎每天", "一直", "总是", "老是", "基本每天",
]

# Per-item positive symptom keywords for GAD-7
GAD7_POSITIVE_KEYWORDS_BY_ITEM = {
    1: ["紧张", "焦虑", "急切", "心慌", "不安"],
    2: ["停不下来", "控制不了", "控制不住", "一直担心"],
    3: ["担心", "担忧", "操心", "放心不下"],
    4: ["放松不了", "放松不下来", "静不下来"],
    5: ["坐不住", "坐立不安", "静不下来", "动来动去"],
    6: ["烦", "急躁", "容易生气", "不耐烦"],
    7: ["害怕", "恐惧", "觉得要出事", "总觉得不好"],
}


def infer_scale_score_from_text(text: str, scale_name: str, item: int = None) -> Optional[int]:
    """Fallback: infer a scale score from the user's plain-text answer.

    Used by the deterministic answer interpreter on the user's plain-text
    answer.  Returns None if the text doesn't match any known option pattern.

    When item is provided, frequency words only apply if the text also
    contains symptom keywords for that item. This prevents "没有，在戒毒所
    里面基本每天都很紧张" from scoring PHQ-9 Q5=3 (it should be Q5=0).
    """
    t = text.strip()
    if not t:
        return None

    # Check if text contains symptom keywords for the current item.
    # Item-aware matching prevents a wrong-item symptom from being scored
    # against the active question (e.g. talking about sleep while on the
    # appetite item should not yield a score).
    _item_symptom_match = True
    if item is not None and scale_name == "PHQ-9":
        item_keywords = PHQ_POSITIVE_KEYWORDS_BY_ITEM.get(item, [])
        _item_symptom_match = any(kw in t for kw in item_keywords)
    elif item is not None and scale_name == "GAD-7":
        item_keywords = GAD7_POSITIVE_KEYWORDS_BY_ITEM.get(item, [])
        _item_symptom_match = any(kw in t for kw in item_keywords)

    if scale_name in ("PHQ-9", "GAD-7"):
        # Denial: "没有"/"不会" → 0 (only if no conflicting symptom keywords)
        if any(x in t for x in ["完全不会", "不会"]) or (t in {"没有", "没"}):
            return 0
        # Frequency: only score if text matches current item's symptoms
        if _item_symptom_match:
            if any(x in t for x in ["好几天", "几天", "偶尔"]):
                return 1
            if any(x in t for x in ["一半以上", "大多数", "超过一半"]):
                return 2
            if any(x in t for x in ["几乎每天", "每天", "天天"]):
                return 3

    if scale_name == "PCL-5":
        if any(x in t for x in ["完全没有", "没有"]):
            return 0
        if "有一点" in t:
            return 1
        if any(x in t for x in ["中等程度", "中等"]):
            return 2
        if "相当严重" in t:
            return 3
        if any(x in t for x in ["极度严重", "非常严重"]):
            return 4

    return None


def detect_phq_item_from_text(text: str) -> Optional[int]:
    """Detect which PHQ-9 item the user's text naturally refers to.

    Returns item number (1-9) or None if no clear match.
    Used to score the symptom the user is actually talking about,
    rather than forcing the current active question.
    """
    t = text or ""
    if any(x in t for x in ["没兴趣", "没意思", "提不起劲", "不想做", "做什么都没劲"]):
        return 1
    if any(x in t for x in ["心情不好", "不开心", "低落", "沮丧", "没希望", "绝望"]):
        return 2
    if any(x in t for x in ["睡不着", "失眠", "睡不好", "早醒", "睡太多", "入睡困难"]):
        return 3
    if any(x in t for x in ["累", "没力气", "没劲", "疲惫", "没活力", "乏力"]):
        return 4
    if any(x in t for x in ["吃不下", "没胃口", "吃太多", "饭量"]):
        return 5
    if any(x in t for x in ["觉得自己很糟", "失败", "失望", "自责", "不够好"]):
        return 6
    if any(x in t for x in ["注意力", "集中不了", "看不进去", "专注"]):
        return 7
    if any(x in t for x in ["动作变慢", "坐不住", "烦躁", "动来动去", "说话慢"]):
        return 8
    if any(x in t for x in ["不想活", "伤害自己", "死", "自杀", "自残"]):
        return 9
    return None


def is_user_explicit_end_text(text: str) -> bool:
    """Check if user explicitly wants to end the session.

    Weak responses like "好吧", "嗯", "没有" should NOT trigger session end.
    """
    t = (text or "").strip("。！？!?,， ")

    # Weak responses — definitely not ending
    weak = {"好吧", "嗯", "哦", "没有", "行吧", "可以吧", "还好吧", "不知道", "嗯嗯", "好的", "行"}
    if t in weak:
        return False

    explicit_end = [
        "不想聊了", "今天不聊了", "今天先这样", "先到这吧", "先这样吧",
        "我要结束", "结束", "结束吧", "不说了", "我想休息了", "我累了想睡了",
        "可以结束了", "聊完了", "退出",
    ]
    return any(x in t for x in explicit_end)


def score_symptom_signals(text: str, existing_scores: dict = None) -> tuple:
    """Score cumulative symptom signals per scale from user text.

    Returns (deltas, reasons) where deltas is {scale: delta} and reasons is list.
    """
    t = (text or "").strip()
    if not t:
        return {}, []

    if existing_scores is None:
        existing_scores = {"PHQ-9": 0, "GAD-7": 0, "PCL-5": 0}

    deltas = {"PHQ-9": 0, "GAD-7": 0, "PCL-5": 0}
    reasons = []

    # --- Shared signals (apply to multiple scales) ---

    # Duration/frequency words (+1 to all active scales)
    duration = ["最近", "一直", "总是", "经常", "每天", "大多数时候", "很久了", "好长时间", "好几周", "好几个月"]
    if any(w in t for w in duration):
        for s in deltas:
            deltas[s] += 1
        reasons.append("持续性")

    # Evasive answers (+1 only if already have some signal)
    evasive = ["不想说", "不想聊", "没有", "不知道", "算了", "不晓得", "说不清"]
    if any(w in t for w in evasive):
        for s in deltas:
            if existing_scores.get(s, 0) >= 1:
                deltas[s] += 1
        reasons.append("回避")

    # --- PHQ-9 specific (depression symptoms) ---

    low_mood = ["不开心", "难受", "失落", "低落", "烦", "痛苦", "压抑", "孤单", "没意思",
                "不高兴", "心情不好", "情绪不好", "心里不舒服", "心里难受", "沮丧"]
    if any(w in t for w in low_mood):
        deltas["PHQ-9"] += 1
        reasons.append("低落情绪→PHQ")

    unexplained = ["没有原因", "不知道为什么", "说不上来", "莫名其妙", "没什么事", "没有为什么"]
    if any(w in t for w in unexplained) and any(w in t for w in ["不开心", "难受", "不好", "烦"]):
        deltas["PHQ-9"] += 2
        reasons.append("无因低落→PHQ")

    phq_symptoms = ["睡不着", "睡不好", "失眠", "没兴趣", "做什么都没意思", "很累",
                    "没力气", "吃不下", "吃太多", "注意力集中不了", "觉得自己没用",
                    "拖累家人", "想死", "不想活", "坐不住", "烦躁"]
    if any(w in t for w in phq_symptoms):
        deltas["PHQ-9"] += 2
        reasons.append("PHQ症状")

    # --- GAD-7 specific (anxiety symptoms) ---

    anxiety_words = ["焦虑", "紧张", "担心", "不安", "心慌", "害怕", "恐惧", "急躁"]
    if any(w in t for w in anxiety_words):
        deltas["GAD-7"] += 2
        reasons.append("焦虑词→GAD")

    gad_symptoms = ["停不下来", "控制不了", "放松不了", "坐不住", "坐立不安", "总觉得要出事"]
    if any(w in t for w in gad_symptoms):
        deltas["GAD-7"] += 2
        reasons.append("GAD症状")

    # --- PCL-5 specific (trauma symptoms) ---

    trauma_words = ["噩梦", "创伤", "闪回", "惊吓", "回想起来", "做噩梦", "不敢想"]
    if any(w in t for w in trauma_words):
        deltas["PCL-5"] += 2
        reasons.append("创伤词→PCL")

    pcl_symptoms = ["回避", "不敢去", "过度警觉", "易受惊", "难以集中"]
    if any(w in t for w in pcl_symptoms):
        deltas["PCL-5"] += 2
        reasons.append("PCL症状")

    # --- Rehab context with emotional content (+1 to PHQ if depression signals present) ---
    rehab = ["戒毒", "吸毒", "戒毒所", "强制"]
    rehab_emotion = ["孤独", "无助", "低落", "睡不好", "没兴趣", "难受"]
    if any(w in t for w in rehab) and any(w in t for w in rehab_emotion):
        deltas["PHQ-9"] += 1
        reasons.append("戒毒+情绪→PHQ")

    return deltas, reasons


def is_scale_interruption_text(text: str) -> bool:
    """Check if user is interrupting/resisting scale questioning.

    Returns True if the user is clearly resisting, changing topic, or
    expressing frustration about being questioned.
    """
    t = (text or "").strip()

    interruption_phrases = [
        "为啥一直问", "为什么一直问", "别问了", "不想回答", "换个话题",
        "不想说这个", "就是想聊天", "你怎么老问", "别再问了",
        "聊点别的", "说点别的", "不想聊这个", "不要问了",
        "我来聊天的", "我不是来做问卷的",
    ]
    return any(x in t for x in interruption_phrases)
