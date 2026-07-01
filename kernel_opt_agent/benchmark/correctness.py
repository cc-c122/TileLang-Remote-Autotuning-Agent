from __future__ import annotations

import math
import re
from dataclasses import dataclass


STRICT_RE = re.compile(
    r'CORRECTNESS_RESULT\s+status=(?P<status>PASS|FAIL)\s+max_error=(?P<max_error>nan|[-+]?[0-9]*\.?[0-9]+)\s+reason="(?P<reason>[^"]*)"',
    re.IGNORECASE,
)


@dataclass
class CorrectnessResult:
    passed: bool
    status: str
    max_error: float | None
    reason: str
    parse_error: bool = False


def _to_float(value: str) -> float | None:
    if value.lower() == "nan":
        return None
    parsed = float(value)
    return None if math.isnan(parsed) else parsed


def parse_correctness(stdout: str, stderr: str, returncode: int) -> CorrectnessResult:
    text = stdout + "\n" + stderr
    match = STRICT_RE.search(text)
    if match:
        status = match.group("status").upper()
        passed = status == "PASS" and returncode == 0
        return CorrectnessResult(passed, status, _to_float(match.group("max_error")), match.group("reason"))
    fail_words = re.search(r"\b(fail|failed|error|mismatch)\b", text, re.IGNORECASE)
    max_match = re.search(r"(?:max[_ ]?error|relative[_ ]?error)\s*[:=]\s*(nan|[-+]?[0-9]*\.?[0-9]+)", text, re.I)
    max_error = _to_float(max_match.group(1)) if max_match else None
    if returncode != 0 or fail_words:
        return CorrectnessResult(False, "FAIL", max_error, "fallback detected failure", parse_error=True)
    if re.search(r"\bpass(?:ed)?\b", text, re.IGNORECASE):
        return CorrectnessResult(True, "PASS", max_error, "fallback detected pass", parse_error=True)
    return CorrectnessResult(False, "FAIL", max_error, "correctness result not found", parse_error=True)

