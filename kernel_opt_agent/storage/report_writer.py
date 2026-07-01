from __future__ import annotations

import csv
import shutil
from pathlib import Path
from typing import Any

import yaml

from kernel_opt_agent.agent.diagnosis import diagnose


def _sort_success(records: list[dict[str, Any]], objective: str) -> list[dict[str, Any]]:
    ok = [r for r in records if r.get("status") == "benchmark_ok" and r.get("objective", {}).get("value") is not None]
    return sorted(ok, key=lambda r: r["objective"]["value"], reverse=(objective == "tflops"))


def write_final_report(results_dir: Path, records: list[dict[str, Any]], objective: str) -> dict[str, Any] | None:
    best_records = _sort_success(records, objective)
    best = best_records[0] if best_records else None
    if best:
        kernel_path = Path(best["paths"]["kernel"])
        if kernel_path.exists():
            shutil.copy2(kernel_path, results_dir / "best_kernel.py")
        with (results_dir / "best_config.yaml").open("w", encoding="utf-8") as f:
            yaml.safe_dump(best["config"], f, sort_keys=True)
    else:
        (results_dir / "best_kernel.py").write_text("# No benchmark-passing kernel was produced.\n", encoding="utf-8")
        (results_dir / "best_config.yaml").write_text("{}\n", encoding="utf-8")

    all_csv = results_dir / "all_results.csv"
    fields = ["iteration", "candidate_id", "status", "latency", "tflops", "bandwidth", "objective_value", "config_hash"]
    with all_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for r in records:
            m = r.get("metrics") or {}
            writer.writerow(
                {
                    "iteration": r.get("iteration"),
                    "candidate_id": r.get("candidate_id"),
                    "status": r.get("status"),
                    "latency": m.get("latency"),
                    "tflops": m.get("tflops"),
                    "bandwidth": m.get("bandwidth"),
                    "objective_value": (r.get("objective") or {}).get("value"),
                    "config_hash": r.get("config_hash"),
                }
            )

    baseline = records[0] if records else None
    lines = [
        "# TileLang Autotuning Report",
        "",
        "This report shows the best-seen result within the configured search budget. It is not a claim of global optimality.",
        "",
        "## Baseline",
        f"- Status: {baseline.get('status') if baseline else 'n/a'}",
        f"- Metrics: {baseline.get('metrics') if baseline else '{}'}",
        "",
        "## Best Seen",
    ]
    if best:
        lines += [
            f"- Iteration/Candidate: {best['iteration']}/{best['candidate_id']}",
            f"- Metrics: {best.get('metrics')}",
            f"- Objective: {best.get('objective')}",
            f"- Config: `{best.get('config')}`",
        ]
        if baseline and baseline.get("objective", {}).get("value") and best.get("objective", {}).get("value"):
            b = baseline["objective"]["value"]
            v = best["objective"]["value"]
            improvement = ((b - v) / b * 100.0) if objective == "latency" else ((v - b) / b * 100.0)
            lines.append(f"- Improvement vs baseline: {improvement:.2f}%")
    else:
        lines.append("- No benchmark-passing candidate was found.")
    lines += ["", "## Attempts"]
    for r in records:
        lines.append(f"- iter {r.get('iteration')} cand {r.get('candidate_id')}: {r.get('status')} {r.get('config')}")
    failures = [r for r in records if r.get("status") != "benchmark_ok"]
    lines += ["", "## Failures", f"- Failed candidates: {len(failures)}. See `failed_cases.jsonl` for details."]
    lines += ["", "## Diagnosis"]
    lines += [f"- {note}" for note in diagnose(best_records + failures)]
    lines += ["", "## Next Steps", "- Increase budget or refine search_space after reviewing failure patterns and profiler data."]
    (results_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return best

