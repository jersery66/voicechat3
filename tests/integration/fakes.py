"""Scripted fake backends for driving the REAL ConversationPipeline.

These fakes implement exactly the call surface the pipeline uses
(verified against services/pipeline.py), so the full turn logic —
ASR-correction skip (text mode), agent routing, RAG suffix, crisis
keyword gate, ||| parsing, scale state machine, tag detection — runs
for real without GPU / Ollama / audio hardware.

Fakes record every call so tests can assert on what the pipeline sent.
"""

from typing import Any, Dict, List, Optional


class FakeLLM:
    """Canned streaming LLM. Pops one full response per chat() call and
    yields it in small chunks to exercise the streaming code path.

    Mirrors the real LLMService side effects on conversation_history
    (user message appended when chat starts, assistant message appended
    when the stream finishes) so multi-turn dependent behavior (agent
    recent_history, RAG multi-turn query) is testable."""

    def __init__(self, responses: Optional[List[str]] = None):
        self.responses: List[str] = list(responses or [])
        self.default_response = "【情绪识别】平静【状态评估】低【变革话语】无【策略选择】肯定|||嗯，我听着呢，你接着说。"
        self.calls: List[Dict[str, Any]] = []
        self.conversation_history: List[Dict[str, str]] = []
        self.history_context: str = ""

    def chat(self, text: str, system_suffix: str = ""):
        full = self.responses.pop(0) if self.responses else self.default_response
        self.calls.append({"user_text": text, "system_suffix": system_suffix, "full": full})
        self.conversation_history.append({"role": "user", "content": text})
        # stream in ~8-char chunks
        for i in range(0, len(full), 8):
            yield full[i:i + 8]
        self.conversation_history.append({"role": "assistant", "content": full})

    def reset_conversation(self):
        self.conversation_history = []

    def set_history_context(self, context: str):
        self.history_context = context


class FakeAgent:
    """Scripted 3B agent. Defaults answer 'chat' with neutral emotion."""

    def __init__(self):
        self.available = True
        self.route_script: List[Dict[str, Any]] = []
        self.route_calls: List[Dict[str, Any]] = []
        self.route_error: Optional[Exception] = None  # raised by routing if set
        self.crisis_keyword_result = {"risk_level": 0, "indicators": [], "immediate_action": False}
        self.crisis_llm_result = {"risk_level": 0, "indicators": [], "immediate_action": False}
        self.emotion_result = {"emotion": "neutral", "intensity": 0.2}
        self.intent_result = {"intent": "counseling", "confidence": 0.9}

    def is_available(self) -> bool:
        return self.available

    def route_conversation_actions(self, user_text="", recent_history="",
                                   current_round=0, active_scale=None,
                                   collected_scales=None, relaxation_done=False):
        call = {
            "user_text": user_text, "current_round": current_round,
            "active_scale": active_scale, "collected_scales": collected_scales,
            "relaxation_done": relaxation_done,
        }
        self.route_calls.append(call)
        if self.route_error is not None:
            raise self.route_error
        if self.route_script:
            return self.route_script.pop(0)
        return {"scale_action": "chat", "scale": None, "item": None,
                "confidence": 0.5, "risk_level": 0, "reason": "fake default"}

    def classify_intent(self, text: str):
        return dict(self.intent_result)

    def detect_emotion(self, text: str):
        return dict(self.emotion_result)

    def _keyword_crisis_risk(self, text: str):
        return dict(self.crisis_keyword_result)

    def assess_crisis_risk(self, text: str, use_llm: bool = False):
        return dict(self.crisis_llm_result)


class FakeRAG:
    """Records queries; returns a scripted suffix."""

    def __init__(self, suffix: str = ""):
        self.suffix = suffix
        self.queries: List[str] = []

    def get_context(self, query: str) -> str:
        self.queries.append(query)
        return self.suffix

    def get_system_suffix(self, query: str) -> str:
        self.queries.append(query)
        return self.suffix


class FakeReport:
    """Minimal ReportService surface used by the pipeline."""

    def __init__(self, start_round: int = 6):
        self.round_count = start_round
        self.over_limit = False

    def increment_round(self):
        self.round_count += 1

    def get_round_count(self) -> int:
        return self.round_count

    def should_warn_time_limit(self):
        return False, ""

    def is_over_limit(self) -> bool:
        return self.over_limit


class FakeData:
    """Records save calls, writes nothing."""

    def __init__(self):
        self.user_messages: List[Dict[str, Any]] = []
        self.assistant_messages: List[Dict[str, Any]] = []
        self.subject_id: Optional[str] = None

    def set_user_id(self, subject_id):
        self.subject_id = subject_id

    def start_new_session(self):
        self.user_messages.clear()
        self.assistant_messages.clear()
        return None

    def save_user_message(self, audio, text):
        self.user_messages.append({"text": text})
        return None, None

    def save_assistant_message(self, audio, text, sample_rate=None):
        self.assistant_messages.append({"text": text})
        return None, None


class FakeTTS:
    """Records what would be spoken."""

    def __init__(self):
        self.played: List[str] = []

    def generate_and_play(self, text: str):
        self.played.append(text)

    def stop_playing(self):
        pass


class EmitCollector:
    """Captures pipeline emit(msg_type, content) calls."""

    def __init__(self):
        self.events: List[tuple] = []

    def __call__(self, msg_type: str, content: Any):
        self.events.append((msg_type, content))

    def types(self) -> List[str]:
        return [t for t, _ in self.events]
