from __future__ import annotations

import argparse
import json
from pathlib import Path

from kernel_opt_agent.diagnosis import diagnose_from_evidence_records, profiler_observations_to_evidence
from kernel_opt_agent.profiler.mxmaca_profiler import MxmacaProfiler
from kernel_opt_agent.profiler.mcprofiler import parse_mcprofiler_case


def import_report(case_dir: Path, out_dir: Path, source_trial_id: str | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    parsed = parse_mcprofiler_case(case_dir)
    result = MxmacaProfiler().collect({"mcprofiler_case_path": str(case_dir)})
    evidence_records = profiler_observations_to_evidence(
        result,
        source_trial_id or parsed.metadata.get("case_name") or case_dir.name,
    )
    diagnoses = diagnose_from_evidence_records(evidence_records)
    parsed_payload = parsed.to_dict()
    evidence_payload = [item.to_dict() for item in evidence_records]
    diagnosis_payload = [item.to_dict() for item in diagnoses]
    (out_dir / "parsed_mcprofiler_case.json").write_text(json.dumps(parsed_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    with (out_dir / "profiler_results.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(result.to_dict(), ensure_ascii=False, default=str) + "\n")
    with (out_dir / "metric_observations.jsonl").open("w", encoding="utf-8") as handle:
        for item in evidence_payload:
            handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    with (out_dir / "diagnoses.jsonl").open("w", encoding="utf-8") as handle:
        for item in diagnosis_payload:
            handle.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    return {
        "collection_status": "imported",
        "collection_mode": "report_import",
        "metadata": parsed_payload["metadata"],
        "artifact_id": parsed_payload["artifacts"].get("artifact_id"),
        "evidence_count": len(evidence_payload),
        "diagnosis_count": len(diagnosis_payload),
        "diagnoses": diagnosis_payload,
        "out_dir": str(out_dir),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    payload = import_report(Path(args.case_dir), Path(args.out_dir))
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
