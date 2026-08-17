"""Final pre-hardware readiness aggregator.

The gate runs deterministic repository tests and checks that operator-facing
artifacts exist.  It never probes hardware or starts services; a PASS means
pre-hardware development is complete, not that deployment has been validated.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from deployment.profiles import get_deployment_profile
from scripts.acceptance import offline_integration

FREEZE_TAG = "pre-hardware-freeze-20260817"

REQUIRED_DOCUMENTS = (
    "docs/deployment/rtxpro6000_operator_runbook.md",
    "docs/deployment/first_machine_checklist.md",
    "docs/deployment/qwen_model_switch_runbook.md",
    "docs/deployment/shutdown_recovery_runbook.md",
    "docs/deployment/troubleshooting.md",
    "docs/deployment/artifact_location_map.md",
    "docs/deployment/pre_hardware_release_inventory.md",
    "docs/deployment/pre_hardware_freeze.md",
    "docs/deployment/pre_hardware_readiness_audit.md",
    "docs/deployment/measurement_observability_contract.md",
    "docs/deployment/windows_wsl2_blackwell_launcher.md",
    "docs/deployment/blackwell_live_probe.md",
    "docs/deployment/qwen_dialogue_ab_harness.md",
)

REQUIRED_TOOLS = (
    "scripts/deployment/doctor.py",
    "scripts/deployment/manifest.py",
    "scripts/deployment/measurement.py",
    "scripts/deployment/memory_snapshot.py",
    "scripts/deployment/observability.py",
    "scripts/deployment/error_taxonomy.py",
    "scripts/acceptance/offline_integration.py",
    "scripts/acceptance/blackwell_live_probe.py",
    "scripts/acceptance/qwen_dialogue_ab.py",
    "scripts/windows/start_blackwell_stack.ps1",
    "scripts/windows/stop_blackwell_stack.ps1",
)

REQUIRED_ENVIRONMENT_TEMPLATES = (".env.example",)


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


def documentation_status() -> tuple[bool, list[str]]:
    missing = [
        path
        for path in (*REQUIRED_DOCUMENTS, *REQUIRED_TOOLS, *REQUIRED_ENVIRONMENT_TEMPLATES)
        if not (PROJECT_ROOT / path).is_file()
    ]
    return not missing, missing


def run_full_regression(*, runner: Callable[..., Any] = subprocess.run) -> int:
    result = runner(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=PROJECT_ROOT,
        check=False,
    )
    return int(result.returncode)


def run_offline_gate(*, output_root: str | Path, git_commit: str | None) -> dict[str, Any]:
    """Run and consume the dedicated offline integration gate artifact."""
    return offline_integration.run_gate(output_root=output_root, git_commit=git_commit)


def _tag_commit(tag: str = FREEZE_TAG) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"{tag}^{{}}"],
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


def freeze_tag_status(current_commit: str | None) -> tuple[bool, str | None]:
    tag_commit = _tag_commit()
    return bool(current_commit and tag_commit and current_commit == tag_commit), tag_commit


def _offline_summary_valid(summary: Mapping[str, Any] | None, current_commit: str | None) -> bool:
    return bool(
        summary
        and summary.get("offline_integration_readiness") == "PASS"
        and summary.get("evidence_type") == "SIMULATED"
        and current_commit
        and summary.get("git_commit") == current_commit
    )


def build_final_summary(
    *,
    test_returncode: int,
    documentation_ok: bool,
    offline_summary: Mapping[str, Any] | None = None,
    freeze_tag_ok: bool = False,
    freeze_tag_commit: str | None = None,
    git_commit: str | None = None,
    missing_documents: list[str] | None = None,
) -> dict[str, Any]:
    current_commit = git_commit if git_commit is not None else _git_commit()
    offline_ok = _offline_summary_valid(offline_summary, current_commit)
    passed = test_returncode == 0 and documentation_ok and offline_ok and freeze_tag_ok
    status = "PASS" if passed else "FAIL"
    baseline_model = get_deployment_profile("rtxpro6000_96g").dialogue_model
    candidate_model = get_deployment_profile("rtxpro6000_96g_qwen38_candidate").dialogue_model
    return {
        "schema_version": 1,
        "artifact_type": "PRE_HARDWARE_FINAL_READINESS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit if git_commit is not None else _git_commit(),
        "pre_hardware_development": "COMPLETE" if passed else "INCOMPLETE",
        "pre_hardware_final_readiness": status,
        "full_regression": "PASS" if test_returncode == 0 else "FAIL",
        "documentation": "PASS" if documentation_ok else "FAIL",
        "missing_documents": list(missing_documents or []),
        "offline_evidence": "SIMULATED / CONTRACT",
        "offline_integration": "PASS / SIMULATED" if offline_ok else "FAIL",
        "offline_integration_git_commit": offline_summary.get("git_commit") if offline_summary else None,
        "offline_integration_evidence_type": offline_summary.get("evidence_type") if offline_summary else None,
        "freeze_tag": FREEZE_TAG,
        "freeze_tag_status": "PASS" if freeze_tag_ok else "FAIL",
        "freeze_tag_commit": freeze_tag_commit,
        "hardware_validation": "NOT RUN",
        "real_gpu": "NOT RUN",
        "real_cuda": "NOT RUN",
        "real_vllm": "NOT RUN",
        "real_phase5": "NOT RUN",
        "real_ab": "NOT RUN",
        "real_stt": "NOT RUN",
        "real_tts": "NOT RUN",
        "real_e2e": "NOT RUN",
        "baseline_model": baseline_model,
        "candidate_model": candidate_model,
        "candidate_promotion": "NOT APPROVED",
        "evidence_type": "SIMULATED / CONTRACT",
    }


def run_final_gate(
    *,
    output_root: str | Path | None = None,
    git_commit: str | None = None,
) -> dict[str, Any]:
    root = Path(output_root) if output_root is not None else PROJECT_ROOT / "test_output" / "pre_hardware_final_readiness"
    root.mkdir(parents=True, exist_ok=True)
    current_commit = git_commit if git_commit is not None else _git_commit()
    docs_ok, missing = documentation_status()
    offline_summary = run_offline_gate(
        output_root=root / "offline_integration",
        git_commit=current_commit,
    )
    test_returncode = run_full_regression()
    tag_ok, tag_commit = freeze_tag_status(current_commit)
    summary = build_final_summary(
        test_returncode=test_returncode,
        documentation_ok=docs_ok,
        offline_summary=offline_summary,
        freeze_tag_ok=tag_ok,
        freeze_tag_commit=tag_commit,
        git_commit=current_commit,
        missing_documents=missing,
    )
    (root / "pre_hardware_final_readiness.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the final pre-hardware readiness gate")
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "test_output" / "pre_hardware_final_readiness"))
    args = parser.parse_args(argv)
    summary = run_final_gate(output_root=args.output_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["pre_hardware_final_readiness"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
