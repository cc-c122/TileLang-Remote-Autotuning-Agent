"""Conservative static mapping for the repository's Paged Attention sample.

This is source/codegen audit evidence, NOT runtime launch or PMU attribution.
Unsupported generated syntax is left unmapped instead of guessing by name/order.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re
from typing import Any


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _role(symbols: set[str]) -> str | None:
    if {"Q", "K", "V", "Output_partial", "glse"} <= symbols and "Output" not in symbols:
        return "split"
    if {"Output", "Output_partial", "glse"} <= symbols and not {"Q", "K", "V"} & symbols:
        return "combine"
    return None


def _generated_functions(text: str) -> list[dict[str, Any]]:
    # Strip comments/strings, preserving character offsets for spans and hashes.
    masked = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda match: "".join("\n" if c == "\n" else " " for c in match.group()), text, flags=re.S)
    pattern = r'__global__\s+void\s+(?:__launch_bounds__\([^)]*\)\s*)?(\w+)\s*\(([^;{}]*)\)\s*\{'
    functions = []
    for match in re.finditer(pattern, masked):
        depth, end = 1, match.end()
        while end < len(masked) and depth:
            depth += (masked[end] == "{") - (masked[end] == "}")
            end += 1
        if depth:
            continue
        symbols = set(re.findall(r"\b\w+\b", match.group(2)))
        body = masked[match.end():end]
        functions.append({"name": match.group(1), "role_candidate": _role(symbols),
            "line_start": text.count("\n", 0, match.start()) + 1,
            "line_end": text.count("\n", 0, end) + 1,
            "function_sha256": hashlib.sha256(text[match.start():end].encode()).hexdigest(),
            "mma_call_sites": len(re.findall(r"\b__builtin_mxc_mma_\w+\s*\(", body)),
            "buffer_symbols": sorted(symbols & {"Q", "K", "V", "Output", "Output_partial", "glse"})})
    return functions


def map_paged_attention_kernels(source_path: Path, generated_path: Path, host_path: Path | None,
                                report_paths: list[Path], modification_lines: tuple[int, int] | None = None) -> dict[str, Any]:
    source = source_path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    regions = []
    warnings = ["runtime_launch_identity_unconfirmed", "report_filename_is_not_launch_identity"]
    try:
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.With) or not any(
                isinstance(item.context_expr, ast.Call) and isinstance(item.context_expr.func, ast.Attribute)
                and isinstance(item.context_expr.func.value, ast.Name) and item.context_expr.func.value.id == "T"
                and item.context_expr.func.attr == "Kernel" for item in node.items
            ):
                continue
            outputs = {child.value.id for child in ast.walk(node) if isinstance(child, ast.Subscript)
                       and isinstance(child.ctx, ast.Store) and isinstance(child.value, ast.Name)}
            outputs &= {"Q", "K", "V", "Output", "Output_partial", "glse"}
            role = "split" if {"Output_partial", "glse"} <= outputs and "Output" not in outputs else "combine" if outputs == {"Output"} else None
            regions.append({"role_candidate": role, "line_start": node.lineno, "line_end": node.end_lineno,
                "source_region_sha256": hashlib.sha256("".join(lines[node.lineno-1:node.end_lineno]).encode()).hexdigest(),
                "output_buffers": sorted(outputs),
                "contains_modification": bool(modification_lines and node.lineno <= modification_lines[0]
                                              <= modification_lines[1] <= node.end_lineno)})
    except SyntaxError:
        warnings.append("source_parse_failed")
    functions = _generated_functions(generated_path.read_text(encoding="utf-8"))
    host_calls = []
    if host_path and host_path.is_file():
        host = host_path.read_text(encoding="utf-8")
        for match in re.finditer(r"TVMFFIFunctionCall\((\w+)_packed\s*,", host):
            if any(item["name"] == match.group(1) for item in functions):
                host_calls.append({"kernel_name": match.group(1), "line": host.count("\n", 0, match.start()) + 1,
                                   "kind": "static_host_call_site_not_runtime_event"})
    else:
        warnings.append("generated_host_code_missing")
    reports = []
    for path in sorted(report_paths):
        match = re.fullmatch(r"(\d+)_(.+)_dumped_result\.json", path.name)
        reports.append({"file_name": path.name, "sha256": _hash(path),
                        "report_slot": int(match.group(1)) if match else None,
                        "kernel_label": match.group(2) if match else None,
                        "launch_id": None, "attribution_verified": False})
    mappings = []
    for region in regions:
        matches = [item for item in functions if item["role_candidate"] == region["role_candidate"]] if region["role_candidate"] else []
        mappings.append({"source_region": region, "generated_candidates": matches,
            "status": "static_candidate" if len(matches) == 1 else "unmapped",
            "report_candidates": [item for item in reports if len(matches) == 1 and item["kernel_label"] == matches[0]["name"]],
            "runtime_launches": [], "planning_eligible": False})
    return {"schema_version": "v2.mcprofiler_kernel_mapping.v1", "operator": "paged_attention_decode",
            "source_sha256": _hash(source_path), "generated_sha256": _hash(generated_path),
            "host_sha256": _hash(host_path) if host_path and host_path.is_file() else None,
            "mappings": mappings, "static_host_calls": host_calls,
            "reports": reports, "warnings": warnings, "runtime_attribution_verified": False}
