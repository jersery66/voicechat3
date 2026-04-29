# RAG Service - Knowledge Retrieval for Psychology Counseling

import os
import sys
import json
import re
from typing import Optional, List, Dict, Any
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class RAGService:
    """
    RAG (Retrieval-Augmented Generation) Service for psychology counseling.
    
    Features:
    - Intent routing: Only search knowledge base when needed
    - Knowledge retrieval from local documents
    - Context injection into LLM prompts
    - Synonym expansion for better recall
    """
    
    # Synonym expansion table: colloquial -> professional terms
    SYNONYM_MAP = {
        # Sleep related
        "睡不着": ["失眠", "睡眠障碍", "入睡困难"],
        "失眠": ["睡眠障碍", "入睡困难"],
        "早醒": ["睡眠障碍", "失眠"],
        "多梦": ["睡眠障碍", "噩梦"],
        "噩梦": ["睡眠障碍", "创伤"],
        "睡不好": ["失眠", "睡眠障碍"],
        
        # Anxiety related
        "心里堵": ["焦虑", "情绪困扰", "心理压力"],
        "烦得很": ["焦虑", "烦躁", "情绪困扰"],
        "脑子乱": ["焦虑", "思维混乱", "注意力不集中"],
        "心慌": ["焦虑", "恐慌", "心悸"],
        "紧张": ["焦虑", "压力"],
        "害怕": ["恐惧", "焦虑", "恐慌"],
        "担心": ["焦虑", "担忧"],
        "坐立不安": ["焦虑", "烦躁"],
        "喘不过气": ["焦虑", "恐慌", "呼吸困难"],
        
        # Depression related
        "没意思": ["抑郁", "兴趣减退", "情绪低落"],
        "不想活": ["抑郁", "自杀倾向", "绝望"],
        "活着没劲": ["抑郁", "绝望", "无意义感"],
        "开心不起来": ["抑郁", "情绪低落"],
        "情绪低落": ["抑郁", "情绪障碍"],
        "绝望": ["抑郁", "无望感"],
        "想哭": ["抑郁", "情绪低落", "悲伤"],
        
        # Anger related
        "想发火": ["愤怒", "情绪管理", "冲动"],
        "脾气大": ["愤怒", "情绪管理"],
        "容易生气": ["愤怒", "情绪管理"],
        "控制不住": ["冲动", "情绪管理"],
        
        # Family related
        "想家": ["思乡", "家庭关系", "情感支持"],
        "想家人": ["思乡", "家庭关系"],
        "家里": ["家庭", "家庭关系"],
        "父母": ["家庭", "家庭关系"],
        "老婆": ["家庭", "夫妻关系"],
        "孩子": ["家庭", "亲子关系"],
        
        # Addiction related
        "想吸毒": ["复吸", "渴求", "戒断"],
        "难受": ["戒断", "身体不适", "痛苦"],
        "痛苦": ["戒断", "心理痛苦"],
        "忍不住": ["渴求", "冲动控制"],
        
        # Cognitive related
        "恍惚": ["解离", "认知障碍"],
        "不真实": ["解离", "现实检验"],
        "幻觉": ["幻觉", "精神病性症状"],
        "幻听": ["幻觉", "精神病性症状"],
        
        # Relationship related
        "没人理解": ["孤独", "社会支持", "人际关系"],
        "孤独": ["孤独感", "社会支持"],
        "不想说话": ["社交退缩", "抑郁", "人际关系"],
        
        # Physical symptoms
        "头疼": ["躯体化", "心理压力"],
        "胃不舒服": ["躯体化", "焦虑"],
        "浑身无力": ["抑郁", "躯体化", "疲劳"],
        
        # Work/Study related
        "压力大": ["压力", "焦虑", "工作压力"],
        "学不进去": ["注意力", "学习困难", "焦虑"],
        "工作不顺": ["工作压力", "职业困扰"],
    }
    
    def __init__(self, knowledge_base_path: str = None):
        self.knowledge_base_path = Path(knowledge_base_path) if knowledge_base_path else self._get_default_kb_path()
        self.knowledge_base: List[Dict[str, str]] = []
        self.embedding_model = None
        self._load_knowledge_base()
        
    def _get_default_kb_path(self) -> Path:
        app_dir = Path(__file__).parent.parent
        return app_dir / "knowledge_base"
    
    def _load_knowledge_base(self):
        """Load knowledge base from JSON files (standard format only)."""
        
        # Standard format files to load (in order)
        standard_files = [
            "knowledge.json",
            "cpsycounr_converted.json",
            "psyqa_converted.json",
            "emollm_single_turn_1.json",
            "emollm_single_turn_2.json",
            "emollm_multi_turn.json"
        ]
        
        for filename in standard_files:
            file_path = self.knowledge_base_path / filename
            if file_path.exists():
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    
                    if isinstance(data, list):
                        # Validate standard format
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
                        print(f"[INFO] RAG: Loaded {len(valid_entries)} entries from {filename}")
                    
                except Exception as e:
                    print(f"[WARNING] RAG: Failed to load {filename}: {e}")
        
        if not self.knowledge_base:
            self._create_default_knowledge_base()
        
        print(f"[INFO] RAG: Total knowledge base size: {len(self.knowledge_base)}")
            
    def _create_default_knowledge_base(self):
        """Create default knowledge base with psychology intervention techniques."""
        self.knowledge_base = [
            {
                "keywords": ["失眠", "睡不着", "入睡困难", "睡眠"],
                "title": "失眠干预技术",
                "content": """针对戒毒人员失眠问题，可采用以下干预技术：
1. 睡眠卫生教育：保持规律作息，避免睡前使用电子设备
2. 刺激控制疗法：床只用于睡眠，睡不着就起床
3. 放松训练：睡前进行深呼吸或渐进式肌肉放松
4. 着陆技术(Grounding)：关注当下环境，寻找房间里的5种颜色"""
            },
            {
                "keywords": ["焦虑", "紧张", "心慌", "害怕", "恐惧"],
                "title": "焦虑情绪干预",
                "content": """针对焦虑情绪的干预技术：
1. 深呼吸练习：4-7-8呼吸法（吸气4秒，屏息7秒，呼气8秒）
2. 着陆技术：5-4-3-2-1感官练习（看5样东西，摸4样，听3种声音，闻2种气味，尝1种味道）
3. 认知重构：识别并挑战灾难化思维
4. 渐进式肌肉放松：从头到脚依次紧张-放松各肌肉群"""
            },
            {
                "keywords": ["幻觉", "幻听", "幻视", "不真实感"],
                "title": "幻觉应对策略",
                "content": """针对幻觉体验的干预策略：
1. 现实检验：引导来访者描述当前环境，确认真实存在的事物
2. 着陆技术：触摸真实物体，感受温度、质地
3. 分散注意力：进行简单任务或对话
4. 安全环境：确保来访者处于安全、安静的环境
5. 医疗转介：如幻觉持续或加重，建议就医评估"""
            },
            {
                "keywords": ["戒断", "难受", "痛苦", "想吸毒", "复吸"],
                "title": "戒断症状应对",
                "content": """针对戒断症状的应对策略：
1. 接纳不适感：理解这是身体恢复的正常过程
2. 转移注意力：进行体育活动、手工制作等
3. 社会支持：与家人、朋友或工作人员交流
4. 放松技巧：深呼吸、冥想、温水浴
5. 正念练习：观察而不评判当下的感受
6. 医疗支持：如症状严重，及时寻求医疗帮助"""
            },
            {
                "keywords": ["抑郁", "情绪低落", "没意思", "不想活", "绝望"],
                "title": "抑郁情绪干预",
                "content": """针对抑郁情绪的干预策略：
1. 行为激活：鼓励参与日常活动，哪怕是很小的事情
2. 正念练习：关注当下，不做评判
3. 社会连接：与信任的人交流
4. 规律作息：保持睡眠和饮食规律
5. 适度运动：每天进行轻度运动
6. 危机干预：如有自杀念头，立即进行安全评估并提供热线支持"""
            },
            {
                "keywords": ["认知解离", "解离", "恍惚", "不真实"],
                "title": "解离症状应对",
                "content": """针对解离症状的干预策略：
1. 着陆技术：强烈的感官刺激（如握冰块、闻强烈气味）
2. 身体活动：原地踏步、伸展运动
3. 现实锚定：说出自己的名字、年龄、当前位置
4. 安全空间：引导来访者回到安全、熟悉的环境
5. 创伤知情：理解解离可能是创伤反应，保持耐心"""
            },
            {
                "keywords": ["愤怒", "生气", "发脾气", "冲动"],
                "title": "愤怒情绪管理",
                "content": """针对愤怒情绪的干预策略：
1. 暂停技术：感到愤怒时先离开现场
2. 深呼吸：进行3轮深呼吸
3. 认知重构：识别触发愤怒的想法
4. 表达训练：用"我感到..."的方式表达情绪
5. 身体释放：进行体育运动或击打枕头
6. 问题解决：分析愤怒背后的需求"""
            },
            {
                "keywords": ["家庭", "想家", "家人", "父母", "孩子"],
                "title": "家庭关系支持",
                "content": """针对家庭情感问题的支持策略：
1. 情感确认：理解思乡之情是正常的
2. 联系支持：鼓励与家人保持联系（电话、信件）
3. 未来规划：讨论回归家庭后的计划
4. 关系修复：讨论如何重建家庭信任
5. 替代支持：在戒毒所内建立支持网络"""
            },
            {
                "keywords": ["放松", "放松训练", "呼吸", "冥想", "肌肉放松"],
                "title": "放松训练技术",
                "content": """放松训练技术汇总：
1. 深呼吸训练：腹式呼吸，吸气时腹部隆起
2. 渐进式肌肉放松：依次紧张-放松各肌肉群
3. 引导式冥想：跟随音频进行正念冥想
4. 想象放松：想象安全、平静的场景
5. 身体扫描：从头到脚关注身体各部位的感觉"""
            },
            {
                "keywords": ["评估", "量表", "测试", "测量"],
                "title": "心理评估工具",
                "content": """常用心理评估工具：
1. SAS（焦虑自评量表）：评估焦虑程度
2. SDS（抑郁自评量表）：评估抑郁程度
3. PSQI（匹兹堡睡眠质量指数）：评估睡眠质量
4. SCL-90：综合心理健康评估
5. 成瘾严重程度指数(ASI)：评估成瘾问题严重程度
注意：量表评估需由专业人员操作，本系统仅提供科普信息"""
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
    
    def _intent_routing(self, text: str) -> str:
        """
        Simple intent routing: determine if knowledge base search is needed.
        
        Returns:
            "search_knowledge" or "chit_chat"
        """
        trigger_keywords = [
            "幻觉", "戒断", "睡不着", "失眠", "焦虑", "紧张", 
            "心慌", "害怕", "恐惧", "抑郁", "情绪低落", "想死",
            "认知解离", "解离", "脱敏", "怎么评估", "量表",
            "愤怒", "生气", "冲动", "想家", "家人", "复吸",
            "难受", "痛苦", "放松训练", "冥想", "呼吸",
            "心里堵", "烦得很", "脑子乱", "没意思", "不想活",
            "想发火", "脾气大", "头疼", "胃不舒服", "压力大",
            "孤独", "没人理解", "恍惚", "不真实", "想吸毒"
        ]
        
        # Also check colloquial expressions from synonym map
        for colloquial in self.SYNONYM_MAP.keys():
            if colloquial in text:
                return "search_knowledge"
        
        if any(kw in text for kw in trigger_keywords):
            return "search_knowledge"
        
        return "chit_chat"
    
    def _simple_search(self, query: str, top_k: int = 2) -> List[Dict[str, str]]:
        """
        Simple keyword-based search with synonym expansion.
        
        For production, replace with:
        - ChromaDB / Milvus for vector search
        - BGE-M3 / text-embedding-ada-002 for embeddings
        """
        results = []
        query_lower = query.lower()
        
        # 1. Expand query with synonyms
        expanded_keywords = set()
        for colloquial, professional_list in self.SYNONYM_MAP.items():
            if colloquial in query_lower:
                expanded_keywords.update(professional_list)
                expanded_keywords.add(colloquial)  # Also add original word
        
        # For Chinese, if no synonym hit, at least keep original query
        if not expanded_keywords:
            expanded_keywords.add(query_lower)
        
        # 2. Score each knowledge entry
        for entry in self.knowledge_base:
            score = 0
            keywords = entry.get("keywords", [])
            content_lower = entry.get("content", "").lower()
            
            # A. Match Keywords field (highest weight)
            if isinstance(keywords, list):
                for keyword in keywords:
                    kw_lower = keyword.lower() if isinstance(keyword, str) else str(keyword).lower()
                    
                    # User's original words directly hit knowledge keywords
                    if kw_lower in query_lower or query_lower in kw_lower:
                        score += 2
                    
                    # Expanded synonyms hit knowledge keywords
                    for expanded in expanded_keywords:
                        if expanded in kw_lower or kw_lower in expanded:
                            score += 1
                            break
            
            # B. Match Content field (lower weight)
            # Check if expanded query words appear in content
            for expanded in expanded_keywords:
                if expanded in content_lower:
                    score += 0.5
            
            if score > 0:
                results.append({
                    "title": entry.get("title", ""),
                    "content": entry.get("content", ""),
                    "score": score
                })
        
        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]
    
    def get_context(self, user_text: str) -> Optional[str]:
        """
        Core method: get reference knowledge for the user query.
        
        Args:
            user_text: User's input text
            
        Returns:
            Context string to inject into LLM prompt, or None if not needed
        """
        intent = self._intent_routing(user_text)
        
        if intent == "chit_chat":
            return None
            
        print(f"[INFO] RAG triggered for: {user_text[:50]}...")
        
        results = self._simple_search(user_text, top_k=2)
        
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
