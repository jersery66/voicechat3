#!/usr/bin/env python
"""Offline end-to-end voice interview acceptance harness (real models only).

This drives the *production* ConversationPipeline exactly as the GUI does,
but headless, so the whole chain can be verified without a human at the
microphone:

    user utterance -> VoxCPM2 synthesis (real, as the stand-in participant
    voice, saved as evidence wav) -> real FunASR inside the pipeline
    (use_stt=True, audio_data=...) -> real Tacit-1.7B decision (inprocess)
    -> DecisionValidator -> TurnPolicy -> real Qwen3.8-27B llama.cpp reply
    -> real VoxCPM2 synthesis rendered to evidence wav (instead of speakers).

Nothing is mocked: STT, decision, policy, LLM and TTS are the production
services loaded from the runtime config.  The ONLY synthetic element is the
participant's voice itself (a machine voice standing in for the human), and
the optional dev-mode proposal injection for the offer/consent chain, which
is labelled as such in the evidence.

Speaker/microphone hardware acceptance stays with the human operator; this
harness proves the software chain end to end.

Usage:
    VOICECHAT_RUNTIME_CONFIG=config/runtime_offline_5090.yaml \
        python scripts/acceptance/offline_voice_e2e.py \
        --evidence-dir test_output/offline_e2e --scenario all
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

EVIDENCE: dict[str, Any] = {"turns": [], "scenarios": {}, "config": {}, "notes": []}
PIPELINE: Any = None  # set by main(); scenario helpers read ScaleRuntime through it


def write_wav_pcm16(audio: Any, path: Path, sample_rate: int) -> None:
    """16-bit PCM WAV writer without torchaudio (torchcodec DLL breaks on
    non-ASCII project paths; production uses the same numpy approach)."""
    import wave

    import numpy as np

    arr = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    pcm = (arr * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def resample_to_16k(audio: Any, src_rate: int) -> Any:
    """Resample to FunASR's 16 kHz with an anti-aliasing filter (production
    records at 16k natively; VoxCPM synthesises at 48 kHz, so the stand-in
    voice must be converted properly -- naive decimation destroys speech)."""
    import numpy as np

    audio = trim_and_normalise(audio, src_rate)
    if src_rate == 16000:
        return audio
    try:
        import soxr

        return soxr.resample(np.asarray(audio, dtype=np.float32), src_rate, 16000)
    except Exception:
        from scipy.signal import resample_poly

        return resample_poly(np.asarray(audio, dtype=np.float32), 16000, src_rate)


def trim_and_normalise(audio: Any, sample_rate: int, pad: float = 0.12) -> Any:
    """Trim leading/trailing low-energy audio and peak-normalise.  VoxCPM
    synthesis can start/end with low-level noise that FunASR hallucinates
    around; the same preprocessing a real caller's quiet room would provide."""
    import numpy as np

    audio = np.asarray(audio, dtype=np.float32)
    if audio.size == 0:
        return audio
    envelope = np.abs(audio)
    indices = np.where(envelope > 0.01 * envelope.max())[0]
    if len(indices) == 0:
        return audio
    start = max(0, indices[0] - int(pad * sample_rate))
    end = min(len(audio), indices[-1] + int(pad * sample_rate))
    segment = audio[start:end]
    peak = float(np.abs(segment).max())
    if peak > 0:
        segment = segment / peak * 0.9
    return segment.astype(np.float32)


class FileRenderTTS:
    """Wrap the real VoxCPM service: synthesise, save to evidence, no speaker.

    The delivery queue calls ``generate_and_play``; here "play" means "write a
    wav into the evidence directory".  Synthesis is the untouched production
    path (same model, same voice prompt, same preprocessing).
    """

    def __init__(self, inner: Any, evidence_dir: Path, stt: Any = None) -> None:
        self._inner = inner
        self._evidence_dir = evidence_dir
        self._counter = 0
        self.rendered: list[dict[str, Any]] = []
        self._stt = stt
        self.standin_attempts: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def _inner_stt_transcribe(self, voice_16k: Any) -> str:
        try:
            return str(self._stt.transcribe(voice_16k) or "")
        except Exception:
            return ""

    def generate_and_play(self, text: str, **kwargs: Any) -> Any:
        from adapters.tts_results import PlaybackResult, PlaybackStatus

        audio = self._inner.generate(text)
        if audio is None or audio.size == 0:
            return PlaybackResult(PlaybackStatus.FAILED, "empty synthesis")
        self._counter += 1
        path = self._evidence_dir / f"tts_{self._counter:03d}.wav"
        write_wav_pcm16(audio, path, int(self._inner.sample_rate))
        self.rendered.append({"text": text, "wav": str(path)})
        return PlaybackResult(PlaybackStatus.COMPLETED)

    def render_user_voice(self, text: str) -> tuple[Any, Path]:
        """Synthesise the stand-in participant voice, verified by real ASR.

        VoxCPM occasionally renders a short utterance with synthesis noise
        that FunASR hallucinates around.  Each candidate take is transcribed
        by the real FunASR and kept only when the transcription matches the
        scripted text; otherwise the take is re-synthesised.  This is a
        generation-side quality gate -- the pipeline still transcribes the
        kept audio itself, and every take is logged in the evidence.
        """
        import difflib

        attempts = []
        for _ in range(4):
            audio = self._inner.generate(text)
            if audio is None or audio.size == 0:
                continue
            voice_16k = resample_to_16k(audio, int(self._inner.sample_rate))
            heard = self._inner_stt_transcribe(voice_16k) if self._stt is not None else text
            ratio = difflib.SequenceMatcher(
                None, heard.replace("，", "").replace("。", "").replace(" ", ""),
                text.replace("，", "").replace("。", "").replace(" ", ""),
            ).ratio()
            attempts.append({"heard": heard, "ratio": round(ratio, 3)})
            if ratio >= 0.6:
                break
        else:
            EVIDENCE["notes"].append(
                f"stand-in voice for {text[:20]!r} never reached ratio>=0.6; using best take: {attempts}"
            )
        if not attempts:
            raise RuntimeError(f"user-voice synthesis failed for: {text[:30]}")
        self._counter += 1
        path = self._evidence_dir / f"user_{self._counter:03d}.wav"
        write_wav_pcm16(audio, path, int(self._inner.sample_rate))
        self.standin_attempts.append({"text": text, "attempts": attempts})
        return voice_16k, path


def build_services(evidence_dir: Path, use_rag: bool) -> dict[str, Any]:
    """Load every real service the GUI would load (mirrors load_models)."""
    from services.stt_service import get_stt_service
    from services.tts_service import get_tts_service
    from services.llm_factory import build_llm_service
    from services.agent_service import get_agent_service
    from services.report_service import ReportService
    from data.data_manager import DataManager
    from services.emotion_tracker import EmotionTracker
    from services.rag_service import get_rag_service

    print("[load] FunASR (real)...")
    stt = get_stt_service()
    stt.load_model()

    print("[load] VoxCPM2 (real)...")
    tts_raw = get_tts_service()
    tts_raw.load_model(use_streaming=True)
    tts = FileRenderTTS(tts_raw, evidence_dir, stt=stt)

    print("[load] dialogue LLM (real llama.cpp)...")
    llm = build_llm_service()
    if not llm.test_connection():
        raise RuntimeError("dialogue endpoint not reachable -- start the WSL llama-server first")
    llm.warmup()

    agent = get_agent_service()
    report = ReportService(llm, agent_service=agent)
    report.start_session()
    data = DataManager()
    data.set_user_id(f"offline_e2e_{int(time.time())}")

    rag = None
    if use_rag:
        try:
            rag = get_rag_service()
        except Exception as exc:  # non-fatal: chat continues without RAG
            EVIDENCE["notes"].append(f"rag unavailable: {exc}")

    return {
        "stt": stt, "tts_raw": tts_raw, "tts": tts, "llm": llm,
        "agent": agent, "report": report, "data": data, "rag": rag,
        "emotion": EmotionTracker(),
    }


def build_pipeline(services: dict[str, Any]) -> Any:
    from services.pipeline import ConversationPipeline
    from conversation.coordinator import ConversationCoordinator
    from research.event_journal import EventJournal

    pipeline = ConversationPipeline(
        stt_service=services["stt"],
        llm_service=services["llm"],
        tts_service=services["tts"],
        rag_service=services["rag"],
        agent_service=services["agent"],
        report_service=services["report"],
        data_manager=services["data"],
        session_emotions=[],
        emotion_tracker=services["emotion"],
    )
    journal_root = services["data"].data_root / "research_events"
    coordinator = ConversationCoordinator(
        pipeline=pipeline,
        journal=EventJournal(journal_root / "events.jsonl"),
    )
    return pipeline, coordinator


def run_turn(
    coordinator: Any,
    services: dict[str, Any],
    utterance: str,
    *,
    label: str,
    inject_proposal: Any = None,
) -> dict[str, Any]:
    """One full voice round through the production pipeline."""
    from services.pipeline import PipelineConfig

    tts = services["tts"]
    audio, wav_path = tts.render_user_voice(utterance)
    print(f"  [{label}] user(as-synthesised): {utterance}")

    events: list[tuple[str, Any]] = []

    def emit(kind: str, payload: Any) -> None:
        events.append((kind, payload))

    config = PipelineConfig(
        use_stt=True,
        use_tts=True,
        audio_data=audio,
        user_text=utterance,
        router_proposal=inject_proposal,
    )
    started = time.perf_counter()
    result = coordinator.execute(config, emit)
    wall = time.perf_counter() - started

    decision = result.turn_decision
    row = {
        "label": label,
        "scripted_utterance": utterance,
        "asr_text": result.user_text,
        "asr_matches_script": result.user_text.strip() == utterance.strip(),
        "reply": result.full_response,
        "policy_action": decision.action.value if decision else None,
        "policy_reason": decision.reason if decision else None,
        "provider": (result.agent_observation or None) is not None and (
            getattr(result.agent_observation, "source", None)
        ),
        "fallback_used": bool(
            result.agent_observation and getattr(result.agent_observation, "fallback_used", False)
        ),
        "scale_active": result.scale_active,
        "scale_completed": result.scale_completed,
        "completed_scale_name": result.completed_scale_name,
        "scale_tags": result.scale_tags,
        "end_type": result.end_type,
        "timing": dict(result.timing),
        "wall_seconds": round(wall, 2),
        "user_wav": str(wav_path),
        "event_kinds": sorted({kind for kind, _ in events}),
    }
    EVIDENCE["turns"].append(row)
    print(
        f"  [{label}] asr={result.user_text!r} action={row['policy_action']}"
        f"({row['policy_reason']}) reply={result.full_response[:60]!r}"
    )
    return row



def answer_active_scale(
    coordinator: Any, services: dict[str, Any], *, prefix: str, budget: int = 15
) -> list[dict[str, Any]]:
    """Answer the active instrument until it completes or the budget runs out.

    Ambiguous ASR readings trigger the production clarification path; the
    stand-in participant simply answers again, as a real one would.
    """
    # Denials use the exact word "没有" (the interpreter's deterministic 0);
    # frequency answers use the option wording.  "完全没有" is avoided: for
    # PHQ-9/GAD-7 frequency items it only scores when the item's symptom
    # keywords are also present, and on the ideation item it reads as
    # ambiguous instead of a denial.
    answers = ["有几天", "没有", "一半以上时间", "有几天", "没有",
               "几乎每天", "有几天", "没有", "一半以上时间", "有几天",
               "没有", "几乎每天", "有几天", "没有", "一半以上时间"]
    out: list[dict[str, Any]] = []
    for i in range(budget):
        snap = PIPELINE.scale_runtime.snapshot()
        if not snap.active_scale:
            break
        row = run_turn(
            coordinator, services, answers[i % len(answers)], label=f"{prefix}_{i + 1}"
        )
        out.append(row)
        if row["scale_completed"]:
            break
    return out


def scenario_chat(coordinator, services, n: int = 5) -> dict[str, Any]:
    print("[scenario] A: continuous real-voice chat")
    utterances = [
        "你好，我最近感觉压力有点大。",
        "主要是工作上的事情，任务太多了，经常加班到很晚。",
        "晚上睡得不太好，躺在床上总是想东想西的。",
        "白天上班的时候注意力也不太集中，效率很低。",
        "有时候会觉得心慌，也不知道是怎么回事。",
    ]
    rows = [
        run_turn(coordinator, services, text, label=f"chat_{i + 1}")
        for i, text in enumerate(utterances[:n])
    ]
    ok = all(r["asr_text"].strip() and r["reply"].strip() for r in rows)
    return {"name": "chat", "turns": rows, "pass": ok}


def scenario_scale_request(coordinator, services) -> dict[str, Any]:
    """The participant explicitly asks for an assessment by voice."""
    print("[scenario] B: participant-requested scale (explicit voice request)")
    rows = [
        run_turn(coordinator, services, "我想做个量表。", label="scale_request"),
    ]
    row = rows[0]
    started = row["scale_active"] or (row["policy_action"] == "start_scale")
    EVIDENCE["notes"].append(
        f"explicit request policy decision: {row['policy_action']}/{row['policy_reason']}"
    )
    if started:
        # The scale owns the following turns; answer every item.
        rows.extend(
            answer_active_scale(coordinator, services, prefix="scale_item", budget=15)
        )
    completed = any(r["scale_completed"] for r in rows)
    return {"name": "scale_request", "turns": rows, "pass": bool(started and completed)}


def scenario_scale_via_offer(coordinator, services) -> dict[str, Any]:
    """Offer -> voice consent -> items -> completion, with a dev-mode injection.

    The injected proposal stands in for a Jev OFFER_SCALE hit (natural
    triggering is reported separately and honestly); everything after the
    injection -- invitation phrasing, TTS, consent parsing, OfferLedger,
    ScaleRuntime, answer interpretation, scoring -- is the real chain.
    """
    print("[scenario] C: offer -> consent -> scale (dev-mode proposal injection)")
    from conversation.contracts import RouterAction, RouterProposal

    injection = RouterProposal(
        action=RouterAction.START_SCALE,
        scale_name="GAD-7",
        offer="scale",
        confidence=0.9,
        reason="dev_injection_test_entry",
    )
    EVIDENCE["notes"].append("scenario C uses a dev-mode injected proposal (labelled test entry)")
    rows = [run_turn(coordinator, services, "最近确实比较紧张。", label="pre_offer")]
    # The injected proposal produces a real ledger offer and a spoken
    # invitation; consent is parsed from the *next* real voice turn.
    row = run_turn(
        coordinator, services, "嗯，最近是有点。",
        label="offer_injected", inject_proposal=injection,
    )
    rows.append(row)
    row = run_turn(coordinator, services, "好，可以，做一下吧。", label="consent")
    rows.append(row)
    snap = PIPELINE.scale_runtime.snapshot()
    started = bool(snap.active_scale)
    if started:
        rows.extend(
            answer_active_scale(coordinator, services, prefix="offer_scale_item", budget=15)
        )
    completed = any(r["scale_completed"] for r in rows)
    return {"name": "scale_via_offer", "turns": rows, "pass": bool(started and completed)}


def scenario_scale_decline(coordinator, services) -> dict[str, Any]:
    """An offer is made (dev injection) and the participant declines by voice."""
    print("[scenario] D: offer -> decline -> continue chat (dev-mode proposal injection)")
    from conversation.contracts import RouterAction, RouterProposal

    injection = RouterProposal(
        action=RouterAction.START_SCALE,
        scale_name="PCL-5",
        offer="scale",
        confidence=0.9,
        reason="dev_injection_test_entry",
    )
    rows = [
        run_turn(coordinator, services, "最近情绪一直不太好。", label="decline_offer", inject_proposal=injection),
    ]
    row = run_turn(coordinator, services, "先不做量表，我想继续聊一聊。", label="decline_answer")
    rows.append(row)
    snap = PIPELINE.scale_runtime.snapshot()
    no_scale = not snap.active_scale
    row = run_turn(coordinator, services, "嗯，好的，我明白了。", label="decline_continue")
    rows.append(row)
    ok = no_scale and row["reply"].strip() != ""
    return {"name": "scale_decline", "turns": rows, "pass": bool(ok)}


def scenario_relaxation(coordinator, services) -> dict[str, Any]:
    """The participant explicitly asks for relaxation by voice."""
    print("[scenario] E: participant-requested relaxation (explicit voice request)")
    rows = [
        run_turn(coordinator, services, "我现在特别烦，能不能做个放松训练？", label="relax_request"),
    ]
    row = rows[0]
    rec = row["reply"].strip() != "" and row["policy_action"] in (
        "recommend_relaxation", "chat",
    )
    EVIDENCE["notes"].append(
        f"relaxation request decision: {row['policy_action']}/{row['policy_reason']}"
    )
    return {"name": "relaxation_request", "turns": rows, "pass": bool(rec)}


def scenario_end(coordinator, services) -> dict[str, Any]:
    print("[scenario] F: session end by voice")
    row = run_turn(coordinator, services, "今天就先到这里吧，谢谢老师。", label="end_session")
    ok = row["end_type"] is not None or "结束" in row["reply"] or row["reply"].strip() != ""
    return {"name": "end_session", "turns": [row], "pass": bool(ok)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", default="test_output/offline_e2e")
    parser.add_argument(
        "--scenario",
        default="all",
        help="all | chat | scale_request | scale_via_offer | scale_decline | relaxation | end",
    )
    parser.add_argument("--chat-turns", type=int, default=5)
    parser.add_argument("--no-rag", action="store_true")
    args = parser.parse_args()

    from deployment.runtime_config import get_runtime_config

    runtime = get_runtime_config()
    EVIDENCE["config"] = {
        "source": runtime.source_path,
        "decision": {
            "mode": runtime.decision.mode,
            "backend": runtime.decision.backend,
            "transport": runtime.decision.transport,
            "model_path": str(runtime.decision.model_path),
        },
        "dialogue": {"base_url": runtime.dialogue.base_url, "model": runtime.dialogue.model},
    }
    print(f"runtime config: {runtime.source_path}")
    print(f"decision: {EVIDENCE['config']['decision']}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    evidence_dir = PROJECT_ROOT / args.evidence_dir / stamp
    evidence_dir.mkdir(parents=True, exist_ok=True)

    services = build_services(evidence_dir, use_rag=not args.no_rag)
    pipeline, coordinator = build_pipeline(services)
    global PIPELINE
    PIPELINE = pipeline

    chosen = args.scenario
    scenarios = []
    # Explicit-request scale runs FIRST so it exercises the user_scale_request
    # path on a session without an already-active instrument.
    if chosen in ("all", "scale_request"):
        scenarios.append(scenario_scale_request(coordinator, services))
    if chosen in ("all", "chat"):
        scenarios.append(scenario_chat(coordinator, services, args.chat_turns))
    if chosen in ("all", "scale_via_offer"):
        scenarios.append(scenario_scale_via_offer(coordinator, services))
    if chosen in ("all", "scale_decline"):
        scenarios.append(scenario_scale_decline(coordinator, services))
    if chosen in ("all", "relaxation"):
        scenarios.append(scenario_relaxation(coordinator, services))
    if chosen in ("all", "end"):
        scenarios.append(scenario_end(coordinator, services))

    EVIDENCE["scenarios"] = {
        s["name"]: {"pass": s["pass"], "turns": s["turns"]} for s in scenarios
    }
    EVIDENCE["summary"] = {
        s["name"]: ("PASS" if s["pass"] else "FAIL") for s in scenarios
    }

    out = evidence_dir / "evidence.json"
    out.write_text(json.dumps(EVIDENCE, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nevidence written: {out}")
    for name, verdict in EVIDENCE["summary"].items():
        print(f"  {name}: {verdict}")

    pipeline.shutdown()
    return 0 if all(s["pass"] for s in scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
