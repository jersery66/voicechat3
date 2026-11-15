"""Transformers-backed scorer.

Loaded lazily and only from a local directory: ``local_files_only=True`` plus a
path check means a typo in ``model_path`` raises immediately instead of silently
reaching HuggingFace.  Nothing in this module is imported unless a real model is
being served, so the rest of the decision layer keeps working (and keeps being
testable) on a machine without torch.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from decision.engine.readout import ReadoutError

_DTYPE_ALIASES = {
    "auto": None,
    "bfloat16": "bfloat16",
    "bf16": "bfloat16",
    "float16": "float16",
    "fp16": "float16",
    "float32": "float32",
    "fp32": "float32",
}


def _resolve_dtype(name: str, torch_module: Any) -> Any:
    key = str(name or "bfloat16").strip().lower()
    if key not in _DTYPE_ALIASES:
        raise ReadoutError(f"unsupported model dtype {name!r}")
    alias = _DTYPE_ALIASES[key]
    if alias is None:
        return None
    if not hasattr(torch_module, alias):
        raise ReadoutError(f"torch has no dtype {alias!r}")
    return getattr(torch_module, alias)


@dataclass
class TransformersScorer:
    """Scores label continuations with a local causal LM.

    Parameters
    ----------
    model_path:
        Absolute path to a directory containing a HuggingFace checkpoint.
    dtype / device:
        ``device="auto"`` uses CUDA when available.
    max_length:
        Hard cap on prompt+continuation tokens; exceeding it raises rather than
        truncating an interview turn into nonsense.
    """

    model_path: str
    dtype: str = "bfloat16"
    device: str = "auto"
    max_length: int = 2048
    trust_remote_code: bool = False

    _model: Any = field(default=None, init=False, repr=False)
    _tokenizer: Any = field(default=None, init=False, repr=False)
    _torch: Any = field(default=None, init=False, repr=False)
    _device: str = field(default="cpu", init=False)
    _labels: dict[str, tuple[int, ...]] = field(default_factory=dict, init=False)
    _load_seconds: float = field(default=0.0, init=False)
    _forwards: int = field(default=0, init=False)

    # -- lifecycle --------------------------------------------------------

    def load(self) -> "TransformersScorer":
        root = Path(self.model_path).expanduser()
        if not root.is_dir():
            raise ReadoutError(f"model_path is not a directory: {root}")

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ReadoutError(
                f"transformers backend unavailable ({exc}); install the offline wheel set"
            ) from exc

        started = time.perf_counter()
        torch_dtype = _resolve_dtype(self.dtype, torch)
        if self.device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            device = self.device
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise ReadoutError("device requested CUDA but torch.cuda.is_available() is False")

        # Offline is enforced, not assumed: no hub lookup may happen here.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

        try:
            tokenizer = AutoTokenizer.from_pretrained(
                str(root), local_files_only=True, trust_remote_code=self.trust_remote_code
            )
            model = AutoModelForCausalLM.from_pretrained(
                str(root),
                local_files_only=True,
                trust_remote_code=self.trust_remote_code,
                torch_dtype=torch_dtype,
            )
        except (OSError, ValueError) as exc:
            raise ReadoutError(f"failed to load local checkpoint {root}: {exc}") from exc

        model.eval()
        model.to(device)
        if getattr(model.config, "pad_token_id", None) is None and tokenizer.pad_token_id is not None:
            model.config.pad_token_id = tokenizer.pad_token_id

        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._device = device
        self._load_seconds = time.perf_counter() - started
        if device.startswith("cuda"):
            torch.cuda.reset_peak_memory_stats()
        return self

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure(self) -> None:
        if not self.loaded:
            self.load()

    # -- Scorer protocol --------------------------------------------------

    def label_tokens(self, label: str) -> tuple[int, ...]:
        self._ensure()
        key = label.strip()
        if key in self._labels:
            return self._labels[key]
        tokenizer = self._tokenizer
        best: Optional[tuple[int, ...]] = None
        for form in (f" {key}", key, f" {key.lower()}", key.lower()):
            ids = tokenizer.encode(form, add_special_tokens=False)
            if len(ids) == 1:
                best = tuple(ids)
                break
        if best is None:
            best = tuple(tokenizer.encode(key, add_special_tokens=False))
        if not best:
            raise ReadoutError(f"label {label!r} tokenises to nothing")
        self._labels[key] = best
        return best

    def token_logprobs(self, prompt: str, token_ids: Sequence[int]) -> list[float]:
        self._ensure()
        torch = self._torch
        ids = self._encode(prompt)
        if not ids:
            raise ReadoutError("prompt tokenises to nothing")
        if len(ids) > self.max_length:
            raise ReadoutError(
                f"prompt is {len(ids)} tokens, exceeds max_length={self.max_length}"
            )
        input_ids = torch.tensor([ids], device=self._device)
        with torch.inference_mode():
            logits = self._model(input_ids=input_ids).logits[0, -1, :]
            logprobs = torch.log_softmax(logits.float(), dim=-1)
            picked = logprobs[torch.tensor(list(token_ids), device=self._device)]
        self._forwards += 1
        return [float(value) for value in picked.tolist()]

    def sequence_logprobs(self, prompt: str, continuations: Sequence[str]) -> list[float]:
        self._ensure()
        torch = self._torch
        prompt_ids = self._encode(prompt)
        if not prompt_ids:
            raise ReadoutError("prompt tokenises to nothing")

        sequences: list[list[int]] = []
        spans: list[tuple[int, int]] = []
        for text in continuations:
            cont_ids = self._encode(text, add_special_tokens=False)
            if not cont_ids:
                raise ReadoutError(f"continuation {text!r} tokenises to nothing")
            full = prompt_ids + cont_ids
            if len(full) > self.max_length:
                raise ReadoutError(
                    f"prompt+continuation is {len(full)} tokens, exceeds max_length={self.max_length}"
                )
            sequences.append(full)
            spans.append((len(prompt_ids), len(full)))

        pad_id = self._tokenizer.pad_token_id
        if pad_id is None:
            pad_id = self._tokenizer.eos_token_id or 0
        width = max(len(seq) for seq in sequences)
        batch = []
        masks = []
        pads = []
        for seq in sequences:
            pad = width - len(seq)
            pads.append(pad)
            batch.append([pad_id] * pad + seq)          # left padding
            masks.append([0] * pad + [1] * len(seq))
        input_ids = torch.tensor(batch, device=self._device)
        attention = torch.tensor(masks, device=self._device)

        with torch.inference_mode():
            logits = self._model(input_ids=input_ids, attention_mask=attention).logits
            logprobs = torch.log_softmax(logits.float(), dim=-1)

        scores: list[float] = []
        for row, (start, end) in enumerate(spans):
            # ``start``/``end`` index the unpadded sequence; shift by the pad so
            # both the scored token and its predicting position line up.
            shift = pads[row]
            total = 0.0
            for pos in range(start, end):
                token = batch[row][shift + pos]
                total += float(logprobs[row, shift + pos - 1, token])
            scores.append(total)
        self._forwards += 1
        return scores

    # -- helpers ----------------------------------------------------------

    def _encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
        self._ensure()
        return list(
            self._tokenizer.encode(text, add_special_tokens=add_special_tokens)
        )

    def vram(self) -> dict[str, Any]:
        """Peak and current device memory for this process."""
        if not self.loaded or self._device == "cpu":
            return {"device": self._device, "available": False}
        torch = self._torch
        free, total = torch.cuda.mem_get_info()
        return {
            "device": self._device,
            "device_name": torch.cuda.get_device_name(0),
            "available": True,
            "allocated_peak_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1),
            "reserved_peak_mib": round(torch.cuda.max_memory_reserved() / 2**20, 1),
            "total_mib": round(total / 2**20, 1),
            "free_mib": round(free / 2**20, 1),
        }

    def reset_peak(self) -> None:
        if self.loaded and self._device.startswith("cuda"):
            self._torch.cuda.reset_peak_memory_stats()

    def describe(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "backend": "transformers",
            "model_path": str(Path(self.model_path).name),  # never the full home path
            "loaded": self.loaded,
            "dtype": self.dtype,
            "device": self._device,
            "forwards": self._forwards,
        }
        if self.loaded:
            model = self._model
            try:
                payload["parameters"] = int(sum(p.numel() for p in model.parameters()))
            except Exception:
                pass
            payload["load_seconds"] = round(self._load_seconds, 2)
            payload["vram"] = self.vram()
        return payload
