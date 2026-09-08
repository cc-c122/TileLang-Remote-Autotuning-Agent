from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path

from .load_schedule import (
    SharedLoadScheduleTarget,
    find_prefetch_shared_load_targets,
    rewrite_prefetch_shared_load,
)


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
    template: str = "parallel_copy_to_t_copy"
    hypothesis: str = "replace a verified elementwise copy loop with the TileLang bulk copy primitive"
    confidence: str = "medium"


SourceOptimizationTarget = CopyLoopTarget | SharedLoadScheduleTarget


@dataclass(frozen=True)
class AnalysisResult:
    targets: tuple[SourceOptimizationTarget, ...]
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


def _shape_info(shape: ast.AST, assignments: dict[str, ast.AST]) -> tuple[int, ast.AST] | None:
    if isinstance(shape, ast.Name) and shape.id in assignments:
        shape = assignments[shape.id]
    if isinstance(shape, (ast.List, ast.Tuple)) and shape.elts:
        return len(shape.elts), shape.elts[-1]
    return None


def _bound_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
    if node.args.vararg:
        names.add(node.args.vararg.arg)
    if node.args.kwarg:
        names.add(node.args.kwarg.arg)

    class ScopeBindingCollector(ast.NodeVisitor):
        def visit_FunctionDef(self, child: ast.FunctionDef) -> None:
            if child is not node:
                names.add(child.name)

        def visit_AsyncFunctionDef(self, child: ast.AsyncFunctionDef) -> None:
            if child is not node:
                names.add(child.name)

        def visit_ClassDef(self, child: ast.ClassDef) -> None:
            names.add(child.name)

        def visit_Import(self, child: ast.Import) -> None:
            for alias in child.names:
                names.add(alias.asname or alias.name.split(".")[0])

        def visit_ImportFrom(self, child: ast.ImportFrom) -> None:
            for alias in child.names:
                names.add(alias.asname or alias.name)

        def visit_Name(self, child: ast.Name) -> None:
            if isinstance(child.ctx, (ast.Store, ast.Del)):
                names.add(child.id)

    collector = ScopeBindingCollector()
    for statement in node.body:
        collector.visit(statement)
    return names


class _Analyzer(ast.NodeVisitor):
    def __init__(self, source: str):
        self.source = source
        self.lines = source.splitlines(keepends=True)
        self.dsl_aliases: set[str] = set()
        self.prim_func_names: set[str] = set()
        self.assignment_scopes: list[dict[str, ast.AST]] = [{}]
        self.binding_scopes: list[set[str]] = [set()]
        self.shadow_scopes: list[set[str]] = [set()]
        self.function_stack: list[str] = []
        self.prim_stack: list[bool] = []
        self.alloc_extents: list[dict[str, tuple[int, ast.AST]]] = []
        self.buffer_extents: list[dict[str, tuple[int, ast.AST]]] = []
        self.targets: list[CopyLoopTarget] = []

    def _visible_assignments(self) -> dict[str, ast.AST]:
        visible: dict[str, ast.AST] = {}
        for bindings, assignments in zip(self.binding_scopes, self.assignment_scopes):
            for name in bindings:
                visible.pop(name, None)
            visible.update(assignments)
        return visible

    def _dsl_active(self, name: str) -> bool:
        return name in self.dsl_aliases and not any(name in scope for scope in self.shadow_scopes)

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
                self.assignment_scopes[-1][target.id] = node.value
                if target.id in self.dsl_aliases:
                    self.shadow_scopes[-1].add(target.id)
        if self.prim_stack and self.prim_stack[-1] and self.alloc_extents:
            call = node.value if isinstance(node.value, ast.Call) else None
            call_name = _call_name(call.func) if call else None
            if call and call_name and self._dsl_active(call_name[0]) and call_name[1] in {"alloc_fragment", "alloc_shared"}:
                extent = _shape_info(call.args[0], self._visible_assignments()) if call.args else None
                for target in node.targets:
                    if isinstance(target, ast.Name) and extent is not None:
                        self.alloc_extents[-1][target.id] = extent
        self.generic_visit(node)

    def _is_prim_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        for decorator in node.decorator_list:
            name = _call_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
            if name and ((self._dsl_active(name[0]) and name[1] == "prim_func") or (not name[0] and name[1] in self.prim_func_names)):
                return True
        return False

    def _function_buffers(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, tuple[int, ast.AST]]:
        buffers: dict[str, tuple[int, ast.AST]] = {}
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            annotation = arg.annotation
            if not isinstance(annotation, ast.Call):
                continue
            name = _call_name(annotation.func)
            if not name or not self._dsl_active(name[0]) or name[1] not in {"Tensor", "Buffer"} or not annotation.args:
                continue
            extent = _shape_info(annotation.args[0], self._visible_assignments())
            if extent is not None:
                buffers[arg.arg] = extent
        return buffers

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        is_prim = self._is_prim_func(node)
        bound_names = _bound_names(node)
        self.assignment_scopes.append({})
        self.binding_scopes.append(bound_names)
        self.shadow_scopes.append(bound_names & self.dsl_aliases)
        self.prim_stack.append(is_prim)
        if is_prim:
            self.alloc_extents.append({})
            self.buffer_extents.append(self._function_buffers(node))
        self.generic_visit(node)
        if is_prim:
            self.buffer_extents.pop()
            self.alloc_extents.pop()
        self.prim_stack.pop()
        self.shadow_scopes.pop()
        self.binding_scopes.pop()
        self.assignment_scopes.pop()
        self.function_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_For(self, node: ast.For) -> None:
        if self.prim_stack and self.prim_stack[-1]:
            target = self._copy_target(node)
            if target is not None:
                self.targets.append(target)
        self.generic_visit(node)

    def _copy_target(self, node: ast.For) -> CopyLoopTarget | None:
        if node.orelse or not isinstance(node.target, ast.Name) or not isinstance(node.iter, ast.Call) or len(node.iter.args) != 1 or node.iter.keywords:
            return None
        call_name = _call_name(node.iter.func)
        if not call_name or not self._dsl_active(call_name[0]) or call_name[1] != "Parallel":
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
        if any(isinstance(item, ast.Name) and item.id == loop_name for item in source_items[:-1]):
            return None
        extent = node.iter.args[0]
        if not _is_pure_index(extent):
            return None
        destination_info = self.alloc_extents[-1].get(destination.value.id)
        source_info = self.buffer_extents[-1].get(source.value.id)
        if destination_info is None or source_info is None:
            return None
        destination_rank, destination_extent = destination_info
        source_rank, source_extent = source_info
        if destination_rank != len(destination_items) or source_rank != len(source_items):
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
    targets: tuple[SourceOptimizationTarget, ...] = tuple(
        sorted(
            [*analyzer.targets, *find_prefetch_shared_load_targets(source, tree)],
            key=lambda item: (item.start_line, item.target_id),
        )
    )
    if not targets:
        return AnalysisResult((), "no statically verified source optimization target was found")
    return AnalysisResult(targets)


def rewrite_copy_loop(source: str, target: CopyLoopTarget) -> str:
    if target.template != "parallel_copy_to_t_copy":
        raise ValueError("target is not a parallel_copy_to_t_copy target")
    lines = source.splitlines(keepends=True)
    replacement = f"{target.indent}{target.dsl_alias}.copy({target.source_expr}, {target.destination_expr})\n"
    lines[target.start_line - 1 : target.end_line] = [replacement]
    rewritten = "".join(lines)
    ast.parse(rewritten)
    return rewritten


def rewrite_source_target(source: str, target: SourceOptimizationTarget) -> str:
    if target.template == "parallel_copy_to_t_copy" and isinstance(target, CopyLoopTarget):
        return rewrite_copy_loop(source, target)
    if target.template == "prefetch_shared_load" and isinstance(target, SharedLoadScheduleTarget):
        return rewrite_prefetch_shared_load(source, target)
    raise ValueError(f"unsupported or mismatched source optimization target: {target.template}")
