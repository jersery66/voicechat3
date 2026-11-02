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
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from decision.integrity import (  # noqa: E402
    MANIFEST_NAME,
    ModelIntegrityError,
    build_manifest,
    sha256_file,
    structural_problems,
    summarise,
    verify_model_dir,
)


def _hub_tree(repo: str, endpoint: str, revision: str) -> tuple[dict[str, dict[str, Any]], str]:
    """File metadata straight from the hub: sizes and LFS sha256, no local hashing."""
    base = endpoint.rstrip("/")
    url = f"{base}/api/models/{repo}/tree/{revision}"
    request = urllib.request.Request(url, headers={"User-Agent": "voicechat3-integrity/1"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        entries = json.loads(opener.open(request, timeout=60).read())
    except Exception as exc:
        raise ModelIntegrityError(
            f"cannot read {url}: {type(exc).__name__}: {exc}"
        ) from exc

    out: dict[str, dict[str, Any]] = {}
    for entry in entries if isinstance(entries, list) else []:
        if entry.get("type") != "file":
            continue
        lfs = entry.get("lfs") or {}
        out[str(entry["path"])] = {
            "size": entry.get("size"),
            "sha256": lfs.get("oid"),
        }
    return out, base


def manifest_from_hub(
    model_dir: Path,
    repo: str,
    *,
    endpoint: str = "https://huggingface.co",
    revision: str = "main",
    model_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build a manifest whose weight hashes come from the source, not from us.

    Hugging Face publishes the sha256 of every LFS object as its ``oid``.  Using
    that value means the number in the manifest was produced by the publisher
    before the bytes ever reached this machine, which is what makes the check a
    provenance check instead of a self-consistency check.  Files that are not
    stored in LFS have no published sha256, so their hash is computed here and
    labelled ``local_after_download`` -- weaker, and marked as such.
    """
    tree, base = _hub_tree(repo, endpoint, revision)
    if not tree:
        raise ModelIntegrityError(f"{repo} exposes no files at revision {revision}")

    # `_iter_files` skips dotfiles when it fingerprints, so a hidden hub file
    # (.gitattributes) must not be demanded here or the two disagree.
    tracked = {path: meta for path, meta in tree.items() if not path.rsplit("/", 1)[-1].startswith(".")}
    missing = [path for path in tracked if not (model_dir / path).is_file()]
    if missing:
        raise ModelIntegrityError(
            "local copy is missing files the hub lists: " + ", ".join(sorted(missing)[:8])
        )

    mismatched: list[str] = []
    hash_sources: dict[str, str] = {}
    sizes: dict[str, int] = {}
    for path, meta in tracked.items():
        local = model_dir / path
        size = local.stat().st_size
        if isinstance(meta.get("size"), int) and meta["size"] != size:
            mismatched.append(f"{path}: hub {meta['size']} local {size}")
            continue
        if meta.get("sha256"):
            digest = sha256_file(local)
            if digest.lower() != str(meta["sha256"]).lower():
                mismatched.append(f"{path}: sha256 differs from hub oid")
                continue
            hash_sources[path] = "hub_lfs_oid"
            sizes[path] = size

    if mismatched:
        raise ModelIntegrityError("local copy does not match the hub:\n  " + "\n  ".join(mismatched[:10]))

    return build_manifest(
        model_dir,
        model_id=model_id or repo,
        source=f"hub:{repo}@{revision}",
        hash_sources=hash_sources,
        extra={
            "hub_endpoint": base,
            "hub_revision": revision,
            "provenance": {
                "method": "huggingface LFS oid for weights; local hash otherwise",
                "hub_verified_files": sorted(hash_sources),
            },
        },
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-path", required=True, help="local checkpoint directory")
    parser.add_argument("--manifest", help=f"manifest path (default: <model>/{MANIFEST_NAME})")
    parser.add_argument("--write", action="store_true", help="create/overwrite the manifest")
    parser.add_argument(
        "--from-hub",
        metavar="REPO",
        help="write a manifest whose weight hashes come from the hub (e.g. morriszjm/Tacit-1.7B); "
        "needs network on the download machine, never on the interview machine",
    )
    parser.add_argument("--hub-endpoint", default="https://huggingface.co",
                        help="https://huggingface.co or a mirror such as https://hf-mirror.com")
    parser.add_argument("--revision", default="main")
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

    if args.from_hub:
        try:
            manifest = manifest_from_hub(
                root,
                args.from_hub,
                endpoint=args.hub_endpoint,
                revision=args.revision,
                model_id=args.model_id,
            )
        except ModelIntegrityError as exc:
            print(f"cannot build manifest from the hub: {exc}", file=sys.stderr)
            return 3
        target = Path(args.manifest) if args.manifest else root / MANIFEST_NAME
        target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"manifest written from source: {target}\n"
            f"  source={manifest.get('source')} files={manifest['file_count']} "
            f"hub_verified={len(manifest['provenance']['hub_verified_files'])} "
            f"total_bytes={manifest['total_bytes']}"
        )
        return 0

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
