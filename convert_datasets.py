#!/usr/bin/env python3
# Convert all datasets to standard format

import json
from pathlib import Path

KB_DIR = Path(r"E:\数据库\代码\Data\PDCH\knowledge_base")

def convert_cpsycounr():
    """Convert CPsyCounR.json to standard format"""
    input_file = KB_DIR / "CPsyCounR.json"
    output_file = KB_DIR / "cpsycounr_converted.json"
    
    if not input_file.exists():
        print(f"[SKIP] {input_file} not found")
        return 0
    
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    entries = data if isinstance(data, list) else data.get('examples', [])
    converted = []
    
    for i, item in enumerate(entries):
        keywords = []
        
        # Extract keywords
        if '案例类别' in item:
            case_type = item['案例类别']
            if isinstance(case_type, list):
                keywords.extend([k.strip() for k in case_type if k.strip()])
            else:
                keywords.append(str(case_type).strip())
        
        if '运用的技术' in item:
            method = item['运用的技术']
            if isinstance(method, list):
                keywords.extend([k.strip() for k in method if k.strip()])
            else:
                keywords.extend([k.strip() for k in str(method).split('、') if k.strip()])
        
        # Build content
        content_parts = []
        
        if '案例标题' in item:
            content_parts.append(f"案例标题: {item['案例标题']}")
        
        if '案例类别' in item:
            ct = item['案例类别']
            if isinstance(ct, list):
                content_parts.append(f"案例类别: {', '.join(ct)}")
            else:
                content_parts.append(f"案例类别: {ct}")
        
        if '运用的技术' in item:
            mt = item['运用的技术']
            if isinstance(mt, list):
                content_parts.append(f"运用技术: {', '.join(mt)}")
            else:
                content_parts.append(f"运用技术: {mt}")
        
        if '案例简述' in item:
            brief = item['案例简述']
            if isinstance(brief, list):
                brief = ' '.join(brief)
            content_parts.append(f"案例简述: {brief}")
        
        if '咨询经过' in item:
            process = item['咨询经过']
            if isinstance(process, list):
                process = ' '.join(process)
            content_parts.append(f"咨询经过: {process}")
        
        if '经验感想' in item:
            exp = item['经验感想']
            if isinstance(exp, list):
                exp = ' '.join(exp)
            content_parts.append(f"经验感想: {exp}")
        
        entry = {
            "id": f"cpsycounr_{i+1}",
            "keywords": list(set(keywords))[:10],
            "title": item.get('案例标题', f'心理咨询案例 {i+1}'),
            "content": "\n".join(content_parts)
        }
        
        if entry['content']:
            converted.append(entry)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(converted, f, ensure_ascii=False, indent=2)
    
    print(f"[OK] CPsyCounR: {len(converted)} entries -> {output_file.name}")
    return len(converted)

def convert_psyqa():
    """Convert PsyQA_full.json to standard format"""
    input_file = KB_DIR / "PsyQA_full.json"
    output_file = KB_DIR / "psyqa_converted.json"
    
    if not input_file.exists():
        print(f"[SKIP] {input_file} not found")
        return 0
    
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    entries = data if isinstance(data, list) else data.get('examples', [])
    converted = []
    
    for i, item in enumerate(entries):
        keywords = []
        
        # Extract keywords
        if 'keywords' in item:
            kw = item['keywords']
            if isinstance(kw, list):
                keywords.extend([k.strip() for k in kw if k.strip()])
            else:
                keywords.append(str(kw).strip())
        
        if 'question' in item:
            q = item['question']
            keywords.extend([w for w in q.split() if len(w) > 1][:5])
        
        # Build content
        content_parts = []
        
        if 'question' in item:
            content_parts.append(f"问题: {item['question']}")
        
        if 'description' in item:
            content_parts.append(f"描述: {item['description']}")
        
        if 'answers' in item:
            answers = item['answers']
            if isinstance(answers, list):
                for j, ans in enumerate(answers[:3]):
                    if isinstance(ans, dict):
                        text = ans.get('answer_text', '')
                        if text:
                            content_parts.append(f"回答{j+1}: {text}")
                    else:
                        content_parts.append(f"回答{j+1}: {ans}")
        
        if 'answer' in item:
            content_parts.append(f"回答: {item['answer']}")
        
        entry = {
            "id": f"psyqa_{item.get('questionID', i+1)}",
            "keywords": list(set(keywords))[:10],
            "title": item.get('question', f'心理问答 {i+1}')[:50],
            "content": "\n".join(content_parts)
        }
        
        if entry['content']:
            converted.append(entry)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(converted, f, ensure_ascii=False, indent=2)
    
    print(f"[OK] PsyQA: {len(converted)} entries -> {output_file.name}")
    return len(converted)

def main():
    print("=" * 50)
    print("Converting datasets to standard format")
    print("=" * 50)
    
    total = 0
    total += convert_cpsycounr()
    total += convert_psyqa()
    
    print("=" * 50)
    print(f"[DONE] Total: {total} entries converted")
    print("=" * 50)

if __name__ == "__main__":
    main()
