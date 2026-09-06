from __future__ import annotations

import re
from dataclasses import dataclass


BEGIN_RE = re.compile(r"^\s*#\s*BEGIN_AGENT_PATCH:\s*([A-Za-z_][A-Za-z0-9_-]*)\s*$")
END_RE = re.compile(r"^\s*#\s*END_AGENT_PATCH\s*$")


@dataclass(frozen=True)
class PatchRegion:
    name: str
    start_line: int
    end_line: int
    body_start_line: int
    body_end_line: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "name": self.name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "body_start_line": self.body_start_line,
            "body_end_line": self.body_end_line,
        }


def find_patch_regions(text: str) -> list[PatchRegion]:
    lines = text.splitlines(keepends=True)
    regions: list[PatchRegion] = []
    open_region: tuple[str, int] | None = None
    for index, line in enumerate(lines, start=1):
        begin = BEGIN_RE.match(line.rstrip("\r\n"))
        if begin:
            if open_region is not None:
                raise ValueError("nested patch regions are not allowed")
            open_region = (begin.group(1), index)
            continue
        if END_RE.match(line.rstrip("\r\n")):
            if open_region is None:
                raise ValueError("END_AGENT_PATCH without matching BEGIN_AGENT_PATCH")
            name, start_line = open_region
            regions.append(
                PatchRegion(
                    name=name,
                    start_line=start_line,
                    end_line=index,
                    body_start_line=start_line + 1,
                    body_end_line=index - 1,
                )
            )
            open_region = None
    if open_region is not None:
        raise ValueError(f"patch region {open_region[0]} is missing END_AGENT_PATCH")
    return regions


def replace_region(text: str, region_name: str, replacement_body: str) -> str:
    lines = text.splitlines(keepends=True)
    regions = {region.name: region for region in find_patch_regions(text)}
    if region_name not in regions:
        raise ValueError(f"patch region not found: {region_name}")
    region = regions[region_name]
    replacement_lines = replacement_body.splitlines(keepends=True)
    if replacement_body and not replacement_body.endswith(("\n", "\r")):
        replacement_lines[-1] = replacement_lines[-1] + "\n"
    before = lines[: region.body_start_line - 1]
    after = lines[region.body_end_line:]
    return "".join(before + replacement_lines + after)
