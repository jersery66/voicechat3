# Logging setup - unified logger for the project

import logging
import os
import sys
from datetime import datetime

# Raw participant text (utterances, transcripts, model replies) is sensitive:
# by default it must never reach console or log files.  The helpers below keep
# the operational value of a log line (event type, text length, error class)
# while making raw text strictly opt-in for dedicated research/debug shells.

SENSITIVE_TEXT_LOG_ENV = "VOICECHAT_LOG_SENSITIVE_TEXT"

_TRUTHY = {"1", "true", "yes", "on"}


def sensitive_text_logging_enabled() -> bool:
    """Whether raw sensitive text may be written to logs (opt-in only)."""
    return os.environ.get(SENSITIVE_TEXT_LOG_ENV, "").strip().lower() in _TRUTHY


def text_meta(label: str, value: object, *, limit: int = 200) -> str:
    """Render potentially sensitive text as metadata for a log line.

    The default rendering keeps only the text length.  The truncated raw text
    is emitted exclusively when ``VOICECHAT_LOG_SENSITIVE_TEXT`` is explicitly
    enabled.  Text hashes are deliberately never substituted for the raw
    text: short utterances stay vulnerable to dictionary attacks and
    cross-record linkage even when hashed.
    """
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value
    else:
        text = str(value)
    if sensitive_text_logging_enabled():
        return f"{label}={text[:limit]!r}"
    return f"{label}_len={len(text)}"


def setup_logging(log_dir: str = None, level: int = logging.DEBUG):
    """Configure root logger with console and file handlers.

    Call once at application entry point (main.py).
    """
    if log_dir is None:
        log_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs"
        )

    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(
        log_dir, f"voicechat_{datetime.now().strftime('%Y%m%d')}.log"
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Clear existing handlers (idempotent)
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console: INFO and above
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    root.addHandler(console)

    # File: DEBUG and above
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    logging.info("Logging initialized: %s", log_file)


def get_logger(name: str) -> logging.Logger:
    """Get or create a logger for the given module name."""
    return logging.getLogger(name)
