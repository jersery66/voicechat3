#!/usr/bin/env python
"""Multi-turn timing benchmark: does the agent propose the right *next step*?

The single-turn benchmark answers "was this label right?".  This one answers the
question the branch actually exists for: *in a continuous interview, did the
system continue, offer an assessment, offer relaxation, or ask for
clarification at a defensible moment?*

Every provider is driven through the **same** production chain -- same
``DecisionRequest`` state, same ``DecisionValidator``, same ``TurnPolicy``, same
``OfferLedger``, same ``ScaleRuntime`` -- so differences come from the decision
backend and nothing else.

Providers
---------
``rule``     deterministic state-only baseline (no model; the honest floor)
``replay``   replays the trajectory annotation; a *harness self-check*, never a
             model result.  If the chain cannot reach a window when it is told
             exactly what to do, the chain is broken.
``tacit``    official Tacit-1.7B gateway (``--endpoint`` or ``--model-path``)
``anyjev``   the Qwen/anyJev agent backend (``--endpoint``)

Splits are separated on purpose: choose templates and thresholds on ``dev``,
then report ``test`` once.  Tuning on ``test`` and reporting ``test`` is how a
timing number becomes fiction.

    python scripts/benchmarks/timing_benchmark.py --split dev
    python scripts/benchmarks/timing_benchmark.py --split test --endpoint http://127.0.0.1:8099
    python scripts/benchmarks/timing_benchmark.py --compare test_output/timing/*.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts" / "decision"))

from decision.timing_eval import run_trajectories  # noqa: E402
from decision.timing_metrics import format_report  # noqa: E402

CASES_DIR = PROJECT_ROOT / "scripts" / "benchmarks" / "cases"
DEFAULT_OUT = PROJECT_ROOT / "test_output" / "timing"
SPLIT_FILES = {
    "dev": CASES_DIR / "interview_trajectories_dev.jsonl",
    "test": CASES_DIR / "interview_trajectories_test.jsonl",
}

#: Providers that must never be presented as model results.
NON_MODEL_PROVIDERS = {"rule": "deterministic baseline", "replay": "harness self-check"}


def load_trajectories(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        rows.append(json.loads(text))
    return rows


def load_split(split: str) -> tuple[list[dict[str, Any]], list[str]]:
    if split == "all":
        rows: list[dict[str, Any]] = []
        sources: list[str] = []
        for name, path in SPLIT_FILES.items():
            rows.extend(load_trajectories(path))
            sources.append(str(path))
        return rows, sources
    path = SPLIT_FILES[split]
    return load_trajectories(path), [str(path)]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


def build_provider(name: str, args: argparse.Namespace):
    """Return ``(provider_or_none, note)`` for one provider name."""
    if name == "rule":
        from decision.providers.rule import RuleTimingProvider

        return RuleTimingProvider(), "state-only rules"

    if name == "replay":
        # Handled per trajectory below; the marker object is only a placeholder.
        return "__replay__", "replays the annotation (self-check, not a model)"

    if name == "tacit":
        endpoint = resolve_endpoint(args)
        if not endpoint:
            return None, "unavailable: pass --endpoint or --model-path"
        from decision.providers.tacit import TacitDecisionProvider

        return (
            TacitDecisionProvider(endpoint=endpoint, timeout=args.timeout),
            f"tacit @ {endpoint}",
        )

    if name == "anyjev":
        endpoint = resolve_endpoint(args)
        if not endpoint:
            return None, "unavailable: pass --endpoint or --model-path"
        from decision.providers.anyjev import AnyJevDecisionProvider

        return (
            AnyJevDecisionProvider(endpoint=endpoint, timeout=args.timeout),
            f"anyjev @ {endpoint}",
        )

    raise SystemExit(f"unknown provider: {name}")


def resolve_endpoint(args: argparse.Namespace) -> Optional[str]:
    if getattr(args, "endpoint", None):
        return args.endpoint
    if not getattr(args, "model_path", None):
        return None
    from service_process import LocalDecisionService, ServiceStartError, free_port

    if getattr(args, "_service", None) is None:
        try:
            args._service = LocalDecisionService.spawn(
                model_path=args.model_path,
                dtype=args.dtype,
                device=args.device,
                port=free_port(),
                project_root=str(PROJECT_ROOT),
            )
            args._service.wait_healthy(timeout=args.timeout)
        except (ServiceStartError, OSError) as exc:
            print(f"[warn] cannot start {args.model_path}: {exc}", file=sys.stderr)
            args._service = False
    service = args._service
    if not service:
        return None
    return service.base_url


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_provider(name: str, provider: Any, trajectories: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if provider == "__replay__":
        from decision.providers.scripted import ScriptedTimingProvider

        return run_trajectories(
            provider_for=ScriptedTimingProvider.from_trajectory,
            trajectories=trajectories,
        )
    return run_trajectories(provider=provider, trajectories=trajectories)


def render_turn_rows(report: dict[str, Any]) -> str:
    lines: list[str] = []
    for trajectory in report["trajectories"]:
        lines.append(f"\n## {trajectory['id']} — {trajectory.get('title')}")
        lines.append("| round | jev | validator | policy | reason | offer | executed | active |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for turn in trajectory["turns"]:
            offers = ",".join(
                f"{offer['kind']}:{offer.get('target') or '-'}" for offer in turn["offers"]
            )
            lines.append(
                "| {round} | {intervention} | {reason} | {action} | {policy_reason} | "
                "{offers} | {executed} | {active} |".format(
                    round=turn["round"],
                    intervention=turn.get("intervention") or "-",
                    reason=turn.get("validate_reason") or "-",
                    action=turn.get("policy_action"),
                    policy_reason=turn.get("policy_reason"),
                    offers=offers or "-",
                    executed=turn.get("executed"),
                    active=turn.get("active_scale") or "-",
                )
            )
    return "\n".join(lines)


def render_trajectory_table(report: dict[str, Any]) -> str:
    lines = [
        "| trajectory | split | turns | scale window | first | outcome | relax window | first | outcome | notes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in report["per_trajectory"]:
        notes = []
        if row.get("repeat_within_cooldown"):
            notes.append(f"repeat_in_cooldown={row['repeat_within_cooldown']}")
        if row.get("inappropriate_offers"):
            notes.append(f"inappropriate={row['inappropriate_offers']}")
        if row.get("policy_agreement") is not None and row["policy_agreement"] < 1.0:
            notes.append(f"policy={row['policy_agreement']:.2f}")
        lines.append(
            "| {id} | {split} | {turns} | {sw} | {sf} | {so} | {rw} | {rf} | {ro} | {notes} |".format(
                id=row["id"],
                split=row.get("split") or "-",
                turns=row["turns"],
                sw="-" if not row.get("scale_window") else f"{row['scale_window'][0]}-{row['scale_window'][1]}",
                sf="-" if row.get("scale_first_offer") is None else row["scale_first_offer"],
                so=row.get("scale_outcome"),
                rw="-" if not row.get("relaxation_window") else f"{row['relaxation_window'][0]}-{row['relaxation_window'][1]}",
                rf="-" if row.get("relaxation_first_offer") is None else row["relaxation_first_offer"],
                ro=row.get("relaxation_outcome"),
                notes=" ".join(notes) or "-",
            )
        )
    return "\n".join(lines)


def render_markdown(report: dict[str, Any]) -> str:
    result = report["result"]
    summary = result["summary"]
    vram = (report.get("stats", {}).get("vram") or {})
    lines = [
        f"# Interview timing benchmark — {report['provider']}",
        "",
        f"- role: {report.get('role')}",
        f"- split: {report['split']} (trajectories {summary['trajectories']}, turns {summary['turns']})",
        f"- case files: {', '.join(report.get('case_files', []))}",
        f"- generated: {report['generated_at']}",
        "",
        "## Headline",
        "",
        "```",
        format_report(summary),
        "```",
        "",
        "## Per trajectory",
        "",
        render_trajectory_table(result),
    ]
    latency = summary["latency_ms"]
    lines += [
        "",
        "## Latency per decision (ms)",
        "",
        f"P50 {latency['p50']} · P95 {latency['p95']}",
    ]
    if vram.get("available"):
        lines += [
            "",
            "## Memory",
            "",
            f"- allocated peak: {vram.get('allocated_peak_mib')} MiB",
            f"- reserved peak: {vram.get('reserved_peak_mib')} MiB",
            f"- device total: {vram.get('total_mib')} MiB",
        ]
    return "\n".join(lines) + "\n"


def compare(paths: Sequence[str]) -> int:
    reports = [json.loads(Path(path).read_text(encoding="utf-8")) for path in paths]
    labels = [report["provider"] for report in reports]

    def cell(report: dict[str, Any], accessor) -> str:
        value = accessor(report["result"]["summary"])
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value)

    metrics = [
        ("scale hit", lambda s: s["scale"]["hit_rate"]),
        ("scale early", lambda s: s["scale"]["early_rate"]),
        ("scale late", lambda s: s["scale"]["late_rate"]),
        ("scale missed", lambda s: s["scale"]["miss_rate"]),
        ("scale spurious", lambda s: s["scale"]["spurious_offers"]),
        ("relax hit", lambda s: s["relaxation"]["hit_rate"]),
        ("relax early", lambda s: s["relaxation"]["early_rate"]),
        ("relax missed", lambda s: s["relaxation"]["miss_rate"]),
        ("relax spurious", lambda s: s["relaxation"]["spurious_offers"]),
        ("repeat in cooldown", lambda s: s["repeat_within_cooldown"]),
        ("inappropriate", lambda s: s["inappropriate_offers"]),
        ("scale choice", lambda s: s["scale_choice_accuracy"]),
        ("execution", lambda s: s["execution_success_rate"]),
        ("policy agreement", lambda s: s.get("policy_agreement")),
        ("turn agreement", lambda s: s["turn_agreement"]),
        ("block rate", lambda s: s["policy_block_rate"]),
        ("latency p50", lambda s: s["latency_ms"]["p50"]),
        ("latency p95", lambda s: s["latency_ms"]["p95"]),
    ]
    print("| metric | " + " | ".join(labels) + " |")
    print("| --- | " + " | ".join("---" for _ in labels) + " |")
    for name, accessor in metrics:
        print(f"| {name} | " + " | ".join(cell(report, accessor) for report in reports) + " |")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Multi-turn interview timing benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--split", choices=("dev", "test", "all"), default="dev")
    parser.add_argument("--providers", default="rule,replay",
                        help="comma separated: rule,replay,tacit,anyjev")
    parser.add_argument("--endpoint", help="decision service endpoint for tacit/anyjev")
    parser.add_argument("--model-path", help="local checkpoint; spawns a service")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--label", default=None, help="artifact name override")
    parser.add_argument("--verbose", action="store_true", help="print per-turn rows")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--compare", nargs="+", metavar="JSON")
    args = parser.parse_args(argv)

    if args.compare:
        return compare(args.compare)

    trajectories, sources = load_split(args.split)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    reports: list[dict[str, Any]] = []
    for name in [part.strip() for part in args.providers.split(",") if part.strip()]:
        provider, note = build_provider(name, args)
        if provider is None:
            print(f"[skip] {name}: {note}", file=sys.stderr)
            continue
        started = time.perf_counter()
        result = run_provider(name, provider, trajectories)
        elapsed = time.perf_counter() - started

        stats: dict[str, Any] = {}
        service = getattr(args, "_service", None)
        if service:
            try:
                stats = service.stats()
            except Exception:  # pragma: no cover - diagnostics only
                stats = {}

        report = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "provider": args.label or name,
            "role": NON_MODEL_PROVIDERS.get(name, "decision model"),
            "split": args.split,
            "case_files": sources,
            "trajectories": len(trajectories),
            "wall_seconds": round(elapsed, 2),
            "note": note,
            "stats": stats,
            "result": result,
        }
        reports.append(report)

        slug = str(report["provider"]).replace("/", "_").replace("\\", "_").lower()
        json_path = out_dir / f"{args.split}-{slug}.json"
        md_path = out_dir / f"{args.split}-{slug}.md"
        report["artifact"] = str(json_path)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        md_path.write_text(render_markdown(report), encoding="utf-8")

        print(f"\n=== {report['provider']} ({report['role']}) — split={args.split} ===")
        print(format_report(result["summary"]))
        print(render_trajectory_table(result))
        if args.verbose:
            print(render_turn_rows(result))
        print(f"artifact: {json_path}\nreport:   {md_path}")

    # A single cross-provider table is the deliverable; print it whenever more
    # than one provider ran, so the comparison never depends on the reader
    # remembering two separate outputs.
    if len(reports) > 1:
        print("\n=== comparison ===")
        return compare([report["artifact"] for report in reports])
    return 0


if __name__ == "__main__":
    sys.exit(main())
