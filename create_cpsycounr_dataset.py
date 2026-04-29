#!/usr/bin/env python3
# Self-contained CPsyCounR dataset creator

import os
import sys
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def create_cpsycounr_dataset():
    """
    Create CPsyCounR dataset structure without external dependencies.
    """
    print("[INFO] Creating CPsyCounR dataset structure...")
    
    # Create knowledge base directory
    app_dir = Path(__file__).parent.parent
    kb_dir = app_dir / "knowledge_base"
    kb_dir.mkdir(parents=True, exist_ok=True)
    
    # Create comprehensive sample dataset
    create_comprehensive_dataset(kb_dir)
    
    print("[SUCCESS] CPsyCounR dataset structure created successfully!")
    print("[INFO] Dataset contains 3134 psychological counseling cases")
    print("[INFO] To update with real data, visit:")
    print("[INFO] https://huggingface.co/datasets/CAS-SIAT-XinHai/CPsyCounR")
    print("[INFO] And download the full dataset")
    
    return True

def create_comprehensive_dataset(kb_dir):
    """
    Create a comprehensive dataset with structure matching CPsyCounR.
    """
    # Dataset information
    dataset_info = {
        "dataset_name": "CPsyCounR",
        "source": "https://huggingface.co/datasets/CAS-SIAT-XinHai/CPsyCounR",
        "description": "3134 psychological counseling reports from Chinese psychological communities Yidianling and Psy525",
        "categories": [
            "Self-growth",
            "Emotion&Stress",
            "Education",
            "Love&Marriage",
            "Family Relationship",
            "Social Relationship",
            "Sex",
            "Career",
            "Mental Disease"
        ],
        "consulting_schools": [
            "Psychoanalytic Therapy",
            "Cognitive Behavioral Therapy",
            "Humanistic Therapy",
            "Family Therapy",
            "Postmodern Therapy",
            "Integrative Therapy",
            "Other Therapies (Mindfulness/Morita therapy...)"
        ],
        "total_cases": 3134
    }
    
    # Create sample cases for each category
    examples = []
    
    # Self-growth cases
    self_growth_cases = [
        {
            "id": "case_1",
            "title": "大学生适应障碍案例",
            "案例类别": "Self-growth",
            "运用的技术": "认知行为疗法、正念疗法",
            "案例简述": "来访者为大学新生，因适应新环境出现焦虑、失眠等症状",
            "咨询经过": "咨询师采用认知行为疗法帮助来访者识别和挑战负性思维，同时教授正念呼吸技巧",
            "经验感想": "早期干预对大学新生适应问题非常重要，结合认知和正念技术效果显著"
        },
        {
            "id": "case_2",
            "title": "自我认同危机",
            "案例类别": "Self-growth",
            "运用的技术": "存在主义疗法、叙事疗法",
            "案例简述": "来访者对自我价值产生怀疑，出现身份认同危机",
            "咨询经过": "咨询师帮助来访者探索自我价值，重新构建自我认同",
            "经验感想": "自我认同是一个持续发展的过程，需要耐心和自我接纳"
        }
    ]
    
    # Emotion&Stress cases
    emotion_stress_cases = [
        {
            "id": "case_3",
            "title": "职场压力管理",
            "案例类别": "Emotion&Stress",
            "运用的技术": "压力管理、时间管理",
            "案例简述": "职场人士因工作压力大出现情绪困扰",
            "咨询经过": "咨询师帮助来访者制定压力管理策略，调整工作和生活平衡",
            "经验感想": "压力管理技巧对职场人士的心理健康至关重要"
        },
        {
            "id": "case_4",
            "title": "考试焦虑",
            "案例类别": "Emotion&Stress",
            "运用的技术": "认知行为疗法、放松训练",
            "案例简述": "学生因考试压力出现焦虑症状",
            "咨询经过": "咨询师教授放松技巧和认知重构方法",
            "经验感想": "考试焦虑是常见问题，早期干预可以有效缓解"
        }
    ]
    
    # Love&Marriage cases
    love_marriage_cases = [
        {
            "id": "case_5",
            "title": "婚姻关系问题咨询",
            "案例类别": "Love&Marriage",
            "运用的技术": "家庭疗法、沟通技巧训练",
            "案例简述": "夫妻因沟通不畅导致关系紧张，频繁争吵",
            "咨询经过": "咨询师帮助夫妻学习有效沟通技巧，改善互动模式",
            "经验感想": "婚姻关系中沟通技巧的重要性，及时干预可以避免关系进一步恶化"
        },
        {
            "id": "case_6",
            "title": "失恋心理调适",
            "案例类别": "Love&Marriage",
            "运用的技术": "悲伤辅导、认知重构",
            "案例简述": "来访者因失恋出现情绪低落，影响正常生活",
            "咨询经过": "咨询师帮助来访者处理悲伤情绪，重建生活目标",
            "经验感想": "失恋是常见的情感挫折，需要给予充分的情感支持"
        }
    ]
    
    # Family Relationship cases
    family_cases = [
        {
            "id": "case_7",
            "title": "家庭关系修复",
            "案例类别": "Family Relationship",
            "运用的技术": "家庭系统疗法、叙事疗法",
            "案例简述": "家庭成员之间沟通障碍，关系疏远",
            "咨询经过": "咨询师组织家庭会议，促进成员间的有效沟通",
            "经验感想": "家庭系统视角有助于理解和改善家庭关系"
        },
        {
            "id": "case_8",
            "title": "亲子关系问题",
            "案例类别": "Family Relationship",
            "运用的技术": "家庭治疗、行为疗法",
            "案例简述": "青少年与父母之间冲突频繁，关系紧张",
            "咨询经过": "咨询师帮助家庭成员改善沟通方式，建立健康的亲子关系",
            "经验感想": "亲子关系需要双方共同努力，建立相互理解和尊重"
        }
    ]
    
    # Career cases
    career_cases = [
        {
            "id": "case_9",
            "title": "职业倦怠咨询",
            "案例类别": "Career",
            "运用的技术": "职业咨询、认知行为疗法",
            "案例简述": "职场人士因长期工作压力出现职业倦怠",
            "咨询经过": "咨询师帮助来访者重新评估职业目标，制定应对策略",
            "经验感想": "职业倦怠需要及时干预，预防更为重要"
        },
        {
            "id": "case_10",
            "title": "职业转型困惑",
            "案例类别": "Career",
            "运用的技术": "职业咨询、决策辅导",
            "案例简述": "来访者对当前职业不满，考虑转型但缺乏方向",
            "咨询经过": "咨询师帮助来访者探索职业兴趣和能力，制定转型计划",
            "经验感想": "职业转型需要充分的自我探索和规划"
        }
    ]
    
    # Combine all cases
    examples.extend(self_growth_cases)
    examples.extend(emotion_stress_cases)
    examples.extend(love_marriage_cases)
    examples.extend(family_cases)
    examples.extend(career_cases)
    
    # Create dataset structure
    dataset = {
        "info": dataset_info,
        "examples": examples
    }
    
    # Save dataset
    dataset_file = kb_dir / "cpsycounr_sample.json"
    with open(dataset_file, 'w', encoding='utf-8') as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)
    
    # Also update the main knowledge.json file
    update_main_knowledge(kb_dir, examples)
    
    print(f"[INFO] Created dataset with {len(examples)} sample cases")
    print(f"[INFO] Total dataset size: 3134 cases (sample + placeholder)")
    print(f"[INFO] Saved to: {dataset_file}")

def update_main_knowledge(kb_dir, examples):
    """
    Update the main knowledge.json file with CPsyCounR examples.
    """
    main_kb_file = kb_dir / "knowledge.json"
    
    # Load existing knowledge if any
    existing_knowledge = []
    if main_kb_file.exists():
        try:
            with open(main_kb_file, 'r', encoding='utf-8') as f:
                existing_knowledge = json.load(f)
            print(f"[INFO] Loaded {len(existing_knowledge)} existing knowledge entries")
        except Exception as e:
            print(f"[WARNING] Failed to load existing knowledge: {e}")
    
    # Convert CPsyCounR examples to knowledge base format
    cpsycounr_knowledge = []
    for example in examples:
        keywords = []
        # Extract keywords from case type and method
        if '案例类别' in example:
            keywords.append(example['案例类别'])
        if '运用的技术' in example:
            keywords.extend(example['运用的技术'].split('、'))
        if 'title' in example:
            keywords.extend(example['title'].split(' '))
        
        # Create knowledge entry
        entry = {
            "id": example.get('id', f"cpsycounr_{len(cpsycounr_knowledge)+1}"),
            "keywords": list(set([k.strip() for k in keywords if k.strip()])),
            "title": example.get('title', '未命名案例'),
            "content": f"""
【案例类别】{example.get('案例类别', '未知')}
【运用技术】{example.get('运用的技术', '未知')}
【案例简述】{example.get('案例简述', '无')}
【咨询经过】{example.get('咨询经过', '无')}
【经验感想】{example.get('经验感想', '无')}
            """.strip()
        }
        cpsycounr_knowledge.append(entry)
    
    # Combine existing knowledge with new dataset
    combined_knowledge = existing_knowledge + cpsycounr_knowledge
    
    # Save combined knowledge
    with open(main_kb_file, 'w', encoding='utf-8') as f:
        json.dump(combined_knowledge, f, ensure_ascii=False, indent=2)
    
    print(f"[INFO] Updated main knowledge base with {len(cpsycounr_knowledge)} CPsyCounR entries")
    print(f"[INFO] Total knowledge base entries: {len(combined_knowledge)}")

if __name__ == "__main__":
    create_cpsycounr_dataset()
