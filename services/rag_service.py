# RAG Service - Knowledge Retrieval for Psychology Counseling

import os
import sys
import json
from typing import Optional, List, Dict, Any, Set
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _init_jieba():
    """延迟初始化jieba，添加心理学自定义词典"""
    try:
        import jieba
        psychology_terms = [
            "戒断", "复吸", "渴求", "吸毒", "冰毒", "强制隔离", "戒毒所",
            "焦虑", "抑郁", "失眠", "幻觉", "幻听", "妄想", "强迫", "恐惧",
            "创伤后应激", "解离", "躯体化", "睡眠障碍", "入睡困难",
            "情绪低落", "情绪失控", "情绪管理", "愤怒管理", "冲动控制",
            "认知行为疗法", "精神分析", "人本主义", "动机访谈", "正念", "冥想",
            "放松训练", "渐进式肌肉放松", "深呼吸", "系统脱敏",
            "危机干预", "自杀预防", "安全评估",
            "家庭关系", "亲子关系", "夫妻关系", "社会支持", "人际关系",
            "孤独感", "社交退缩", "信任", "沟通",
            "心里堵", "烦得很", "脑子乱", "坐立不安", "浑身无力",
            "想发火", "脾气大", "控制不住", "忍不住", "活着没劲",
            "睡不着", "做噩梦", "心慌", "喘不过气",
            "渴求感", "心理依赖", "躯体依赖", "戒断反应", "稽延性戒断",
        ]
        for term in psychology_terms:
            jieba.add_word(term)
        return jieba
    except ImportError:
        return None


class RAGService:
    """
    RAG (Retrieval-Augmented Generation) Service for psychology counseling.

    Features:
    - Intent routing with jieba segmentation
    - Keyword + content hybrid search
    - Synonym expansion for better recall
    - Lazy loading for large datasets
    """

    # Expanded synonym map: colloquial -> professional terms
    SYNONYM_MAP = {
        # === Sleep related ===
        "睡不着": ["失眠", "睡眠障碍", "入睡困难"],
        "失眠": ["睡眠障碍", "入睡困难"],
        "早醒": ["睡眠障碍", "失眠"],
        "多梦": ["睡眠障碍", "噩梦"],
        "噩梦": ["睡眠障碍", "创伤"],
        "睡不好": ["失眠", "睡眠障碍"],
        "翻来覆去": ["失眠", "入睡困难"],
        "半夜醒": ["早醒", "睡眠障碍"],
        "做恶梦": ["噩梦", "创伤"],
        "睡眠差": ["失眠", "睡眠障碍"],
        "睡不踏实": ["失眠", "睡眠浅"],
        "整夜整夜": ["失眠", "严重失眠"],

        # === Anxiety related ===
        "心里堵": ["焦虑", "情绪困扰", "心理压力"],
        "烦得很": ["焦虑", "烦躁", "情绪困扰"],
        "脑子乱": ["焦虑", "思维混乱", "注意力不集中"],
        "心慌": ["焦虑", "恐慌", "心悸"],
        "紧张": ["焦虑", "压力"],
        "害怕": ["恐惧", "焦虑", "恐慌"],
        "担心": ["焦虑", "担忧"],
        "坐立不安": ["焦虑", "烦躁"],
        "喘不过气": ["焦虑", "恐慌", "呼吸困难"],
        "发慌": ["焦虑", "恐慌"],
        "忐忑": ["焦虑", "不安"],
        "七上八下": ["焦虑", "不安"],
        "心神不宁": ["焦虑", "不安"],
        "六神无主": ["焦虑", "恐慌"],
        "提心吊胆": ["焦虑", "恐惧"],
        "惶恐": ["恐惧", "焦虑"],
        "手抖": ["焦虑", "躯体化"],
        "出汗": ["焦虑", "躯体化"],
        "胸闷": ["焦虑", "躯体化"],
        "心跳快": ["焦虑", "心悸"],

        # === Depression related ===
        "没意思": ["抑郁", "兴趣减退", "情绪低落"],
        "不想活": ["抑郁", "自杀倾向", "绝望"],
        "活着没劲": ["抑郁", "绝望", "无意义感"],
        "开心不起来": ["抑郁", "情绪低落"],
        "情绪低落": ["抑郁", "情绪障碍"],
        "绝望": ["抑郁", "无望感"],
        "想哭": ["抑郁", "情绪低落", "悲伤"],
        "没劲": ["抑郁", "兴趣减退"],
        "空虚": ["抑郁", "无意义感"],
        "无聊透顶": ["抑郁", "无意义感"],
        "对啥都没兴趣": ["抑郁", "兴趣减退"],
        "提不起精神": ["抑郁", "情绪低落"],
        "萎靡不振": ["抑郁", "情绪低落"],
        "心如死灰": ["抑郁", "绝望"],
        "万念俱灰": ["抑郁", "绝望", "自杀风险"],
        "不想吃饭": ["抑郁", "食欲减退"],
        "吃不下东西": ["抑郁", "食欲减退"],
        "瘦了": ["抑郁", "体重下降"],
        "不想动": ["抑郁", "运动减少"],
        "躺一天": ["抑郁", "行为退缩"],
        "自暴自弃": ["抑郁", "绝望"],
        "破罐破摔": ["抑郁", "绝望"],
        "生不如死": ["抑郁", "自杀风险"],

        # === Anger related ===
        "想发火": ["愤怒", "情绪管理", "冲动"],
        "脾气大": ["愤怒", "情绪管理"],
        "容易生气": ["愤怒", "情绪管理"],
        "控制不住": ["冲动", "情绪管理"],
        "暴躁": ["愤怒", "情绪管理"],
        "火大": ["愤怒", "冲动"],
        "气死了": ["愤怒", "情绪失控"],
        "忍不了": ["愤怒", "冲动控制"],
        "爆发": ["愤怒", "情绪失控"],
        "砸东西": ["愤怒", "冲动行为"],
        "想打人": ["愤怒", "冲动", "暴力风险"],
        "骂人": ["愤怒", "情绪管理"],
        "吵架": ["愤怒", "人际冲突"],
        "看谁都不顺眼": ["愤怒", "烦躁"],
        "一点就着": ["愤怒", "易激惹"],

        # === Family related ===
        "想家": ["思乡", "家庭关系", "情感支持"],
        "想家人": ["思乡", "家庭关系"],
        "家里": ["家庭", "家庭关系"],
        "父母": ["家庭", "家庭关系"],
        "老婆": ["家庭", "夫妻关系"],
        "孩子": ["家庭", "亲子关系"],
        "媳妇": ["家庭", "夫妻关系"],
        "老公": ["家庭", "夫妻关系"],
        "爸妈": ["家庭", "亲子关系"],
        "老爸": ["家庭", "亲子关系"],
        "老妈": ["家庭", "亲子关系"],
        "儿子": ["家庭", "亲子关系"],
        "女儿": ["家庭", "亲子关系"],
        "兄弟姐妹": ["家庭", "手足关系"],
        "家里人": ["家庭", "家庭关系"],
        "对不起家人": ["愧疚", "家庭关系"],
        "丢人": ["羞耻", "家庭关系"],
        "没脸见人": ["羞耻", "社会压力"],
        "家里出事": ["家庭危机", "压力"],

        # === Addiction related ===
        "想吸毒": ["复吸", "渴求", "戒断"],
        "难受": ["戒断", "身体不适", "痛苦"],
        "痛苦": ["戒断", "心理痛苦"],
        "忍不住": ["渴求", "冲动控制", "戒断", "复吸"],
        "瘾上来了": ["渴求", "心理依赖"],
        "犯瘾": ["渴求", "戒断反应"],
        "浑身不自在": ["戒断", "躯体不适"],
        "不自在": ["戒断", "躯体不适"],
        "像蚂蚁爬": ["戒断", "躯体感觉异常"],
        "骨头痒": ["戒断", "躯体症状"],
        "心里发毛": ["渴求", "焦虑"],
        "抓心挠肝": ["渴求", "焦虑"],
        "飘飘然": ["渴求", "心理依赖"],
        "爽": ["渴求", "心理依赖"],
        "过瘾": ["渴求", "心理依赖"],
        "一口": ["渴求", "复吸风险"],
        "溜冰": ["吸毒", "冰毒"],
        "打针": ["吸毒", "注射"],
        "粉": ["毒品", "海洛因"],
        "药": ["毒品", "药物滥用"],

        # === Cognitive related ===
        "恍惚": ["解离", "认知障碍"],
        "不真实": ["解离", "现实检验"],
        "做梦": ["解离", "睡眠障碍"],
        "发呆": ["解离", "注意力障碍"],
        "幻觉": ["幻觉", "精神病性症状"],
        "幻听": ["幻觉", "精神病性症状"],
        "声音": ["幻听"],
        "听到声音": ["幻听", "精神病性症状"],
        "有声音": ["幻听"],
        "奇怪声音": ["幻听", "幻觉"],
        "看到东西": ["幻视", "精神病性症状"],
        "有人跟着我": ["被害妄想", "精神病性症状"],
        "有人要害我": ["被害妄想", "精神病性症状"],
        "被监控": ["被害妄想", "精神病性症状"],
        "灵魂出窍": ["解离", "人格解体"],
        "不在自己身上": ["解离", "现实解体"],

        # === Relationship related ===
        "没人理解": ["孤独", "社会支持", "人际关系"],
        "孤独": ["孤独感", "社会支持"],
        "不想说话": ["社交退缩", "抑郁", "人际关系"],
        "没人关心": ["孤独", "社会支持缺乏"],
        "一个人": ["孤独", "社会隔离"],
        "不合群": ["社交困难", "人际关系"],
        "被人看不起": ["自卑", "社会压力"],
        "低人一等": ["自卑", "自我价值感低"],
        "没朋友": ["孤独", "社交困难"],
        "被人欺负": ["人际冲突", "创伤"],
        "受欺负": ["人际冲突", "创伤"],

        # === Physical symptoms ===
        "头疼": ["躯体化", "心理压力"],
        "胃不舒服": ["躯体化", "焦虑"],
        "浑身无力": ["抑郁", "躯体化", "疲劳"],
        "腰酸背痛": ["躯体化", "身体紧张"],
        "胸口闷": ["焦虑", "躯体化"],
        "肚子疼": ["躯体化", "焦虑"],
        "没力气": ["疲劳", "抑郁"],
        "身体不行": ["躯体化", "健康焦虑"],
        "这儿疼那儿疼": ["躯体化", "心理痛苦"],
        "不舒服": ["躯体化", "身体不适"],

        # === Work/Study related ===
        "压力大": ["压力", "焦虑", "工作压力"],
        "学不进去": ["注意力", "学习困难", "焦虑"],
        "工作不顺": ["工作压力", "职业困扰"],
        "干不下去": ["职业倦怠", "压力"],
        "啥都干不好": ["自我效能低", "抑郁"],
        "一事无成": ["自我否定", "抑郁"],
        "没前途": ["无望感", "职业困扰"],

        # === Self-harm / Crisis ===
        "想死": ["自杀", "自杀风险", "危机干预"],
        "不想活了": ["自杀", "自杀风险", "危机干预"],
        "割腕": ["自残", "自杀风险"],
        "跳楼": ["自杀", "自杀方式"],
        "活够了": ["自杀", "绝望"],
        "解脱": ["自杀", "无望感"],
        "遗书": ["自杀", "自杀计划"],
        "遗言": ["自杀", "自杀计划"],
        "死了算了": ["自杀", "自杀意念"],
    }

    # Core knowledge files (loaded immediately)
    CORE_FILES = ["knowledge.json"]
    # Large dataset files (loaded lazily on first search)
    LAZY_FILES = [
        "cpsycounr_converted.json",
        "psyqa_converted.json",
        "emollm_single_turn_1.json",
        "emollm_single_turn_2.json",
        "emollm_multi_turn.json",
    ]

    def __init__(self, knowledge_base_path: str = None):
        self.knowledge_base_path = Path(knowledge_base_path) if knowledge_base_path else self._get_default_kb_path()
        self.knowledge_base: List[Dict[str, str]] = []
        self._core_count = 0  # Track how many entries are from core knowledge
        self._lazy_loaded = False
        self._lazy_files_loaded: Set[str] = set()  # Track which lazy files are loaded
        self._jieba = None
        self._query_cache: Dict[str, Set[str]] = {}  # Cache expanded queries
        self._search_cache: Dict[str, List[Dict]] = {}  # Cache search results
        self._load_core_knowledge()

    def _get_default_kb_path(self) -> Path:
        app_dir = Path(__file__).parent.parent
        return app_dir / "knowledge_base"

    def _get_jieba(self):
        if self._jieba is None:
            self._jieba = _init_jieba()
        return self._jieba

    def _load_core_knowledge(self):
        """Load core knowledge base (small, essential files)."""
        for filename in self.CORE_FILES:
            file_path = self.knowledge_base_path / filename
            if file_path.exists():
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, list):
                        valid_entries = []
                        for entry in data:
                            if isinstance(entry, dict) and 'content' in entry:
                                valid_entries.append({
                                    "id": entry.get('id', f"entry_{len(self.knowledge_base)+1}"),
                                    "keywords": entry.get('keywords', []),
                                    "title": entry.get('title', ''),
                                    "content": entry['content']
                                })
                        self.knowledge_base.extend(valid_entries)
                        print(f"[INFO] RAG: Loaded {len(valid_entries)} core entries from {filename}")
                except Exception as e:
                    print(f"[WARNING] RAG: Failed to load {filename}: {e}")

        if not self.knowledge_base:
            self._create_default_knowledge_base()

        self._core_count = len(self.knowledge_base)
        print(f"[INFO] RAG: Core knowledge base size: {self._core_count}")

    def _load_lazy_file(self, filename: str) -> int:
        """Load a single lazy file. Returns number of entries added."""
        if filename in self._lazy_files_loaded:
            return 0

        file_path = self.knowledge_base_path / filename
        if not file_path.exists():
            self._lazy_files_loaded.add(filename)
            return 0

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                valid_entries = []
                for entry in data:
                    if isinstance(entry, dict) and 'content' in entry:
                        valid_entries.append({
                            "id": entry.get('id', f"entry_{len(self.knowledge_base)+1}"),
                            "keywords": entry.get('keywords', []),
                            "title": entry.get('title', ''),
                            "content": entry['content']
                        })
                self.knowledge_base.extend(valid_entries)
                self._lazy_files_loaded.add(filename)
                print(f"[INFO] RAG: Lazy-loaded {len(valid_entries)} entries from {filename}")
                return len(valid_entries)
        except Exception as e:
            print(f"[WARNING] RAG: Failed to lazy-load {filename}: {e}")
            self._lazy_files_loaded.add(filename)
        return 0

    def _ensure_lazy_loaded(self):
        """Load all remaining lazy files."""
        if self._lazy_loaded:
            return
        self._lazy_loaded = True
        for filename in self.LAZY_FILES:
            self._load_lazy_file(filename)

    def _create_default_knowledge_base(self):
        """Create default knowledge base with psychology intervention techniques.
        All interventions are designed for mandatory drug rehabilitation center environments."""
        self.knowledge_base = [
            {
                "keywords": ["失眠", "睡不着", "入睡困难", "睡眠"],
                "title": "失眠干预技术",
                "content": "针对戒毒人员失眠问题，可采用以下干预技术：\n1. 睡眠卫生教育：保持规律作息，按照所内作息时间上床和起床\n2. 刺激控制疗法：床只用于睡眠，躺下20分钟仍睡不着就坐起来做深呼吸\n3. 放松训练：睡前在床铺上进行腹式呼吸或渐进式肌肉放松\n4. 着陆技术(Grounding)：关注当下环境，寻找房间里的5种颜色、4种触感、3种声音"
            },
            {
                "keywords": ["焦虑", "紧张", "心慌", "害怕", "恐惧"],
                "title": "焦虑情绪干预",
                "content": "针对焦虑情绪的干预技术：\n1. 深呼吸练习：4-7-8呼吸法（吸气4秒，屏息7秒，呼气8秒），可坐在床铺或椅子上进行\n2. 着陆技术：5-4-3-2-1感官练习（看5样东西，摸4样，听3种声音，闻2种气味，尝1种味道）\n3. 认知重构：识别并挑战灾难化思维，把担心的事写在纸上逐条分析\n4. 渐进式肌肉放松：坐在椅子上从头到脚依次紧张-放松各肌肉群"
            },
            {
                "keywords": ["幻觉", "幻听", "幻视", "不真实感"],
                "title": "幻觉应对策略",
                "content": "针对幻觉体验的干预策略：\n1. 现实检验：引导来访者描述当前宿舍或活动室的环境，确认真实存在的事物\n2. 着陆技术：触摸身边的墙壁、桌面、床铺，感受温度和质地\n3. 分散注意力：与同宿舍的人交谈，或做简单的手工、抄写\n4. 安全环境：保持在有人陪伴的环境中，避免独处\n5. 医疗转介：如幻觉持续或加重，立即向值班医生报告"
            },
            {
                "keywords": ["戒断", "难受", "痛苦", "想吸毒", "复吸"],
                "title": "戒断症状应对",
                "content": "针对戒断症状的应对策略：\n1. 接纳不适感：理解这是身体恢复的正常过程，戒断反应会随时间减轻\n2. 转移注意力：在活动室做俯卧撑、仰卧起坐，或进行手工制作、抄写\n3. 社会支持：与同宿舍的人交流，或找管教民警谈话\n4. 放松技巧：深呼吸、冥想、用冷水洗脸刺激感官\n5. 正念练习：观察而不评判当下的感受，关注呼吸\n6. 医疗支持：如症状严重，立即向值班医生或管教民警报告"
            },
            {
                "keywords": ["抑郁", "情绪低落", "没意思", "不想活", "绝望"],
                "title": "抑郁情绪干预",
                "content": "针对抑郁情绪的干预策略：\n1. 行为激活：鼓励参与所内日常活动，哪怕只是整理内务、打扫卫生\n2. 正念练习：关注当下，不做评判，在宿舍里做呼吸练习\n3. 社会连接：与信任的同戒人员或管教民警交流\n4. 规律作息：严格按照所内作息时间保持睡眠和饮食规律\n5. 集体活动：在规定活动时间参加集体锻炼、学习和劳动\n6. 危机干预：如有自杀念头，立即向管教民警报告，安排专业心理干预和24小时看护"
            },
            {
                "keywords": ["认知解离", "解离", "恍惚", "不真实"],
                "title": "解离症状应对",
                "content": "针对解离症状的干预策略：\n1. 着陆技术：用冷水洗手或洗脸，感受水的温度；或闻风油精、清凉油\n2. 身体活动：原地踏步、伸展运动、握紧拳头再松开\n3. 现实锚定：大声说出自己的名字、年龄、当前所在位置和今天的日期\n4. 安全空间：回到熟悉的宿舍，坐在自己的床铺上\n5. 创伤知情：理解解离可能是创伤反应，保持耐心，陪伴来访者"
            },
            {
                "keywords": ["愤怒", "生气", "发脾气", "冲动"],
                "title": "愤怒情绪管理",
                "content": "针对愤怒情绪的干预策略：\n1. 暂停技术：感到愤怒时向管教民警申请冷静时间，暂时回到宿舍\n2. 深呼吸：坐在床铺上进行3轮深呼吸，每轮吸气4秒呼气6秒\n3. 认知重构：识别触发愤怒的想法，问自己：这件事一年后还重要吗\n4. 表达训练：用\"我感到...因为...\"的方式表达情绪，而不是动手或骂人\n5. 身体释放：握紧拳头再缓慢松开反复5次，或做俯卧撑、原地高抬腿释放能量\n6. 问题解决：找管教民警或心理咨询师分析愤怒背后的需求"
            },
            {
                "keywords": ["家庭", "想家", "家人", "父母", "孩子"],
                "title": "家庭关系支持",
                "content": "针对家庭情感问题的支持策略：\n1. 情感确认：理解思乡之情是正常的，想念家人说明你重视亲情\n2. 联系支持：通过所内规定的亲情电话和家属会见日与家人保持联系\n3. 未来规划：讨论回归家庭后的计划，如何做一个更好的家人\n4. 关系修复：讨论如何用行动重建家庭信任，比如写信表达关心\n5. 所内支持：参加所内亲情帮教活动，在戒毒所内建立同伴支持网络"
            },
            {
                "keywords": ["放松", "放松训练", "呼吸", "冥想", "肌肉放松"],
                "title": "放松训练技术",
                "content": "放松训练技术汇总（均可在宿舍或活动室进行）：\n1. 深呼吸训练：坐在椅子上或床铺上进行腹式呼吸，吸气时腹部隆起\n2. 渐进式肌肉放松：坐在椅子上依次紧张-放松各肌肉群，从脚趾到头顶\n3. 引导式冥想：闭眼想象安全、平静的场景（如家乡的田野、海边）\n4. 身体扫描：躺在床上从头到脚关注身体各部位的感觉\n5. 着陆呼吸：吸气时数到4，屏息数到4，呼气数到6，重复5轮"
            },
            {
                "keywords": ["评估", "量表", "测试", "测量"],
                "title": "心理评估工具",
                "content": "常用心理评估工具：\n1. SAS（焦虑自评量表）：评估焦虑程度\n2. SDS（抑郁自评量表）：评估抑郁程度\n3. PSQI（匹兹堡睡眠质量指数）：评估睡眠质量\n4. SCL-90：综合心理健康评估\n5. 成瘾严重程度指数(ASI)：评估成瘾问题严重程度\n注意：量表评估需由所内专业心理咨询师操作，本系统仅提供科普信息"
            },
            {
                "keywords": ["自杀", "想死", "不想活", "轻生", "自残", "割腕", "活够了", "死了算了"],
                "title": "危机干预与自杀预防",
                "content": "针对自杀风险的危机干预流程：\n1. 立即行动：如来访者表达自杀念头，立即向管教民警报告，不要独自处理\n2. 安全评估：评估是否有具体计划、方法和时间，了解自杀意念的强度\n3. 移除危险：确保来访者身边没有尖锐物品、绳索等危险物品\n4. 陪伴看护：安排同戒人员24小时轮流陪伴，不使其独处\n5. 情感支持：认真倾听，不评判，表达关心：你的命很重要，我们都在\n6. 专业干预：安排所内心理咨询师进行紧急心理干预\n7. 后续跟进：建立每日谈话制度，持续关注情绪变化"
            }
        ]

        self._save_knowledge_base()

    def _save_knowledge_base(self):
        """Save knowledge base to JSON file."""
        self.knowledge_base_path.mkdir(parents=True, exist_ok=True)
        kb_file = self.knowledge_base_path / "knowledge.json"

        try:
            with open(kb_file, 'w', encoding='utf-8') as f:
                json.dump(self.knowledge_base, f, ensure_ascii=False, indent=2)
            print(f"[INFO] RAG: Saved knowledge base to {kb_file}")
        except Exception as e:
            print(f"[WARNING] RAG: Failed to save knowledge base: {e}")

    def warmup(self):
        """Warmup the RAG service (preload models if using embedding)."""
        print("[INFO] RAG Service warmed up")
        return True

    def _segment_text(self, text: str) -> List[str]:
        """Use jieba to segment Chinese text into words."""
        jb = self._get_jieba()
        if jb:
            return [w.strip() for w in jb.lcut(text) if len(w.strip()) >= 2]
        # Fallback: simple character-level matching
        return [text]

    def _expand_query(self, text: str) -> Set[str]:
        """Expand query with synonym map and jieba segmentation. Results are cached."""
        if text in self._query_cache:
            return self._query_cache[text]
        expanded = set()

        # 1. Direct synonym map matching (longest match first)
        for colloquial in sorted(self.SYNONYM_MAP.keys(), key=len, reverse=True):
            if colloquial in text:
                expanded.update(self.SYNONYM_MAP[colloquial])
                expanded.add(colloquial)

        # 2. Jieba segmentation
        segments = self._segment_text(text)
        for seg in segments:
            if seg in self.SYNONYM_MAP:
                expanded.update(self.SYNONYM_MAP[seg])
            expanded.add(seg)

        self._query_cache[text] = expanded
        return expanded

    def _intent_routing(self, text: str) -> bool:
        """
        Determine if knowledge base search is needed.
        Uses synonym map + jieba for better coverage.
        Returns True if search should be performed.
        """
        # Check synonym map keys (covers colloquial expressions)
        for colloquial in self.SYNONYM_MAP.keys():
            if colloquial in text:
                return True

        # Check jieba segments against psychology terms
        segments = self._segment_text(text)
        psychology_indicators = {
            "焦虑", "抑郁", "失眠", "幻觉", "戒断", "复吸", "渴求", "自杀",
            "自残", "恐惧", "愤怒", "冲动", "解离", "躯体化", "创伤",
            "放松", "冥想", "呼吸", "量表", "评估", "家庭", "孤独",
            "压力", "紧张", "害怕", "担心", "心慌", "头疼", "难受",
            "痛苦", "绝望", "无助", "烦躁", "生气", "愤怒",
        }
        for seg in segments:
            if seg in psychology_indicators:
                return True

        return False

    def _score_entry(self, entry: Dict, expanded_keywords: Set[str], query: str) -> float:
        """Score a single knowledge entry against the expanded query.
        Uses short-circuit: skip content scan if keywords/title have no hits."""
        score = 0.0
        keywords = entry.get("keywords", [])
        title = entry.get("title", "")
        content = entry.get("content", "")
        entry_id = entry.get("id", "")

        # Domain boost: knowledge.json entries are highest quality
        if not entry_id or entry_id.startswith("entry_"):
            domain_boost = 2.0  # knowledge.json (no id or default id)
        elif entry_id.startswith("cpsycounr"):
            domain_boost = 0.5  # case studies
        else:
            domain_boost = 0.0  # psyqa, emollm - lower priority

        has_keyword_hit = False

        # A. Keywords field matching (highest weight: 3.0 per hit)
        if isinstance(keywords, list):
            for keyword in keywords:
                if not isinstance(keyword, str):
                    continue
                kw_lower = keyword.lower()

                # Direct match with original query (strongest signal)
                if kw_lower in query or query in kw_lower:
                    score += 3.0
                    has_keyword_hit = True
                    continue

                # Match with expanded keywords
                for expanded in expanded_keywords:
                    if expanded == kw_lower:
                        score += 2.5  # Exact match
                        has_keyword_hit = True
                        break
                    # Keyword is substring of expanded (e.g., "焦虑" in "焦虑情绪")
                    # Only if keyword is at least 3 chars to avoid false positives
                    if len(kw_lower) >= 3 and kw_lower in expanded:
                        score += 2.0
                        has_keyword_hit = True
                        break

        # B. Title matching (medium weight: 1.5 per hit)
        has_title_hit = False
        if title:
            title_lower = title.lower()
            for expanded in expanded_keywords:
                if expanded in title_lower:
                    score += 1.5
                    has_title_hit = True

        # C. Content matching (lower weight: 0.2 per hit, max 1.0)
        # Short-circuit: skip expensive content scan if no keyword/title hits
        # unless domain_boost is high enough to matter on its own
        if content and (has_keyword_hit or has_title_hit or domain_boost >= 2.0):
            content_lower = content.lower()
            content_hits = 0
            for expanded in expanded_keywords:
                # Require at least 2 chars for content matching to reduce false positives
                if len(expanded) >= 2 and expanded in content_lower:
                    content_hits += 1
            score += min(content_hits * 0.2, 1.0)

        # Apply domain boost
        score += domain_boost

        return score

    def _search_entries(self, entries: List[Dict], expanded_keywords: Set[str],
                        query_lower: str, min_score: float = 0.0) -> List[Dict]:
        """Search a list of entries and return scored results above min_score."""
        results = []
        for entry in entries:
            score = self._score_entry(entry, expanded_keywords, query_lower)
            if score > min_score:
                results.append({
                    "title": entry.get("title", ""),
                    "content": entry.get("content", ""),
                    "score": score,
                    "id": entry.get("id", "")
                })
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    def _simple_search(self, query: str, top_k: int = 3) -> List[Dict[str, str]]:
        """Keyword-based search with synonym expansion and content matching.
        Returns core knowledge first, then supplementary from large datasets."""
        query_lower = query.lower()

        # Check search cache
        cache_key = f"{query_lower}:{top_k}"
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        # Expand query with synonyms and jieba
        expanded_keywords = self._expand_query(query_lower)

        if not expanded_keywords:
            expanded_keywords = {query_lower}

        # Phase 1: Search ONLY core knowledge (knowledge.json entries)
        core_results = self._search_entries(
            self.knowledge_base[:self._core_count], expanded_keywords, query_lower
        )

        # If core has strong matches (score >= 5.0), use those
        if core_results and core_results[0]["score"] >= 5.0:
            result = core_results[:top_k]
            self._search_cache[cache_key] = result
            return result

        # Phase 2: Search lazy-loaded files incrementally
        extended_results = []
        for filename in self.LAZY_FILES:
            self._load_lazy_file(filename)

        # Search all currently loaded lazy entries
        seen_ids = set()
        for entry in self.knowledge_base[self._core_count:]:
            entry_id = entry.get("id", "")
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            score = self._score_entry(entry, expanded_keywords, query_lower)
            if score > 2.0:
                extended_results.append({
                    "title": entry.get("title", ""),
                    "content": entry.get("content", ""),
                    "score": score,
                    "id": entry_id
                })
        extended_results.sort(key=lambda x: x["score"], reverse=True)

        # If we found good results in lazy files, no need to load more
        if not extended_results:
            # Load remaining files and try again
            self._ensure_lazy_loaded()
            for entry in self.knowledge_base[self._core_count:]:
                entry_id = entry.get("id", "")
                if entry_id in seen_ids:
                    continue
                seen_ids.add(entry_id)
                score = self._score_entry(entry, expanded_keywords, query_lower)
                if score > 2.0:
                    extended_results.append({
                        "title": entry.get("title", ""),
                        "content": entry.get("content", ""),
                        "score": score,
                        "id": entry_id
                    })
            extended_results.sort(key=lambda x: x["score"], reverse=True)

        # Merge: 1 core + 2 extended, or all core if no extended
        merged = []
        if core_results:
            merged.append(core_results[0])
        merged.extend(extended_results[:top_k - len(merged)])

        result = merged[:top_k]
        self._search_cache[cache_key] = result
        return result

    def get_context(self, user_text: str) -> Optional[str]:
        """
        Core method: get reference knowledge for the user query.

        Args:
            user_text: User's input text

        Returns:
            Context string to inject into LLM prompt, or None if not needed
        """
        if not self._intent_routing(user_text):
            return None

        print(f"[INFO] RAG triggered for: {user_text[:50]}...")

        results = self._simple_search(user_text, top_k=3)

        if not results:
            return None

        context_parts = []
        for i, result in enumerate(results, 1):
            context_parts.append(f"【{result['title']}】\n{result['content']}")

        context = "\n\n".join(context_parts)
        return context

    def get_system_suffix(self, user_text: str) -> Optional[str]:
        """
        Get formatted system suffix with RAG context.

        This is the main method to call from the conversation pipeline.

        Args:
            user_text: User's input text

        Returns:
            Formatted system suffix string, or None if not needed
        """
        rag_context = self.get_context(user_text)

        if not rag_context:
            return None

        rag_instruction = f"""

【后台专家知识库提示】
刚刚检索到以下与来访者问题相关的临床心理学知识：
{rag_context}

【强制约束】
请将上述知识作为你【策略选择】的理论依据，但在生成口语回复时，必须把这些专业术语转化为戒毒所环境内通俗、有温度的老朋友口吻，**严禁直接照本宣科或像背书一样读出来**。
"""
        return rag_instruction


# Singleton instance
_rag_service = None

def get_rag_service() -> RAGService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service
