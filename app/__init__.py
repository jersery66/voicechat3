# app — application orchestration layer.
#
# This package sits between the pure domain logic (core/) and the
# presentation layer (ui/). It owns:
#   - contracts.py : command/event message contracts (single source of truth
#     for what the UI can request and what the engine can emit; the same
#     contract becomes the WebSocket protocol in Phase 3)
#   - engine.py    : SessionEngine, the single-writer facade that owns all
#     mutable session state and orchestrates pipeline/services
#
# Nothing in app/ may import ui/. Adapters (services) are injected.
