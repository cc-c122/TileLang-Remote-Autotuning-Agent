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
