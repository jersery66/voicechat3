"""Stable deployment/runtime error categories and codes.

Classification is deliberately separate from recovery.  This module returns
machine-readable evidence only; callers retain their existing behavior.
"""

from __future__ import annotations

from typing import Any

ERROR_CATEGORIES = {
    "DEPLOYMENT",
    "PROFILE",
    "PROCESS",
    "PORT",
    "MODEL_IDENTITY",
    "WSL",
    "GPU",
    "CUDA",
    "VLLM",
    "STT",
    "VAD",
    "POLICY",
    "SCALE",
    "SESSION",
    "RAG",
    "DIALOGUE",
    "DELIVERY",
    "TTS",
    "AUDIO",
    "UI",
    "REPORT",
    "UNKNOWN",
}

ERROR_CODES = {
    "MODEL_IDENTITY_MISMATCH": "MODEL_IDENTITY",
    "ENDPOINT_UNAVAILABLE": "DEPLOYMENT",
    "UNKNOWN_PORT_OWNER": "PORT",
    "PROCESS_OWNERSHIP_MISMATCH": "PROCESS",
    "LLM_TIMEOUT": "DIALOGUE",
    "LLM_EMPTY_RESPONSE": "DIALOGUE",
    "LLM_STREAM_INTERRUPTED": "DIALOGUE",
    "STT_PROVIDER_ERROR": "STT",
    "VAD_PROVIDER_ERROR": "VAD",
    "TTS_PROVIDER_ERROR": "TTS",
    "AUDIO_DEVICE_UNAVAILABLE": "AUDIO",
    "DELIVERY_CANCELLED": "DELIVERY",
    "STALE_GENERATION_DROPPED": "DELIVERY",
    "PROFILE_INVALID": "PROFILE",
    "WSL_UNAVAILABLE": "WSL",
    "GPU_UNAVAILABLE": "GPU",
    "CUDA_UNAVAILABLE": "CUDA",
    "VLLM_START_FAILED": "VLLM",
    "POLICY_ERROR": "POLICY",
    "SCALE_RUNTIME_ERROR": "SCALE",
    "SESSION_RUNTIME_ERROR": "SESSION",
    "RAG_PROVIDER_ERROR": "RAG",
    "UI_RUNTIME_ERROR": "UI",
    "REPORT_PERSISTENCE_ERROR": "REPORT",
}


def error_entry(
    code: str,
    *,
    component: str | None = None,
    status: str = "FAILED",
    detail: str | None = None,
) -> dict[str, Any]:
    if code not in ERROR_CODES:
        raise ValueError(f"unknown error code: {code}")
    entry: dict[str, Any] = {
        "category": ERROR_CODES[code],
        "code": code,
        "component": component,
        "status": status,
    }
    if detail is not None:
        # Keep diagnostic details deliberately operator-oriented; callers must
        # not pass participant text or hidden reasoning here.
        entry["detail"] = str(detail)
    return entry


def classify_error(code: str, *, detail: str | None = None, component: str | None = None) -> dict[str, Any]:
    return error_entry(code, component=component, detail=detail)
