# C500 native mcProfiler collection acceptance

Date: 2026-10-21 (Asia/Shanghai). Scope: collector and parser acceptance only.
This is **not** a TileLang/Paged Attention baseline or an optimization result.
No ncu/nsys implementation is included in this change.

## Observed Environment

| Item | Remote observation |
| --- | --- |
| GPU | MetaX C500, one visible runtime device, UUID binding verified |
| Driver / MXMACA runtime | 3.8.30 / 3.3.0.15 |
| Python / Torch | 3.10.10 / 2.8.0+metax3.3.0.2 |
| TileLang / mcTileLang | Not installed; no version invented |
| mcProfiler | 3.9.0.29, build c63ea13089, Ubuntu18 distribution |
| Sample | Native FP32 axpby, N=32768, 128 blocks, 256 threads |

The Linux toolkit was downloaded from the [official software portal](https://developer.metax-tech.com/softnova/metax).
Archive: `linux-perf-kit_3.9.0.29.tar.gz`; MD5:
`b2dd400710cda71752b64565afc2ba52`; SHA256:
`db39a333bc1096119eb0c1bebc708695392f87e980cf68178e3ab96a5ee8e5f0`.
Only CLI components were extracted to the marker-protected remote workspace.
No system SDK/driver installation, GUI control, or credential forwarding to the vendor was performed.

## Actual Results

| Gate | Result |
| --- | --- |
| Same existing SSHRunner for detection/upload/build/collection/download | PASS |
| Build linked with libmcToolsExt | PASS |
| CPU reference correctness | PASS, max_error=0, atol=0, rtol=0 |
| Warmup / individual GPU-event measurements | 3 / 5 |
| Benchmark median | 0.0340479984879 ms, not an optimization claim |
| Official native report and complete local Case | PASS, 24 native files |
| Named-kernel report selected and remote SHA verified | PASS |
| Parsed observations / evidence | 12 / 12 |
| Diagnosis | insufficient_evidence, low confidence |
| Known-sensitive-value / endpoint scan | PASS, 162 result/fixture files checked |

Final Case: `c500-native-profiler-smoke-20261021-final`.
Exec ID: `55474e88-35ee-48ef-87f8-e5d819efb751`.
Collection ID: `mcprof-a0e1762e095b1a7d663d3b49`.
Primary report: `1_profiler_vector_axpby_dumped_result.json`.
Primary native SHA256: `45c6178421a6cd66e1ff7432ba950c6d23076201b62aae9fcb485ac8b629cf46`.

Included observations retain the native values, for example: private reads/writes 0/0,
L2C hit rate 57.636304836736265%, DNoC latency 345.5125730911173 cycles,
shared conflict 0.08358073372791035 cycles/instruction, waves 278754/270566.
No percentages were capped, no unavailable hardware fields or metrics were filled in.

**Attribution remains unverified:** the elementwise sample contains no MMA instruction,
but the native named report contains nonzero MMA duty. Counters may include other
device activity or undocumented aggregation. The unmodified observations prove the
collection/parser path, not isolated target-kernel utilization or a bottleneck.
The aggregate report also contains `period0`; it is not substituted for the named report.
`--custom` did not change the generated `profile_from_start=true` in this vendor build.
These limitations must be resolved before using counters to guide optimization.

## Reproduction Path

Reuse an already connected, fingerprint-verified, device-bound `SSHRunner`.
Do not pass SSH credentials through mcProfiler's remote flags. All commands below
run via `runner.run()` / `run_cancellable()`, including the command guard, timeout,
process-group cancellation, and marker-protected workspace boundary.

1. Upload `kernel_opt_agent/samples/profiler_smoke/vector_add.cpp` as `vector_add.cpp`.
2. Run `mkdir -p results logs` in the managed workspace.
3. Build: `/opt/maca/mxgpu_llvm/bin/mxcc -x maca -O2 -offload-arch native --maca-path=/opt/maca vector_add.cpp -L/opt/maca/lib -lmcToolsExt -o kernel_probe`.
4. Correctness: `./kernel_probe --mode correctness`; stop on nonzero exit or FAIL.
5. Benchmark: `./kernel_probe --mode benchmark`; retain `results/benchmark.json`.
6. Use `get_profiler_adapter("mcprofiler")` and `ProfileContext` with the same runner,
   private redactor/secret context, and a `McProfilerCollectionRequest`:

```python
request = McProfilerCollectionRequest(
    target_command="bash -c 'export MACA_PATH=/opt/maca; "
                   'export LD_LIBRARY_PATH="/opt/maca/lib:/opt/maca/lib64:${LD_LIBRARY_PATH_ORIG:-}"; '
                   "./kernel_probe --mode profile'",
    case_name="native-c500-acceptance",
    source_trial_id="native-baseline",
    source_sha256=source_sha256,
    shape_id="FP32_N32768_threads256",
    executable=managed_client_path,
    kernel_names=("profiler_vector_axpby",),
    per_kernel=True,
    profile_from_start=False,
    timeout_seconds=300,
)
adapter = get_profiler_adapter("mcprofiler")
artifact = adapter.collect(context)  # context.request = request
assert artifact.status == "collected", artifact.reason_category
observations = adapter.parse(artifact)
profiler = adapter.normalize(observations)
```

The collector enables `MCTX_TARGET_INIT=true` only in the target child, uses the CLI's
embedded loopback server, and keeps HOME/TMPDIR under the exclusive collection directory.
The optional `managed_external` compatibility mode is not validated with CLI 3.9.0.29;
the verified path is the default `embedded` mode.
No assumptions are made from the presence of an executable alone.

## Artifacts and Failure Evidence

Full native Case, stdout/stderr, benchmark samples, and import JSONL are retained locally
under `kernel_opt_agent/workspace/server_runtime/profiler_real_20261021/results/verified_final/`.
The Case contains native JSON/TXT/CSV/PDF/HTML, per-kernel reports, configuration,
counter batches, and runtime logs. Local/remote generated directories are not committed.

The committed golden subset is `tests/fixtures/mcprofiler/real_c500_native_smoke_20261021/`.
Its manifest keeps separate `source_sha256` / `stored_sha256`; included native files
are byte-identical, with no line-ending conversion. Identifying logs, databases,
device UUID and absolute workspace paths are omitted. The full original Case remains local.

Earlier failures remain failed, not promoted to successful evidence:

- Duplicate manual/embedded server startup: address-in-use and no valid report.
- Missing runtime hook: empty `perf_counters`, vendor `IndexError`, exit 0, no valid report.
- A development retry with a stale in-process parser: retained as `report_invalid`;
  final verification reloaded the implementation and reran actual collection.

The collector now retains bounded partial native artifacts after a verified process stop.
`report_not_found`, `report_invalid`, timeout, cancellation and target-report ambiguity
cannot become `collected`. A configured secret found in downloaded artifacts causes
that owned local copy to be removed rather than published. Failed collection still
allows the existing benchmark-only fallback; no counters are fabricated.

## Verification

```text
python -m unittest tests.test_mcprofiler_real_c500_collection tests.test_mcprofiler_collection tests.test_profiler_adapters
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests
git diff --check
```

Regression tests cover deterministic real report import, all source/stored digests,
sample source digest, evidence references, conservative diagnostics, named-vs-aggregate
selection, ambiguous/missing/empty/unavailable reports, failed partial artifacts,
secret rejection, cancellation, timeout, workspace boundaries and legacy compatibility.

Observed test outcomes: the three focused profiler modules passed 32/32, and
compileall/diff checks passed. An earlier full run passed 256/256 before adding the
seven real-fixture regressions. Subsequent full runs of 263 tests had 1 and 3 failures:
two untouched CPU wall-clock acceptance assertions returned `no_improvement`, and
one legacy local-API polling assertion was still `running` at its deadline. The
web CPU timing test passed when rerun alone. Failure logs are retained locally as
`full_unittest.log` / `full_unittest_rerun.log`; the full suite is **not claimed green**.
Rerunning the three affected tests together passed local API and real-execution
checks, but the web CPU timing assertion failed again (`focused_existing_regressions.log`).
No optimization algorithm or timing acceptance threshold was changed to force a pass.
Keep the PR Draft pending stable full-suite verification and counter-attribution review.
