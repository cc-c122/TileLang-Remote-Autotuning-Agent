# mcProfiler Attribution Validation

Status: native calibration and two real TileLang Paged Attention Web source
trials completed. Neither candidate was accepted; both rolled back to baseline.
Counter attribution remains unverified. Keep the PR Draft.

## Scope Findings

- Installed mcProfiler: 3.9.0.29, build c63ea13089. This was queried on the runner,
  not inferred from an archive name.
- The official [3.9.0 usage manual](https://developer.metax-tech.com/api/client/document/preview/%E6%80%A7%E8%83%BD%E6%B5%8B%E8%AF%95%E5%8F%8A%E5%88%86%E6%9E%90%E5%B7%A5%E5%85%B7/mcProfiler%E4%BD%BF%E7%94%A8%E6%89%8B%E5%86%8C/%E6%9B%A6%E4%BA%91C500%E7%B3%BB%E5%88%97/3.9.0/split_files/mcprofiler.html)
  describes linking libmcToolsExt, launching a target and multiple metric passes.
  It does not establish process isolation of each PMU counter or define their
  statistical denominators. The 3.9.1 manual was checked too, without finding
  that guarantee. The local 3.9.0 HTML snapshot has stored SHA-256
  `72eff4b55e1d4cb49038438988235f874535c0abc659c7a1840e054044a5b358`.
- The native calibration `mcProfiler.json` files record `counts=100` and
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

## Real TileLang Web Tasks

`scripts/smoke_paged_attention_web.py` exercises real HTTP session creation,
hardware resolution, multipart sample upload, task creation, polling and results.
The existing Paged Attention sample is used without replacing it with GEMM, MoE,
CPU mock or the native controls. Build/correctness/benchmark are separate modes;
the sample fixes seed 42 and retains single-call samples after three warmups.
Optimizer thresholds and its five-repeat protocol were not relaxed. The separate
`profile` mode requests one operator call, rather than profiling all benchmark
warmups/samples. The vendor may still replay kernels. Build output saves generated
MACA code and its SHA-256. Normal timing includes split/combine launches, Python
dispatch and adapter output allocation: it is operator end-to-end latency, not
isolated `main_kernel` time.

The source installation was found at `/app/tilelang-metax`. Default import/pip
metadata checks had missed it; no replacement wheel or system package was
installed. Actual `determine_target('auto')` returns `maca`, generated code includes
MACA templates and `__builtin_mxc_mma_16x16x16f16`, and the build cache records
`USE_CUDA:STRING=OFF`. The version label alone is not backend proof.

| Environment | Observed value |
| --- | --- |
| GPU / driver / MXMACA runtime | MetaX C500 / 3.8.30 / 3.5.3.20 |
| Python / Torch | 3.12.11 / 2.8.0+metax3.5.3.9 |
| TileLang version label | 0.1.9+cuda.gitf1ca0fb9 |
| TileLang source commit | f1ca0fb9c018f2807f789cef04a1303c1b68ab20 |
| Compiler | mxcc 1.0.0 (6477545d4d) |
| mcProfiler | 3.9.0.29 (c63ea13089) |

See the [read-only environment receipt](artifacts/c500_tilelang_environment_20261022.json),
including the installed library SHA-256. Source dirty state could not be obtained
and remains null; it is not assumed clean. No driver/system configuration was changed.

| Context | Real task_id | Baseline ms | Candidate ms | Observed candidate gain | Correctness max error | Rollback |
| --- | --- | --- | --- | --- | --- | --- |
| 128 | 363df1fb4e9b442c9813738d8f516cca | 0.079947 | 0.084283 | -5.4234% | 0.0009765625 | verified |
| 512 | 1a9eed7c98104ed38b78a942c268537b | 0.087525 | 0.088080 | -0.6340% | 0.000244140625 | verified |

These table latencies are medians of five benchmark-command medians. Each command
has three warmups and five individual host-synchronized samples, seed 42, FP16,
B=1, QH=32, KVH=8, D=128, page=16, atol/rtol=0.002. All 100 individual baseline /
candidate samples are retained in the [raw CSV](artifacts/paged_attention_web_raw_samples_20261022.csv);
per-command raw values, median, p90 and standard deviation are in the
[structured evidence](artifacts/paged_attention_web_real_20261022.json).

The existing rule-based `prefetch_shared_load` moved the V global-to-shared load
ahead of independent computation. See the [executed source diff](artifacts/paged_attention_prefetch_shared_load_20261022.patch).
Original SHA-256 is `dca2e0114b88a029ec7e61e61cc791eaa97a4e2f5c4065cf3eef6b082c6a86c6`;
candidate SHA-256 is `9dfd076047df1e98359d5efc5b319cb6ab2f120f763e5db24eeb4dcf9f788956`.
Both passed correctness but failed the unchanged performance acceptance rule.
Their `no_improvement` decisions, generated-source hashes and verified rollback
hashes are retained. Both best-kernel downloads match the original SHA; final
best improvement is 0%. No performance improvement or hardware bottleneck is claimed.

| Context / version | Case | Result |
| --- | --- | --- |
| 128 baseline | mcprof-667ab77860746a74d66b6e3b | collected, exact `1_main_kernel_dumped_result.json` |
| 128 candidate | mcprof-d8a584ba86a19ec5001529b8 | collected, exact named report |
| 512 baseline | mcprof-59bcb7e412d9e2c9d47617fa | collected, exact named report |
| 512 candidate | mcprof-035c8b0267fa0a56f57261b1 | failed: `report_not_found`, CLI returned 0 |

The last collection did not produce a valid report. Its partial artifacts/logs
remain under the ctx512 task's `results/mcprofiler_failed/` directory. It is not
counted as successful, and no metrics are invented for it. The two baseline
Cases are frozen as original-byte [golden subsets](../tests/fixtures/mcprofiler/real_c500_paged_attention_20261022/)
with separate source/stored hashes, actual timing records and provenance. Full
native Cases and SHA manifests remain in each task workspace.

All native observations stay low-confidence/unverified/planning-ineligible;
parse confidence is separate. Both planners explicitly excluded 12 baseline
counter records and used only a low-confidence source scheduling hypothesis.
Diagnoses remain `insufficient_evidence`. Current Paged Attention configurations
record per-kernel layout and counts=100, but do not report a verified target-only
window or statistical isolation. Report names do not establish either.

Task detail/events/results and both artifact downloads were checked with real
HTTP requests (not TestClient or browser clicks). The [acceptance receipt](artifacts/paged_attention_web_acceptance_20261022.json)
records ten HTTP 200 checks, internally generated run_request/effective_config
files, and 276 task files scanned with zero configured-secret value matches.
No endpoint, login name, session credential or real configuration is committed.

### Earlier Attempts and Minimal Fixes

| Context | Final task_id | Task status | Baseline |
| --- | --- | --- | --- |
| 128 | 70ae6424838e4ebd8105461294bdf71d | failed | build_failed: No module named 'tilelang' |
| 512 | 7b2f2881adad467eb1b683991aa65e5f | failed | build_failed: No module named 'tilelang' |

See [the sanitized attempt summary](artifacts/paged_attention_web_attempts_20261022.json).
Those default Python import paths could not import TileLang. They did not prove
that no source build existed elsewhere. Later discovery in the new container
found `/app/tilelang-metax`, and task commands supplied its `PYTHONPATH` plus the
existing SDK paths. The historical failures are retained, not relabeled as passes.

The source inspector found existing `prefetch_shared_load` and
`parallel_copy_to_t_copy` targets. Neither was applied because baseline failed.
Neither target was applied in those earlier attempts. New real trials above
executed the prefetch transformation; no candidate became best.

This real failure exposed an outer task-status bug: source baseline failure used
to end as `completed`. The minimal fix now returns `failed` and does not emit a
profiling-start event after failed build. The initial two attempts remain in the
workspace as history; the table lists fresh runs after the fix.

The earlier two task workspaces were scanned: 120 files, zero password-value
matches. Their source hashes, build failure logs and partial results are retained
at `kernel_opt_agent/workspace/tasks/<task_id>/results/`. No real configuration,
endpoint, username, password or session identifier is committed.

Real retries also exposed three narrowly scoped issues:

- A gateway returned partial authentication without raising from Paramiko
  connect. Runner now requires an active, authenticated transport before SFTP
  or persisting trust; incomplete authentication becomes `auth_failed`.
- The cancellation supervisor's relative PID cleanup was combined with user
  read-only `/usr/bin` paths by a textual guard. The original command and fixed
  supervisor are now guarded separately; user text is a quoted positional
  argument (`sh -c "$1"`), not supervisor source. Dangerous commands remain denied.
- A cold Torch/GPU query took 14.4 seconds before management-tool queries. The
  per-interpreter budget is now 25 seconds, still under the same 50-second total
  deadline. Stable UUID verification is unchanged; failed queries still block.

An earlier benchmark-mode capture yielded nine same-name target reports and was
correctly rejected as ambiguous. That complete native Case is preserved in task
`045e9042124148b1af37d8e01d37d292`. The later independent profile mode resolves
report selection without treating selection as attribution verification.

Remaining limits: context 2048 has not been collected for the three-context
baseline manifest; ctx512 candidate profiling failed; PMU isolation/denominators
and final ISA inspection remain unverified. This PR stays Draft.

## Verification

```sh
python -m unittest tests.test_attribution_contract tests.test_attribution_real_c500 tests.test_paged_attention_web_protocol
python -m unittest tests.test_paged_attention_real_c500
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests scripts
git diff --check
```

Pre-submission full run on 2026-10-24 with the new real fixtures: 298/298 PASS
(203.728 seconds), following earlier green 293/298-test runs. Focused
attribution/native/protocol checks also pass.
`compileall` and `git diff --check` pass. The first full run had one existing
host-timing acceptance test reject its candidate because repeat envelopes did
not satisfy the unchanged performance gate; the complete log is retained as
`workspace/server_runtime/attribution_validation_20261022/full_unittest.log`.
Earlier timing-test rejections and an obsolete wording-assertion failure are
preserved too. The latest green log is `final_submission_unittest_20261024.log`.
No performance test workload or acceptance threshold was modified; the wording
assertion now correctly requires operator end-to-end/allocation scope.

Regressions cover missing attribution, target mismatch, forged verification and
eligibility, native collection failure, deterministic import, source/raw hashes,
unknown scope, unavailable idle MMA metrics, API propagation and correctness
rollback. Existing performance gates are unchanged. One focused host-timing test
initially rejected an overlapping sample envelope; an isolated rerun passed.
Both the rejection artifact and test logs are retained, not treated as GPU
optimization evidence.
