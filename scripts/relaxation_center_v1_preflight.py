"""Software-only preflight for Relaxation Center V1.

This command checks repository facts and imports.  It never probes hardware,
starts a provider, opens a window, or claims that media files actually play.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from relaxation.catalog import build_default_catalog
from relaxation.contracts import RelaxationContentRole
from relaxation.runtime import RelaxationRuntime


ROOT = Path(__file__).resolve().parents[1]
CORE_IDS = ["breathing", "muscle_relaxation", "meditation"]
GAME_IDS = ["bubble_pop", "gentle_search", "calm_puzzle", "falling_leaves"]
GAME_MODULES = [
    "relaxation.games.bubble_pop",
    "relaxation.games.gentle_search",
    "relaxation.games.calm_puzzle",
    "relaxation.games.falling_leaves",
]
FORBIDDEN_IMPORTS = ("import pygame", "import phaser", "webview", "electron")


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def run_preflight() -> dict[str, Any]:
    errors: list[str] = []
    catalog_status = "PASS"
    import_status = "PASS"
    forbidden_status = "PASS"
    catalog = None
    try:
        catalog = build_default_catalog()
        core_ids = [
            item.id for item in catalog.list_by_role(RelaxationContentRole.CORE_RELAXATION)
        ]
        game_ids = [
            item.id for item in catalog
            if item.role is RelaxationContentRole.LEISURE and item.id in GAME_IDS
        ]
        if core_ids != CORE_IDS or game_ids != GAME_IDS:
            catalog_status = "FAIL"
            errors.append("catalog IDs do not match the V1 contract")
        if any(not catalog.require(item_id).is_available for item_id in CORE_IDS + GAME_IDS):
            catalog_status = "FAIL"
            errors.append("an expected V1 content definition is unavailable")
    except Exception as exc:
        catalog_status = "FAIL"
        core_ids, game_ids = [], []
        errors.append(f"catalog load failed: {exc}")

    if catalog is not None:
        try:
            RelaxationRuntime(catalog)
        except Exception as exc:
            errors.append(f"RelaxationRuntime construction failed: {exc}")

    for module_name in GAME_MODULES:
        try:
            module = importlib.import_module(module_name)
            source = inspect.getsource(module).lower()
            if any(marker in source for marker in FORBIDDEN_IMPORTS):
                forbidden_status = "FAIL"
                errors.append(f"forbidden runtime dependency in {module_name}")
        except Exception as exc:
            import_status = "FAIL"
            errors.append(f"game import failed for {module_name}: {exc}")

    notice_files = (
        ROOT / "THIRD_PARTY_NOTICES.md",
        ROOT / "docs" / "third_party" / "leisure_game_sources.md",
    )
    notices_status = "PASS" if all(path.is_file() for path in notice_files) else "FAIL"
    if notices_status != "PASS":
        errors.append("third-party attribution notices are missing")

    resource_status = "NOT VERIFIED"
    if catalog is not None:
        configured = [
            item.resource_path for item in catalog
            if item.role is RelaxationContentRole.CORE_RELAXATION
        ]
        if not all(configured):
            errors.append("a core resource identifier is missing")
            catalog_status = "FAIL"

    software_checks_pass = not errors or all(
        "resource" in error.lower() for error in errors
    )
    if catalog_status == "FAIL" or import_status == "FAIL" or forbidden_status == "FAIL" or notices_status == "FAIL":
        software_checks_pass = False

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "overall_status": "PASS" if software_checks_pass else "FAIL",
        "catalog_status": catalog_status,
        "game_imports": import_status,
        "forbidden_runtime_dependencies": forbidden_status,
        "third_party_notices": notices_status,
        "core_resource_existence": resource_status,
        "catalog": {"core_ids": core_ids, "leisure_game_ids": game_ids},
        "hardware_validation": "NOT RUN",
        "real_media_playback": "NOT RUN",
        "gpu": "NOT RUN",
        "cuda": "NOT RUN",
        "vllm": "NOT RUN",
        "agent": "NOT RUN",
        "dialogue": "NOT RUN",
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    result = run_preflight()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Relaxation Center V1 software preflight: {result['overall_status']}")
        print(f"Core resources: {result['core_resource_existence']}")
        print(f"Hardware validation: {result['hardware_validation']}")
        for error in result["errors"]:
            print(f"ERROR: {error}")
    return 0 if result["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
