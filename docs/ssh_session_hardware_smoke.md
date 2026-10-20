# SSH Session and Hardware Smoke

Real HTTP and C500 verification was performed on 2026-10-20. No credentials,
connection address, username, or real settings file are committed.

## API Contract

Start the loopback service with `python -m kernel_opt_agent.server.app`.
For a host that is not already trusted, `POST /api/ssh/sessions` returns HTTP 409:

```json
{"detail":{"code":"host_key_confirmation_required","fingerprint":"SHA256:<observed-host-key>"}}
```

Verify this fingerprint against the container's host public key, then resubmit
with `ssh.host_key_fingerprint`. A changed pinned fingerprint is rejected.
Trust records store a hash of the host/port and the public fingerprint only.

Sanitized session request and successful response:

```json
{
  "ssh": {
    "host": "<redacted>",
    "port": 22,
    "username": "<redacted>",
    "auth_type": "password",
    "host_key_fingerprint": "SHA256:<confirmed-host-key>",
    "remote_workspace": "/tmp/<managed-workspace>"
  },
  "password": "<provided-only-in-memory>"
}
```

```json
{"ok":true,"session_id":"<redacted>","expires_at":"<UTC expiry, 30 minutes after creation>"}
```

`DELETE /api/ssh/sessions/{session_id}` prevents further operations. An accepted
task retains a lease on its connection and credentials until it finishes.
Expired sessions return HTTP 410 for new operations. Sessions cannot be recovered
after the backend restarts.

`POST /api/hardware/resolve` accepts `ssh_session_id` and optional `device_id`.
Its response contains `hardware`, `devices`, `selected_device_id`,
`detection_status` (`complete`, `partial`, or `failed`), and `warnings`.
The added `connection_id` is an audit identifier, not an authentication credential.
Hardware fields retain `value/source/confidence/notes` and add `unit/detected_at`.
User overrides take precedence over remote values, then matching builtin profiles.
Profile values are explicitly labeled and never presented as remotely measured.

Submit a task with the returned device ID:

```json
{
  "project_name": "session-hardware-real-ssh-smoke",
  "ssh_session_id": "<redacted>",
  "runner": {"type":"ssh"},
  "target": {"device_id":"mxmaca:<selected-device-uuid>"},
  "sample": {"source_type":"inline","entry_file":"kernel.py","inline_text":"<GPU_SAMPLE from scripts/smoke_ssh_session.py>"},
  "commands": {
    "build_command":"python3 -m py_compile kernel.py",
    "correctness_command":"python3 kernel.py --mode correctness",
    "benchmark_command":"python3 kernel.py --mode benchmark"
  },
  "budget":{"max_iterations":1,"candidates_per_iteration":1,"timeout_seconds":120},
  "profiler":{"enabled":false},
  "patching":{"enabled":false},
  "optimization":{"enabled":false}
}
```

The same authenticated SSHClient and SFTP session are used for detection, device
verification, upload, build, correctness, benchmark, and artifact download.
Tasks run in a marker-protected `remote_workspace/tasks/{task_id}` directory.
Multiple devices require an explicit choice. The selected identity is checked
again before execution, after applying device visibility, before upload, and
after the baseline. A mismatch fails the task instead of silently selecting GPU 0.

The password is passed directly to SSHRunner through a private lease context.
Neither `os.environ` password switching nor a password environment variable is
required. The real smoke ran with `KERNEL_AGENT_SSH_PASSWORD` absent.
CLI and saved-settings environment/key authentication remain supported, with
explicit host-key confirmation required for previously unseen hosts.

## Real C500 Evidence

The smoke sample ran a GPU elementwise operation and compared its output with
a Torch CPU reference. It verifies connection/device/execution plumbing and does
not claim a TileLang optimization or performance improvement.

| Check | Result |
| --- | --- |
| Password session creation | PASS, HTTP 200 |
| Actual remote device query | PASS, HTTP 200, `partial` |
| Task creation and completion | PASS, HTTP 200, `completed` |
| Device identity after visibility remapping | PASS, snapshot `execution_device_verified=true` |
| Sample upload and marker-protected workspace | PASS |
| Build, correctness, benchmark | PASS, in the task remote workspace |
| Correctness before benchmark | PASS, confirmed in task events |
| Torch CPU correctness gate | PASS, max_error=0.0, atol=0.0, rtol=0.0 |
| GPU benchmark | PASS, 3 warmups and 5 measurements |
| Raw samples (ms) | 0.0854842365, 0.0695912167, 0.0696536154, 0.0727819279, 0.0730808824 |
| Median latency | 0.0727819279 ms |
| Downloaded remote results and logs | PASS |
| best_kernel.py and report download | PASS |
| Password/session-ID scan of task files | PASS |

Observed hardware/environment: MetaX C500, one visible device, driver 3.8.30,
MXMACA 3.3.0.15, Torch 2.8.0+metax3.3.0.2, Python 3.10.10. The visible memory
limit was 33,151,778,816 bytes, shared memory per block 65,536 bytes, and the
runtime warp size 64. SM count, CU count, wave size, and maximum threads per
block remained null. The runtime's multiprocessor count (104) is kept in its own
field and is not relabeled as either SM or CU. Compiler and TileLang package
metadata were unavailable in this environment and are reported as warnings.

Local evidence retained outside Git:

- `kernel_opt_agent/workspace/server_runtime/ssh_smoke_verified/smoke_evidence.json`
- `kernel_opt_agent/workspace/tasks/92ebf071234e4f5d9a6279434d6fb6a9/results/`

The results directory contains 24 files, including `hardware_snapshot.yaml`,
`hardware_detected.yaml`, `hardware_detection.log`, `effective_config.yaml`,
`experiments.jsonl`, `summary.csv`, `best_kernel.py`, `best_config.yaml`,
`report.md`, and the downloaded remote correctness/benchmark JSON and logs.
The earlier failed attempt is retained under
`workspace/server_runtime/ssh_smoke/`: system Python lacked Torch; discovery of
the existing Conda interpreter resolved that issue without installing packages.

SHA-256 of retained real artifacts:

| Artifact | SHA-256 |
| --- | --- |
| best_kernel.py | a614b4b7f1fc360c2aa96b639ac520abc5200c1c834dd80528b62e281ee7099b |
| report.md | 4072c0f0e391c58c59afcb56e629186bec91fdbf22ad1b7354e330a1cc1377e2 |
| hardware_snapshot.yaml | e82290d294ebdb593a1c34c94ec8a60be658f173e21e48c27c535420fcdda1a3 |
| experiments.jsonl | 8efa0c834109d26943d5acee32e22c9864cf061114a084f90230330de791354a |
| effective_config.yaml | 7649d5f89a428174bf2cae1071c224484e02c457756c3345994a1dba02ba9737 |

## Repeatable Checks

```text
python -m unittest tests.test_ssh_sessions
python -m unittest discover -s tests
python -m compileall -q kernel_opt_agent tests scripts
git diff --check
```

`tests/test_ssh_sessions.py` uses controlled fake transports/devices to cover
wrong passwords, timeout, expiry, in-flight lease retention, session isolation,
unknown fields, missing drivers, multi-GPU selection, device changes, key auth,
host-key confirmation/change, command guard, origin checks, and secret-free 422,
configuration, logs, and task results. These unit tests are separate from the real
HTTP/C500 smoke above.

`scripts/smoke_ssh_session.py` repeats the HTTP smoke against an already running
service, reading the password from an environment-variable name supplied by the
operator. Use its `--help` for connection arguments. It writes sanitized evidence
only inside its configured local evidence directory.

Limitations: one active worker is retained; hardware detection is read-only and
does not install runtimes. A stable UUID/PCI identity and a working GPU runtime
are required to accept a device-bound task. CUDA property handling is unit-tested;
this round's real hardware validation used C500 only. The service binds to
loopback and checks client/Host/Origin; it is not a multi-user network service.
