#!/usr/bin/env python
"""Verify (or create) the integrity manifest for an offline decision checkpoint.

Run this on the machine that produced the copy, then again on the interview
machine before the service is allowed to start.

    # after copying Tacit-1.7B onto the machine, once, with full hashes
    python scripts/decision/tacit_model_check.py --model-path D:/models/Tacit-1.7B --write

    # later, before serving (exit code 0 only when everything matches)
    python scripts/decision/tacit_model_check.py --model-path D:/models/Tacit-1.7B

    # fast structural check without hashing (no manifest needed)
    python scripts/decision/tacit_model_check.py --model-path D:/models/Tacit-1.7B --structural-only

Exit codes: 0 ok / 3 integrity failure / 2 bad arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from decision.integrity import (  # noqa: E402
    MANIFEST_NAME,
    ModelIntegrityError,
    build_manifest,
    structural_problems,
    summarise,
    verify_model_dir,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-path", required=True, help="local checkpoint directory")
    parser.add_argument("--manifest", help=f"manifest path (default: <model>/{MANIFEST_NAME})")
    parser.add_argument("--write", action="store_true", help="create/overwrite the manifest")
    parser.add_argument("--model-id", help="label recorded in the manifest (e.g. morriszjm/Tacit-1.7B)")
    parser.add_argument("--no-hashes", action="store_true", help="size-only manifest (weaker)")
    parser.add_argument("--max-hash-bytes", type=int, default=None,
                        help="skip hashing files larger than this (size-only entries)")
    parser.add_argument("--structural-only", action="store_true",
                        help="only check that the checkpoint is structurally loadable")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.model_path).expanduser()
    if not root.is_dir():
        print(f"model directory does not exist: {root}", file=sys.stderr)
        return 3

    if args.structural_only:
        problems = structural_problems(root)
        payload = {"model_dir": str(root), "ok": not problems, "problems": problems}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"structural check {'ok' if not problems else 'FAILED'}: {root}")
            for problem in problems:
                print(f"  - {problem}")
        return 0 if not problems else 3

    if args.write:
        try:
            manifest = build_manifest(
                root,
                include_hashes=not args.no_hashes,
                max_hash_bytes=args.max_hash_bytes,
                model_id=args.model_id,
            )
        except ModelIntegrityError as exc:
            print(f"cannot build manifest: {exc}", file=sys.stderr)
            return 3
        target = Path(args.manifest) if args.manifest else root / MANIFEST_NAME
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"manifest written: {target}\n"
            f"  files={manifest['file_count']} hashed={manifest['hashed_count']} "
            f"total_bytes={manifest['total_bytes']}"
        )
        return 0

    report = verify_model_dir(
        root,
        args.manifest,
        check_hashes=not args.no_hashes,
        require_manifest=True,
        extra_files_ok=True,
    )
    if args.json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(summarise(report))
        for problem in report.problems[:25]:
            print(f"  - {problem}")
        if not report.ok and not report.manifest_found:
            print(
                "hint: run with --write once on a trusted copy, then re-run this check",
                file=sys.stderr,
            )
    return 0 if report.ok else 3


if __name__ == "__main__":
    sys.exit(main())
