# core.scoring — scale scoring & symptom signal detection (pure logic).
#
# Extracted from services/pipeline.py without behavior change.
# services/pipeline.py re-exports every name below for backward
# compatibility.

from typing import Optional


# Per-item positive symptom keywords for PHQ-9
# Used to detect "symptom confirmed but frequency missing" state
#
# The lists carry two registers on purpose.  Clinical phrasings ("兴趣减退")
# are what the instrument says; the colloquial ones below are how the writer
# actually paraphrases the item aloud and how participants answer.  A writer
# that asks "心里那种闷闷的、想哭的感觉" and a table that only knows
# "心情不好/低落" turn a perfectly good answer into `unmatched`, and the item
# is re-asked until the scale dies.  Every addition here was observed in a
# real session transcript.
PHQ_POSITIVE_KEYWORDS_BY_ITEM = {
    1: ["没兴趣", "没意思", "提不起劲", "不想做", "做什么都没劲",
        "提不起精神", "打不起精神", "不想动", "什么都不想做"],
    2: ["心情不好", "不开心", "低落", "沮丧", "难过", "绝望", "没希望",
        "闷", "心里堵", "想哭", "高兴不起来", "压抑"],
    3: ["睡不着", "失眠", "睡不好", "早醒", "睡太多", "入睡困难",
        "半夜醒", "老醒", "翻来覆去", "睡不踏实", "睡得浅"],
    4: ["累", "没力气", "没劲", "疲惫", "乏力", "没活力",
        "没精神", "撑不住", "浑身没劲", "累得慌"],
    5: ["吃不下", "没胃口", "吃太多", "食欲", "饭量",
        "不想吃", "没食欲", "吃不下饭"],
    6: ["觉得自己很糟", "失败", "让家人失望", "自责", "不够好",
        "没用", "拖累家人", "嫌弃自己"],
    7: ["注意力", "集中不了", "看不进去", "专注不了", "分心",
        "记不住", "走神"],
    8: ["坐不住", "坐立不安", "烦躁", "急躁", "动作变慢", "说话变慢", "停不下来",
        "静不下来", "心里烦"],
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


def infer_scale_score_from_text(
    text: str,
    scale_name: str,
    item: int = None,
    question_text: str = "",
) -> Optional[int]:
    """Fallback: infer a scale score from the user's plain-text answer.

    Used by the deterministic answer interpreter on the user's plain-text
    answer.  Returns None if the text doesn't match any known option pattern.

    When item is provided, frequency words only apply if the turn is actually
    about that item. This prevents "没有，在戒毒所里面基本每天都很紧张" from
    scoring PHQ-9 Q5=3 (it should be Q5=0).

    ``question_text`` is the reply we just spoke.  It matters because a bare
    "没有" carries no topic of its own: it only means "Q1 = 0" if the previous
    question was about losing interest.  When the assistant had drifted to
    another topic ("有没有什么事儿能让你静一静？") the same "没有" is not an
    answer to the active item, and scoring it as one silently deflates the
    total.  Two independent witnesses establish relevance -- the answer names
    the symptom, or the question we asked named it.
    """
    t = text.strip()
    if not t:
        return None

    item_keywords = []
    if item is not None:
        if scale_name == "PHQ-9":
            item_keywords = PHQ_POSITIVE_KEYWORDS_BY_ITEM.get(item, [])
        elif scale_name == "GAD-7":
            item_keywords = GAD7_POSITIVE_KEYWORDS_BY_ITEM.get(item, [])

    # A bare denial is evidence of nothing on its own; it needs the question to
    # have been about this item.  Callers that pass no question_text keep the
    # historical behaviour (score on the text alone) so the pure-text contract
    # of this helper is unchanged.
    _asked_about_item = True
    if item is not None and item_keywords:
        _asked_about_item = any(kw in t for kw in item_keywords)
        if question_text:
            _asked_about_item = _asked_about_item or any(
                kw in question_text for kw in item_keywords
            )

    if scale_name in ("PHQ-9", "GAD-7"):
        # Unambiguous denial: the phrase itself asserts absence, so it scores
        # even without topical confirmation.
        if any(x in t for x in ["完全不会", "从来没有", "一直没有", "一点都没有"]):
            return 0
        # Bare denial ("没有"/"没"/"不会"): score only when this turn is about
        # the item.  Otherwise the answer belongs to whatever was just asked
        # and must stay unscored rather than landing on the active question.
        if any(x in t for x in ["不会"]) or (t in {"没有", "没"}):
            if _asked_about_item:
                return 0
            return None
        # Frequency: only score if the turn is about the current item.
        if _asked_about_item:
            if any(x in t for x in ["好几天", "几天", "偶尔", "很少", "难得"]):
                return 1
            if any(x in t for x in ["一半以上", "大多数", "超过一半", "一半", "经常", "时常"]):
                return 2
            # "一直" is how participants actually answer a duration probe
            # ("一直都会"); it means nearly every day, not "sometimes".
            if any(x in t for x in [
                "几乎每天", "每天", "天天", "基本每天", "一直", "总是", "老是",
            ]):
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
        # Colloquial closings.  These are how participants actually leave an
        # interview ("今天就到这儿吧"); missing them made the session ignore an
        # explicit request to stop, which must never be overridden by a
        # pending assessment or relaxation suggestion.
        "到这儿吧", "到这儿了", "就到这里", "聊到这儿", "先聊到这",
        "今天先到", "今天就这样", "先这样",
    ]
    return any(x in t for x in explicit_end)


#: Event markers that must be present before trauma symptoms count.
#:
#: PCL-5 is not a free-floating anxiety inventory: its items are anchored to a
#: specific index event ("那件压力很大的事"), and DSM-5 criterion A requires
#: actual or threatened death, serious injury, or sexual violence.  Without
#: this gate, any "注意力不集中" or "易受惊" a participant mentions would credit
#: PCL-5, and a bad night's sleep could route an anxious participant into a
#: trauma questionnaire.
#:
#: Deliberately narrow: ordinary losses (breakup, divorce, job loss) are common
#: and distressing but do not satisfy criterion A, so they are *not* markers
#: here — otherwise every unhappy participant would be screened for PTSD.
TRAUMA_EVENT_MARKERS = [
    "车祸", "事故", "火灾", "地震", "洪水", "灾难", "溺水", "工伤",
    "丧亲", "去世", "死了人", "自杀身亡", "目睹", "亲眼看到",
    "强奸", "性侵", "猥亵", "家暴", "暴力", "殴打", "被打", "抢劫",
    "绑架", "霸凌", "校园欺凌", "虐待",
    "那件事", "那件事之后", "创伤事件", "应激事件",
]

#: Default order used only to break exact ties.  Depression stays the default
#: because it is the most common chief complaint in this population, not
#: because it is the most important instrument.
SCALE_PRIORITY = ("PHQ-9", "GAD-7", "PCL-5")

#: Evidence a single dimension must accumulate before the system will offer an
#: instrument on its own initiative.  Set by observation, not theory: one
#: passing mention of "累" plus the shared duration bonus reaches 2, two
#: specific symptom statements reach 3–4, and 3 is where the transcript starts
#: to look like a complaint rather than a bad day.
SCALE_EVIDENCE_THRESHOLD = 3


def has_trauma_event(text: str) -> bool:
    """True when the utterance names a concrete index event for PCL-5."""
    t = (text or "").strip()
    return bool(t) and any(marker in t for marker in TRAUMA_EVENT_MARKERS)


def primary_scale_from_scores(scores: dict) -> Optional[str]:
    """The scale with the strongest evidence, or ``None`` if there is none.

    Ties break toward :data:`SCALE_PRIORITY`.  A score of zero is absence of
    evidence, not a weak preference, so it never nominates a scale: the caller
    decides the fallback.
    """
    if not scores:
        return None
    ranked = [
        (name, int(scores.get(name, 0) or 0))
        for name in SCALE_PRIORITY
    ]
    ranked.sort(key=lambda pair: (-pair[1], SCALE_PRIORITY.index(pair[0])))
    best_name, best_score = ranked[0]
    return best_name if best_score > 0 else None


def score_symptom_signals(text: str, existing_scores: dict = None) -> tuple:
    """Score cumulative symptom signals per scale from user text.

    Returns (deltas, reasons) where deltas is {scale: delta} and reasons is list.

    This is the **routing** table, not the item-scoring table.  The two answer
    different questions and must not be merged:

    - ``PHQ_POSITIVE_KEYWORDS_BY_ITEM`` answers *"is this phrasing an answer to
      item 8?"* — where "坐立不安" is legitimately a depressive item.
    - This table answers *"which instrument does this participant most need?"*
      — where the same phrase is the leading sign of anxiety.

    Overlaps are therefore resolved by **primary attribution**: a symptom is
    credited full weight to the scale it best characterises, and at most a
    reduced weight elsewhere.  Previously "坐不住 / 烦躁" sat in both the PHQ-9
    and GAD-7 lists while PHQ-9 also collected two extra bonuses, so the
    depression score dominated every session and the other two instruments
    could never win the ranking.
    """
    t = (text or "").strip()
    if not t:
        return {}, []

    if existing_scores is None:
        existing_scores = {"PHQ-9": 0, "GAD-7": 0, "PCL-5": 0}

    deltas = {"PHQ-9": 0, "GAD-7": 0, "PCL-5": 0}
    reasons = []

    # --- Shared signals (apply to multiple scales) ---

    # Evasive answers (+1 only if already have some signal)
    evasive = ["不想说", "不想聊", "没有", "不知道", "算了", "不晓得", "说不清"]
    if any(w in t for w in evasive):
        for s in deltas:
            if existing_scores.get(s, 0) >= 1:
                deltas[s] += 1
        reasons.append("回避")

    # --- PHQ-9 specific (depression: mood, interest, sleep, energy, appetite,
    #     self-worth, concentration, psychomotor, self-harm ideation) ---

    low_mood = ["不开心", "难受", "失落", "低落", "痛苦", "压抑", "孤单",
                "不高兴", "心情不好", "情绪不好", "心里不舒服", "心里难受", "沮丧",
                "闷", "心里堵", "想哭", "高兴不起来"]
    if any(w in t for w in low_mood):
        deltas["PHQ-9"] += 1
        reasons.append("低落情绪→PHQ")

    unexplained = ["没有原因", "不知道为什么", "说不上来", "莫名其妙", "没什么事", "没有为什么"]
    if any(w in t for w in unexplained) and any(w in t for w in ["不开心", "难受", "不好", "烦"]):
        deltas["PHQ-9"] += 2
        reasons.append("无因低落→PHQ")

    # Anhedonia is a core criterion, so it carries full symptom weight on its
    # own: "做什么都没意思" is more diagnostic than "睡不着".
    anhedonia = ["没兴趣", "没意思", "什么都不想干", "做什么都没意思", "提不起劲",
                 "提不起精神", "提不起兴趣", "打不起精神", "干什么都没劲",
                 "做什么都没劲", "什么都没劲", "干啥都没劲"]
    if any(w in t for w in anhedonia):
        deltas["PHQ-9"] += 2
        reasons.append("兴趣减退→PHQ")

    phq_symptoms = ["睡不着", "睡不好", "失眠", "很累", "没力气", "疲惫", "乏力",
                    "吃不下", "吃太多", "没胃口", "注意力集中不了", "看不进去",
                    "觉得自己没用", "觉得自己很糟", "失败", "自责", "拖累家人",
                    "想死", "不想活", "活不下去", "撑不住"]
    if any(w in t for w in phq_symptoms):
        deltas["PHQ-9"] += 2
        reasons.append("PHQ症状")

    # Psychomotor agitation belongs to PHQ-9 item 8, but it is also the
    # cardinal sign of anxiety — so it only earns a reduced PHQ-9 weight while
    # GAD-7 collects it in full below.
    phq_agitation = ["坐不住", "坐立不安", "烦躁", "急躁", "停不下来", "静不下来", "心里烦"]
    if any(w in t for w in phq_agitation):
        deltas["PHQ-9"] += 1
        reasons.append("激越→PHQ弱")

    # --- GAD-7 specific (anxiety: apprehension, uncontrollable worry,
    #     restlessness, irritability, dread) ---

    anxiety_words = ["焦虑", "紧张", "不安", "心慌", "害怕", "恐惧", "发慌", "着急", "发急"]
    if any(w in t for w in anxiety_words):
        deltas["GAD-7"] += 2
        reasons.append("焦虑词→GAD")

    # Excessive/uncontrollable worry — GAD-7 items 2-3, and the single most
    # specific anxiety phrase in Chinese colloquial speech.
    worry_words = ["担心", "担忧", "操心", "放心不下", "胡思乱想", "想太多",
                   "控制不了", "控制不住", "停不下来"]
    if any(w in t for w in worry_words):
        deltas["GAD-7"] += 2
        reasons.append("难以控制的担忧→GAD")

    if any(w in t for w in phq_agitation):
        deltas["GAD-7"] += 2
        reasons.append("激越→GAD")

    gad_symptoms = ["放松不了", "放松不下来", "总觉得要出事", "坐立不安", "静不下来"]
    if any(w in t for w in gad_symptoms):
        deltas["GAD-7"] += 1
        reasons.append("GAD症状")

    # --- PCL-5 specific (trauma) ---
    #
    # Gated on a named event: see TRAUMA_EVENT_MARKERS.  Symptoms alone are not
    # evidence of trauma, and crediting them without an event is how an
    # anxious participant ends up filling in a PTSD screen.
    #
    # The gate is per *participant*, not per sentence: once someone has named
    # the event, the following turns describe its aftermath ("晚上总做噩梦")
    # without repeating the word 车祸.  Requiring the marker in every turn meant
    # nothing after the first line ever scored -- a real session showed 车祸
    # scoring +3 and then three symptom-heavy turns scoring zero.

    if has_trauma_event(t):
        deltas["PCL-5"] += 3
        reasons.append("创伤事件→PCL")

    if has_trauma_event(t) or int(existing_scores.get("PCL-5", 0) or 0) > 0:
        intrusion = ["噩梦", "做噩梦", "闪回", "反复想起", "不由自主地想起",
                     "总想起来", "回想起来", "画面", "惊醒"]
        if any(w in t for w in intrusion):
            deltas["PCL-5"] += 2
            reasons.append("侵入性回忆→PCL")

        avoidance = ["回避", "避免", "不敢想", "不敢去", "不敢坐", "不敢再",
                     "不想提", "躲开", "避开"]
        if any(w in t for w in avoidance):
            deltas["PCL-5"] += 2
            reasons.append("回避→PCL")

        hyperarousal = ["过度警觉", "易受惊", "吓一跳", "警惕", "睡不着"]
        if any(w in t for w in hyperarousal):
            deltas["PCL-5"] += 1
            reasons.append("警觉增高→PCL")

    # --- Rehab context with emotional content (+1 to PHQ if depression signals present) ---
    rehab = ["戒毒", "吸毒", "戒毒所", "强制"]
    rehab_emotion = ["孤独", "无助", "低落", "睡不好", "没兴趣", "难受"]
    if any(w in t for w in rehab) and any(w in t for w in rehab_emotion):
        deltas["PHQ-9"] += 1
        reasons.append("戒毒+情绪→PHQ")

    # --- Shared: duration/frequency amplifies existing evidence only ---
    #
    # "最近" on its own says nothing about which dimension is affected, so it
    # must not put a floor under every scale.  Applied unconditionally it made
    # trauma symptoms score PCL-5 >= 1 for any sentence containing "最近" —
    # enough to look like evidence in a chart and enough to make a bare mention
    # of duration indistinguishable from a reported symptom.
    duration = ["最近", "一直", "总是", "经常", "每天", "大多数时候", "很久了",
                "好长时间", "好几周", "好几个月", "一个多月", "两三个月"]
    if any(w in t for w in duration) and any(value > 0 for value in deltas.values()):
        for s in deltas:
            if deltas[s] > 0:
                deltas[s] += 1
        reasons.append("持续性")

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


def is_scale_resume_text(text: str) -> bool:
    """Check if the participant asks to go back to the interrupted questions.

    Deliberately narrow: a bare "继续" is *not* enough, because it just as
    often means "carry on with what we were talking about".  Only a phrase
    that names the questions themselves reopens a paused scale.
    """
    t = (text or "").strip()
    if not t:
        return False
    # A refusal that happens to contain a resume phrase ("别问了吧") is still a
    # refusal; interruption detection runs first, but this keeps the helper
    # honest when called on its own.
    if any(x in t for x in ("别问", "不想问", "不要问", "不用问", "先不问")):
        return False

    resume_phrases = [
        "继续问", "接着问", "接着刚才的", "接着之前的", "继续刚才的",
        "继续之前", "还是接着问", "问吧", "你问吧", "可以问了",
        "回到刚才", "说回刚才", "那几个问题", "刚才那个问题",
        "接着做", "继续做问卷", "继续量表", "把问卷做完",
        "准备好了你问",
    ]
    return any(x in t for x in resume_phrases)
