# File-Driven Task Intake

This is an additive `POST /api/tasks` contract, using the existing upload store,
task manager, serial worker, command guard and controlled source optimizer.
It does not add parameter search or import the unmerged PR #72 implementation.

## Request Contract

The confirmed field names are `sample.entry_file`, `test_files.correctness` and
`test_files.benchmark`. All three paths are relative to the uploaded sample root.
See [the example request](examples/file_task_request.json). Obtain `upload_id`
from the existing `POST /api/samples/upload` multipart endpoint first. Users do
not author config, run request or settings YAML files.

```json
{
  "sample": {
    "source_type": "upload",
    "upload_id": "<upload response upload_id>",
    "entry_file": "kernel.py"
  },
  "test_files": {
    "correctness": "correctness.py",
    "benchmark": "benchmark.py"
  },
  "runner": {"type": "local"}
}
```

Omit `commands` for automatic syntax/correctness/benchmark commands. The backend
quotes paths, uses the selected Python interpreter, runs in the task workspace
and passes every command through command guard. Spaces in filenames work;
absolute paths, parent traversal, links, shell metacharacters and non-Python
test files are rejected. The operator cannot also be a protected test file.
Tests can be in subdirectories; their parent and the sample root are on the
Python import path. Relative package imports in a directly executed script
retain Python's normal compatibility restrictions.

For file requests, omission of `optimization` enables the existing source loop.
Explicit `optimization.enabled=false` requests baseline validation only. An
unsupported source transformation returns `execution_mode=baseline_only`,
`source_optimization.status=unavailable` and its reason, not successful optimization.

Explicit `commands` still select advanced mode, including when `test_files` is
present. They are not replaced. With test files they retain hash protection,
dependency preflight and strict result parsing; automatic entry-execution
attestation is not claimed for arbitrary advanced commands. Advanced users are
responsible for their command/harness binding. Legacy requests without
`test_files`, V1 config and CLI/run-request behavior are unchanged.

## Direct Python Script Protocol

Both test files must be directly runnable Python programs, not uninvoked pytest
functions. They must load the workspace operator, execute the intended workload,
and print exactly one applicable structured record to stdout:

```text
CORRECTNESS_RESULT status=PASS max_error=0.0001 reason="reference; fixed atol/rtol"
BENCHMARK_RESULT latency_ms=1.23 tflops=nan bandwidth_gbps=nan
```

Correctness may print `FAIL`. The benchmark requires a finite positive
`latency_ms`; optional unavailable metrics can be omitted or `nan`. Scientific
notation is accepted. Empty output, arbitrary `PASS`, regex latency fallbacks,
duplicate records, invalid numbers or only exit code zero are incompatible.
The task reports `script_protocol_incompatible`; there is no LLM timing guess.

In automatic mode, a backend-generated Python wrapper uses an execution audit
hook to verify that the selected workspace entry file was executed, and records
its SHA-256. The controller compares it with that trial's expected source hash.
Loading an installed/old operator outside the workspace fails this check.
This proves entry loading and source identity, not mathematical completeness of
an arbitrary user test or integrity against deliberately hostile Python code.
User-supplied scripts remain executable trusted code, not an OS sandbox.

## Preflight And Protected Files

- Check uploaded file presence before creating a task; recheck integrity during
  materialization. Validate Python source and hash every original auxiliary file.
- Import unconditional module-level external dependencies in an isolated
  preflight workspace using the execution interpreter (Python >= 3.10). Each
  runner command has a timeout. Missing/broken dependencies fail with explicit
  module status; nothing is installed and no driver/system configuration changes.
- Conditional, deferred and dynamic dependencies cannot be inferred reliably;
  they remain subject to actual script execution, with captured failure logs.
- SSH uses the existing memory session. If no session is provided, the backend
  can acquire a task-scoped lease from saved SSH settings and password env/key
  path. Credentials are not placed in commands or temporarily swapped through
  process-wide environment variables.
- A single stable identifiable GPU binds automatically. Multiple unresolved GPUs,
  absent devices, unverifiable identity or changed binding fail closed. Running
  task leases keep their own credentials through completion.
- Local is an explicit no-GPU demo/test mode. Its preflight never claims real
  GPU validation. No built-in hardware profile substitutes for measured identity.
- Only the authorized operator entry is changed. Correctness/benchmark scripts,
  reference implementations and all other uploaded files are hash-protected.
  Hashes are checked before/after each command and throughout the existing source
  loop. Recovery is hash-verified; failed candidates cannot become best.
- Correctness precedes benchmark. Source benchmark repeats, baseline recheck,
  noise/drift checks and performance thresholds are unchanged. Regressions are
  recorded and baseline retained. Cancellation starts no later repeat/trial after
  the current local command ends; SSH retains managed cancellable execution.

## Results

Existing task status/events/results/download APIs remain the entry points.
Results add optional `preflight` and `file_task` objects:

```json
{
  "preflight": {
    "status": "passed",
    "runner": "local",
    "gpu_check": "local_demo_no_gpu_validation",
    "python_compatible": true,
    "dependencies": []
  },
  "file_task": {
    "input_mode": "files",
    "entry_file": "kernel.py",
    "test_files": {"correctness": "correctness.py", "benchmark": "benchmark.py"},
    "entry_execution_attestation": true,
    "runner_mode": "local_demo"
  }
}
```

The backend saves `preflight.json`, `preflight.stdout.log`,
`preflight.stderr.log`, `file_task_manifest.json`, internal run request/effective
config, ordinary trial records and source optimization/rollback evidence.
Failures also have `file_task_failure.json`. These managed generated files must
not be committed. Known credential values are redacted from recorded output.

## Validation Boundary

```text
python -m unittest tests.test_file_task_intake
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests
```

For real HTTP CPU-only intake (not TestClient), run the ordinary server in one
terminal and the smoke in another:

```text
python -m kernel_opt_agent.server.app --port 8884
python -m scripts.smoke_file_task_local --base-url http://127.0.0.1:8884
```

This smoke uploads four files, omits commands, runs a real CPU reference check
and five measured CPU timing samples, polls the existing APIs and downloads the
baseline/report. Its plain Python source has no eligible TileLang transformation;
the asserted result is explicitly baseline-only/unavailable, not an optimized kernel.

No-GPU integration uses real local Python subprocesses plus a clearly labeled
test-only DSL and synthetic deterministic timing values. It covers acceptance
mechanics, rejection, test/reference protection, regression and cancellation;
it is not GPU performance or architecture-adaptation evidence.

Still requiring real GPU acceptance on this branch:

- C500/MXMACA and CUDA Python dependency/import compatibility and selected-device
  binding through ordinary Web file requests.
- A real directly runnable correctness harness and repeated benchmark on the
  protected uploaded TileLang source, including generated-code availability.
- An eligible controlled transformation, actual unchanged performance gates,
  real acceptance/rejection and download identity.
- Remote cancellation while executing a candidate and verified process cleanup
  and source/test hash restoration. Earlier-branch smoke is not credited here.
- Script-specific dynamic imports, native extensions or non-Python launchers
  outside this first-phase compatibility contract.
