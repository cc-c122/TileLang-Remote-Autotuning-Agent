# mcProfiler MMA Attribution Calibration: Stage One

Status on 2026-10-30: **blocked, no metric authorized for planning**.
This change starts from main `4d6aebaa9a225eb722f3401b85ad144296c0a6a9`.
It does not include the unaccepted #72/#73 branches or stage-two optimization.
No LLM call, new source transformation, performance claim, installation, driver
change, or termination of another user's process is part of this stage.

## Target Mapping

The original-byte [real Case subset](../tests/fixtures/mcprofiler/real_c500_paged_mapping_20261029/manifest.json)
is from historical task `47b955a4380d44b89a53f9d434a77897`, baseline collection
`mcprof-2b736b75cdcc8f18b3f6ea3f`, captured 2026-10-29. It is not a new capture.
Shape: FP16, B=1, QH=32, KVH=8, D=128, page=16, context=128.
Its source SHA-256 is
`dca2e0114b88a029ec7e61e61cc791eaa97a4e2f5c4065cf3eef6b082c6a86c6`.

`kernel_mapping.py` uses Python AST regions, externally written buffers,
generated function signatures/bodies and generated host call sites. Names and
function order alone cannot determine a role. Mapping is a static candidate,
never a runtime/PMU verification. The inspected modification location is the
existing V global-to-shared copy inside split; no modification is applied.

| Source region | Generated function | Generated MMA call sites | Native report label | MMA Duty raw value |
| --- | --- | --- | --- | --- |
| split, lines 58-132 | main_kernel | 2 | 1_main_kernel | 1.5819497066672845 |
| combine, lines 135-167 | main_kernel_1 | 0 | 2_main_kernel_1 | 0.0 |
| whole capture, not a subkernel | not applicable | not applicable | aggregate | 0.24211350430038153 |

The host code resolves and calls both packed functions, at separate static call
sites. This is launch intent, not an observed sequence of runtime launches.
Report slot numbers are not launch IDs. Repeated same-name reports remain
distinct, and neither an aggregate nor a same-name repeated launch can replace
a missing uniquely identified target. Native source/config/report SHA-256 values
are recorded separately from stored hashes; included bytes were not normalized.

The effective native configuration names only `main_kernel`, has
`include=true`, `is_perkernel=true`, `counts=100`, and omits
`profile_from_start`. Nevertheless both split and combine reports exist.
`counts=100` is a configuration request, not proof of actual replay count.
Actual target launch ID, replay count, target-only window and exclusion of other
GPU workloads remain unknown. The manifest wall clock is not a GPU window.

## Metric Definition And Scope

The live read-only inventory observed mcProfiler **3.9.0.29**, build
**c63ea13089**, distro ubuntu18; GPU MetaX C500, driver 3.8.30, MXMACA 3.5.3.20,
Torch 2.8.0+metax3.5.3.9, Python 3.12.11. The official client lists
`GPU Throughput Statistics / AP MMA Duty ratio`.

The [official 3.9.0 usage manual](https://developer.metax-tech.com/api/client/document/preview/%E6%80%A7%E8%83%BD%E6%B5%8B%E8%AF%95%E5%8F%8A%E5%88%86%E6%9E%90%E5%B7%A5%E5%85%B7/mcProfiler%E4%BD%BF%E7%94%A8%E6%89%8B%E5%86%8C/%E6%9B%A6%E4%BA%91C500%E7%B3%BB%E5%88%97/3.9.0/split_files/mcprofiler.html)
was retrieved again on 2026-10-30; response-byte SHA-256:
`4380ca1940f47a9d7f0a87344d73905bd08f8806798d98a3a1e18d59e8eced87`.
It documents runtime extension linking and multi-pass measurement but does not
establish the exact numerator/denominator or per-process/kernel PMU isolation.
This snapshot is distinct from the older manual response hash in the historical
attribution notes. Neither hash has been substituted for an original capture.

| Property | Finding |
| --- | --- |
| Raw field | AP MMA Duty ratio |
| Normalized parser field | mma_duty |
| Parser unit | percent-like field retained as reported, not capped |
| Official numerator and denominator | unknown |
| Device/process/kernel statistical scope | unknown |
| Applicable, validated tool/build/GPU combination | none |
| Background workload exclusion | unknown; no other workload was stopped |

The historical [native controls](../tests/fixtures/mcprofiler/real_c500_attribution_controls_20261022/manifest.json)
remain available unchanged: idle MMA is null (vendor division by zero),
elementwise is 0, MMA is 22.141887006642296. Device LLVM IR contains zero and
128 MMA intrinsic calls respectively. These controls used an older MXMACA
runtime; they are not calibration of the current environment or final ISA.
Different responses do not prove target attribution, isolation or a denominator.

Fresh repeat collection was attempted twice on 2026-10-30 through SSHRunner,
with in-memory credentials, existing host trust, a new managed workspace and
bounded timeouts. Both stopped at `connect / AuthenticationException` before
upload or GPU execution. See the [sanitized blocking receipt](artifacts/mma_calibration_blocked_20261030.json).
No fresh controls or repeated-capture consistency result is claimed.

## Backend Validator

`attribution_validation.py` separates calibration applicability from this
collection's identity. It checks actual source/generated/report/config bytes,
hashes, metric value/unit, exact effective filter, tool version/build, device,
shape/dtype, trial, kernel/launch, replay and window. All required native
artifacts must be inside the managed root, without symlink/path escape.

A code-reviewed tool-specific native trace reader must establish actual scope
and identity; a generic JSON `verified` flag cannot do so. There is currently no
approved definition or native reader. Missing facts yield stable reason codes,
not a guessed scope or a report-wide confidence upgrade. The live collector
records blocked results in observations/evidence/imports and its collection
audit; unrelated metrics are not promoted.

Backend receipts bind the exact value, unit, source field, artifact and collection
identity. They exist only in backend memory and recheck artifacts and calibration
on use. Changing report bytes, recomputing a replacement hash, changing a
binding, retiring a definition, or backend restart invalidates authorization.
An import, API request, LLM claim or uploaded manifest cannot create a receipt.
After restart a saved receipt is audit-only and planning fails closed.

The observation/evidence constructors no longer *unconditionally* discard a
genuine backend validation; they ask this validator. Missing/forged results
still force unverified/low and remain excluded. A test-only synthetic adapter
exercises the positive path; it is never registered in production and is not GPU
evidence. No production performance or noise threshold was changed.

## Additive Results Contract

Existing results API fields remain unchanged. Each mcProfiler observation and
evidence record can carry:

```json
{
  "metric": "mma_duty",
  "parse_confidence": "high",
  "confidence": "low",
  "attribution_status": "unverified",
  "attribution_validation": {
    "schema_version": "v2.mcprofiler_metric_validation.v1",
    "status": "blocked",
    "reason_codes": [
      "metric_definition_unconfirmed",
      "native_scope_verifier_unavailable",
      "collection_window_unverified",
      "replay_count_unconfirmed"
    ]
  },
  "planning_eligible": false
}
```

`validation_id` and `collection_id` are additive audit fields when a live backend
check ran. Missing validation defaults to blocked. Results API and planner
recompute eligibility; the serialized boolean is never authority. Parsing
confidence stays separate from attribution confidence.

## Reproduction And Exit Gate

For a reachable container, use the existing **private** effective connection
config and trusted host store. Password is read from its existing environment
reference or an unechoed prompt, not put on the command line. Tool paths are
explicit non-sensitive existing installations. No software is downloaded.

```sh
python -m scripts.calibrate_mcprofiler_mma --connection-config <private-effective-config> --host-key-store <private-host-trust-store> --client <installed-mcProfiler> --server <installed-profiler_server> --output-dir kernel_opt_agent/workspace/mma-controls-new
python -m unittest tests.test_mma_attribution_validation tests.test_mcprofiler_kernel_mapping tests.test_attribution_contract tests.test_mcprofiler_attribution tests.test_optimization_rounds
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests scripts
git diff --check
```

The collector/SSHRunner guards commands, applies timeout/cancellation supervision,
retains original Cases/logs and separates profiler execution from benchmarking.
The script compiles the existing native control once plus device LLVM IR, runs
reference checks, then collects only MMA Duty for idle/elementwise/MMA twice.
Idle null is retained as unavailable and never relabeled as a usable metric.
All emitted findings remain blocked unless the real native proof is complete.
The script exits 1 on execution/connection failure and 2 on blocked calibration;
only verified calibration exits 0.

Before stage two: obtain an applicable official definition and native scope proof;
restore SSH; perform fresh repeated controls with observed windows/replays and
bound launches; explicitly resolve background isolation. Only then may a later
accepted PR let the LLM reference an eligible `evidence_id` and attempt an
appropriate authorized source transformation. If none applies, stop explicitly.

## Verification Notes

The focused attribution/mapping/round regressions pass (53 tests). Compileall and
diff checks pass. Two full-suite attempts in the deep Windows worktree each had
one failure in the pre-existing CPU-timed Web source-optimization test: the first
had overlapping repeat envelopes; the second reached baseline recheck but failed
with Windows `WinError 267` (invalid working directory) in the long task path.
Neither a rejection nor an unavailable recheck was relabeled accepted. The tests
and production performance thresholds were not changed. Final verification uses
a short-path detached worktree of the submitted commit; results are in the PR.
