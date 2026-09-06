from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from kernel_opt_agent.diagnosis.diagnosis_report import diagnosis_records, profiler_metrics_table
from kernel_opt_agent.hardware.hardware_info import HardwareInfo


def _sort_success(records: list[dict[str, Any]], objective: str) -> list[dict[str, Any]]:
    ok = [r for r in records if r.get("status") == "benchmark_ok" and r.get("objective", {}).get("value") is not None]
    return sorted(ok, key=lambda r: r["objective"]["value"], reverse=(objective == "tflops"))


def _hardware_lines(hardware_info: HardwareInfo | None) -> list[str]:
    if hardware_info is None:
        return ["", "## Hardware Detection", "- Hardware detection data was not available."]
    data = hardware_info.to_dict()
    fields = data["fields"]
    lines = [
        "",
        "## Hardware Detection",
        f"- Declared hardware: {fields.get('target_name', {}).get('value')}",
        f"- Backend: {fields.get('backend', {}).get('value')}",
        f"- Built-in profile: {data.get('profile_used')}",
        f"- Doc lookup used: {data.get('doc_lookup_used')}",
        f"- Safe probe used: {data.get('safe_probe_used')}",
        f"- Conservative mode: {data.get('conservative_mode')}",
        f"- Unknown fields: {', '.join(data.get('unknown_fields') or []) or 'none'}",
        "- Field sources:",
    ]
    for name, field in fields.items():
        lines.append(f"  - {name}: source={field.get('source')} confidence={field.get('confidence')} value={field.get('value')}")
    if data.get("unknown_fields"):
        lines.append("- Hardware parameters are incomplete; current search results may not be optimal for the target device.")
    probe_results = data.get("safe_probe_results") or []
    lines += [
        "",
        "## Safe Probe Summary",
        "- Safe probe is a low/medium-confidence availability check, not an official hardware limit.",
        f"- Probe records: {len(probe_results)}",
    ]
    if any("runner_mode=local_mock" in str(probe.get("inference", "")) for probe in probe_results):
        lines.append("- Local mock probe was used; 未验证真实 GPU 能力.")
    if any("runner_mode=ssh_probe" in str(probe.get("inference", "")) for probe in probe_results):
        lines.append("- SSH probe attempted TileLang/GPU small kernels.")
    if any(probe.get("status") == "skipped" for probe in probe_results):
        lines.append("- Some probes were skipped because TileLang or the target backend runtime was unavailable.")
    for probe in probe_results:
        lines.append(
            f"- {probe.get('probe_name')} {probe.get('param_name')}={probe.get('candidate_value')}: status={probe.get('status')} "
            f"confidence={probe.get('confidence')} inference={probe.get('inference')}"
        )
    return lines


def _patch_trial_lines(results_dir: Path) -> list[str]:
    path = results_dir / "patch_trials.jsonl"
    trials: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                trials.append(json.loads(line))
            except json.JSONDecodeError:
                trials.append({"status": "parse_error", "error": "invalid patch_trials.jsonl line"})
    lines = [
        "",
        "## Patch Trials",
        "",
        "| Status | Hypothesis | Improvement | Rollback | Error |",
        "| --- | --- | --- | --- | --- |",
    ]
    if not trials:
        lines.append("| none | none | null | none | none |")
        return lines
    for trial in trials:
        rollback = ((trial.get("artifacts") or {}).get("rollback") or {}).get("verified")
        lines.append(
            "| {status} | {hypothesis} | {improvement} | {rollback} | {error} |".format(
                status=trial.get("status"),
                hypothesis=trial.get("hypothesis") or "none",
                improvement="null" if trial.get("improvement") is None else trial.get("improvement"),
                rollback="none" if rollback is None else str(bool(rollback)).lower(),
                error=trial.get("error") or "none",
            )
        )
    return lines


def _report_diagnoses(record: dict[str, Any] | None) -> list[dict[str, Any]] | None:
    if not record:
        return None
    diagnoses = record.get("diagnoses")
    if diagnoses is not None:
        source_trial_id = record.get("trial_id")
        return [
            {**item, "source_trial_id": item.get("source_trial_id") or source_trial_id}
            for item in diagnoses
        ]
    return record.get("bottleneck_diagnosis")


def write_final_report(
    results_dir: Path,
    records: list[dict[str, Any]],
    objective: str,
    hardware_info: HardwareInfo | None = None,
    execution_mode: str = "parameter_search",
) -> dict[str, Any] | None:
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
    baseline_only = execution_mode == "baseline_only"
    lines = [
        "# TileLang Autotuning Report",
        "",
        (
            "This is a baseline-only validation and measurement. No source optimization or parameter search was performed."
            if baseline_only
            else "This report shows the best-seen result within the configured search budget. It is not a claim of global optimality."
        ),
        "",
        f"- Execution mode: `{execution_mode}`",
        "",
        "## Baseline",
        f"- Status: {baseline.get('status') if baseline else 'n/a'}",
        f"- Metrics: {baseline.get('metrics') if baseline else '{}'}",
        "",
        "## Baseline Result" if baseline_only else "## Best Seen",
    ]
    if best:
        lines += [
            f"- Iteration/Candidate: {best['iteration']}/{best['candidate_id']}",
            f"- Metrics: {best.get('metrics')}",
            f"- Objective: {best.get('objective')}",
            f"- Config: `{best.get('config')}`",
        ]
        if not baseline_only and baseline and baseline.get("objective", {}).get("value") and best.get("objective", {}).get("value"):
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
    report_record = best or baseline
    lines += profiler_metrics_table((report_record or {}).get("profiler") if report_record else None)
    lines += diagnosis_records(_report_diagnoses(report_record))
    lines += _patch_trial_lines(results_dir)
    lines += _hardware_lines(hardware_info)
    lines += ["", "## Next Steps"]
    if baseline_only:
        lines.append("- Use this verified baseline as evidence for a later, separately audited source-optimization trial.")
    else:
        lines.append("- Review failure patterns and profiler data before changing the legacy search budget or source implementation.")
    (results_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return best
