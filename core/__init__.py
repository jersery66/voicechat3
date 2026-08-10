# core — pure domain logic for the voicechat counseling engine.
#
# Rules for this package:
#   1. NO third-party imports (no Qt, no ollama, no torch, no pygame).
#   2. NO imports from services/ or ui/ (no circular deps, no I/O).
#   3. Everything here must be unit-testable in milliseconds with zero setup.
#
# This package is the foundation of the modular refactor: adapters and the
# session engine (app/) build on top of it; the UI never contains copies of
# this logic.
