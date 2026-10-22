# mcProfiler Attribution Validation

Status: native calibration collected; target-only attribution unverified; real
TileLang Paged Attention source optimization blocked. Keep the PR Draft.

## Scope Findings

- Installed mcProfiler: 3.9.0.29, build c63ea13089. This was queried on the runner,
  not inferred from an archive name.
- The official [3.9.0 usage manual](https://developer.metax-tech.com/api/client/document/preview/%E6%80%A7%E8%83%BD%E6%B5%8B%E8%AF%95%E5%8F%8A%E5%88%86%E6%9E%90%E5%B7%A5%E5%85%B7/mcProfiler%E4%BD%BF%E7%94%A8%E6%89%8B%E5%86%8C/%E6%9B%A6%E4%BA%91C500%E7%B3%BB%E5%88%97/3.9.0/split_files/mcprofiler.html)
  describes linking libmcToolsExt, launching a target and multiple metric passes.
  It does not establish process isolation of each PMU counter or define their
  statistical denominators. The 3.9.1 manual was checked too, without finding
  that guarantee. The local 3.9.0 HTML snapshot has stored SHA-256
  `72eff4b55e1d4cb49038438988235f874535c0abc659c7a1840e054044a5b358`.
- Actual generated `mcProfiler.json` files record `counts=100` and
  `profile_from_start=true`, despite requesting the custom/API window. The
  elementwise/MMA files also record `is_perkernel=true` and exact kernel filters.
  Configuration and named output describe selection/layout, not proof that
  counters exclude other processes.
- Full native Cases contain `perf_counters` and device topology; `umd_data` is
  empty. These artifacts do not establish a process/kernel attribution mapping.
- Manifest start/finish timestamps are collector wall-clock bounds, not verified
  target-only GPU intervals. Device selection uses the Runner's visible-device
  binding; published evidence contains only its SHA-256 identifier. A visibility
  request is not PMU-device/process-isolation proof.
- Device/process/kernel counter scope and background-workload isolation remain
  unknown. No other user's processes were stopped; no driver or system settings
  were changed. Other workloads could not be excluded.

## Frontend Contract

Use [the sanitized JSON example](examples/mcprofiler_attribution_result.json).
Existing attribution fields remain. Additive fields are:

- `planning_eligible`: backend-computed boolean, never an authorization input.
- `eligibility_reasons`: stable reason codes, evaluated independently per metric.
- `device_id`: `device_sha256:<digest>` or null; never the raw selector/UUID.
- `tool_version`: observed version or null.
- `metric_definition`: original name/unit, definition status and source. The
  initial mapping is explicitly `unconfirmed`, not an official denominator.
- `collection_scope`: reported layout, requested/configured kernel, actual tool
  settings, counter scope, wall-clock bounds and background-isolation status.

`scope=unknown` describes statistical scope; `report_layout=named_kernel` does
not change it. `parse_confidence=high` means a recognized field was extracted,
not that its attribution or interpretation is trustworthy.

Observations, evidence JSONL, profiler results and the existing results API carry
these fields. Legacy/injected eligibility claims are recomputed. The backend has
no validated mcProfiler version/metric attribution policy yet: all such records
remain unverified/low and excluded from diagnosis/planning. Imported manifests,
calibration values and report-wide flags cannot promote them. Future validation
must be metric-specific; no report-wide upgrade exists.

## Real Controls

Environment: MetaX C500, driver 3.8.30, MXMACA 3.3.0.15, Python 3.10.10,
Torch 2.8.0+metax3.3.0.2. Tool version and configuration evidence are retained in
the managed workspace and the [fixture manifest](../tests/fixtures/mcprofiler/real_c500_attribution_controls_20261022/manifest.json).

| Control | Target | MMA Duty | Achieved/Dispatched waves | Reference |
| --- | --- | --- | --- | --- |
| Idle | No target launch | null, vendor division-by-zero warning | 0 / 0 | Not applicable |
| Elementwise | attribution_elementwise | 0 | 512 / 512 | max_error=0 |
| MMA | attribution_mma | 22.141887006642296 | 128 / 128 | max_error=0 |

Elementwise uses no MMA. The positive control uses 128 WMMA operations with
constant FP16 inputs and FP32 accumulation; its expected result is 4096.
Device LLVM IR contains zero MMA intrinsic calls in the elementwise function and
128 `llvm.mxc.mma.f32.16x16x16f16` calls in the positive control. This is compiler
IR inspection, not final ISA disassembly. The compiler driver rejected a separate
device-assembly invocation using `-S`; final ISA inspection remains unresolved.

The compiler reports mxcc 1.0.0 (de3b0e5687). These observations are calibration
results, not proof of per-metric isolation.
No metric was promoted to verified or used to select an optimization. Earlier
captures are preserved too; their values were not replaced to make validation
appear stronger.

The complete native Cases (including vendor databases/logs) are retained under
`kernel_opt_agent/workspace/server_runtime/attribution_validation_20261022/controls_final/`.
The three collection IDs and wall-clock windows are in the fixture manifest.
Each full directory has 21/25/25 artifact entries, including one agent sidecar.
Only non-identifying native reports/configuration, hashed context and device IR
are committed as a regression subset. `source_sha256` and `stored_sha256` are
separate fields and equal because included bytes were not normalized.

Reproduction uses SSHRunner/command_guard with bounded timeouts and an exclusive
collection directory per attempt:

```sh
/opt/maca/mxgpu_llvm/bin/mxcc -x maca -O2 -offload-arch native --maca-path=/opt/maca attribution_controls.cpp -L/opt/maca/lib -lmcToolsExt -o attribution_controls
./attribution_controls idle
./attribution_controls elementwise
./attribution_controls mma
/opt/maca/mxgpu_llvm/bin/mxcc -x maca -O2 -offload-arch native --maca-path=/opt/maca -maca-device-only -S -emit-llvm attribution_controls.cpp -o attribution_controls.ll
```

The collector invokes the installed official CLI with the selected-device Runner
context, `MCTX_TARGET_INIT=true`, separate metrics and `--per-kernel` for named
controls. It preserves the actual generated configuration rather than assuming
the requested API window took effect. Normal benchmarks are not run under the
profiler.

## Normal Web Tasks

`scripts/smoke_paged_attention_web.py` exercises real HTTP session creation,
hardware resolution, multipart sample upload, task creation, polling and results.
The existing Paged Attention sample is used without replacing it with GEMM, MoE,
CPU mock or the native controls. Build/correctness/benchmark are separate modes;
the sample fixes seed 42 and retains single-call samples after three warmups.
Optimizer thresholds and its five-repeat protocol were not relaxed.

| Context | Final task_id | Task status | Baseline |
| --- | --- | --- | --- |
| 128 | 70ae6424838e4ebd8105461294bdf71d | failed | build_failed: No module named 'tilelang' |
| 512 | 7b2f2881adad467eb1b683991aa65e5f | failed | build_failed: No module named 'tilelang' |

See [the sanitized attempt summary](artifacts/paged_attention_web_attempts_20261022.json).
System Python 3.12.3 has neither Torch nor TileLang; Conda Python 3.10.10 has GPU
Torch but neither TileLang nor mcTileLang. No additional Conda environment was
found. This is a missing-dependency result, not proof that a particular wheel/SDK
combination is incompatible. A matching MetaX TileLang environment is still
required; the system SDK/driver was not upgraded to manufacture one.

The source inspector found existing `prefetch_shared_load` and
`parallel_copy_to_t_copy` targets. Neither was applied because baseline failed.
There is no executed optimization diff, correctness result, latency series or
Paged Attention Case; those fields remain null. No candidate became best.

This real failure exposed an outer task-status bug: source baseline failure used
to end as `completed`. The minimal fix now returns `failed` and does not emit a
profiling-start event after failed build. The initial two attempts remain in the
workspace as history; the table lists fresh runs after the fix.

The final two task workspaces were scanned: 120 files, zero password-value
matches. Full source hashes, build failure logs and partial results are retained
at `kernel_opt_agent/workspace/tasks/<task_id>/results/`. No real configuration,
endpoint, username, password or session identifier is committed.

## Verification

```sh
python -m unittest tests.test_attribution_contract tests.test_attribution_real_c500 tests.test_paged_attention_web_protocol
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests scripts
git diff --check
```

Final full rerun: 285/285 PASS (211.686 seconds), following an earlier green
285-test rerun. Focused attribution/native/protocol checks also pass.
`compileall` and `git diff --check` pass. The first full run had one existing
host-timing acceptance test reject its candidate because repeat envelopes did
not satisfy the unchanged performance gate; the complete log is retained as
`workspace/server_runtime/attribution_validation_20261022/full_unittest.log`.
The green full reruns are `full_unittest_rerun.log` and `full_unittest_final.log`
in the same directory.

Regressions cover missing attribution, target mismatch, forged verification and
eligibility, native collection failure, deterministic import, source/raw hashes,
unknown scope, unavailable idle MMA metrics, API propagation and correctness
rollback. Existing performance gates are unchanged. One focused host-timing test
initially rejected an overlapping sample envelope; an isolated rerun passed.
Both the rejection artifact and test logs are retained, not treated as GPU
optimization evidence.
