"""Small same-directory atomic file writers for session persistence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def _atomic_write(path: Path, writer) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def atomic_write_json(path: str | Path, data: Any) -> None:
    """Write JSON through a same-directory temporary file and atomic replace."""
    _atomic_write(
        Path(path),
        lambda handle: json.dump(data, handle, ensure_ascii=False, indent=2),
    )


def atomic_write_text(path: str | Path, text: str) -> None:
    """Write UTF-8 text through a same-directory temporary file."""
    _atomic_write(Path(path), lambda handle: handle.write(text))
