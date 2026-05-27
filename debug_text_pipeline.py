import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import OLLAMA_MODEL
print(f"OLLAMA_MODEL = {OLLAMA_MODEL}")

from services.llm_service import LLMService
print(f"\nLLMService model: {LLMService().model}")
assert LLMService().model == OLLAMA_MODEL, "LLM should use OLLAMA_MODEL!"

from services.pipeline import ConversationPipeline, PipelineConfig
from services.llm_service import LLMService
from services.agent_service import AgentService
from services.report_service import ReportService
from data.data_manager import DataManager

llm = LLMService()
agent = AgentService()
report = ReportService(llm, agent_service=agent)
data = DataManager()

pipeline = ConversationPipeline(
    stt_service=None,
    llm_service=llm,
    tts_service=None,
    rag_service=None,
    agent_service=agent,
    report_service=report,
    data_manager=data,
    session_emotions=[],
    emotion_tracker=None,
)

def emit(msg_type, content):
    if msg_type == "append_chat":
        role, text = content
        print(f"[{role}] {text[:80]}")
    elif msg_type == "stream_text":
        print(f"[AI chunk] {content}")
    elif msg_type == "status":
        print(f"[STATUS] {content}")
    elif msg_type == "start_ai_message":
        print("[AI starts responding]")
    elif msg_type == "finish_streaming":
        print("[AI finished]")
    else:
        print(f"[{msg_type}] {content}")

print("\n--- Testing text input pipeline ---")
data.start_new_session()
report.start_session()

config = PipelineConfig(use_stt=False, use_tts=False, user_text="你好，最近感觉怎么样？")
result = pipeline.execute(config, emit)

print(f"\nPipeline result:")
print(f"  user_text: {result.user_text}")
print(f"  full_response length: {len(result.full_response)}")
print(f"  spoken_text: {result.spoken_text[:100] if result.spoken_text else 'N/A'}")
print(f"  intent: {result.intent}")
print(f"  end_type: {result.end_type}")

print("\nDone!")