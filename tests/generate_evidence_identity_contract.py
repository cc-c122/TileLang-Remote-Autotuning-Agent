"""Generate browser inputs from backend #79's test-only receipt/API fixtures.

Run against an isolated checkout, not a configured live server. No SSH/GPU run.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys


def apply_patch(row, operations):
    result = deepcopy(row)

    def get(path):
        value = result
        for key in path:
            value = value[key]
        return value

    for operation in operations:
        path = operation["path"]
        parent = get(path[:-1])
        if operation["op"] == "remove":
            del parent[path[-1]]
        elif operation["op"] == "add":
            parent[path[-1]] = deepcopy(operation["value"])
        elif operation["op"] == "copy":
            parent[path[-1]] = deepcopy(get(operation["from"]))
        else:
            raise ValueError("Unsupported test patch operation")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases_path = Path(__file__).parent / "fixtures/evidence_identity_cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    # An immutable git-archive may have no .git; check the source file contract too.
    backend = args.backend.resolve()
    identity_file = backend / "kernel_opt_agent/profiler/identity.py"
    if not identity_file.is_file() or hashlib.sha256(identity_file.read_bytes()).hexdigest() != cases["backend_identity_sha256"]:
        raise SystemExit("Pinned backend #79 identity.py is required")
    sys.path.insert(0, str(backend))
    from kernel_opt_agent.profiler.identity import IDENTITY_ALIASES, canonical_identity
    from tests.test_evidence_identity import EvidenceIdentityTests

    fixture = EvidenceIdentityTests()
    try:
        fixture.setUp()
        signed = fixture._sign(fixture.row)
        records = []
        for case in cases["receipt_cases"]:
            raw = apply_patch(signed, case["patch"])
            public, eligible, excluded = fixture._save_and_read(raw)
            values, issues = canonical_identity(raw)
            if issues or case.get("expect_api_eligible") is False:
                assert public["planning_eligible"] is False and not eligible, case["id"]
            else:
                assert public["planning_eligible"] is True and len(eligible) == 1, (case["id"], public["eligibility_reasons"])
            records.append({"id": case["id"], "raw": raw, "api": public, "identity": {"values": values, "issues": issues},
                            "api_planning_eligible": public["planning_eligible"], "planner_eligible_count": len(eligible), "planner_excluded": excluded})
        canonical_cases = []
        inputs = list(cases["canonical_cases"])
        for name, aliases in IDENTITY_ALIASES.items():
            inputs.extend([
                {"id": name + "_null_vs_known", "row": {aliases[0]: None, "collection_scope": {aliases[-1]: "known"}}},
                {"id": name + "_known_vs_null", "row": {aliases[0]: "known", "collection_scope": {aliases[-1]: None}}},
                {"id": name + "_consistent_null", "row": {aliases[0]: None, "collection_scope": {aliases[-1]: None}}},
            ])
        for case in inputs:
            values, issues = canonical_identity(case["row"])
            canonical_cases.append({**case, "expected": {"values": values, "issues": issues}})
        result = {"contract_only": True, "backend_revision": cases["backend_revision"],
                  "backend_identity_sha256": cases["backend_identity_sha256"],
                  "base_identity": canonical_identity(signed)[0],
                  "aliases": {key: list(value) for key, value in IDENTITY_ALIASES.items()},
                  "receipt_cases": records, "canonical_cases": canonical_cases}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"receipt_cases": len(records), "canonical_cases": len(canonical_cases), "output": str(args.output), "contract_only": True}))
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    main()
