# Evidence-Guided Source Feedback Rounds

This extends the existing Web task, runner, mcProfiler adapter, authorized source
templates and performance gate. It does not add parameter search, free-form code
generation, or the unaccepted PR #72 discovery/target-selection implementation.

## Execution

1. Build, strict correctness, and independent repeated baseline measurements.
   A baseline failure stops planning and retains its correctness/error/log records.
2. Attempt existing mcProfiler collection after measurement, separately from all
   latencies used for ranking. Native reports and SHA-256 manifests remain in
   the existing task artifact layout; reports are not numerically combined.
3. Before every round, reanalyze the current verified best source. Filter metrics
   with the existing backend attribution/eligibility policy, plus current source,
   trial, and known device/shape/dtype/kernel bindings. Unknown binding fields
   remain null. `collected` does not verify target attribution or prove a gain.
4. Call the configured LLM again with current source/target summaries, eligible
   metrics, build-log excerpt, prior outcomes and remaining candidate budget.
   Only concise conclusions are retained, never model internal reasoning or
   rejected raw model responses. Invalid JSON/schema/references retry once,
   then use a recorded conservative structural rule fallback.
5. Make at most one existing authorized source transformation. Preserve both
   the model hypothesis and the separate rule template description. Unsupported
   transformations, stop, and insufficient-evidence decisions do not create an
   unrelated candidate. Source versions already attempted are not executed again.
6. Save original source and diff; syntax/build, correctness, independent repeated
   benchmark, then separate profiler collection. Failed correctness never runs
   benchmark. Collection failure degrades to source/log/benchmark analysis and
   is not itself a correctness failure or an accepted optimization.
7. Apply the unchanged production gain/noise/codegen/parent-recheck gate. Only a
   passing candidate becomes the next parent. Others restore the last verified
   parent. Reanalyze after each acceptance; never reuse old target IDs.
8. Publish only the final verified best, with the existing hash/publication
   checks. Results API and downloads validate the accepted parent chain.

LLM configuration is optional. Without it each round explicitly records
`plan.source=rule_based`, not a fictitious LLM analysis. Source/log-only hypotheses
have low confidence and do not establish a measured hardware bottleneck.
The inspected mcProfiler metrics remain ineligible under the unchanged policy.

## JSON Contract

The frontend-confirmed `results.optimization_rounds` is an additive array read
from `results/optimization_rounds.jsonl`. Existing result fields remain unchanged.
See [the field example](examples/optimization_rounds.example.json).

- `schema_version`: `v2.optimization_round.v1`
- `round_index`: 0 for baseline; subsequent planning rounds are 1-based.
- `trial_id`, `parent_source_sha256`, `candidate_source_sha256`
- `evidence_ids`: actual eligible IDs cited by this round; empty in source-only mode.
- `analysis`: `bottleneck_type`, `confidence`, `hypothesis`, `expected_effect`,
  `risk`, `validation_plan`, `basis`.
- `plan`: `decision`, `target_id`, `template`, `source`.
- `template_description`, `diff`, `validation`, `acceptance`, `artifacts`.

LLM decisions are `attempt`, `stop`, `insufficient_evidence`. Stop/insufficient
decisions must not authorize a target or template. An unsupported transformation
is recorded as a stop with its reason and proposed template; no source is applied.
Round acceptance selects a verified next parent; it is not a final-publication
claim. Task/source result status and publication hashes remain authoritative.
Per-round improvement is parent-relative. API/report improvement is cumulative
against the original source baseline; both retain their raw measurement arrays.

The budget is the existing `optimization.max_candidates`; it bounds planning
rounds/candidate attempts. One invalid-response retry is allowed inside a round.
There is no new parameter-search budget or relaxation of the performance gate.
File-intake requests without an authorized transformation still complete strict
repeated baseline measurement and report `baseline_only / unavailable`. Legacy
advanced-command requests without file contracts retain their previous
baseline-only compatibility instead of acquiring a new strict script protocol.

## Validation Boundary

`tests/test_optimization_rounds.py` uses explicitly labeled synthetic timing and
codegen data for deterministic multi-round/failure/rollback/budget checks. It
also imports the existing real Gate Case to test deterministic parsing and the
ineligibility boundary. Gate data is not Paged Attention evidence.

Real C500 feedback-loop acceptance must be recorded separately on the final
commit through the normal Web task path. No synthetic test speedup or unavailable
profiler result is GPU optimization evidence. No new tool installation, system
changes or profiler attribution validation is performed by this change.

Earlier attempts on 2026-10-29 stopped in build because their selected Python
environment could not import `tilelang`. Those failure records are retained;
they are not credited as successful GPU runs.

### Real C500 Follow-Up

The replacement container provided an existing MetaX TileLang source runtime.
Task command environments selected it without changing the system environment
or driver. On executable commit `793e0f71ffb412d0ca33ef478bd355c9e77b3e9a`,
the ordinary HTTP upload/session/hardware/task/results/download path on
`http://127.0.0.1:8886` completed both supported shapes. This is a live SSH/GPU
run, not artifact replay, a CPU mock, or a substitute GEMM/MoE workload.

| Context | Task ID | Torch-reference max error | Baseline median (ms) | Adopted gain |
| --- | --- | --- | --- | --- |
| 128 | `47b955a4380d44b89a53f9d434a77897` | 0.0009765625 | 0.08003902621567249 | 0% |
| 512 | `3086c8e0869d4239beb30a18b8ccc85b` | 0.000244140625 | 0.08320389315485954 | 0% |

Both shapes used FP16, batch 1, 32 query heads, 8 KV heads, head dimension 128,
page size 16, seed 42, and atol/rtol 0.002. Each benchmark invocation performed
3 warmups and 5 separately timed operator calls; each source version had 5
independent benchmark invocations. All 150 individual samples, per-invocation
median/p90/sample standard deviation, source diffs, stage hashes, correctness,
and gate/rollback decisions are in the
[sanitized evidence bundle](artifacts/c500_feedback_rounds_20261029.json).
Reported baseline medians are medians of the five invocation medians.
Latency is **operator end-to-end**, including split/combine launches, adapter
output allocation and host/device synchronization, not isolated subkernel time.
Profiler durations never contribute to the benchmark comparison.

Environment: MetaX C500; driver 3.8.30; MXMACA runtime 3.5.3.20; Python 3.12.11;
Torch 2.8.0+metax3.5.3.9; MetaX TileLang reports
`0.1.9+cuda.gitf1ca0fb9`, target `maca`, source commit
`f1ca0fb9c018f2807f789cef04a1303c1b68ab20`; mcProfiler 3.9.0.29. The runtime
library hash and compiler version are preserved in the evidence bundle.
Automatic hardware detection remains partial; a separate mcTileLang version is
unknown, not inferred from the TileLang version string.

DeepSeek `deepseek-chat` was called anew in all four candidate rounds. Each
accepted plan is explicitly `source_and_logs_only / low`: there were **zero
planning-eligible mcProfiler metrics**. Source hypotheses and the authorized rule
template descriptions remain separate. The first round in each shape changed
an elementwise copy to `T.copy`; generated code was unchanged, so it was not
adopted. The second moved an independent V shared load earlier. Context 128 was
inconclusive under the unchanged repeat-envelope/noise gate; context 512 failed
the minimum median-gain gate. Both candidates in both shapes passed correctness
and were rolled back with verified original hashes. Both downloaded best kernels
match the original baseline hash. No optimization gain is claimed.

Six native mcProfiler Cases (baseline plus two candidates per shape) were
collected separately, imported, and parsed. All 210 native artifact SHA-256
entries were rechecked. The selected report is
`1_main_kernel_dumped_result.json`, not the aggregate report. Tool/server paths
and kernel selection were explicit ordinary Web request fields; this does not
claim the unaccepted #72 automatic discovery/selection work. Filename selection
does not prove metric attribution: counter scope, background activity exclusion,
metric definitions and a target-only collection window remain unverified.
Those observations stay ineligible and cannot establish a hardware bottleneck.

The task results retain native Cases and raw logs under
`kernel_opt_agent/workspace/tasks/<task_id>/results/`. Private audit copies and
the 150-row measurement CSV are under
`kernel_opt_agent/workspace/server_runtime/c500_tilelang_ready/web_run_v3/`.
These generated directories and real connection settings are not committed.
Known password/API-key scans passed for all 492 task files and both server
logs. The committed evidence additionally omits SSH address, username, remote
workspace, session identifiers, raw device identifiers and machine-absolute
paths. The service remains loopback-only with both real tasks available.

Validation on this executable source state: 351 unittests passed;
`python -m compileall -q kernel_opt_agent tests scripts` and
`git diff --check HEAD` passed. The evidence-only follow-up changes no executable
code: the backend Git tree is `bacb2258df2201af2ab9567a97e24ec79c01b6a9` and
the scripts tree is `dcfd07ea05aa5739edc5fa94e04d4c04674f35a8`.
The PR remains Draft for review; collection and a completed source-only LLM
validation loop are not proof of verified profiler attribution or performance
improvement.
