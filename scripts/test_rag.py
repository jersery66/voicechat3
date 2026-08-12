"""RAG Service 测试脚本"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.rag_service import RAGService

def test_rag():
    rag = RAGService()

    test_cases = [
        # 戒毒场景 - 应该命中
        "我最近睡不着，每天晚上翻来覆去的",
        "心里堵得慌，特别烦躁",
        "我有时候会听到一些奇怪的声音",
        "忍不住想那个东西",
        "活着没意思，不如死了算了",
        "想家了，想老婆孩子",
        "浑身不自在，像蚂蚁在爬",
        "我脾气大，控制不住想发火",
        "头疼，胃也不舒服",
        "感觉像做梦一样，不真实",

        # 日常闲聊 - 不应命中
        "你好呀",
        "今天天气不错",
        "吃饭了吗",
        "你叫什么名字",
    ]

    print("=" * 70)
    print("RAG Service 测试")
    print("=" * 70)

    for text in test_cases:
        print(f"\n输入: {text}")
        suffix = rag.get_system_suffix(text)
        if suffix:
            # Extract just the titles from the context
            import re
            titles = re.findall(r'【(.+?)】', suffix)
            print(f"  -> RAG 触发，检索到: {', '.join(titles)}")
        else:
            print(f"  -> 未触发 RAG (闲聊)")

    print("\n" + "=" * 70)
    print("测试完成")
    print("=" * 70)

if __name__ == "__main__":
    test_rag()
