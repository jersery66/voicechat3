# adapters — infrastructure adapters behind stable interfaces.
#
# Phase-2 stage 3 of the refactor. The orchestration layer (app/) depends
# ONLY on the Protocols in adapters.protocols, never on concrete services.
# Production wiring goes through adapters.factory; tests inject fakes from
# tests/integration/fakes.py, which conform to the same Protocols.
#
# Benefits:
#   - TTS/STT backend switching becomes configuration, not source edits
#   - engine/SessionEngine becomes testable without GPU/audio/Ollama
#   - Phase-3 process split reuses the same boundary (protocol -> protocol)
