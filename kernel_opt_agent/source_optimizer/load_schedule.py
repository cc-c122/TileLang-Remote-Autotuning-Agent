from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, field


PREFETCH_SHARED_LOAD = "prefetch_shared_load"
_DSL_MODULES = {
    "tilelang",
    "tilelang.language",
    "mctilelang",
    "mctilelang.language",
    "mcTileLang",
    "mcTileLang.language",
}
_PURE_DSL_CALLS = {
    "ceildiv",
    "exp2",
    "floordiv",
    "floormod",
    "if_then_else",
    "infinity",
    "log2",
    "max",
    "min",
}
_LOOP_DSL_CALLS = {"Parallel", "Pipelined", "serial"}
_SYNC_DSL_CALLS = {
    "async_commit_queue",
    "async_wait",
    "barrier",
    "cp_async",
    "ptx_cp_async",
    "sync_threads",
    "tvm_storage_sync",
    "wait",
}


@dataclass(frozen=True)
class SharedLoadScheduleTarget:
    target_id: str
    function_name: str
    start_line: int
    end_line: int
    insert_before_line: int
    source_expr: str
    destination_expr: str
    extent_expr: str
    dsl_alias: str
    original_statement_sha256: str
    template: str = PREFETCH_SHARED_LOAD
    hypothesis: str = (
        "low-confidence scheduling hypothesis: move an independently verified global-to-shared load "
        "earlier to test whether additional load/compute overlap is possible; no speedup is assumed"
    )
    confidence: str = "low"


@dataclass
class _Effects:
    reads: set[str] = field(default_factory=set)
    writes: set[str] = field(default_factory=set)
    global_writes: set[str] = field(default_factory=set)
    unknown_call: bool = False
    synchronization: bool = False
    unsupported_control: bool = False

    def merge(self, other: "_Effects") -> None:
        self.reads.update(other.reads)
        self.writes.update(other.writes)
        self.global_writes.update(other.global_writes)
        self.unknown_call = self.unknown_call or other.unknown_call
        self.synchronization = self.synchronization or other.synchronization
        self.unsupported_control = self.unsupported_control or other.unsupported_control


def _call_name(node: ast.AST) -> tuple[str, str] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return node.value.id, node.attr
    if isinstance(node, ast.Name):
        return "", node.id
    return None


def _root_name(node: ast.AST) -> str | None:
    while isinstance(node, ast.Subscript):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _source_text(source: str, node: ast.AST) -> str:
    return ast.get_source_segment(source, node) or ast.unparse(node)


def _dsl_aliases(tree: ast.Module) -> set[str]:
    aliases: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.Import):
            for item in statement.names:
                if item.name in {"tilelang.language", "mctilelang.language", "mcTileLang.language"}:
                    aliases.add(item.asname or item.name.split(".")[-1])
        elif isinstance(statement, ast.ImportFrom) and statement.module in _DSL_MODULES:
            for item in statement.names:
                if item.name == "language":
                    aliases.add(item.asname or item.name)
    valid: set[str] = set()
    for alias in aliases:
        rebound = False
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for item in statement.names:
                    bound = item.asname or item.name.split(".")[0]
                    if bound == alias and item.name not in {"tilelang.language", "mctilelang.language", "mcTileLang.language"}:
                        rebound = True
            elif isinstance(statement, ast.ImportFrom):
                for item in statement.names:
                    bound = item.asname or item.name
                    if bound == alias and not (statement.module in _DSL_MODULES and item.name == "language"):
                        rebound = True
            elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                rebound = rebound or statement.name == alias
            else:
                rebound = rebound or any(
                    isinstance(child, ast.Name)
                    and isinstance(child.ctx, (ast.Store, ast.Del))
                    and child.id == alias
                    for child in ast.walk(statement)
                )
        if not rebound:
            valid.add(alias)
    return valid


def _function_binds_name(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if any(argument.arg == name for argument in arguments):
        return True
    if node.args.vararg and node.args.vararg.arg == name:
        return True
    if node.args.kwarg and node.args.kwarg.arg == name:
        return True
    for child in ast.walk(node):
        if child is node:
            continue
        if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)) and child.id == name:
            return True
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == name:
            return True
        if isinstance(child, (ast.Import, ast.ImportFrom)):
            for item in child.names:
                if (item.asname or item.name.split(".")[0]) == name:
                    return True
    return False


def _is_prim_func(node: ast.FunctionDef | ast.AsyncFunctionDef, aliases: set[str]) -> str | None:
    for decorator in node.decorator_list:
        name = _call_name(decorator.func if isinstance(decorator, ast.Call) else decorator)
        if name and name[0] in aliases and name[1] == "prim_func" and not _function_binds_name(node, name[0]):
            return name[0]
    return None


def _global_buffers(node: ast.FunctionDef | ast.AsyncFunctionDef, dsl_alias: str) -> set[str]:
    buffers: set[str] = set()
    for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
        annotation = argument.annotation
        if not isinstance(annotation, ast.Call):
            continue
        name = _call_name(annotation.func)
        if name and name[0] == dsl_alias and name[1] in {"Tensor", "Buffer"}:
            buffers.add(argument.arg)
    return buffers


def _shared_buffers(node: ast.FunctionDef | ast.AsyncFunctionDef, dsl_alias: str) -> set[str]:
    buffers: set[str] = set()
    for child in ast.walk(node):
        if not isinstance(child, (ast.Assign, ast.AnnAssign)):
            continue
        value = child.value
        if not isinstance(value, ast.Call):
            continue
        name = _call_name(value.func)
        if not name or name != (dsl_alias, "alloc_shared"):
            continue
        targets = child.targets if isinstance(child, ast.Assign) else [child.target]
        if len(targets) == 1 and isinstance(targets[0], ast.Name):
            buffers.add(targets[0].id)
    return buffers


def _has_buffer_aliasing(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    buffers: set[str],
    shared_buffers: set[str],
    dsl_alias: str,
) -> bool:
    def contains_bare_buffer(value: ast.AST) -> bool:
        if isinstance(value, ast.Name):
            return value.id in buffers
        if isinstance(value, ast.Subscript):
            return any(isinstance(item, ast.Slice) for item in ast.walk(value.slice)) and _root_name(value) in buffers
        if isinstance(value, ast.Attribute) and _root_name(value.value) in buffers:
            return True
        return any(contains_bare_buffer(child) for child in ast.iter_child_nodes(value))

    def is_alias_value(value: ast.AST) -> bool:
        if isinstance(value, ast.Name):
            return value.id in buffers
        if isinstance(value, ast.Subscript) and _root_name(value) in buffers:
            return any(isinstance(item, ast.Slice) for item in ast.walk(value.slice))
        if isinstance(value, ast.Attribute) and _root_name(value.value) in buffers:
            return True
        if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            return any(is_alias_value(item) for item in value.elts)
        if isinstance(value, ast.Dict):
            return any(is_alias_value(item) for item in [*value.keys, *value.values] if item is not None)
        if isinstance(value, ast.Call):
            return any(contains_bare_buffer(item) for item in [*value.args, *(keyword.value for keyword in value.keywords)])
        return False

    shared_allocations: dict[str, int] = {name: 0 for name in shared_buffers}
    for child in ast.walk(node):
        if not isinstance(child, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            continue
        value = child.value
        targets = child.targets if isinstance(child, ast.Assign) else [child.target]
        if is_alias_value(value):
            return True
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in buffers:
                continue
            call_name = _call_name(value.func) if isinstance(value, ast.Call) else None
            if target.id in shared_buffers and call_name == (dsl_alias, "alloc_shared"):
                shared_allocations[target.id] += 1
            else:
                return True
    known_buffer_calls = {
        "clear",
        "copy",
        "fill",
        "gemm",
        "reduce_max",
        "reduce_sum",
    }
    for child in ast.walk(node):
        if isinstance(child, ast.Return) and child.value is not None and contains_bare_buffer(child.value):
            return True
        if not isinstance(child, ast.Call):
            continue
        name = _call_name(child.func)
        if name and name[0] == dsl_alias and name[1] in known_buffer_calls:
            continue
        if any(contains_bare_buffer(item) for item in [*child.args, *(keyword.value for keyword in child.keywords)]):
            return True
    return any(count != 1 for count in shared_allocations.values())


def _read_expression(node: ast.AST, effects: _Effects, dsl_alias: str) -> None:
    if isinstance(node, ast.NamedExpr):
        effects.unsupported_control = True
        return
    if isinstance(node, ast.Name):
        if isinstance(node.ctx, ast.Load) and node.id != dsl_alias:
            effects.reads.add(node.id)
        return
    if isinstance(node, ast.Subscript):
        root = _root_name(node)
        if root:
            effects.reads.add(root)
        else:
            _read_expression(node.value, effects, dsl_alias)
        _read_expression(node.slice, effects, dsl_alias)
        return
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name and name[0] == dsl_alias:
            if name[1] in _SYNC_DSL_CALLS:
                effects.synchronization = True
            elif name[1] not in _PURE_DSL_CALLS and name[1] not in _LOOP_DSL_CALLS:
                effects.unknown_call = True
        else:
            effects.unknown_call = True
        for argument in node.args:
            _read_expression(argument, effects, dsl_alias)
        for keyword in node.keywords:
            _read_expression(keyword.value, effects, dsl_alias)
        return
    for child in ast.iter_child_nodes(node):
        _read_expression(child, effects, dsl_alias)


def _write_target(node: ast.AST, effects: _Effects, global_buffers: set[str], dsl_alias: str) -> None:
    if isinstance(node, ast.Name):
        effects.writes.add(node.id)
        if node.id in global_buffers:
            effects.global_writes.add(node.id)
        return
    if isinstance(node, ast.Subscript):
        root = _root_name(node)
        if root:
            effects.writes.add(root)
            if root in global_buffers:
                effects.global_writes.add(root)
        _read_expression(node.slice, effects, dsl_alias)
        return
    if isinstance(node, (ast.Tuple, ast.List)):
        for item in node.elts:
            _write_target(item, effects, global_buffers, dsl_alias)
        return
    effects.unsupported_control = True


def _dsl_effect_call(
    call: ast.Call,
    effects: _Effects,
    global_buffers: set[str],
    dsl_alias: str,
) -> None:
    name = _call_name(call.func)
    if not name or name[0] != dsl_alias:
        effects.unknown_call = True
        return
    operation = name[1]
    if operation in _SYNC_DSL_CALLS:
        effects.synchronization = True
        return
    read_positions: tuple[int, ...]
    write_positions: tuple[int, ...]
    if operation == "copy":
        read_positions, write_positions = (0,), (1,)
    elif operation in {"clear", "fill"}:
        read_positions, write_positions = tuple(range(1, len(call.args))), (0,)
    elif operation in {"reduce_max", "reduce_sum"}:
        read_positions, write_positions = (0,), (1,)
    elif operation == "gemm":
        read_positions, write_positions = (0, 1), (2,)
    elif operation in _PURE_DSL_CALLS:
        read_positions, write_positions = tuple(range(len(call.args))), ()
    else:
        effects.unknown_call = True
        return
    for index in read_positions:
        if index < len(call.args):
            _read_expression(call.args[index], effects, dsl_alias)
    for index in write_positions:
        if index < len(call.args):
            _write_target(call.args[index], effects, global_buffers, dsl_alias)
    for keyword in call.keywords:
        _read_expression(keyword.value, effects, dsl_alias)


def _statement_effects(statement: ast.stmt, global_buffers: set[str], dsl_alias: str) -> _Effects:
    effects = _Effects()
    if isinstance(statement, ast.Assign):
        _read_expression(statement.value, effects, dsl_alias)
        for target in statement.targets:
            _write_target(target, effects, global_buffers, dsl_alias)
    elif isinstance(statement, ast.AnnAssign):
        if statement.value:
            _read_expression(statement.value, effects, dsl_alias)
        _write_target(statement.target, effects, global_buffers, dsl_alias)
    elif isinstance(statement, ast.AugAssign):
        _read_expression(statement.target, effects, dsl_alias)
        _read_expression(statement.value, effects, dsl_alias)
        _write_target(statement.target, effects, global_buffers, dsl_alias)
    elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        _dsl_effect_call(statement.value, effects, global_buffers, dsl_alias)
    elif isinstance(statement, ast.If):
        _read_expression(statement.test, effects, dsl_alias)
        for child in [*statement.body, *statement.orelse]:
            effects.merge(_statement_effects(child, global_buffers, dsl_alias))
    elif isinstance(statement, ast.For):
        iterator = statement.iter
        name = _call_name(iterator.func) if isinstance(iterator, ast.Call) else None
        if statement.orelse or not name or name[0] != dsl_alias or name[1] not in _LOOP_DSL_CALLS:
            effects.unsupported_control = True
            return effects
        _read_expression(iterator, effects, dsl_alias)
        _write_target(statement.target, effects, global_buffers, dsl_alias)
        for child in statement.body:
            effects.merge(_statement_effects(child, global_buffers, dsl_alias))
    elif isinstance(statement, (ast.Pass,)):
        pass
    else:
        effects.unsupported_control = True
    return effects


def _copy_parts(
    statement: ast.stmt,
    global_buffers: set[str],
    shared_buffers: set[str],
    dsl_alias: str,
) -> tuple[ast.AST, ast.AST, str, str] | None:
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return None
    call = statement.value
    if _call_name(call.func) != (dsl_alias, "copy") or len(call.args) != 2 or call.keywords:
        return None
    source, destination = call.args[:2]
    source_root = _root_name(source)
    destination_root = _root_name(destination)
    if source_root not in global_buffers or not isinstance(destination, ast.Name) or destination_root not in shared_buffers:
        return None
    return source, destination, source_root, destination_root


def _statement_lists(statements: list[ast.stmt], dsl_alias: str):
    yield statements
    for statement in statements:
        if isinstance(statement, ast.If):
            yield from _statement_lists(statement.body, dsl_alias)
            yield from _statement_lists(statement.orelse, dsl_alias)
        elif isinstance(statement, ast.For) and not statement.orelse:
            iterator = statement.iter
            name = _call_name(iterator.func) if isinstance(iterator, ast.Call) else None
            if name and name[0] == dsl_alias and name[1] in _LOOP_DSL_CALLS:
                yield from _statement_lists(statement.body, dsl_alias)
        elif isinstance(statement, ast.With) and len(statement.items) == 1:
            context = statement.items[0].context_expr
            name = _call_name(context.func) if isinstance(context, ast.Call) else None
            if name == (dsl_alias, "Kernel"):
                yield from _statement_lists(statement.body, dsl_alias)


def _destination_is_consumed(
    statements: list[ast.stmt],
    destination: str,
    global_buffers: set[str],
    dsl_alias: str,
) -> bool:
    for statement in statements:
        effects = _statement_effects(statement, global_buffers, dsl_alias)
        if effects.unknown_call or effects.synchronization or effects.unsupported_control:
            return False
        if destination in effects.writes:
            return False
        if destination in effects.reads:
            return True
    return False


def _targets_in_list(
    source: str,
    statements: list[ast.stmt],
    function_name: str,
    global_buffers: set[str],
    shared_buffers: set[str],
    dsl_alias: str,
) -> list[SharedLoadScheduleTarget]:
    targets: list[SharedLoadScheduleTarget] = []
    for candidate_index, statement in enumerate(statements):
        parts = _copy_parts(statement, global_buffers, shared_buffers, dsl_alias)
        if parts is None:
            continue
        source_node, destination_node, source_root, destination_root = parts
        prior_index = None
        for index in range(candidate_index - 1, -1, -1):
            if _copy_parts(statements[index], global_buffers, shared_buffers, dsl_alias) is not None:
                prior_index = index
                break
        if prior_index is None:
            continue
        source_effects = _Effects()
        _read_expression(source_node, source_effects, dsl_alias)
        if source_effects.unknown_call or source_effects.synchronization or source_effects.unsupported_control:
            continue
        dependencies = source_effects.reads - {source_root}
        insertion_index = prior_index + 1
        for index in range(insertion_index, candidate_index):
            effects = _statement_effects(statements[index], global_buffers, dsl_alias)
            if effects.writes & dependencies:
                insertion_index = index + 1
        crossed = statements[insertion_index:candidate_index]
        if not crossed:
            continue
        safe = True
        for crossed_statement in crossed:
            effects = _statement_effects(crossed_statement, global_buffers, dsl_alias)
            if (
                effects.unknown_call
                or effects.synchronization
                or effects.unsupported_control
                or effects.global_writes
                or destination_root in effects.reads
                or destination_root in effects.writes
                or source_root in effects.writes
                or bool(effects.writes & dependencies)
            ):
                safe = False
                break
        if not safe or not _destination_is_consumed(
            statements[candidate_index + 1 :], destination_root, global_buffers, dsl_alias
        ):
            continue
        insert_before = statements[insertion_index]
        if statement.col_offset != insert_before.col_offset:
            continue
        statement_text = _source_text(source, statement)
        source_expr = _source_text(source, source_node)
        destination_expr = _source_text(source, destination_node)
        identity = f"{function_name}:{statement.lineno}:{insert_before.lineno}:{source_expr}:{destination_expr}"
        targets.append(
            SharedLoadScheduleTarget(
                target_id="prefetch-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
                function_name=function_name,
                start_line=statement.lineno,
                end_line=statement.end_lineno or statement.lineno,
                insert_before_line=insert_before.lineno,
                source_expr=source_expr,
                destination_expr=destination_expr,
                extent_expr="",
                dsl_alias=dsl_alias,
                original_statement_sha256=hashlib.sha256(statement_text.encode("utf-8")).hexdigest(),
            )
        )
    return targets


def find_prefetch_shared_load_targets(source: str, tree: ast.Module | None = None) -> tuple[SharedLoadScheduleTarget, ...]:
    tree = tree or ast.parse(source)
    aliases = _dsl_aliases(tree)
    targets: list[SharedLoadScheduleTarget] = []

    def visit_functions(
        statements: list[ast.stmt],
        stack: tuple[str, ...],
        inherited_shadows: frozenset[str],
    ) -> None:
        for node in statements:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            function_stack = (*stack, node.name)
            dsl_alias = _is_prim_func(node, aliases)
            local_shadows = frozenset(alias for alias in aliases if _function_binds_name(node, alias))
            nested_scope = any(
                child is not node and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda))
                for child in ast.walk(node)
            )
            if (
                dsl_alias is not None
                and dsl_alias not in inherited_shadows
                and not isinstance(node, ast.AsyncFunctionDef)
                and not nested_scope
            ):
                global_buffers = _global_buffers(node, dsl_alias)
                shared_buffers = _shared_buffers(node, dsl_alias)
                if global_buffers and shared_buffers and not _has_buffer_aliasing(
                    node, global_buffers | shared_buffers, shared_buffers, dsl_alias
                ):
                    for sibling_list in _statement_lists(node.body, dsl_alias):
                        targets.extend(
                            _targets_in_list(
                                source,
                                sibling_list,
                                ".".join(function_stack),
                                global_buffers,
                                shared_buffers,
                                dsl_alias,
                            )
                        )
            visit_functions(node.body, function_stack, inherited_shadows | local_shadows)

    visit_functions(tree.body, (), frozenset())
    return tuple(sorted(targets, key=lambda item: (item.start_line, item.target_id)))


def rewrite_prefetch_shared_load(source: str, target: SharedLoadScheduleTarget) -> str:
    if target.template != PREFETCH_SHARED_LOAD:
        raise ValueError("target is not a prefetch_shared_load target")
    if target.insert_before_line >= target.start_line:
        raise ValueError("prefetch target must move to an earlier statement")
    tree = ast.parse(source)
    current = next(
        (
            item
            for item in find_prefetch_shared_load_targets(source, tree)
            if item.target_id == target.target_id
            and item.start_line == target.start_line
            and item.insert_before_line == target.insert_before_line
        ),
        None,
    )
    if current is None or current.original_statement_sha256 != target.original_statement_sha256:
        raise ValueError("prefetch target is stale or no longer statically authorized")
    lines = source.splitlines(keepends=True)
    moved = lines[target.start_line - 1 : target.end_line]
    del lines[target.start_line - 1 : target.end_line]
    lines[target.insert_before_line - 1 : target.insert_before_line - 1] = moved
    rewritten = "".join(lines)
    ast.parse(rewritten)
    return rewritten
