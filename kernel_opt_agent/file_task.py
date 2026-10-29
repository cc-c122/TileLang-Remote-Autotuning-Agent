"""File-driven execution contracts shared by Web intake and existing runners."""
from __future__ import annotations

import ast
import base64
import hashlib
import inspect
import json
import math
import os
import re
import shlex
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from kernel_opt_agent.benchmark.correctness import CorrectnessResult
from kernel_opt_agent.benchmark.parser import BenchmarkMetrics
from kernel_opt_agent.config_model import FileTaskConfig, TestFilesConfig, python_sample_path
from kernel_opt_agent.sample_security import ensure_no_link_components


class FileTaskError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_files(root: Path, entry: str, tests: TestFilesConfig) -> None:
    root = root.resolve()
    for role, value in (("operator", entry), ("correctness", tests.correctness), ("benchmark", tests.benchmark)):
        name = python_sample_path(value)
        path = root.joinpath(*PurePosixPath(name).parts)
        ensure_no_link_components(path)
        path.resolve().relative_to(root)
        if not path.is_file():
            raise FileTaskError("sample_file_missing", f"sample file not found for {role}")


def prepare_contract(root: Path, entry: str, tests: TestFilesConfig, attest: bool) -> FileTaskConfig:
    validate_files(root, entry, tests)
    files = sorted(path for path in root.rglob("*") if path.is_file())
    protected = {}
    modules = {}
    for path in files:
        ensure_no_link_components(path)
        relative = path.relative_to(root).as_posix()
        if relative != entry:
            protected[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.suffix == ".py":
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            modules[".".join(parts)] = path
    required = set()
    visited = set()

    def visit(path, script_dir):
        key = (path, script_dir)
        if key in visited:
            return
        visited.add(key)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=path.name)
        except (SyntaxError, UnicodeError) as exc:
            raise FileTaskError("python_script_incompatible", f"cannot parse Python script: {path.relative_to(root).as_posix()}") from exc
        module_name = next((name for name, candidate in modules.items() if candidate == path), "")
        package = module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]
        search_prefix = ".".join(script_dir.relative_to(root).parts)
        # Conditional/deferred/dynamic imports are verified by actual script execution,
        # not guessed to be mandatory dependencies during a read-only preflight.
        for node in tree.body:
            names = []
            relative_import = False
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    relative_import = True
                    prefix = package.split(".")[:len(package.split(".")) - node.level + 1]
                    name = ".".join([*prefix, *([node.module] if node.module else [])])
                else:
                    name = node.module or ""
                names = [name, *(name + "." + alias.name if name else alias.name for alias in node.names)]
            for name in names:
                if not name or name == "__future__":
                    continue
                top = name.split(".")[0]
                sibling_name = search_prefix + "." + name if search_prefix and not relative_import else name
                sibling_top = search_prefix + "." + top if search_prefix and not relative_import else top
                local = modules.get(sibling_name) or modules.get(name) or modules.get(sibling_top) or modules.get(top)
                local_namespace = any(key.startswith(top + ".") or key.startswith(sibling_top + ".") for key in modules)
                if local:
                    visit(local, script_dir)
                elif not local_namespace:
                    required.add(top)

    for name in (entry, tests.correctness, tests.benchmark):
        path = root.joinpath(*PurePosixPath(name).parts)
        visit(path, root if name == entry else path.parent)
    return FileTaskConfig(test_files=tests, attest_entry=attest, protected_files=protected, required_modules=sorted(required))


def _test_bootstrap():
    import hashlib
    import os
    import runpy
    import sys
    from pathlib import Path

    role, entry_name, script_name = sys.argv[1:4]
    root = Path.cwd().resolve()
    entry = (root / entry_name).resolve()
    entry.relative_to(root)
    cache = root / ".kernel_opt_agent" / "runtime"
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("TMPDIR", "XDG_CACHE_HOME", "TILELANG_CACHE_DIR"):
        os.environ[name] = str(cache)
    if role == "syntax":
        compile(entry.read_bytes(), str(entry), "exec")
        return
    script = (root / script_name).resolve()
    script.relative_to(root)
    executed = []

    def audit(event, args):
        if event == "exec":
            try:
                if Path(args[0].co_filename).resolve() == entry:
                    executed.append(hashlib.sha256(entry.read_bytes()).hexdigest())
            except (OSError, ValueError):
                pass

    sys.addaudithook(audit)
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(script.parent), str(root)]
    sys.argv = [str(script)]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise
    finally:
        if executed:
            print("FILE_TASK_ENTRY_SHA256=" + executed[-1], flush=True)
    if not executed:
        print("FILE_TASK_COMPATIBILITY_ERROR entry_not_executed", file=sys.stderr)
        raise SystemExit(78)


def python_command(runner_type: str, script: str, *arguments: str) -> str:
    executable = sys.executable if runner_type == "local" else "python3"
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    bootstrap = "exec(__import__('base64').b64decode('" + encoded + "'))"
    values = [executable, "-B", "-c", bootstrap, *arguments]
    if runner_type == "local" and os.name == "nt":
        return " ".join('"' + value.replace('"', '\\"') + '"' for value in values)
    return " ".join(shlex.quote(value) for value in values)


def automatic_commands(runner_type: str, entry: str, tests: TestFilesConfig) -> dict[str, str]:
    script = inspect.getsource(_test_bootstrap) + "\n_test_bootstrap()\n"
    return {
        "build_command": python_command(runner_type, script, "syntax", entry, entry),
        "correctness_command": python_command(runner_type, script, "correctness", entry, tests.correctness),
        "benchmark_command": python_command(runner_type, script, "benchmark", entry, tests.benchmark),
    }


def _dependency_probe():
    import importlib
    import json
    import os
    import sys
    from pathlib import Path

    cache = Path.cwd() / ".kernel_opt_agent" / "preflight"
    cache.mkdir(parents=True, exist_ok=True)
    for name in ("HOME", "TMPDIR", "XDG_CACHE_HOME"):
        os.environ[name] = str(cache)
    modules = json.loads(sys.argv[1])
    checks = []
    for name in modules:
        try:
            importlib.import_module(name)
            checks.append({"module": name, "available": True})
        except Exception as exc:
            checks.append({"module": name, "available": False, "exception_type": type(exc).__name__})
    print("FILE_TASK_PREFLIGHT_RESULT " + json.dumps({"python_executable": sys.executable, "python_version": sys.version.split()[0], "python_compatible": sys.version_info >= (3, 10), "dependencies": checks}, sort_keys=True))


def dependency_command(runner_type: str, contract: FileTaskConfig) -> str:
    script = inspect.getsource(_dependency_probe) + "\n_dependency_probe()\n"
    return python_command(runner_type, script, json.dumps(contract.required_modules))


def audit_files(runner: Any, expected: dict[str, str]) -> dict[str, str]:
    actual = {name: runner.file_sha256(name) for name in expected}
    if actual != expected:
        raise FileTaskError("protected_file_changed", "protected test/reference/source artifact hash changed")
    return actual


def restore_files(runner: Any, original: Path, expected: dict[str, str]) -> dict[str, Any]:
    try:
        if hasattr(runner, "upload"):
            runner.upload(original)
        else:
            root = runner.workspace.resolve()
            for name in expected:
                target = (root / name).resolve()
                target.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original / name, target)
        return {"verified": True, "hashes": audit_files(runner, expected)}
    except Exception as exc:
        return {"verified": False, "exception_type": type(exc).__name__}


_CORRECTNESS = re.compile(r'^CORRECTNESS_RESULT status=(PASS|FAIL) max_error=(nan|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?) reason="([^"\r\n]*)"$')
_BENCHMARK = re.compile(r'^BENCHMARK_RESULT\s+(.+)$')
_NUMBER = re.compile(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|nan')


def binding_valid(stdout: str, expected_hash: str | None) -> bool:
    if expected_hash is None:
        return True
    hashes = [line.split("=", 1)[1] for line in stdout.splitlines() if line.startswith("FILE_TASK_ENTRY_SHA256=")]
    return hashes == [expected_hash]


def strict_correctness(stdout: str, stderr: str, returncode: int, expected_hash: str | None = None) -> CorrectnessResult:
    rows = [line for line in stdout.splitlines() if line.startswith("CORRECTNESS_RESULT")]
    match = _CORRECTNESS.fullmatch(rows[0]) if len(rows) == 1 else None
    if match is None or not binding_valid(stdout, expected_hash):
        return CorrectnessResult(False, "FAIL", None, "script_protocol_incompatible: require one complete CORRECTNESS_RESULT and candidate entry execution", True)
    error = None if match[2] == "nan" else float(match[2])
    if error is not None and (not math.isfinite(error) or error < 0):
        return CorrectnessResult(False, "FAIL", None, "script_protocol_incompatible: invalid max_error", True)
    passed = match[1] == "PASS" and returncode == 0
    return CorrectnessResult(passed, match[1], error, match[3])


def strict_benchmark(stdout: str, expected_hash: str | None = None) -> BenchmarkMetrics:
    rows = [line for line in stdout.splitlines() if line.startswith("BENCHMARK_RESULT")]
    match = _BENCHMARK.fullmatch(rows[0]) if len(rows) == 1 else None
    if match is not None and binding_valid(stdout, expected_hash):
        fields = {}
        for token in match[1].split():
            pair = token.split("=", 1)
            if len(pair) != 2 or pair[0] in fields or pair[0] not in {"latency_ms", "tflops", "bandwidth_gbps"} or not _NUMBER.fullmatch(pair[1]):
                break
            fields[pair[0]] = None if pair[1] == "nan" else float(pair[1])
        else:
            latency = fields.get("latency_ms")
            if latency is not None and math.isfinite(latency) and latency > 0 and all(value is None or math.isfinite(value) and value >= 0 for value in fields.values()):
                return BenchmarkMetrics(latency, fields.get("tflops"), fields.get("bandwidth_gbps"))
    return BenchmarkMetrics(parse_error=True, reason="script_protocol_incompatible: require one BENCHMARK_RESULT with finite positive latency_ms and candidate entry execution")
