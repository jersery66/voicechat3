# Phase 6 — Scale pause/resume and Center context restoration

Phase 6 moves the pause boundary from individual content providers to the
Relaxation Center entry point.

When an active `ScaleRuntime` exists, entering the Center pauses that Runtime
and sends `ScaleProjectionCommand(active=False)` to SessionEngine.  The scale
and accepted answers remain intact; the projection only releases the
participant-facing turn/media slot so core videos and leisure games can start.

Returning explicitly to chat resumes the actual `ScaleRuntime` first
unanswered item and sends the corresponding active projection.  No UI item,
Agent proposal, TurnDecision, or RelaxationRuntime field is used as a resume
authority.  A game finishing restores the Games page but does not resume the
scale until the participant chooses “返回聊天”.  Core relaxation retains its
existing `POST_RELAXATION` choice: ContinueChat resumes; ending the session
does not resume.

Compound active-scale turns are handled deterministically before pause.  A
clear answer followed by “先让我休息一下” is accepted first, then the next
unanswered item is paused.  An ambiguous answer is not scored and the same
item remains after resume.  The answer interpreter accepts a standalone clear
frequency in the context of the current scale question, without guessing
clinical meaning.

`RelaxationReturnContext` is a frozen, session-memory-only hint containing
source, whether a scale was paused, scale name for diagnostics, and one short
existing user-turn anchor.  It is cleared on explicit return, session end, or
new subject; no transcript or clinical data is persisted.
