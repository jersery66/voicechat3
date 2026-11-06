"""Wire providers from ``RuntimeConfig``.

Failure policy: building a decision backend is *never* allowed to stop the
application.  If a Jev/Tacit gateway or checkpoint is missing, the coordinator
is still constructed with an unavailable primary provider; every turn then
escalates through the normal fallback path.  That satisfies the requirement that
a missing decision service must not block ordinary conversation.
"""

from __future__ import annotations

from typing import Any, Optional

from decision.contracts import (
    DecisionBackend,
    DecisionMode,
    EscalationMode,
    RawDecision,
    DecisionRequest,
)
from decision.coordinator import DecisionCoordinator
from decision.journal import build_journal
from decision.providers.base import DecisionBackendUnavailable
from decision.providers.fake import FakeDecisionProvider
from decision.providers.legacy import LegacyDecisionProvider
from decision.validator import DecisionValidator

from services.logger import get_logger

logger = get_logger(__name__)


class UnavailableProvider:
    """A provider that always fails, used when a backend cannot be built."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason

    def is_available(self) -> bool:
        return False

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "available": False, "reason": self.reason}

    def decide(self, request: DecisionRequest) -> RawDecision:  # noqa: ARG002
        raise DecisionBackendUnavailable(self.reason)


def _build_backend(name: str, decision_config: Any) -> Any:
    """Construct one provider, translating hard failures into a placeholder."""
    kind = str(name or "").strip().lower()
    try:
        if kind == DecisionBackend.ANYJEV_DECIDER.value:
            from decision.providers.anyjev import AnyJevDecisionProvider

            return AnyJevDecisionProvider(
                transport=str(getattr(decision_config, "transport", "server") or "server"),
                endpoint=getattr(decision_config, "endpoint", None),
                model=getattr(decision_config, "model", None),
                timeout=getattr(decision_config, "timeout", 5.0),
                allow_remote_endpoint=getattr(decision_config, "allow_remote_endpoint", False),
                allow_model_download=getattr(decision_config, "allow_model_download", False),
                batch=getattr(decision_config, "batch", True),
            )
        if kind == DecisionBackend.TACIT.value:
            from decision.providers.tacit import TacitDecisionProvider

            model_ref = getattr(decision_config, "model", None)
            if not model_ref:
                # ``model`` names a served model for the server transport;
                # inprocess inference needs the checkpoint directory from
                # ``model_path`` instead (offline configs keep model: null
                # precisely because no server is started).
                if str(getattr(decision_config, "transport", "") or "") == "inprocess":
                    model_ref = getattr(decision_config, "model_path", None)

            return TacitDecisionProvider(
                transport=str(getattr(decision_config, "transport", "server") or "server"),
                endpoint=getattr(decision_config, "endpoint", None),
                model=model_ref,
                timeout=getattr(decision_config, "timeout", 5.0),
                allow_remote_endpoint=getattr(decision_config, "allow_remote_endpoint", False),
                allow_model_download=getattr(decision_config, "allow_model_download", False),
                adaptive=getattr(decision_config, "adaptive", False),
                tau=getattr(decision_config, "tau", 0.5),
                max_cot_share=getattr(decision_config, "max_cot_share", 0.2),
            )
        if kind == DecisionBackend.FAKE.value:
            return FakeDecisionProvider()
    except Exception as exc:
        logger.warning("[Decision] backend %s unavailable: %s", kind, type(exc).__name__)
        return UnavailableProvider(kind, f"{type(exc).__name__}: {exc}")
    return UnavailableProvider(kind or "unknown", f"unsupported decision backend: {kind!r}")


def build_decision_coordinator(
    *,
    decision_config: Any,
    agent: Any = None,
    journal_path: Optional[str] = None,
) -> DecisionCoordinator:
    """Build the coordinator described by ``decision_config``."""
    mode = DecisionMode(str(getattr(decision_config, "mode", "legacy") or "legacy"))
    backend_name = str(getattr(decision_config, "backend", "legacy_qwen") or "legacy_qwen")
    escalation = EscalationMode(str(getattr(decision_config, "escalation_mode", "legacy") or "legacy"))

    legacy_provider = LegacyDecisionProvider(agent) if agent is not None else None

    if mode is DecisionMode.LEGACY:
        primary: Any = legacy_provider or UnavailableProvider(
            "legacy_qwen", "no agent configured"
        )
    else:
        primary = _build_backend(backend_name, decision_config)

    shadow_provider = None
    if mode is DecisionMode.SHADOW:
        shadow_backend = str(getattr(decision_config, "shadow_backend", "") or "").strip()
        shadow_provider = _build_backend(shadow_backend, decision_config)

    validator = DecisionValidator(
        abstain_threshold=float(getattr(decision_config, "abstain_threshold", 0.55)),
        require_probabilities=(mode is DecisionMode.JEV),
    )

    journal = build_journal(journal_path or getattr(decision_config, "journal_path", None))

    return DecisionCoordinator(
        mode=mode,
        primary=primary,
        validator=validator,
        shadow=shadow_provider,
        legacy=legacy_provider,
        escalation=escalation,
        journal=journal,
    )
