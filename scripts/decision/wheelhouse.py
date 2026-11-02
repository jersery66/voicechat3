#!/usr/bin/env python
"""Seal and verify an offline wheelhouse for the decision service.

``pip download`` gives you files; it does not tell you whether the copy that
arrived on the interview machine is the same copy.  ``seal`` records sha256 for
every wheel plus the platform and interpreter it was built against; ``verify``
re-checks that offline and refuses on any drift.

    # on the networked machine, after pip download -d wheelhouse ...
    python scripts/decision/wheelhouse.py seal   --dir wheelhouse
    # on the offline machine
    python scripts/decision/wheelhouse.py verify --dir wheelhouse

Exit codes: 0 ok / 3 verification failed / 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from decision.integrity import sha256_file  # noqa: E402

MANIFEST_NAME = "wheelhouse_manifest.json"
WHEEL_SUFFIXES = (".whl", ".tar.gz", ".zip")


def _wheels(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_file() and p.name.endswith(WHEEL_SUFFIXES))


def _platform_tag() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "system": platform.system(),
    }


def cmd_seal(args: argparse.Namespace) -> int:
    root = Path(args.dir).expanduser().resolve()
    if not root.is_dir():
        print(f"wheelhouse directory missing: {root}", file=sys.stderr)
        return 2
    wheels = _wheels(root)
    if not wheels:
        print(f"no wheels found in {root}", file=sys.stderr)
        return 2
    entries = [
        {"name": path.name, "size": path.stat().st_size, "sha256": sha256_file(path)}
        for path in wheels
    ]
    manifest = {
        "schema": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        **(_platform_tag() if not args.from_current_env else {}),
        "wheel_count": len(entries),
        "total_bytes": sum(entry["size"] for entry in entries),
        "wheels": entries,
    }
    target = root / MANIFEST_NAME
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"sealed {len(entries)} wheels -> {target}")
    print(f"total_bytes={manifest['total_bytes']}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.dir).expanduser().resolve()
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"missing manifest: {manifest_path} (run 'seal' where the wheels were fetched)",
              file=sys.stderr)
        return 3
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    checked = 0
    for entry in manifest.get("wheels") or []:
        name = entry.get("name")
        if not name:
            problems.append("manifest_entry_without_name")
            continue
        path = root / name
        checked += 1
        if not path.is_file():
            problems.append(f"missing:{name}")
            continue
        size = path.stat().st_size
        if entry.get("size") is not None and size != entry["size"]:
            problems.append(f"size_mismatch:{name}")
            continue
        if args.hash:
            digest = sha256_file(path)
            if digest != entry.get("sha256"):
                problems.append(f"sha256_mismatch:{name}")

    declared = {entry.get("name") for entry in manifest.get("wheels") or []}
    for path in _wheels(root):
        if path.name not in declared:
            problems.append(f"unexpected:{path.name}")

    host = _platform_tag()
    recorded_python = manifest.get("python")
    if recorded_python and recorded_python != host["python"] and not args.allow_python_drift:
        problems.append(f"python_version_drift:{recorded_python}!={host['python']}")

    if problems:
        print(f"wheelhouse verification FAILED ({len(problems)} problems)", file=sys.stderr)
        for problem in problems[:25]:
            print(f"  - {problem}", file=sys.stderr)
        return 3

    print(
        f"wheelhouse ok: {checked} wheels, "
        f"{manifest.get('total_bytes', 0) / 2**20:.1f} MiB, hashes={'checked' if args.hash else 'skipped'}"
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Seal/verify the offline wheelhouse")
    sub = parser.add_subparsers(dest="command", required=True)

    seal = sub.add_parser("seal", help="record sha256 for every wheel")
    seal.add_argument("--dir", required=True)
    seal.add_argument("--from-current-env", action="store_true",
                      help="omit platform metadata (e.g. sealing on a different host)")
    seal.set_defaults(handler=cmd_seal)

    verify = sub.add_parser("verify", help="re-check a wheelhouse offline")
    verify.add_argument("--dir", required=True)
    verify.add_argument("--no-hash", dest="hash", action="store_false", default=True)
    verify.add_argument("--allow-python-drift", action="store_true")
    verify.set_defaults(handler=cmd_verify)

    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
