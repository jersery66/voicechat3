# Phase 4.1 — Leisure lifecycle correction

Phase 4.1 keeps the four native games and their licensing boundary unchanged,
but separates their surrounding lifecycle from core relaxation.

## Lifecycle

The new games use `PlayLeisureCommand(content_id)` and
`LeisureFinishedCommand(content_id, completed, cancelled, reason)`.  The
SessionEngine tracks them as active participant-facing media with
`playback_kind="leisure"` and enters the existing `VIDEO_PLAYING` state for
compatibility.  It does not populate `current_relaxation_type`, emit
`POST_RELAXATION`, or emit a `ContinueOrEndAskEvent` for normal leisure
completion.

The UI opens a native game only after the engine emits `LeisureStartedEvent`.
While the game is active, pipeline start is blocked and an end request is
deferred by the existing lifecycle guard.  Completion or participant exit
returns the engine to `CHATTING` (or resumes the deferred end flow).

## Center and reporting

After a game ends, `RelaxationRuntime` remains in `CENTER`; the Games page is
restored so the participant can choose another game or explicitly return to
chat.  Only that explicit Center action transitions `CENTER → RETURNING →
INACTIVE`.

Leisure usage is recorded only in `activity_log` with `type="leisure"`,
`content_role="LEISURE"`, `content_type="GAME"`, completion/cancellation
flags, and timestamp.  It is not written through
`report_service.record_relaxation()` and is not added to the core relaxation
intervention list.  No therapeutic chat message or automatic TTS is emitted
when a leisure game ends.

Legacy `PlayGameCommand`, `PlayRelaxationCommand(relaxation="game")`, and
`services.game_service` remain compatibility-only and are not used by the
four V1 Center games.
