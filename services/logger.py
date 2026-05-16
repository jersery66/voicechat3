# Logging setup - unified logger for the project

import logging
import os
import sys
from datetime import datetime


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
