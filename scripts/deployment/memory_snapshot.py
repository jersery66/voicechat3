"""Read-only GPU memory snapshot contract.

The collector uses ``nvidia-smi`` when available and never applies a target
VRAM threshold.  A snapshot is descriptive evidence, not hardware approval.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EVIDENCE_MEASURED = "MEASURED"
EVIDENCE_SIMULATED = "SIMULATED"
EVIDENCE_NOT_AVAILABLE = "NOT AVAILABLE"


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _number(value: str) -> int | None:
    normalized = value.strip().replace("[MiB]", "").replace("MiB", "").replace("MB", "").strip()
    if not normalized or normalized.upper() in {"N/A", "NA", "NOT AVAILABLE"}:
        return None
    try:
        return int(float(normalized))
    except ValueError:
        return None


def parse_nvidia_smi_csv(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in csv.reader(line for line in output.splitlines() if line.strip()):
        if len(row) < 5:
            continue
        index = _number(row[0])
        if index is None:
            continue
        rows.append(
            {
                "gpu_index": index,
                "gpu_name": row[1].strip() or None,
                "memory_total_mb": _number(row[2]),
                "memory_used_mb": _number(row[3]),
                "memory_free_mb": _number(row[4]),
            }
        )
    return rows


def _base_snapshot(
    *,
    profile: str | None,
    git_commit: str | None,
    evidence_type: str,
    status: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_type": evidence_type,
        "hardware_evidence": "NOT RUN",
        "git_commit": git_commit if git_commit is not None else _git_commit(),
        "profile": profile,
        "status": status,
        "gpu_index": None,
        "gpu_name": None,
        "memory_total_mb": None,
        "memory_used_mb": None,
        "memory_free_mb": None,
        "processes": None,
        "processes_status": "NOT AVAILABLE",
    }


def _with_row(snapshot: dict[str, Any], row: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("gpu_index", "gpu_name", "memory_total_mb", "memory_used_mb", "memory_free_mb"):
        snapshot[key] = row.get(key)
    return snapshot


def capture_memory_snapshot(
    *,
    profile: str | None = None,
    git_commit: str | None = None,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    command = [
        "nvidia-smi",
        "--query-gpu=index,name,memory.total,memory.used,memory.free",
        "--format=csv,noheader,nounits",
    ]
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return _base_snapshot(
            profile=profile,
            git_commit=git_commit,
            evidence_type=EVIDENCE_NOT_AVAILABLE,
            status="NOT AVAILABLE",
        )
    output = result.stdout or ""
    rows = parse_nvidia_smi_csv(output) if result.returncode == 0 else []
    if result.returncode != 0 or not rows:
        return _base_snapshot(
            profile=profile,
            git_commit=git_commit,
            evidence_type=EVIDENCE_NOT_AVAILABLE,
            status="NOT AVAILABLE",
        )
    snapshot = _base_snapshot(
        profile=profile,
        git_commit=git_commit,
        evidence_type=EVIDENCE_MEASURED,
        status="PASS",
    )
    return _with_row(snapshot, rows[0])


def simulated_memory_snapshot(row: Mapping[str, Any], *, profile: str | None = None) -> dict[str, Any]:
    snapshot = _base_snapshot(
        profile=profile,
        git_commit="SIMULATED",
        evidence_type=EVIDENCE_SIMULATED,
        status="PASS",
    )
    return _with_row(snapshot, row)


def append_snapshot(path: str | Path, snapshot: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(snapshot), ensure_ascii=False) + "\n")
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture a read-only nvidia-smi memory snapshot")
    parser.add_argument("--profile", default=os.environ.get("VOICECHAT_DEPLOYMENT_PROFILE"))
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)
    snapshot = capture_memory_snapshot(profile=args.profile)
    if args.output:
        append_snapshot(args.output, snapshot)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0 if snapshot["status"] in {"PASS", "NOT AVAILABLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
