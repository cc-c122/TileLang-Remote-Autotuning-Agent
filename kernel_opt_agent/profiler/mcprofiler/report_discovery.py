from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class McProfilerCase:
    case_root: Path
    report_path: Path | None
    sha256sums_path: Path | None
    readme_path: Path | None
    manifest_path: Path | None

    @property
    def artifact_id(self) -> str:
        suffix = self.report_path.name if self.report_path else "missing:report_dumped_result.json"
        return f"{self.case_root.name}:{suffix}"


def discover_case(path: str | Path) -> McProfilerCase:
    root = Path(path).expanduser()
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    explicit_report = root if root.is_file() else None
    if explicit_report is not None:
        if not (root.name == "report_dumped_result.json" or root.name.endswith("_dumped_result.json")):
            raise ValueError("expected a native mcProfiler dumped result JSON")
        root = root.parent.parent if root.parent.name == "case_report" else root.parent
    report_candidates = [
        root / "case_report" / "report_dumped_result.json",
        root / "report_dumped_result.json",
    ]
    report_path = explicit_report or next((candidate for candidate in report_candidates if candidate.exists()), None)
    sha_path = root / "SHA256SUMS.txt"
    readme_path = root / "README.md"
    manifest_path = root / "manifest.json"
    return McProfilerCase(
        case_root=root,
        report_path=report_path,
        sha256sums_path=sha_path if sha_path.exists() else None,
        readme_path=readme_path if readme_path.exists() else None,
        manifest_path=manifest_path if manifest_path.exists() else None,
    )
