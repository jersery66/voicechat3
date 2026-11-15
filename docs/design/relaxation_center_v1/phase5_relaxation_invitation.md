# Phase 5 — Relaxation invitation integration

Phase 5 keeps content selection outside the Agent and TurnPolicy:

```text
Agent observes an opportunity
        ↓
TurnPolicy authorizes one invitation
        ↓
Dialogue offers the Relaxation Center
        ↓
participant chooses whether and what to open
```

## Decision semantics

`RECOMMEND_RELAXATION` means an invitation to enter the Center.  A proactive
Router proposal or deterministic candidate always carries
`intervention_type=None`; it cannot select breathing, muscle relaxation,
meditation, a video, or a game.  A generic explicit request also carries no
type.  Only a deterministic signal extracted from an explicit user phrase
preserves a canonical core preference (`breathing`, `muscle`, or
`meditation`), and that preference is used only to highlight a Center card.

`RECOMMEND_GAME` remains in the compatibility vocabulary but is accepted only
for an explicit user leisure request.  Its execution opens the Center Games
page; it never calls the legacy game service and never selects a concrete game.

The proactive offer remains bounded to one per session.  The offered marker
and the completed core-relaxation fact remain separate.  An active scale keeps
priority over a proactive offer, while an explicit user request may still ask
to pause/leave the scale under the existing policy boundary.

## Runtime boundary

No RelaxationRuntime or game mechanics were changed in this phase.  The
invitation path does not start media or mutate `RelaxationRuntime`; only a
subsequent user action in the Center can do so.  The Phase 4.1 leisure
SessionEngine contract remains unchanged.

The stale core `current_relaxation_type` is cleared only when
`POST_RELAXATION → CHATTING` is explicitly completed, so the completed type
remains available while the post-relaxation choice is shown.

## Validation boundary

Tests cover generic and typed explicit requests, proactive type suppression,
explicit-game gating, one-off offer eligibility, active-scale precedence,
Agent fallback conservatism, Center-only execution, and the stale-type
sequence.  This phase does not add games, scale pause/resume mechanics,
hardware behavior, or STT/TTS changes.
