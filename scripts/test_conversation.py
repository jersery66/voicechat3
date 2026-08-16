"""LEGACY / DEPRECATED Ollama conversation smoke script.

This historical development helper exercises the old Ollama + ``|||``
response path only. It is not a production acceptance tool and must not be
used to validate vLLM, Blackwell profiles, Qwen3.8, or the current
``RouterProposal -> TurnPolicy -> TurnDecision`` authority chain.

Usage (development diagnostics only): ``python scripts/test_conversation.py``
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.rag_service import RAGService
from config import SYSTEM_PROMPT, OLLAMA_MODEL, OLLAMA_HOST
import ollama


def test_conversation():
    # 从 config 读取模型名与 host（不再硬编码）
    model = OLLAMA_MODEL
    host = OLLAMA_HOST
    client = ollama.Client(host=host)

    # 测试连接
    print(f"[1/3] 测试 Ollama 连接...")
    try:
        models = client.list()
        available = [m.model for m in models.models]
        print(f"  可用模型: {available}")
        if model not in available:
            print(f"  [ERROR] 模型 {model} 不可用!")
            return
    except Exception as e:
        print(f"  [ERROR] Ollama 连接失败: {e}")
        return

    # 初始化 RAG
    print(f"[2/3] 初始化 RAG...")
    rag = RAGService()

    # 测试对话
    print(f"[3/3] 开始对话测试...\n")
    print("=" * 60)

    test_inputs = [
        "你好，我是新来的",
        "我最近老是睡不着，每天晚上翻来覆去的",
        "心里堵得慌，特别烦躁，控制不住想发火",
        "想家了，想老婆孩子",
        "有时候会听到一些奇怪的声音",
    ]

    conversation_history = []

    for i, user_input in enumerate(test_inputs, 1):
        print(f"\n[来访者] {user_input}")

        # RAG 检索
        rag_suffix = rag.get_system_suffix(user_input)

        # 构建 system prompt
        current_system = SYSTEM_PROMPT
        if rag_suffix:
            current_system += rag_suffix

        # 构建消息
        messages = [{"role": "system", "content": current_system}]
        messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_input})

        # LLM 生成
        try:
            response = client.chat(
                model=model,
                messages=messages,
                stream=False,
                options={"temperature": 0.7, "num_predict": 300}
            )
            reply = response["message"]["content"]

            # 解析 ||| 分隔符
            if "|||" in reply:
                parts = reply.split("|||", 1)
                analysis = parts[0].strip()
                spoken = parts[1].strip()
                print(f"[分析] {analysis[:100]}...")
                print(f"[心医生] {spoken}")
            else:
                print(f"[心医生] {reply}")
                analysis = ""
                spoken = reply

            # 保存对话历史
            conversation_history.append({"role": "user", "content": user_input})
            conversation_history.append({"role": "assistant", "content": reply})

        except Exception as e:
            print(f"[ERROR] LLM 生成失败: {e}")

    print("\n" + "=" * 60)
    print("对话测试完成")


if __name__ == "__main__":
    test_conversation()
