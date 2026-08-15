"""Generation-scoped text delivery primitives for cancellable sentence TTS.

This module deliberately contains delivery bookkeeping only.  It does not
make a TurnPolicy decision, mutate ScaleRuntime, submit a SessionEngine
command, or decide whether retrieval/media/end actions are appropriate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import queue
import threading
import time
from typing import Any, Callable, Optional

from core.tags import clean_for_display


@dataclass
class GenerationRecord:
    """Mutable delivery record for one assistant output generation."""

    generation_id: int
    cancelled: bool = False
    generated_text: str = ""
    delivered_text: str = ""
    next_sentence_seq: int = 0
    finalized: bool = False
    cancel_reason: str = ""
    cancellation_event: threading.Event = field(
        default_factory=threading.Event,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True)
class SentenceReady:
    """Immutable, business-action-free sentence delivery event."""

    generation_id: int
    seq: int
    text: str
    # ``text`` is the participant-visible normalized sentence.  Prosody
    # markers supported by the TTS adapter are kept separately so they never
    # leak into visible chat/history.
    tts_text: str = ""


@dataclass(frozen=True)
class GenerationStarted:
    generation_id: int


@dataclass(frozen=True)
class TextChunk:
    generation_id: int
    text: str


@dataclass(frozen=True)
class SentenceDelivered:
    generation_id: int
    seq: int
    text: str


@dataclass(frozen=True)
class AudioStarted:
    generation_id: int
    seq: int


@dataclass(frozen=True)
class AudioFinished:
    generation_id: int
    seq: int
    ok: bool


@dataclass(frozen=True)
class GenerationCancelled:
    generation_id: int
    reason: str = ""


@dataclass(frozen=True)
class GenerationFinished:
    generation_id: int
    generated_text: str
    delivered_text: str


class GenerationController:
    """Single allocator and stale-state owner for assistant deliveries."""

    def __init__(
        self,
        *,
        on_cancel: Optional[Callable[[GenerationCancelled], None]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._next_id = 0
        self._current_id: Optional[int] = None
        self._records: dict[int, GenerationRecord] = {}
        self._cancel_listeners: list[Callable[[GenerationCancelled], None]] = []
        if on_cancel is not None:
            self._cancel_listeners.append(on_cancel)

    @property
    def current_generation_id(self) -> Optional[int]:
        with self._lock:
            return self._current_id

    def add_cancel_listener(self, listener: Callable[[GenerationCancelled], None]) -> None:
        with self._lock:
            if listener not in self._cancel_listeners:
                self._cancel_listeners.append(listener)

    def start_generation(self) -> GenerationRecord:
        """Cancel the previous active record and allocate the next ID."""
        previous_event: Optional[GenerationCancelled] = None
        listeners: tuple[Callable[[GenerationCancelled], None], ...] = ()
        with self._lock:
            if self._current_id is not None:
                previous = self._records.get(self._current_id)
                if previous is not None and not previous.cancelled:
                    previous.cancelled = True
                    previous.cancel_reason = "superseded by new generation"
                    previous.cancellation_event.set()
                    previous_event = GenerationCancelled(
                        previous.generation_id, previous.cancel_reason
                    )
                    listeners = tuple(self._cancel_listeners)

            self._next_id += 1
            record = GenerationRecord(self._next_id)
            self._records[record.generation_id] = record
            self._current_id = record.generation_id

        self._notify_cancel(previous_event, listeners)
        return record

    def cancel_generation(self, generation_id: int, *, reason: str = "") -> bool:
        """Cancel a record once; return ``True`` only on the first transition."""
        event: Optional[GenerationCancelled] = None
        listeners: tuple[Callable[[GenerationCancelled], None], ...] = ()
        with self._lock:
            record = self._records.get(generation_id)
            if record is None or record.cancelled:
                return False
            record.cancelled = True
            record.cancel_reason = reason
            record.cancellation_event.set()
            if self._current_id == generation_id:
                self._current_id = None
            event = GenerationCancelled(generation_id, reason)
            listeners = tuple(self._cancel_listeners)

        self._notify_cancel(event, listeners)
        return True

    @staticmethod
    def _notify_cancel(
        event: Optional[GenerationCancelled],
        listeners: tuple[Callable[[GenerationCancelled], None], ...],
    ) -> None:
        if event is None:
            return
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # Cancellation must remain idempotent even if an adapter's
                # best-effort stop hook fails.
                continue

    def is_current(self, generation_id: int) -> bool:
        with self._lock:
            record = self._records.get(generation_id)
            return bool(
                record
                and self._current_id == generation_id
                and not record.cancelled
            )

    def get_record(self, generation_id: int) -> Optional[GenerationRecord]:
        with self._lock:
            return self._records.get(generation_id)


class SentenceSegmenter:
    """Deterministic Chinese sentence/phrase segmenter.

    The timer is observed by callers through :meth:`flush_if_due`; no timer
    thread is created for each model generation.
    """

    _OPEN_TO_CLOSE = {
        "“": "”",
        "‘": "’",
        "「": "」",
        "『": "』",
        "（": "）",
        "(": ")",
        "【": "】",
        "[": "]",
        "《": "》",
        "<": ">",
    }
    _CLOSERS = set(_OPEN_TO_CLOSE.values()) | {'"', "'"}
    _TERMINATORS = set("。！？!?．")
    _SOFT_BREAKS = set("，,、；;：: \t\n")

    def __init__(
        self,
        *,
        max_chars: int = 80,
        max_wait_ms: int = 800,
        min_stable_chars: int = 4,
    ) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        if max_wait_ms < 0:
            raise ValueError("max_wait_ms must be non-negative")
        if min_stable_chars < 1:
            raise ValueError("min_stable_chars must be positive")
        self.max_chars = int(max_chars)
        self.max_wait_ms = int(max_wait_ms)
        self.min_stable_chars = int(min_stable_chars)
        self._buffer = ""
        self._generation_id: Optional[int] = None
        self._next_seq = 0
        self._last_feed_at = time.monotonic()

    @property
    def buffered_text(self) -> str:
        return self._buffer

    def feed(self, generation_id: int, chunk: str) -> list[SentenceReady]:
        """Consume one provider chunk and return newly stable sentences."""
        self._ensure_generation(generation_id)
        if chunk:
            self._buffer += str(chunk)
            self._last_feed_at = time.monotonic()

        events: list[SentenceReady] = []
        while self._buffer:
            boundary = self._find_boundary(self._buffer)
            if boundary is not None:
                events.append(self._emit(generation_id, boundary))
                continue
            if len(self._buffer) < self.max_chars:
                break
            cut = self._find_soft_cut(self._buffer)
            events.append(self._emit(generation_id, cut))
        return events

    def flush_if_due(
        self,
        generation_id: int,
        *,
        now: Optional[float] = None,
    ) -> list[SentenceReady]:
        self._ensure_generation(generation_id)
        if not self._buffer:
            return []
        current = time.monotonic() if now is None else now
        elapsed_ms = (current - self._last_feed_at) * 1000.0
        if elapsed_ms < self.max_wait_ms or len(self._buffer) < self.min_stable_chars:
            return []
        return self.flush(generation_id)

    def flush(self, generation_id: int) -> list[SentenceReady]:
        self._ensure_generation(generation_id)
        if not self._buffer.strip():
            self._buffer = ""
            return []
        return [self._emit(generation_id, len(self._buffer))]

    def _ensure_generation(self, generation_id: int) -> None:
        if self._generation_id is None:
            self._generation_id = generation_id
            return
        if self._generation_id == generation_id:
            return
        # A segmenter is normally one-per-generation.  Resetting a stale
        # buffer is safer than leaking text from a previous assistant turn.
        self._buffer = ""
        self._next_seq = 0
        self._generation_id = generation_id
        self._last_feed_at = time.monotonic()

    def _emit(self, generation_id: int, length: int) -> SentenceReady:
        text = self._buffer[:length].strip()
        self._buffer = self._buffer[length:]
        while self._buffer.startswith((" ", "\t", "\n")):
            self._buffer = self._buffer[1:]
        event = SentenceReady(generation_id, self._next_seq, text)
        self._next_seq += 1
        self._last_feed_at = time.monotonic()
        return event

    @classmethod
    def _find_boundary(cls, text: str) -> Optional[int]:
        stack: list[str] = []
        quote_open = False
        i = 0
        while i < len(text):
            char = text[i]
            if char in cls._OPEN_TO_CLOSE:
                stack.append(cls._OPEN_TO_CLOSE[char])
            elif char in cls._CLOSERS:
                if char in {'"', "'"}:
                    quote_open = not quote_open
                elif stack and stack[-1] == char:
                    stack.pop()

            is_ellipsis = char == "…" and i + 1 < len(text) and text[i + 1] == "…"
            if char in cls._TERMINATORS or is_ellipsis:
                end = cls._boundary_end(text, i, is_ellipsis)
                next_char = text[i + (2 if is_ellipsis else 1):i + (3 if is_ellipsis else 2)]
                closes_quote = next_char and next_char[0] in cls._CLOSERS
                if not stack and not quote_open:
                    return end
                if closes_quote:
                    return end
                if is_ellipsis:
                    i += 1
            i += 1
        return None

    @classmethod
    def _boundary_end(cls, text: str, index: int, is_ellipsis: bool) -> int:
        """Consume repeated terminal punctuation and paired closers."""
        end = index + (2 if is_ellipsis else 1)
        while end < len(text):
            if text.startswith("……", end):
                end += 2
                continue
            if text[end] in cls._TERMINATORS:
                end += 1
                continue
            break
        while end < len(text) and text[end] in cls._CLOSERS:
            end += 1
        return end

    def _find_soft_cut(self, text: str) -> int:
        limit = min(len(text), self.max_chars)
        # ``max_chars`` is applied by the caller; use the full prefix here and
        # choose the last safe pause before the configured limit.
        prefix = text[:limit]
        for index in range(len(prefix) - 1, 0, -1):
            if prefix[index - 1] in self._SOFT_BREAKS:
                return index
        return limit


class DeliveryLedger:
    """Visible-text ledger and exactly-once history finalizer."""

    def __init__(self, controller: GenerationController) -> None:
        self.controller = controller
        self._lock = threading.RLock()
        self._history_committed: set[int] = set()

    def record_generated(self, generation_id: int, text: str) -> bool:
        with self._lock:
            record = self.controller.get_record(generation_id)
            if record is None or record.cancelled or record.finalized:
                return False
            record.generated_text += str(text or "")
            return True

    def commit_visible(self, event: SentenceReady) -> bool:
        with self._lock:
            if not self.controller.is_current(event.generation_id):
                return False
            record = self.controller.get_record(event.generation_id)
            if record is None or record.finalized:
                return False
            if event.seq != record.next_sentence_seq:
                return False
            text = clean_for_display(str(event.text or "")).strip()
            if not text:
                return False
            record.delivered_text += text
            record.next_sentence_seq += 1
            return True

    def finalize(self, generation_id: int) -> str:
        with self._lock:
            record = self.controller.get_record(generation_id)
            if record is None:
                return ""
            record.finalized = True
            return record.delivered_text

    def finalize_history(
        self,
        generation_id: int,
        history_owner: Any = None,
        data_manager: Any = None,
        *,
        sample_rate: int = 48000,
    ) -> str:
        """Finalize delivered history and persistence exactly once.

        ``history_owner`` is expected to expose ``conversation_history`` and
        ``data_manager`` is expected to expose ``save_assistant_message``.
        Both are optional so the pure ledger remains usable in headless tests.
        """
        with self._lock:
            record = self.controller.get_record(generation_id)
            if record is None:
                return ""
            # A cancellation owner may finalize its already-visible prefix
            # while no replacement generation exists.  Once a newer
            # generation is current, a late history/DataManager callback from
            # the cancelled record is stale and must have no side effect.
            current_id = self.controller.current_generation_id
            if generation_id not in self._history_committed:
                if record.cancelled:
                    if current_id is not None and current_id != generation_id:
                        return ""
                elif current_id != generation_id:
                    return ""
            delivered = self.finalize(generation_id)
            if generation_id in self._history_committed:
                return delivered
            self._history_committed.add(generation_id)

            if not delivered:
                return ""

            history = getattr(history_owner, "conversation_history", None)
            if isinstance(history, list):
                if history and history[-1].get("role") == "assistant":
                    if history[-1].get("content") != delivered:
                        history.pop()
                if not history or history[-1].get("content") != delivered:
                    history.append({"role": "assistant", "content": delivered})

            save = getattr(data_manager, "save_assistant_message", None)
            if callable(save):
                try:
                    save(None, delivered, sample_rate=sample_rate)
                except Exception:
                    # Persistence is best-effort; it must not turn a visible
                    # delivery into a duplicate retry or UI failure.
                    pass
            return delivered

    def generated_text(self, generation_id: int) -> str:
        with self._lock:
            record = self.controller.get_record(generation_id)
            return record.generated_text if record else ""

    def delivered_text(self, generation_id: int) -> str:
        with self._lock:
            record = self.controller.get_record(generation_id)
            return record.delivered_text if record else ""


class SentenceDeliveryQueue:
    """Single ordering point for generation-scoped sentence playback."""

    def __init__(
        self,
        controller: GenerationController,
        tts: Any,
        *,
        on_event: Optional[Callable[[Any], None]] = None,
    ) -> None:
        self.controller = controller
        self.tts = tts
        self.on_event = on_event
        self._queue: queue.Queue[Optional[SentenceReady]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._next_seq: dict[int, int] = {}
        self._active: Optional[SentenceReady] = None
        self.controller.add_cancel_listener(self._on_cancel)

    @property
    def worker_count(self) -> int:
        # Exactly one worker is allocated by this queue, even after shutdown.
        return 1

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="sentence-tts-worker",
                daemon=True,
            )
            self._thread.start()

    def enqueue(self, event: SentenceReady) -> bool:
        if not self.controller.is_current(event.generation_id):
            return False
        text = str(event.text or "").strip()
        if not text:
            return False
        with self._lock:
            expected = self._next_seq.get(event.generation_id, 0)
            if event.seq != expected:
                return False
            self._next_seq[event.generation_id] = expected + 1
            self._queue.put(
                SentenceReady(
                    event.generation_id,
                    event.seq,
                    text,
                    event.tts_text or text,
                )
            )
        return True

    def cancel_generation(self, generation_id: int) -> None:
        """Discard queued items and interrupt the currently playing item."""
        retained: list[Optional[SentenceReady]] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None or item.generation_id != generation_id:
                retained.append(item)
            self._queue.task_done()
        for item in retained:
            self._queue.put(item)
        active = self._active
        if active is not None and active.generation_id == generation_id:
            try:
                self.tts.stop_playing()
            except Exception:
                pass

    def shutdown(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self._queue.put(None)
        thread = self._thread
        if thread:
            thread.join(timeout=timeout)
        self._thread = None

    def _on_cancel(self, event: GenerationCancelled) -> None:
        self.cancel_generation(event.generation_id)

    def _emit(self, event: Any) -> None:
        if self.on_event is not None:
            try:
                self.on_event(event)
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if item is None:
                self._queue.task_done()
                break
            self._active = item
            try:
                if not self.controller.is_current(item.generation_id):
                    continue
                self._emit(AudioStarted(item.generation_id, item.seq))
                if not self.controller.is_current(item.generation_id):
                    continue
                ok = False
                try:
                    self.tts.generate_and_play(item.tts_text or item.text)
                    ok = True
                except Exception:
                    ok = False
                if self.controller.is_current(item.generation_id):
                    self._emit(AudioFinished(item.generation_id, item.seq, ok))
            finally:
                self._active = None
                self._queue.task_done()


__all__ = [
    "AudioFinished",
    "AudioStarted",
    "DeliveryLedger",
    "GenerationCancelled",
    "GenerationController",
    "GenerationFinished",
    "GenerationStarted",
    "GenerationRecord",
    "SentenceDelivered",
    "SentenceDeliveryQueue",
    "SentenceReady",
    "SentenceSegmenter",
    "TextChunk",
]
