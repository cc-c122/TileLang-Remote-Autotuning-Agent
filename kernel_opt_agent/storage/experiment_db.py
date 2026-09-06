from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Callable

from kernel_opt_agent.patcher import PatchTrial


SUMMARY_FIELDS = [
    "run_id",
    "iteration",
    "candidate_id",
    "status",
    "latency",
    "tflops",
    "bandwidth",
    "objective_value",
    "config_hash",
    "kernel_path",
    "patch_path",
]

ABLATION_SUMMARY_FIELDS = [
    "trial_id",
    "patch_ids",
    "status",
    "objective",
    "baseline_value",
    "patched_value",
    "improvement",
    "reason",
]


class ExperimentDB:
    def __init__(self, results_dir: Path, redactor: Callable[[Any], Any] | None = None):
        self.results_dir = results_dir
        self.logs_dir = results_dir / "logs"
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.experiments_path = results_dir / "experiments.jsonl"
        self.failed_path = results_dir / "failed_cases.jsonl"
        self.profiler_path = results_dir / "profiler_results.jsonl"
        self.metric_observations_path = results_dir / "metric_observations.jsonl"
        self.diagnosis_path = results_dir / "diagnosis.jsonl"
        self.diagnoses_path = results_dir / "diagnoses.jsonl"
        self.patch_trials_path = results_dir / "patch_trials.jsonl"
        self.ablation_summary_path = results_dir / "ablation_summary.csv"
        self.summary_path = results_dir / "summary.csv"
        self.records: list[dict[str, Any]] = []
        self.redactor = redactor or (lambda value: value)
        self._init_run_files()

    def _init_run_files(self) -> None:
        self.experiments_path.write_text("", encoding="utf-8")
        self.failed_path.write_text("", encoding="utf-8")
        self.profiler_path.write_text("", encoding="utf-8")
        self.metric_observations_path.write_text("", encoding="utf-8")
        self.diagnosis_path.write_text("", encoding="utf-8")
        self.diagnoses_path.write_text("", encoding="utf-8")
        self.patch_trials_path.write_text("", encoding="utf-8")
        for log_path in self.logs_dir.glob("*.log"):
            log_path.unlink()
        with self.summary_path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=SUMMARY_FIELDS).writeheader()
        with self.ablation_summary_path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=ABLATION_SUMMARY_FIELDS).writeheader()

    def write_logs(self, label: str, stdout: str, stderr: str) -> tuple[Path, Path]:
        stdout_path = self.logs_dir / f"{label}.stdout.log"
        stderr_path = self.logs_dir / f"{label}.stderr.log"
        stdout_path.write_text(self.redactor(stdout or ""), encoding="utf-8")
        stderr_path.write_text(self.redactor(stderr or ""), encoding="utf-8")
        return stdout_path, stderr_path

    def append_patch_trial(self, trial: PatchTrial) -> None:
        with self.patch_trials_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self.redactor(trial.to_dict()), ensure_ascii=False, default=str) + "\n")

    def append(self, record: dict[str, Any]) -> None:
        record = self.redactor(record)
        self.records.append(record)
        with self.experiments_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        if record.get("status") != "benchmark_ok":
            with self.failed_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        profiler_record = {
            "run_id": record.get("run_id"),
            "iteration": record.get("iteration"),
            "candidate_id": record.get("candidate_id"),
            "trial_id": record.get("trial_id"),
            "config_hash": record.get("config_hash"),
            "status": record.get("status"),
            "profiler": record.get("profiler"),
        }
        with self.profiler_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(profiler_record, ensure_ascii=False, default=str) + "\n")
        with self.metric_observations_path.open("a", encoding="utf-8") as f:
            for evidence in record.get("metric_observations") or []:
                metric_observation_record = {
                    "run_id": record.get("run_id"),
                    "iteration": record.get("iteration"),
                    "candidate_id": record.get("candidate_id"),
                    "trial_id": record.get("trial_id"),
                    "config_hash": record.get("config_hash"),
                    "status": record.get("status"),
                    **evidence,
                }
                f.write(json.dumps(metric_observation_record, ensure_ascii=False, default=str) + "\n")
        diagnosis_record = {
            "run_id": record.get("run_id"),
            "iteration": record.get("iteration"),
            "candidate_id": record.get("candidate_id"),
            "trial_id": record.get("trial_id"),
            "config_hash": record.get("config_hash"),
            "status": record.get("status"),
            "bottleneck_diagnosis": record.get("bottleneck_diagnosis") or [],
        }
        with self.diagnosis_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(diagnosis_record, ensure_ascii=False, default=str) + "\n")
        with self.diagnoses_path.open("a", encoding="utf-8") as f:
            for diagnosis in record.get("diagnoses") or []:
                diagnoses_record = {
                    "run_id": record.get("run_id"),
                    "iteration": record.get("iteration"),
                    "candidate_id": record.get("candidate_id"),
                    "trial_id": record.get("trial_id"),
                    "config_hash": record.get("config_hash"),
                    "status": record.get("status"),
                    **diagnosis,
                }
                f.write(json.dumps(diagnoses_record, ensure_ascii=False, default=str) + "\n")
        metrics = record.get("metrics") or {}
        paths = record.get("paths") or {}
        objective = record.get("objective") or {}
        row = {
            "run_id": record.get("run_id"),
            "iteration": record.get("iteration"),
            "candidate_id": record.get("candidate_id"),
            "status": record.get("status"),
            "latency": metrics.get("latency"),
            "tflops": metrics.get("tflops"),
            "bandwidth": metrics.get("bandwidth"),
            "objective_value": objective.get("value"),
            "config_hash": record.get("config_hash"),
            "kernel_path": paths.get("kernel"),
            "patch_path": paths.get("patch"),
        }
        with self.summary_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
            writer.writerow(row)
