# Relaxation Center V1 — Phase 4 native leisure games

Phase 4 adds four small, local leisure interactions to the Center:

- 泡泡 (`bubble_pop`)
- 找一找 (`gentle_search`)
- 轻拼图 (`calm_puzzle`)
- 接住落叶 (`falling_leaves`)

They are native PySide6 widgets backed by deterministic, testable Python
models.  A participant can choose a game from the Center and end it at any
time.  Completion/exit returns through the existing `RelaxationRuntime`; the
games do not call the Agent, TurnPolicy, ScaleRuntime, SessionEngine, RAG, or
legacy `game_service`.

The interactions are intentionally non-competitive.  They do not expose or
persist scores, lives, countdown pressure, levels, difficulty, rewards,
achievements, or game-over states.  A missed falling leaf is simply removed;
an incorrect visual-search click simply leaves the trial available.

## Source and licensing boundary

Bubble motion and pointer hit-testing were adapted from the MIT-licensed
`sausi-7/games` bubble mechanics at pinned commit
`c97ef8bec4a4ce3154b4345a79aeda3ea2a6a465`.  Only mechanics were ported into
Python; no JavaScript, Phaser runtime, fonts, images, or audio were copied.
Calm Puzzle, Gentle Search, and Falling Leaves are local implementations.
The complete attribution record and MIT notice are in
`docs/third_party/leisure_game_sources.md` and `THIRD_PARTY_NOTICES.md`.

## Validation boundary

Phase 4 deterministic tests cover motion, hit-testing, trial progression,
piece placement, falling-leaf cleanup, widget construction, catalog
availability, Center selection, and runtime completion.  This is an offline
software contract only.  Real GPU, VoxCPM2 coexistence, audio devices, and
participant-facing hardware acceptance remain outside this phase.
