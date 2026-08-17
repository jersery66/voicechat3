"""Run the deterministic offline production-integration contract gate.

This observer invokes only synthetic/fake-provider tests.  It never starts a
model, probes hardware, opens an audio device, or changes production state.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OFFLINE_TEST_TARGETS = (
    "tests/test_stt_fixture_integration.py",
    "tests/test_tts_fixture_integration.py",
    "tests/test_production_chain_dry_run.py",
    "tests/test_session_lifecycle_contract.py",
    "tests/test_scale_runtime_contract.py",
    "tests/test_rag_authority_contract.py",
    "tests/test_report_source_contract.py",
    "tests/test_measurement_contract.py",
    "tests/test_observability_contract.py",
    "tests/test_error_taxonomy_contract.py",
    "tests/test_deployment_doctor.py",
    "tests/test_deployment_lifecycle_contract.py",
    "tests/test_deployment_manifests.py",
    "tests/test_blackwell_live_probe.py",
    "tests/test_qwen_dialogue_ab.py",
)


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


def build_gate_summary(*, returncode: int, git_commit: str | None = None) -> dict[str, Any]:
    status = "PASS" if returncode == 0 else "FAIL"
    return {
        "schema_version": 1,
        "artifact_type": "OFFLINE_INTEGRATION_READINESS",
        "evidence_type": "SIMULATED",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit if git_commit is not None else _git_commit(),
        "offline_integration_readiness": status,
        "fixture_evidence": "SIMULATED",
        "hardware_validation": "NOT RUN",
        "real_stt": "NOT RUN",
        "real_tts": "NOT RUN",
        "real_e2e": "NOT RUN",
        "real_vllm": "NOT RUN",
        "real_blackwell": "NOT RUN",
        "checks": {target: status for target in OFFLINE_TEST_TARGETS},
        "promotion": "NOT APPROVED",
        "evidence_reference": None,
    }


def run_contract_tests(*, runner: Callable[..., Any] = subprocess.run) -> int:
    command = [sys.executable, "-m", "pytest", *OFFLINE_TEST_TARGETS, "-q"]
    result = runner(
        command,
        cwd=PROJECT_ROOT,
        check=False,
    )
    return int(result.returncode)


def run_gate(
    *,
    output_root: str | Path | None = None,
    git_commit: str | None = None,
) -> dict[str, Any]:
    root = Path(output_root) if output_root is not None else PROJECT_ROOT / "test_output" / "offline_integration"
    root.mkdir(parents=True, exist_ok=True)
    returncode = run_contract_tests()
    summary = build_gate_summary(returncode=returncode, git_commit=git_commit)
    (root / "offline_integration_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline production integration readiness contracts")
    parser.add_argument("--output-root", default=str(PROJECT_ROOT / "test_output" / "offline_integration"))
    args = parser.parse_args(argv)
    summary = run_gate(output_root=args.output_root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["offline_integration_readiness"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
