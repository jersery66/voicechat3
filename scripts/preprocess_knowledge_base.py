"""
知识库预处理脚本 - 修复各数据集的keywords字段
用法: python scripts/preprocess_knowledge_base.py
"""

import json
import re
import os
import sys
from pathlib import Path
from collections import Counter

# 添加jieba的心理学自定义词典
PSYCHOLOGY_TERMS = [
    # 戒毒相关
    "戒断", "复吸", "渴求", "毒品", "吸毒", "冰毒", "海洛因", "摇头丸", "大麻",
    "强制隔离", "戒毒所", "戒断症状", "躯体戒断", "心理戒断", "戒断反应",
    # 心理症状
    "焦虑", "抑郁", "失眠", "幻觉", "幻听", "幻视", "妄想", "强迫", "恐惧",
    "创伤后应激", "解离", "躯体化", "失眠症", "睡眠障碍", "入睡困难", "早醒",
    "情绪低落", "情绪失控", "情绪管理", "愤怒管理", "冲动控制",
    # 心理咨询技术
    "认知行为疗法", "精神分析", "人本主义", "动机访谈", "正念", "冥想",
    "放松训练", "渐进式肌肉放松", "深呼吸", "系统脱敏", "暴露疗法",
    "叙事疗法", "焦点解决", "艺术治疗", "音乐治疗", "沙盘治疗",
    "危机干预", "自杀预防", "自残", "安全评估",
    # 人际关系
    "家庭关系", "亲子关系", "夫妻关系", "社会支持", "人际交往",
    "孤独感", "社交退缩", "信任", "沟通",
    # 戒毒人员常见表达
    "心里堵", "烦得很", "脑子乱", "坐立不安", "浑身无力",
    "想发火", "脾气大", "控制不住", "忍不住", "活着没劲",
    "睡不着", "做噩梦", "心慌", "喘不过气",
]

# 停用词 - 过滤掉无意义的高频词
STOPWORDS = set([
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "吗", "吧", "呢",
    "啊", "呀", "嗯", "哦", "哈", "哈哈", "呵呵", "嘿嘿",
    "什么", "怎么", "为什么", "哪里", "哪个", "多少", "几",
    "可以", "应该", "需要", "想要", "能够", "可能", "必须",
    "但是", "可是", "然而", "不过", "而且", "并且", "或者",
    "因为", "所以", "如果", "虽然", "即使", "无论", "不管",
    "这个", "那个", "这些", "那些", "这里", "那里",
    "现在", "以前", "以后", "最近", "已经", "正在", "将要",
    "医生", "老师", "同学", "朋友", "家人", "父母", "孩子",
])


def init_jieba():
    """初始化jieba并添加心理学词典"""
    import jieba
    for term in PSYCHOLOGY_TERMS:
        jieba.add_word(term)
    return jieba


PSYCHOLOGY_SET = set(PSYCHOLOGY_TERMS)


def extract_keywords_from_text(jieba_mod, text: str, max_keywords: int = 8) -> list:
    """从文本中提取关键词，优先保留心理学术语"""
    if not text:
        return []

    # 分词
    words = jieba_mod.lcut(text)

    # 过滤：去停用词、去短词、去纯标点
    psychology_hits = []
    other_words = []
    for w in words:
        w = w.strip()
        if len(w) < 2:
            continue
        if w in STOPWORDS:
            continue
        if re.match(r'^[\s\W]+$', w):
            continue
        # 心理学术语优先
        if w in PSYCHOLOGY_SET:
            psychology_hits.append(w)
        elif len(w) >= 3:  # 非术语至少3个字
            other_words.append(w)

    # 心理学术语优先，然后补充其他词
    counter = Counter(psychology_hits)
    psych_top = [w for w, _ in counter.most_common(max_keywords)]

    remaining = max_keywords - len(psych_top)
    if remaining > 0:
        other_counter = Counter(other_words)
        psych_top.extend([w for w, _ in other_counter.most_common(remaining)])

    return psych_top


def preprocess_cpsycounr(data: list, jieba_mod) -> list:
    """修复cpsycounr的keywords：拆分逗号连接的技术名 + 从content提取"""
    fixed = []
    for entry in data:
        keywords = entry.get("keywords", [])
        content = entry.get("content", "")
        new_keywords = []

        for kw in keywords:
            if not isinstance(kw, str):
                continue
            # 拆分逗号/顿号连接的多个技术名
            parts = re.split(r'[,，、;；]', kw)
            for part in parts:
                part = part.strip()
                if len(part) >= 2 and len(part) <= 15:
                    new_keywords.append(part)

        # 从content的"案例类别"和"运用技术"中补充
        category_match = re.search(r'案例类别[：:]\s*(.+?)[\n\\]', content)
        tech_match = re.search(r'运用技术[：:]\s*(.+?)[\n\\]', content)

        if category_match:
            cat = category_match.group(1).strip()
            if cat and len(cat) <= 10:
                new_keywords.append(cat)
        if tech_match:
            tech = tech_match.group(1).strip()
            parts = re.split(r'[,，、;；]', tech)
            for part in parts:
                part = part.strip()
                if 2 <= len(part) <= 15:
                    new_keywords.append(part)

        # 从"案例简述"中提取关键词补充
        summary_match = re.search(r'案例简述[：:]\s*(.+?)[\n\\]', content, re.DOTALL)
        if summary_match:
            summary_text = summary_match.group(1)[:200]  # 只取前200字
            extracted = extract_keywords_from_text(jieba_mod, summary_text, max_keywords=5)
            new_keywords.extend(extracted)

        # 去重保序
        seen = set()
        unique_keywords = []
        for kw in new_keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        entry["keywords"] = unique_keywords[:10]
        fixed.append(entry)

    return fixed


# 问题片段过滤词
QUESTION_NOISE = set(['怎么办', '为什么', '是什么', '能不能', '可不可以', '会不会',
                       '有没有', '是不是', '对不对', '好不好', '怎么样', '如何',
                       '给我', '建议', '帮忙', '帮助', '告诉', '觉得', '感觉'])


def preprocess_psyqa(data: list, jieba_mod) -> list:
    """修复psyqa的keywords：解析逗号分隔标签 + 从title提取"""
    fixed = []
    for entry in data:
        keywords = entry.get("keywords", [])
        title = entry.get("title", "")
        new_keywords = []

        for kw in keywords:
            if not isinstance(kw, str):
                continue
            # 如果包含逗号，说明是逗号分隔的标签串
            if ',' in kw or '，' in kw:
                parts = re.split(r'[,，]', kw)
                for part in parts:
                    part = part.strip()
                    if 2 <= len(part) <= 6 and not any(q in part for q in QUESTION_NOISE):
                        new_keywords.append(part)
            elif 2 <= len(kw) <= 6 and not any(q in kw for q in QUESTION_NOISE):
                # 短字符串直接作为关键词（过滤问题片段）
                new_keywords.append(kw)
            # 长字符串(问题标题)不加入keywords，但用于jieba提取

        # 从title中提取关键词
        if title:
            extracted = extract_keywords_from_text(jieba_mod, title, max_keywords=5)
            new_keywords.extend(extracted)

        # 去重保序
        seen = set()
        unique_keywords = []
        for kw in new_keywords:
            if kw not in seen:
                seen.add(kw)
                unique_keywords.append(kw)

        entry["keywords"] = unique_keywords[:10]
        fixed.append(entry)

    return fixed


def _backup_file(path: Path) -> Path:
    """Create a timestamped backup of ``path`` before mutating it in place.

    Returns the backup path (caller passes it to ``_restore_backup`` on error).
    The backup is skipped silently if the source can't be read.
    """
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}{path.suffix}.bak_{ts}")
    try:
        backup.write_bytes(path.read_bytes())
    except Exception as e:  # pragma: no cover - defensive
        print(f"  [WARN] 备份失败（继续，风险自负）: {e}")
    return backup


def _restore_backup(path: Path, backup: Path) -> None:
    """Restore ``path`` from ``backup`` if the backup exists."""
    try:
        if backup.exists():
            path.write_bytes(backup.read_bytes())
            print(f"  [OK] 已从备份还原: {backup}")
    except Exception as e:  # pragma: no cover - defensive
        print(f"  [ERROR] 还原备份失败: {e}")


def preprocess_emollm(data: list, jieba_mod) -> list:
    """修复emollm的keywords：从问题原文中提取关键词"""
    fixed = []
    for entry in data:
        keywords = entry.get("keywords", [])
        content = entry.get("content", "")

        # 合并所有keywords文本和content作为提取源
        source_text = " ".join([kw for kw in keywords if isinstance(kw, str)])

        # 从content中提取"问题:"后面的内容
        question_match = re.search(r'问题[：:]\s*(.+?)(?:\n|描述)', content, re.DOTALL)
        if question_match:
            source_text += " " + question_match.group(1)

        # 用jieba提取关键词
        extracted = extract_keywords_from_text(jieba_mod, source_text, max_keywords=8)

        entry["keywords"] = extracted
        fixed.append(entry)

    return fixed


def main():
    kb_dir = Path(__file__).parent.parent / "knowledge_base"

    print("=" * 60)
    print("知识库预处理 - 修复keywords字段")
    print("=" * 60)

    # 初始化jieba
    print("\n[1/5] 初始化jieba分词器...")
    jieba_mod = init_jieba()

    # 处理cpsycounr
    cpsy_path = kb_dir / "cpsycounr_converted.json"
    if cpsy_path.exists():
        print("\n[2/7] 处理 cpsycounr_converted.json ...")
        backup = _backup_file(cpsy_path)
        try:
            with open(cpsy_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            fixed = preprocess_cpsycounr(data, jieba_mod)
            with open(cpsy_path, 'w', encoding='utf-8') as f:
                json.dump(fixed, f, ensure_ascii=False, indent=2)
            print(f"  修复 {len(fixed)} 条")
            # 展示示例（空文件安全）
            if fixed:
                print(f"  示例keywords: {fixed[0]['keywords']}")
            else:
                print("  [WARN] 结果为空，未写入示例")
        except Exception as e:
            print(f"  [ERROR] 处理失败，已从备份恢复: {backup}")
            _restore_backup(cpsy_path, backup)
            raise

    # 处理psyqa
    psyqa_path = kb_dir / "psyqa_converted.json"
    if psyqa_path.exists():
        print("\n[3/7] 处理 psyqa_converted.json ...")
        backup = _backup_file(psyqa_path)
        try:
            with open(psyqa_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            fixed = preprocess_psyqa(data, jieba_mod)
            with open(psyqa_path, 'w', encoding='utf-8') as f:
                json.dump(fixed, f, ensure_ascii=False, indent=2)
            print(f"  修复 {len(fixed)} 条")
            if fixed:
                print(f"  示例keywords: {fixed[0]['keywords']}")
            else:
                print("  [WARN] 结果为空，未写入示例")
        except Exception as e:
            print(f"  [ERROR] 处理失败，已从备份恢复: {backup}")
            _restore_backup(psyqa_path, backup)
            raise

    # 处理emollm_single_turn_1
    emollm_files = [
        "emollm_single_turn_1.json",
        "emollm_single_turn_2.json",
        "emollm_multi_turn.json"
    ]
    for i, filename in enumerate(emollm_files):
        filepath = kb_dir / filename
        if filepath.exists():
            print(f"\n[{4+i}/7] 处理 {filename} ...")
            backup = _backup_file(filepath)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                fixed = preprocess_emollm(data, jieba_mod)
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(fixed, f, ensure_ascii=False, indent=2)
                print(f"  修复 {len(fixed)} 条")
                if fixed:
                    print(f"  示例keywords: {fixed[0]['keywords']}")
                else:
                    print("  [WARN] 结果为空，未写入示例")
            except Exception as e:
                print(f"  [ERROR] 处理失败，已从备份恢复: {backup}")
                _restore_backup(filepath, backup)
                raise

    print("\n" + "=" * 60)
    print("预处理完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
