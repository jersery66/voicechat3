"""Offline probe: does an anxious participant actually receive GAD-7?

Runs the real ``ConversationPipeline`` with fake I/O so the routing can be
checked without a model server or a GUI.  Prints the instrument the pipeline
started for three chief complaints.
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

CASES = {
    "抑郁主诉": [
        "最近心里很难受，什么都不想干。",
        "晚上总是睡不着，翻来覆去到两三点。",
        "白天一点力气都没有，吃饭也没胃口。",
        "会觉得自己挺没用的，让家里人失望。",
        "这种状态已经一个多月了。",
        "我能做个问卷吗？",
    ],
    "焦虑主诉": [
        "最近总是很紧张，心里发慌。",
        "控制不住地担心，什么都往坏处想。",
        "坐立不安，静不下来。",
        "晚上也放松不下来，翻来覆去。",
        "这种状态已经好几个月了。",
        "我能做个问卷吗？",
    ],
    "创伤主诉": [
        "去年出了车祸，现在还会做噩梦。",
        "一看到车就闪回当时的画面。",
        "不敢开车，也不敢走那条路。",
        "有点动静就吓一跳。",
        "这种状态已经很久了。",
        "我能做个问卷吗？",
    ],
}


def run_case(name: str, turns: list[str]) -> str:
    pipeline = ConversationPipeline(
        stt_service=None,
        llm_service=FakeLLM(responses=[REPLY] * len(turns)),
        tts_service=FakeTTS(),
        rag_service=FakeRAG(),
        agent_service=FakeAgent(),
        report_service=FakeReport(start_round=len(turns) + 1),
        data_manager=FakeData(),
        session_emotions=[],
    )
    started = None
    try:
        for text in turns:
            pipeline.execute(PipelineConfig(user_text=text), lambda *_: None)
            runtime = pipeline.scale_runtime.snapshot()
            if runtime.active_scale and started is None:
                started = runtime.active_scale
    finally:
        pipeline.shutdown()
    return started or "(未启动量表)"


def main() -> int:
    print(f"{'主诉':<10} {'启动的量表':<12}")
    print("-" * 26)
    for name, turns in CASES.items():
        print(f"{name:<10} {run_case(name, turns):<12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
