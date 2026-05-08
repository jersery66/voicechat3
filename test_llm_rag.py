"""LLM + RAG 集成测试脚本 (文字模式，无需 STT/TTS)"""
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.llm_service import LLMService
from services.rag_service import get_rag_service


def strip_tags(text):
    """Remove all control tags from text."""
    text = re.sub(r'\[REC_[A-Z_]+\]', '', text)
    text = re.sub(r'\[END_[A-Z_]+\]', '', text)
    text = re.sub(r'<\|[^|]+\|>', '', text)
    text = re.sub(r'【[^】]*】', '', text)
    return text.strip()


def parse_response(full_response):
    """Parse LLM response into analysis and spoken parts."""
    if '|||' in full_response:
        parts = full_response.split('|||', 1)
        analysis = parts[0].strip()
        spoken = parts[1].strip()
    else:
        analysis = ""
        spoken = full_response.strip()
    return analysis, spoken


def main():
    print("=" * 50)
    print("LLM + RAG 集成测试")
    print("=" * 50)

    print("\n[1] 加载 LLM...")
    llm = LLMService()
    print(f"    模型: {llm.model}")
    if not llm.test_connection():
        print("    [ERROR] 无法连接 Ollama!")
        return
    print("    连接成功!")

    print("\n[2] 加载 RAG 知识库...")
    rag = get_rag_service()

    print("\n" + "=" * 50)
    print("开始对话 (输入 q 退出，输入 r 重置)")
    print("=" * 50)

    while True:
        try:
            user_input = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if user_input.lower() == 'q':
            break
        if user_input.lower() == 'r':
            llm.reset_conversation()
            print("[会话已重置]")
            continue

        # RAG lookup
        rag_context = rag.get_context(user_input)
        system_suffix = ""
        if rag_context:
            system_suffix = f"\n\n【参考知识】\n{rag_context}"
            print(f"  [RAG] 命中知识库")

        # Collect full response
        print("正在思考...", end="", flush=True)
        full_response = ""
        for chunk in llm.chat(user_input, system_suffix=system_suffix if system_suffix else None):
            full_response += chunk
        print("\r" + " " * 20 + "\r", end="")

        # Parse
        analysis, spoken = parse_response(full_response)

        # Display analysis
        if analysis:
            clean_analysis = strip_tags(analysis)
            print(f"  [分析] {clean_analysis}")

        # Display spoken
        clean_spoken = strip_tags(spoken)
        if clean_spoken:
            print(f"心医生: {clean_spoken}")

        # Show tags
        end_match = re.search(r'\[(END_[A-Z_]+)\]', full_response)
        rec_match = re.search(r'\[(REC_[A-Z_]+)\]', full_response)
        if end_match:
            print(f"  [标签] 会话结束: {end_match.group(1)}")
        if rec_match:
            print(f"  [标签] 推荐放松: {rec_match.group(1)}")

        if end_match:
            print(f"\n[会话结束: {end_match.group(1)}]")
            break

    print("\n测试结束。")


if __name__ == "__main__":
    main()
