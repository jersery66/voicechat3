"""Local decision service: one independent process, one loopback port.

The service exists so the main application never imports torch.  It loads a
checkpoint from disk, exposes ``POST /v1/decide`` with the same payload shape as
``python -m anyjev.serve``, and refuses to bind until the checkpoint passed
:mod:`decision.integrity`.
"""

from decision.service.engine import DecisionEngine, EngineUnavailable
from decision.service.server import DecisionService

__all__ = ["DecisionEngine", "DecisionService", "EngineUnavailable"]
