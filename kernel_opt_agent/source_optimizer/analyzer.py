from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CopyLoopTarget:
    target_id: str
    function_name: str
    start_line: int
    end_line: int
    indent: str
    source_expr: str
    destination_expr: str
    extent_expr: str
    dsl_alias: str


@dataclass(frozen=True)
class AnalysisResult:
    targets: tuple[CopyLoopTarget, ...]
    reason: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _call_name(node: ast.AST) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    if isinstance(node, ast.Name):
        return "", node.id
    return None


def _same_expr(left: ast.AST, right: ast.AST) -> bool:
    return ast.dump(left, include_attributes=False) == ast.dump(right, include_attributes=False)


def _is_pure_index(node: ast.AST) -> bool:
    return isinstance(node, (ast.Name, ast.Constant))


def _slice_items(node: ast.Subscript) -> list[ast.AST]:
    return list(node.slice.elts) if isinstance(node.slice, ast.Tuple) else [node.slice]


def _shape_last_extent(shape: ast.AST, assignments: dict[str, ast.AST]) -> ast.AST | None:
    if isinstance(shape, ast.Name) and shape.id in assignments:
        shape = assignments[shape.id]
    if isinstance(shape, (ast.List, ast.Tuple)) and shape.elts:
        return shape.elts[-1]
    return None


class _Analyzer(ast.NodeVisitor):
    def __init__(self, source: str):
        self.source = source
        self.lines = source.splitlines(keepends=True)
        self.dsl_aliases: set[str] = set()
        self.prim_func_names: set[str] = set()
        self.assignments: dict[str, ast.AST] = {}
        self.function_stack: list[str] = []
        self.prim_depth = 0
        self.alloc_extents: list[dict[str, ast.AST]] = []
        self.buffer_extents: list[dict[str, ast.AST]] = []
        self.targets: list[CopyLoopTarget] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in {"tilelang.language", "mctilelang.language", "mcTileLang.language"}:
                self.dsl_aliases.add(alias.asname or alias.name.split(".")[-1])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in {"tilelang", "mctilelang", "mcTileLang"}:
            for alias in node.names:
                if alias.name == "language":
                    self.dsl_aliases.add(alias.asname or alias.name)
        if node.module in {"tilelang.language", "mctilelang.language", "mcTileLang.language"}:
            for alias in node.names:
                if alias.name == "prim_func":
                    self.prim_func_names.add(alias.asname or alias.name)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.assignments[target.id] = node.value
        if self.prim_depth and self.alloc_extents:
            call = node.value if isinstance(node.value, ast.Call) else None
            call_name = _call_name(call.func) if call else None
            if call and call_name and call_name[0] in self.dsl_aliases and call_name[1] in {"alloc_fragment", "alloc_shared"}:
                extent = _shape_last_extent(call.args[0], self.assignments) if call.args else None
                for target in node.targets:
                    if isinstance(target, ast.Name) and extent is not None:
                        self.alloc_extents[-1][target.id] = extent
        self.generic_visit(node)

    def _is_prim_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for decorator in node.decorator_list:
            name = _call_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
            if name and ((name[0] in self.dsl_aliases and name[1] == "prim_func") or (not name[0] and name[1] in self.prim_func_names)):
                return True
        return False

    def _function_buffers(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, ast.AST]:
        buffers: dict[str, ast.AST] = {}
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            annotation = arg.annotation
            if not isinstance(annotation, ast.Call):
                continue
            name = _call_name(annotation.func)
            if not name or name[0] not in self.dsl_aliases or name[1] not in {"Tensor", "Buffer"} or not annotation.args:
                continue
            extent = _shape_last_extent(annotation.args[0], self.assignments)
            if extent is not None:
                buffers[arg.arg] = extent
        return buffers

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        is_prim = self._is_prim_func(node)
        if is_prim:
            self.prim_depth += 1
            self.alloc_extents.append({})
            self.buffer_extents.append(self._function_buffers(node))
        self.generic_visit(node)
        if is_prim:
            self.buffer_extents.pop()
            self.alloc_extents.pop()
            self.prim_depth -= 1
        self.function_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_For(self, node: ast.For) -> None:
        if self.prim_depth:
            target = self._copy_target(node)
            if target is not None:
                self.targets.append(target)
        self.generic_visit(node)

    def _copy_target(self, node: ast.For) -> CopyLoopTarget | None:
        if not isinstance(node.target, ast.Name) or not isinstance(node.iter, ast.Call) or len(node.iter.args) != 1 or node.iter.keywords:
            return None
        call_name = _call_name(node.iter.func)
        if not call_name or call_name[0] not in self.dsl_aliases or call_name[1] != "Parallel":
            return None
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Assign) or len(node.body[0].targets) != 1:
            return None
        assignment = node.body[0]
        destination = assignment.targets[0]
        source = assignment.value
        if not isinstance(destination, ast.Subscript) or not isinstance(source, ast.Subscript):
            return None
        if not isinstance(destination.value, ast.Name) or not isinstance(source.value, ast.Name):
            return None
        if destination.value.id == source.value.id:
            return None
        loop_name = node.target.id
        destination_items = _slice_items(destination)
        source_items = _slice_items(source)
        if len(destination_items) != 1 or not isinstance(destination_items[0], ast.Name) or destination_items[0].id != loop_name:
            return None
        if not source_items or not isinstance(source_items[-1], ast.Name) or source_items[-1].id != loop_name:
            return None
        if any(not _is_pure_index(item) for item in source_items[:-1]):
            return None
        extent = node.iter.args[0]
        if not _is_pure_index(extent):
            return None
        destination_extent = self.alloc_extents[-1].get(destination.value.id)
        source_extent = self.buffer_extents[-1].get(source.value.id)
        if destination_extent is None or source_extent is None:
            return None
        if not (_same_expr(extent, destination_extent) and _same_expr(extent, source_extent)):
            return None
        extent_text = ast.get_source_segment(self.source, extent) or ast.unparse(extent)
        source_prefix = ", ".join(ast.get_source_segment(self.source, item) or ast.unparse(item) for item in source_items[:-1])
        source_expr = f"{source.value.id}[{source_prefix + ', ' if source_prefix else ''}0:{extent_text}]"
        destination_expr = destination.value.id
        raw_line = self.lines[node.lineno - 1]
        indent = raw_line[: len(raw_line) - len(raw_line.lstrip())]
        identity = f"{'/'.join(self.function_stack)}:{node.lineno}:{source_expr}:{destination_expr}"
        return CopyLoopTarget(
            target_id="copy-" + sha256_bytes(identity.encode("utf-8"))[:12],
            function_name=".".join(self.function_stack),
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            indent=indent,
            source_expr=source_expr,
            destination_expr=destination_expr,
            extent_expr=extent_text,
            dsl_alias=call_name[0],
        )


def analyze_source(source: str) -> AnalysisResult:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return AnalysisResult((), f"entry source is not valid Python: {exc.msg}")
    analyzer = _Analyzer(source)
    analyzer.visit(tree)
    targets = tuple(sorted(analyzer.targets, key=lambda item: (item.start_line, item.target_id)))
    if not targets:
        return AnalysisResult((), "no statically verified direct 1D T.Parallel copy loop was found")
    return AnalysisResult(targets)


def rewrite_copy_loop(source: str, target: CopyLoopTarget) -> str:
    lines = source.splitlines(keepends=True)
    replacement = f"{target.indent}{target.dsl_alias}.copy({target.source_expr}, {target.destination_expr})\n"
    lines[target.start_line - 1 : target.end_line] = [replacement]
    rewritten = "".join(lines)
    ast.parse(rewritten)
    return rewritten
