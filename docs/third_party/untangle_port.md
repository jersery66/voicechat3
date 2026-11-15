# Untangle V2-A port record

## Upstream

- Project: Simon Tatham’s Portable Puzzle Collection
- Source: <https://git.tartarus.org/simon/puzzles.git>
- Pinned revision: `3c3632259d298ab62aafa8a5858823569ab1af46`
- Upstream file reviewed: `untangle.c`
- Official documentation: <https://www.chiark.greenend.org.uk/~sgtatham/puzzles/doc/untangle.html>
- License: MIT

## Reuse classification

`PORTED / ADAPTED`.

The candidate reimplements the following mechanics in Python:

- graph represented by point IDs and undirected edges;
- segment crossing semantics with shared graph endpoints excluded;
- planar target graph plus scrambled vertex positions;
- node-count presets 6/10/15;
- crossing feedback and completion when no edge pair crosses.

The port does not embed or compile the upstream C backend and does not copy
the upstream front ends, icons, screenshots, fonts, or other assets. It uses
native PySide6 painting and a Qt-free deterministic model.

## Product adaptations

- difficulty names are `EASY`, `NORMAL`, and `CHALLENGE`;
- no countdown, score, ranking, reward, leaderboard, or competitive result;
- undo, reset, new puzzle, and explicit close are local play controls;
- crossing count is puzzle-state feedback, not a participant score;
- no Agent, TurnPolicy, ScaleRuntime, SessionEngine, RelaxationRuntime, report,
  or Catalog integration is present in V2-A.

## Attribution notice

```text
This software is copyright (c) 2004-2024 Simon Tatham.

Portions copyright Richard Boulton, James Harvey, Mike Pinna, Jonas
Kölker, Dariusz Olszewski, Michael Schierl, Lambros Lambrou, Bernd
Schmidt, Steffen Bauer, Lennard Sprong, Rogier Goossens, Michael
Quevillon, Asher Gordon, Didi Kohen and Ben Harris.

Permission is hereby granted, free of charge, to any person
obtaining a copy of this software and associated documentation files
(the "Software"), to deal in the Software without restriction,
including without limitation the rights to use, copy, modify, merge,
publish, distribute, sublicense, and/or sell copies of the Software,
and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be
included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
