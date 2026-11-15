"""Offline probe: what does the policy decide for a resistant participant?

Ten vague answers in a row, run through the real ConversationPipeline with fake
I/O.  The GUI run showed no exercise invitation by round 10 even though the
pipeline logged "proactive candidate queued ... round=8", so the question is
whether the candidate ever reaches the policy and what the policy does with it.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.pipeline import ConversationPipeline, PipelineConfig
from tests.integration.fakes import (
    FakeAgent,
    FakeData,
    FakeLLM,
    FakeRAG,
    FakeReport,
    FakeTTS,
)

logging.disable(logging.CRITICAL)

REPLY = "嗯，我在听。"

RESISTANT = [
    "不知道，没什么好说的。",
    "就那样吧。",
    "不想聊这个。",
    "差不多吧，说不清。",
    "都行，你问吧。",
    "嗯。",
    "没什么特别的感觉。",
    "你问吧，我配合。",
    "这个也说不上来。",
    "随便。",
]


def main() -> int:
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=FakeLLM(responses=[REPLY] * len(RESISTANT)),
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(start_round=1),
        data_manager=FakeData(),
        session_emotions=[],
    )
    try:
        for index, text in enumerate(RESISTANT, start=1):
            result = pipeline.execute(PipelineConfig(user_text=text), lambda *_: None)
            decision = result.turn_decision
            runtime = pipeline.scale_runtime.snapshot()
            print(
                f"{index:>2} action={getattr(decision.action, 'value', decision.action):<20}"
                f" reason={decision.reason:<34}"
                f" cand={pipeline._proactive_relaxation_candidate}"
                f" scale={runtime.active_scale}"
            )
    finally:
        pipeline.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
