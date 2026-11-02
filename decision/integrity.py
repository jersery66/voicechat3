"""Offline model integrity verification.

The decision service loads weights from a local directory and never reaches
the network.  That makes one failure mode fatal and silent: a half-copied or
truncated checkpoint still loads far enough to produce *plausible* numbers, and
plausible numbers in a clinical decision component are worse than a hard error.

This module therefore provides two things:

* :func:`build_manifest` -- produce a size+sha256 manifest from a directory that
  was verified once, by hand, on a networked machine.
* :func:`verify_model_dir` -- re-check a directory against that manifest before
  the service binds a port.

Nothing here imports torch or transformers, so it runs on a machine with no
CUDA, no GPU driver and no model installed.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

MANIFEST_SCHEMA = 1
MANIFEST_NAME = "tacit_manifest.json"

#: Files a HuggingFace-format causal LM must have before ``from_pretrained`` is
#: even meaningful.  Missing any of these is a structural failure, not a hash
#: mismatch, so it is reported separately.
REQUIRED_FILES: tuple[str, ...] = ("config.json",)
#: At least one of these must exist; some checkpoints ship tokenizer files with
#: different names depending on the converter that produced them.
TOKENIZER_CANDIDATES: tuple[str, ...] = (
    "tokenizer.json",
    "tokenizer_config.json",
    "tokenizer.model",
    "spiece.model",
)
WEIGHT_SUFFIXES: tuple[str, ...] = (".safetensors", ".bin", ".pt", ".pth")
INDEX_SUFFIXES: tuple[str, ...] = (".safetensors.index.json", ".bin.index.json")

_CHUNK = 1024 * 1024


class ModelIntegrityError(RuntimeError):
    """Raised when a model directory cannot be trusted for offline serving."""

    def __init__(self, message: str, report: Optional["IntegrityReport"] = None) -> None:
        super().__init__(message)
        self.report = report


@dataclass(frozen=True)
class FileFingerprint:
    """One file's identity, relative to the model directory."""

    path: str
    size: int
    sha256: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"path": self.path, "size": self.size}
        if self.sha256:
            payload["sha256"] = self.sha256
        return payload


@dataclass(frozen=True)
class IntegrityReport:
    """Result of verifying a directory against a manifest."""

    model_dir: str
    ok: bool
    manifest_found: bool
    files_checked: int = 0
    total_bytes: int = 0
    hashed_files: int = 0
    problems: tuple[str, ...] = field(default_factory=tuple)
    detail: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_dir": self.model_dir,
            "ok": self.ok,
            "manifest_found": self.manifest_found,
            "files_checked": self.files_checked,
            "total_bytes": self.total_bytes,
            "hashed_files": self.hashed_files,
            "problems": list(self.problems),
            "detail": dict(self.detail),
        }

    def raise_for_status(self) -> None:
        if self.ok:
            return
        joined = "; ".join(self.problems) or "unknown integrity failure"
        raise ModelIntegrityError(
            f"model directory {self.model_dir} failed integrity check: {joined}", self
        )


# ---------------------------------------------------------------------------
# Directory inspection
# ---------------------------------------------------------------------------


def _iter_files(root: Path, *, skip_hidden: bool = True) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if skip_hidden and any(part.startswith(".") for part in rel.parts):
            continue
        yield path


def sha256_file(path: Path, *, chunk_size: int = _CHUNK) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _is_weight_file(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in WEIGHT_SUFFIXES)


def _is_index_file(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in INDEX_SUFFIXES)


def _shards_from_index(index_path: Path) -> list[str]:
    """Read the shard list a ``*.index.json`` promises to provide."""
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    weight_map = payload.get("weight_map")
    if not isinstance(weight_map, Mapping):
        return []
    shards = {str(value) for value in weight_map.values() if isinstance(value, str)}
    return sorted(shards)


def structural_problems(model_dir: Path) -> list[str]:
    """Cheap checks that need no manifest: is this a loadable checkpoint at all?"""
    problems: list[str] = []
    for name in REQUIRED_FILES:
        if not (model_dir / name).is_file():
            problems.append(f"missing_required_file:{name}")
    if not any((model_dir / name).is_file() for name in TOKENIZER_CANDIDATES):
        problems.append("missing_tokenizer:" + "|".join(TOKENIZER_CANDIDATES))

    weights = [p for p in model_dir.iterdir() if p.is_file() and _is_weight_file(p.name)]
    if not weights:
        problems.append("missing_weights:" + "|".join(WEIGHT_SUFFIXES))

    for index_path in model_dir.glob("*.index.json"):
        if not _is_index_file(index_path.name):
            continue
        for shard in _shards_from_index(index_path):
            if not (model_dir / shard).is_file():
                problems.append(f"missing_shard:{shard}")
    return problems


#: Files a self-distilled Tacit checkpoint ships that a plain causal LM does not.
_TACIT_MARKERS = ("tacit_decision.py",)


def tacit_checkpoint_markers(model_dir: str | Path) -> list[str]:
    """Evidence that a directory holds a Tacit checkpoint, not a plain causal LM.

    It matters because Tacit was distilled to answer with a position letter read
    after its own chat template; a different readout gives legal-looking
    probabilities that mean something else.  Detection is deliberately
    evidence-based (shipped files, model card, directory name) rather than a
    guess from ``architectures``, which is unchanged from the base model.
    """
    root = Path(model_dir).expanduser()
    markers = [name for name in _TACIT_MARKERS if (root / name).is_file()]
    if "tacit" in root.name.lower():
        markers.append(f"directory_name:{root.name}")
    readme = root / "README.md"
    if readme.is_file():
        try:
            head = readme.read_text(encoding="utf-8", errors="replace")[:4096].lower()
        except OSError:
            head = ""
        if "anyjev" in head and "tacit" in head:
            markers.append("model_card:anyjev-tacit")
    return markers



# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def build_manifest(
    model_dir: str | Path,
    *,
    include_hashes: bool = True,
    max_hash_bytes: Optional[int] = None,
    model_id: Optional[str] = None,
    source: Optional[str] = None,
    hash_sources: Optional[Mapping[str, str]] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Fingerprint a trusted directory so it can be re-verified offline.

    ``max_hash_bytes`` caps hashing cost: files above the limit are recorded by
    size only and flagged ``hashed: false``.  Full hashes are the default
    because a size-only manifest does not detect same-size corruption.
    """
    root = Path(model_dir).expanduser().resolve()
    if not root.is_dir():
        raise ModelIntegrityError(f"model directory does not exist: {root}")

    files: list[dict[str, Any]] = []
    total = 0
    hashed = 0
    for path in _iter_files(root):
        size = path.stat().st_size
        total += size
        rel = path.relative_to(root).as_posix()
        record: dict[str, Any] = {
            "path": rel,
            "size": size,
        }
        should_hash = include_hashes and (max_hash_bytes is None or size <= max_hash_bytes)
        if should_hash:
            record["sha256"] = sha256_file(path)
            if hash_sources and rel in hash_sources:
                record["sha256_source"] = hash_sources[rel]
            hashed += 1
        else:
            record["hashed"] = False
        files.append(record)

    payload: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "kind": "huggingface_causal_lm",
        "model_id": model_id,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "file_count": len(files),
        "total_bytes": total,
        "hashed_count": hashed,
        "files": files,
    }
    if source:
        payload["source"] = source
    if extra:
        payload.update(dict(extra))
    return payload


def write_manifest(
    model_dir: str | Path,
    output_path: str | Path | None = None,
    **kwargs: Any,
) -> Path:
    root = Path(model_dir).expanduser().resolve()
    target = Path(output_path) if output_path else root / MANIFEST_NAME
    payload = build_manifest(root, **kwargs)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ModelIntegrityError(f"cannot read manifest {manifest_path}: {exc}") from exc
    except ValueError as exc:
        raise ModelIntegrityError(f"manifest {manifest_path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ModelIntegrityError(f"manifest {manifest_path} must be a JSON object")
    schema = payload.get("schema")
    if schema != MANIFEST_SCHEMA:
        raise ModelIntegrityError(
            f"manifest {manifest_path} has unsupported schema {schema!r} "
            f"(this build understands schema {MANIFEST_SCHEMA})"
        )
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ModelIntegrityError(f"manifest {manifest_path} lists no files")
    return dict(payload)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_model_dir(
    model_dir: str | Path,
    manifest_path: str | Path | None = None,
    *,
    check_hashes: bool = True,
    require_manifest: bool = True,
    extra_files_ok: bool = False,
) -> IntegrityReport:
    """Verify ``model_dir`` against a manifest.

    Fails closed: any structural problem, size mismatch, hash mismatch or
    unexpected extra file (unless ``extra_files_ok``) marks the report not ok.
    """
    root = Path(model_dir).expanduser().resolve()
    problems: list[str] = []
    detail: dict[str, Any] = {}

    if not root.is_dir():
        return IntegrityReport(
            model_dir=str(root),
            ok=False,
            manifest_found=False,
            problems=("model_dir_missing",),
        )

    problems.extend(structural_problems(root))

    candidate = Path(manifest_path).expanduser() if manifest_path else root / MANIFEST_NAME
    manifest_found = candidate.is_file()
    if not manifest_found:
        if require_manifest:
            problems.append("manifest_missing:" + str(candidate))
        return IntegrityReport(
            model_dir=str(root),
            ok=not problems,
            manifest_found=False,
            problems=tuple(problems),
            detail={"manifest_expected": str(candidate)},
        )

    payload = load_manifest(candidate)
    entries = payload.get("files") or []
    files_checked = 0
    total_bytes = 0
    hashed_files = 0
    missing: list[str] = []
    size_mismatch: list[str] = []
    hash_mismatch: list[str] = []
    unhashed: list[str] = []
    declared = set()

    for entry in entries:
        if not isinstance(entry, Mapping):
            problems.append("manifest_entry_invalid")
            continue
        rel = str(entry.get("path") or "").strip()
        if not rel:
            problems.append("manifest_entry_without_path")
            continue
        declared.add(rel)
        target = root / rel
        expected_size = entry.get("size")
        files_checked += 1
        if not target.is_file():
            missing.append(rel)
            continue
        actual_size = target.stat().st_size
        total_bytes += actual_size
        if isinstance(expected_size, int) and actual_size != expected_size:
            size_mismatch.append(f"{rel} (expected {expected_size}, got {actual_size})")
            continue
        expected_hash = entry.get("sha256")
        if check_hashes and isinstance(expected_hash, str) and expected_hash:
            actual_hash = sha256_file(target)
            hashed_files += 1
            if actual_hash.lower() != expected_hash.lower():
                hash_mismatch.append(f"{rel} (expected {expected_hash[:12]}…, got {actual_hash[:12]}…)")
        elif check_hashes:
            unhashed.append(rel)

    unknown: list[str] = []
    if not extra_files_ok:
        for path in _iter_files(root):
            rel = path.relative_to(root).as_posix()
            if rel == candidate.name or rel in declared:
                continue
            unknown.append(rel)

    if missing:
        problems.extend(f"missing_file:{name}" for name in missing)
    if size_mismatch:
        problems.extend(f"size_mismatch:{item}" for item in size_mismatch)
    if hash_mismatch:
        problems.extend(f"sha256_mismatch:{item}" for item in hash_mismatch)
    if unknown:
        problems.extend(f"unexpected_file:{name}" for name in unknown)

    detail.update(
        {
            "manifest": str(candidate),
            "manifest_schema": payload.get("schema"),
            "model_id": payload.get("model_id"),
            "declared_files": len(declared),
            "missing": missing,
            "size_mismatch": size_mismatch,
            "hash_mismatch": hash_mismatch,
            "unhashed": unhashed,
            "unexpected": unknown,
            "total_bytes_declared": payload.get("total_bytes"),
        }
    )

    return IntegrityReport(
        model_dir=str(root),
        ok=not problems,
        manifest_found=True,
        files_checked=files_checked,
        total_bytes=total_bytes,
        hashed_files=hashed_files,
        problems=tuple(problems),
        detail=detail,
    )


def summarise(report: IntegrityReport) -> str:
    """One-line, log-safe summary (paths only, never file contents)."""
    size_mib = report.total_bytes / (1024 * 1024) if report.total_bytes else 0.0
    state = "ok" if report.ok else "FAILED"
    head = (
        f"model integrity {state}: {report.model_dir} "
        f"files={report.files_checked} hashed={report.hashed_files} size={size_mib:.1f}MiB"
    )
    if report.problems:
        return head + " problems=" + ", ".join(report.problems[:6])
    return head


def describe_requirements() -> dict[str, Sequence[str]]:
    """Static description used by docs and the offline bundle checklist."""
    return {
        "required_files": REQUIRED_FILES,
        "tokenizer_candidates": TOKENIZER_CANDIDATES,
        "weight_suffixes": WEIGHT_SUFFIXES,
        "index_suffixes": INDEX_SUFFIXES,
    }
