# 08 — 20–30 turn stability run

Use only synthetic researcher-operated dialogue. Do not use participant
transcripts, recordings, names, or clinical scores.

Exercise the same session through ordinary conversation, symptom accumulation,
scale entry, several answers, refusal/pause, explicit relaxation, return to the
scale, game request, one generation interruption, a new turn, and session end.

Every 5--10 turns record:

- request failures, empty responses, service crashes, and CUDA OOM;
- stale generation/audio callbacks and sentence ordering;
- scale item continuity and pause/resume behavior;
- SessionEngine state and delivered-history consistency;
- GPU VRAM/CPU RAM and process/thread/queue observations.

Do not treat a single successful turn as stability evidence. Preserve all raw
logs and artifacts, and leave the profile unchanged if a blocker appears.
