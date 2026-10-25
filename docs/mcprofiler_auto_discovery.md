# mcProfiler Tool Discovery

This backend change starts from main containing PRs #70 and #71. It does not
change source transformations, numerical tolerances or performance acceptance.

## Web Contract

See [the additive JSON examples](examples/mcprofiler_discovery_contract.json).
`POST /api/settings` accepts optional nonsecret `profiler` executable overrides.
An older client omitting `profiler` does not erase an existing override.
An explicit task override takes precedence over saved settings. Users do not
need to author YAML. Generated effective configuration records the tool paths
passed by the worker; collection revalidates the executable/version before use.

`POST /api/hardware/resolve` with an SSH session and
`GET /api/tasks/{task_id}/results` expose `profiler_capabilities.mcprofiler`.
Status, availability, client version, nullable server version, discovery source,
selected paths, reason category and warnings are separate from normalized metric
availability. Without a remote probe the capability remains `not_checked`.

## Bounded Discovery

Discovery tries an explicit executable, remote PATH, then these fixed roots:

- `/opt/maca/tools`
- `/opt/metax`
- `/opt/mcProfiler`
- `/usr/local/mcProfiler`
- `/app/mcProfiler`
- `/data/mcProfiler`
- `/workspace/tools`
- `/tmp/kernel_agent_profiler_collection/tools` (existing managed legacy tools)

Only known tool/bin/backend/platform directories and mcProfiler/linux-perf-kit
version-directory names are considered. Traversal is limited to depth 3,
32 unique directories, and 32 enumerated entries per directory. There is no
whole-disk search, network download, package installation or system mutation.

Both client and required server components must be executable regular files.
The client must return a recognizable `version` result. Server version remains
null when not independently reported; sharing a bundle is not version proof.
A non-adjacent server uses the existing managed-external-server path.
Unsafe paths, shell substitutions, parent traversal, credential directories and
symlinks resolving to unrelated executables are rejected.

Version queries use a predefined guarded command, task-scoped HOME/TMP/cache,
and a credential-free subprocess environment. The enclosing SSH managed process
group owns timeout/cancellation, including bundled-executable children. Discovery
is bounded to at most 20 seconds (or a smaller task/collector timeout). No password
or API-key environment value is passed to the vendor executable.

## Default Target Selection

Normal Web clients may omit `profiler.kernel_names`. The collector uses build
log JSON records with `generated_source_path` and `generated_source_sha256`,
cross-checked against `GENERATED_SOURCE_SHA256`. It downloads only that relative
workspace compiler artifact (one file, at most 2 MiB), checks its bytes/hash
against both the log and the execution runner, and inspects compiled kernel
definitions. There is no special-case kernel name or frontend change.

A single compiled kernel is selected directly. For multi-kernel output, a
unique kernel with a recognized matrix intrinsic call may be preferred. This is
a conservative structural preference, not proof of its performance relevance,
counter attribution or hardware limits. Multiple eligible kernels, an invalid
artifact or unsupported code syntax fail closed. Without build metadata the
existing unique-per-kernel-report fallback remains; it never selects the first,
slowest or aggregate report from a multi-report Case. Explicit names still win.

Results expose the additive
`profiler_collections[].execution_manifest.target_selection` object, including
mode/status/reason, compiled and selected names, generated-source hash and local
artifact. Existing `kernel_names`/`primary_report` describe the actual selection.
All unverified observations remain ineligible for planning; selection does not
promote attribution confidence. Missing/ambiguous targets preserve benchmark/log
fallback and original native artifacts. New regression tests cover mixed Torch
and compiled reports, renamed symbols, ambiguity, unsafe/hash-mismatched source,
cancellation, secret removal, and normal/source-worker propagation.

Repeated native reports of that same selected symbol are not different target
choices. Up to 128 distinct native report indices in one report directory are
retained in `target_reports`, each with its own raw SHA-256 and artifact ID.
`primary_report` is null for a multi-report Case; there is no aggregate fallback
or arbitrary representative. Each report is independently parsed and imported.
Repeated scalar fields are null; original per-report observations remain
available and ratios/counters are not averaged or summed. Native report indices
are not claimed to identify benchmark/warmup iterations: invocation mapping is
unknown. Different symbols, duplicate indices/directories or any invalid report
still fail closed. Single-report clients keep the existing primary-report form.

## Failure and Trust

Missing tools, unrecognized versions and ordinary collection failures preserve
benchmark/log fallback. Discovery and native execution logs plus
`profiler_capabilities.json` are retained under managed results directories.
Confirmed user cancellation stops further work. Unverified process termination
is a safety failure: no further candidate or unverified workspace rewrite occurs.

Tool availability does not prove that collection will work. `collected` means a
valid report was obtained/imported; it does not establish target-only attribution
or optimization success. Existing per-metric eligibility and confidence rules
are unchanged. Unknown hardware fields and independent server versions are not
invented.

## Tests and Acceptance

Run `python -m unittest discover -s tests` and
`python -m compileall -q kernel_opt_agent tests scripts`.
Tests cover discovery precedence, bounded roots, required components, unsafe
paths, cancellation/timeouts, cleanup safety, settings compatibility, effective
configuration and results capability propagation.

The two source-loop acceptance tests now use explicitly labeled deterministic
benchmark fixtures while still executing correctness/hash/publication checks.
They are not performance evidence. Independent real CPU measurements remain in
`python -m scripts.smoke_source_copy_timing`; that smoke does not require a gain.
Production thresholds and noise/drift checks are unchanged.

For isolated real HTTP acceptance, start
`python -m scripts.serve_acceptance_api --port 8880 --state-dir kernel_opt_agent/workspace/server_runtime/acceptance`.
This serves the ordinary API/worker with an isolated internally generated settings
file and does not change runners, command guard or request handling. It avoids
overwriting a user's saved settings during acceptance.

Real C500 task IDs, pinned commit, environment, original samples/Cases/hashes,
download checks, cancellation/process/restore audit and remaining limitations are
recorded in the PR acceptance appendices. No live acceptance pass is asserted by
this static document.
