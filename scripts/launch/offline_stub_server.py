#!/usr/bin/env python3
"""Offline rehearsal server: a minimal OpenAI-compatible ``/v1`` endpoint.

What it is
----------
A *stand-in* for llama-server / vLLM when this workstation has no model
weights.  It speaks the same wire protocol the application already uses
(``GET /v1/models``, ``POST /v1/chat/completions``, ``POST /v1/completions``,
SSE streaming), so the UI, the turn pipeline, the delivery ledger, the scale
trigger and the report writer can all be exercised end to end without a GPU.

What it is NOT
--------------
It does not understand language.  Replies come from a small rule table that
mimics the register required by ``SYSTEM_PROMPT`` (one to three short spoken
sentences, at most one question).  This is for interface and flow rehearsal
only.  Never run a real interview against it, and never report its output as
model output.

Protocol notes
--------------
* stdlib only (http.server / json), so it starts before the venv is populated;
* every chunk the app receives is a normal OpenAI delta, so nothing in the
  application needs a special case for "stub mode";
* the served model id defaults to ``voicechat-offline-stub``, which makes the
  mode visible in ``/v1/models`` and in the app's own console banner.

Usage:
    python scripts/launch/offline_stub_server.py [--port 8010] [--model NAME]
    python scripts/launch/offline_stub_server.py --check     # is it up?
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PORT = 8010
DEFAULT_MODEL = "voicechat-offline-stub"

# --------------------------------------------------------------------- replies
# Openers are tracked so the same one never lands twice in a row: the system
# prompt explicitly forbids repeating the previous turn's opening.
_SHORT_ACKS = ("嗯", "哦", "好", "好的", "还行", "可以", "知道了", "行", "还好")

_TOPIC_TURNS = (
    ("吃饭", "最近吃饭怎么样，食堂的饭菜还合口吗"),
    ("睡觉", "晚上睡得踏实吗，几点能睡着"),
    ("身体", "身上有没有哪儿一直不舒服的"),
    ("相处", "同屋的人里，有没有能说上几句话的"),
    ("时间", "白天这些时间，你一般都怎么打发"),
    ("家里", "家里最近有联系吗，上次通话是什么时候"),
    ("打算", "出去以后，有没有哪件事是你想先做的"),
)

_RULES = (
    (
        ("睡不着", "失眠", "睡不好", "半夜醒"),
        ("晚上躺下之后，是脑子停不下来，还是身体不舒服",
         "睡不着的那些夜里，你一般会做什么"),
    ),
    (
        ("想家", "想孩子", "想妈", "想爸", "想老婆"),
        ("这种想念，一天里什么时候最重",
         "最近一次和他们说上话，是什么时候"),
    ),
    (
        ("烦", "焦虑", "紧张", "心慌", "害怕", "担心"),
        ("这股劲儿是一直在，还是一阵一阵地来",
         "心里发紧的时候，身上会有什么感觉"),
    ),
    (
        ("累", "疲惫", "没劲", "没力气"),
        ("这种累更像是没睡够，还是做什么都提不起劲",
         "白天里有没有哪一段是能松快一点的"),
    ),
    (
        ("无聊", "没意思", "待不住", "闷"),
        ("一天里最难熬的是哪一段",
         "这阵子有没有什么事，是你愿意多花点时间的"),
    ),
    (
        ("后悔", "对不起", "自责", "没用"),
        ("这件事，你心里翻来覆去想的是哪一部分",
         "如果当时能重来一次，你最想改的是哪一步"),
    ),
    (
        ("戒", "毒品", "吸", "复吸"),
        ("这一回，你觉得最难扛的是什么时候",
         "以前有过撑过去的时候吗，那次是怎么过去的"),
    ),
    (
        ("出去", "回家", "以后", "将来", "工作"),
        ("回去以后，最想先做的一件事是什么",
         "这些打算里，有哪一件是现在就能开始准备的"),
    ),
)

_GENERIC = (
    "你说的这个，能再具体讲讲吗，是什么时候开始的",
    "这件事里，最让你放不下的是哪一点",
    "听起来这件事在你心里放了一阵了，最近一次想起它是什么情形",
    "那时候你人在哪儿，身边有人吗",
    "如果今天只聊一件事，你想聊哪一件",
)

_OPENERS = (
    "嗯。",
    "这样啊。",
    "我在听。",
    "听起来不轻。",
    "我记下了。",
)


class ReplyEngine:
    """Deterministic-ish rule table; the state is only 'last opener'."""

    def __init__(self) -> None:
        self._last_opener = ""
        self._lock = threading.Lock()
        self._rng = random.Random(20261117)

    # ------------------------------------------------------------- utilities
    @staticmethod
    def _last_user_text(messages: list[dict]) -> str:
        for message in reversed(messages):
            if str(message.get("role", "")).lower() == "user":
                return str(message.get("content", "")).strip()
        return ""

    @staticmethod
    def _system_text(messages: list[dict]) -> str:
        return "\n".join(
            str(message.get("content", ""))
            for message in messages
            if str(message.get("role", "")).lower() == "system"
        )

    def _opener(self) -> str:
        choices = [o for o in _OPENERS if o != self._last_opener] or list(_OPENERS)
        picked = self._rng.choice(choices)
        self._last_opener = picked
        return picked

    @staticmethod
    def _is_trivial(text: str) -> bool:
        stripped = text.strip("，。！？,.!?~、 ")
        return len(stripped) <= 3 and any(
            stripped.startswith(ack) for ack in _SHORT_ACKS
        )

    @staticmethod
    def _is_question(text: str) -> bool:
        return text.rstrip().endswith(("？", "?")) or "吗" in text[-6:]

    def _report(self, system: str) -> str:
        """A plain-text stand-in for the structured report writer."""
        if "督导" in system or "咨询报告" in system or "报告" in system:
            return (
                "本次会话共进行若干轮，来访者主要谈及近期睡眠、情绪与对家人的挂念，"
                "情绪表达较为克制，对封闭环境下的日常安排有轻度不适，未见明显激越表现。"
                "建议后续继续关注睡眠与情绪波动，必要时由专业人员进一步评估。"
            )
        if "摘要压缩" in system or "摘要" in system:
            return "来访者谈到近期睡眠不稳、情绪偏低，并多次提到家人。"
        if "意图分类" in system:
            return json.dumps({"intent": "chat", "confidence": 0.6}, ensure_ascii=False)
        if "情绪分析" in system:
            return json.dumps({"emotion": "neutral", "intensity": 0.5}, ensure_ascii=False)
        return ""

    def reply(self, messages: list[dict], max_tokens: int = 1024) -> str:
        system = self._system_text(messages)
        user = self._last_user_text(messages)

        with self._lock:
            structured = self._report(system)
            if structured:
                return structured[: max(40, max_tokens * 2)]

            if not user:
                return "你好，我是小薇。今天想从哪儿聊起都可以。"

            if self._is_trivial(user):
                label, question = self._rng.choice(_TOPIC_TURNS)
                return f"那咱们换个话题。{question}？"

            lowered = user.lower()
            for keywords, answers in _RULES:
                if any(word in lowered for word in keywords):
                    return self._rng.choice(answers) + "？"

            if self._is_question(user):
                # The prompt forbids adding a follow-up question right after
                # answering one, so this branch never appends one.
                return f"这个我现在说不太准。{self._opener()}不过你问这个，是不是有件事一直搁在心里。"

            fragment = self._fragment(user)
            if fragment:
                return f"{self._opener()}你说的“{fragment}”，是怎么一回事？"
            return self._rng.choice(_GENERIC) + "？"

    @staticmethod
    def _fragment(text: str) -> str:
        """Pick a concrete phrase to reflect, never the whole utterance."""
        cleaned = text.strip().strip("。！？，, ")
        for separator in ("，", ",", "。", "；", ";"):
            if separator in cleaned:
                parts = [p for p in cleaned.split(separator) if len(p) >= 3]
                if parts:
                    cleaned = max(parts, key=len)
                    break
        return cleaned[:12] if 2 <= len(cleaned) <= 40 else ""


ENGINE = ReplyEngine()


# ---------------------------------------------------------------------- server
class Handler(BaseHTTPRequestHandler):
    server_version = "voicechat-offline-stub/1.0"
    protocol_version = "HTTP/1.1"

    # -- helpers ----------------------------------------------------------
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            return {}

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib name
        # The application owns its own logs; keep this quiet but traceable.
        if self.server.verbose:  # type: ignore[attr-defined]
            sys.stderr.write("[stub] " + fmt % args + "\n")

    # -- routes -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        path = self.path.split("?", 1)[0].rstrip("/")
        if path in ("/v1/models", "/models"):
            self._send_json({
                "object": "list",
                "data": [{
                    "id": self.server.model_id,  # type: ignore[attr-defined]
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "offline-rehearsal",
                }],
            })
            return
        if path in ("/health", "/v1/health", "/healthz"):
            self._send_json({"status": "ok", "mode": "offline-rehearsal"})
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802 - stdlib name
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/v1/chat/completions":
            self._chat()
            return
        if path == "/v1/completions":
            self._completion()
            return
        self._send_json({"error": "not found"}, status=404)

    # -- generation -------------------------------------------------------
    def _chat(self) -> None:
        payload = self._read_json()
        messages = payload.get("messages") or []
        max_tokens = int(payload.get("max_tokens") or 1024)
        text = ENGINE.reply(messages, max_tokens)
        model = payload.get("model") or self.server.model_id  # type: ignore[attr-defined]

        if not payload.get("stream"):
            self._send_json({
                "id": f"chatcmpl-stub-{int(time.time() * 1000)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": len(text),
                          "total_tokens": len(text)},
            })
            return

        self._stream(text, model, chat=True)

    def _completion(self) -> None:
        payload = self._read_json()
        prompt = str(payload.get("prompt") or "")
        messages = [{"role": "user", "content": prompt}]
        text = ENGINE.reply(messages, int(payload.get("max_tokens") or 1024))
        model = payload.get("model") or self.server.model_id  # type: ignore[attr-defined]

        if not payload.get("stream"):
            self._send_json({
                "id": f"cmpl-stub-{int(time.time() * 1000)}",
                "object": "text_completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "text": text, "finish_reason": "stop"}],
            })
            return

        self._stream(text, model, chat=False)

    def _stream(self, text: str, model: str, *, chat: bool) -> None:
        """Emit SSE deltas shaped like llama-server / vLLM output."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        completion_id = f"{'chatcmpl' if chat else 'cmpl'}-stub-{int(time.time() * 1000)}"
        created = int(time.time())
        chunk_size = 4
        try:
            for start in range(0, len(text), chunk_size):
                piece = text[start:start + chunk_size]
                if chat:
                    delta = {"content": piece}
                    choice = {"index": 0, "delta": delta, "finish_reason": None}
                    frame = {"id": completion_id, "object": "chat.completion.chunk",
                             "created": created, "model": model, "choices": [choice]}
                else:
                    choice = {"index": 0, "text": piece, "finish_reason": None}
                    frame = {"id": completion_id, "object": "text_completion",
                             "created": created, "model": model, "choices": [choice]}
                self.wfile.write(
                    f"data: {json.dumps(frame, ensure_ascii=False)}\n\n".encode("utf-8")
                )
                self.wfile.flush()
                # A tiny pause keeps the delivery ledger's incremental path
                # exercised instead of collapsing into one giant chunk.
                time.sleep(0.012)
            if chat:
                final = {"id": completion_id, "object": "chat.completion.chunk",
                         "created": created, "model": model,
                         "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            else:
                final = {"id": completion_id, "object": "text_completion",
                         "created": created, "model": model,
                         "choices": [{"index": 0, "text": "", "finish_reason": "stop"}]}
            self.wfile.write(
                f"data: {json.dumps(final, ensure_ascii=False)}\n\n".encode("utf-8")
            )
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # The application cancels generations by closing the stream; that is
            # normal operation, not an error worth reporting.
            pass


def is_running(port: int = DEFAULT_PORT, host: str = "127.0.0.1") -> str | None:
    """Return the served model id when a stub already answers on `port`."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{host}:{port}/v1/models", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    for item in (payload.get("data") or []) if isinstance(payload, dict) else []:
        if isinstance(item, dict) and item.get("id"):
            return str(item["id"])
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline OpenAI-compatible rehearsal server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--check", action="store_true", help="probe an existing instance")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.check:
        model = is_running(args.port, args.host)
        if model:
            print(f"up: http://{args.host}:{args.port}/v1 model={model}")
            return 0
        print("down")
        return 1

    if is_running(args.port, args.host):
        print(f"[stub] already running on :{args.port}")
        return 0

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    server.model_id = args.model           # type: ignore[attr-defined]
    server.verbose = args.verbose          # type: ignore[attr-defined]

    banner = (
        "=" * 62 + "\n"
        "  OFFLINE REHEARSAL MODEL SERVER  --  not real inference\n"
        f"  endpoint : http://{args.host}:{args.port}/v1\n"
        f"  model id : {args.model}\n"
        "  Use for UI / pipeline rehearsal only. Never for real interviews.\n"
        + "=" * 62
    )
    print(banner, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[stub] stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
