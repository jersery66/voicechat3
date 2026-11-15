"""Jev-style readout: turn a language model's next-token view into a decision.

The point of this package is narrow and deliberate.  A generative Agent asks a
chat model to *write* JSON and hopes the JSON is well formed.  A Jev-style
decision reads the model's *distribution over label continuations* instead, so
the output is a probability vector by construction, not by parsing.

Everything here obeys three rules:

1. The model only ever sees the five whitelisted decision questions.  It is
   never asked for a diagnosis, a scale score, a risk verdict or a session
   ending -- those remain exclusively owned by ``TurnPolicy``.
2. The readout is a *restricted softmax over option labels*.  If the model
   cannot express an option as a continuation, that is an error, not a silent
   default.
3. No network access and no implicit downloads.  Weights come from a local
   directory that passed :mod:`decision.integrity`.
"""

from decision.engine.readout import (
    LabelTokens,
    Readout,
    ReadoutError,
    ReadoutQuestion,
    ReadoutResult,
    softmax,
)

__all__ = [
    "LabelTokens",
    "Readout",
    "ReadoutError",
    "ReadoutQuestion",
    "ReadoutResult",
    "softmax",
]
