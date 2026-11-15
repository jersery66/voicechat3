# Leisure game source and attribution record

This file records the only upstream mechanics reviewed for Relaxation Center
V1.  The games run as native Python/PySide6 widgets; no browser, Phaser,
pygame, or upstream asset runtime is shipped.

## Bubble Pop

- Repository: <https://github.com/sausi-7/games>
- Pinned review commit: `c97ef8bec4a4ce3154b4345a79aeda3ea2a6a465`
- Upstream files reviewed: `games/casual/bubble-pop/mechanics.js`,
  `games/casual/bubble-pop/config.json`
- License: MIT, Copyright (c) 2026 Saurabh Singh
- Reuse type: **ADAPTED / PORTED MECHANICS**
- Code reused: bubble spawning, bounded motion, pointer hit-testing and
  cleanup concepts only; the Python implementation is a local rewrite.
- Assets reused: **NO**.  No upstream images, fonts, audio, HTML, JavaScript,
  or Phaser runtime are copied.

The MIT notice retained for this adapted portion is:

```text
MIT License

Copyright (c) 2026 Saurabh Singh

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The port intentionally removes score, countdown pressure, lives, levels,
difficulty, BGM, rewards, assets, and game-over states.

## PyJig review

- Repository: <https://github.com/tomdeabreucodes/PyJig>
- License observed upstream: MIT
- Reuse type: **NOT USED**
- Reason: Calm Puzzle V1 is self-implemented with deterministic local pieces
  and Qt painting.  No PyJig code or assets are copied and no PyJig runtime
  dependency is added.

## Other V1 activities

- Gentle Search: **NATIVE**, deterministic local implementation; no upstream
  source or assets.
- Falling Leaves: **NATIVE**, deterministic local implementation; no upstream
  source or assets.

## Dependency and scope statement

- New runtime dependencies: **NONE**
- Browser/WebView/Phaser runtime: **NOT USED**
- `pygame`: **NOT USED by the new games**
- Upstream files vendored: **NONE**
- Participant/session/assessment data written by these games: **NONE**
- Activity authority: the existing `RelaxationRuntime` lifecycle only; these
  widgets do not recommend, score, diagnose, or mutate SessionEngine,
  TurnPolicy, ScaleRuntime, or Agent state.
