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

## Validation Boundary

`tests/test_optimization_rounds.py` uses explicitly labeled synthetic timing and
codegen data for deterministic multi-round/failure/rollback/budget checks. It
also imports the existing real Gate Case to test deterministic parsing and the
ineligibility boundary. Gate data is not Paged Attention evidence.

Real C500 feedback-loop acceptance must be recorded separately on the final
commit through the normal Web task path. No synthetic test speedup or unavailable
profiler result is GPU optimization evidence. No new tool installation, system
changes or profiler attribution validation is performed by this change.
