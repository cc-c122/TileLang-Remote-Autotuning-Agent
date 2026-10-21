// Browser contract tests only; these devices and timings are not hardware evidence.
// Open the same-origin frontend, then run with playwright-cli --filename.
async (page) => {
  const checks = [];
  const secret = "TransientSshPasswordOnly-42";
  const sessionId = "private-session-contract-only";
  const fingerprint = "SHA256:" + "A".repeat(43);
  const hostA = "race-a.fixture.invalid";
  const hostB = "race-b.fixture.invalid";
  const sessionA = "private-race-session-a";
  const sessionB = "private-race-session-b";
  const taskRequests = [];
  const resolveRequests = [];
  const savedRequests = [];
  const revokedSessionIds = [];
  let sessionOutcome = "ok";
  let hardwareOutcome = "single";
  let taskOutcome = "ok";
  let releaseConnection;
  let deleted = 0;
  let cancelled = 0;
  let sessionCreates = 0;
  let backendSettings;
  let holdHostA = false;
  let releaseHostA;

  const check = (condition, label) => {
    if (!condition) throw new Error(label);
    checks.push(label);
  };
  const json = (route, body, status = 200) => route.fulfill({status, contentType: "application/json", body: JSON.stringify(body)});
  const device = (index) => ({
    device_id: `mxmaca:GPU-contract-${index}`,
    runtime_index: index,
    selector: `GPU-contract-${index}`,
    identity_verified: true,
    backend: "mxmaca",
    fields: {target_name: `Contract GPU ${index}`, backend: "mxmaca", device_id: `mxmaca:GPU-contract-${index}`, device_uuid: `GPU-contract-${index}`, pci_bus_id: `0000:0${index}:00.0`, total_memory_bytes: (index + 1) * 1000000000},
  });
  const settings = {ssh: {host: "fixture.invalid", port: 22, username: "contract-user", remote_workspace: "/tmp/contract-only", auth_type: "password", password_env_exists: false}, llm: {base_url: "https://example.invalid/v1", model: "contract", api_key_env: "OPENAI_API_KEY", api_key_env_exists: false}};
  backendSettings = settings;
  const task = (status = "running") => ({task_id: "session-task", project_name: "session-contract", status, execution_mode: "baseline_only", current_stage: "benchmark_started", baseline_latency: status === "completed" ? 1 : null, events: []});

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/health") return json(route, {ok: true, capabilities: {source_optimization: true}});
    if (path === "/api/settings" && request.method() === "GET") return json(route, {ok: true, settings: backendSettings});
    if (path === "/api/settings" && request.method() === "POST") {
      savedRequests.push(request.postDataJSON());
      return json(route, {ok: true, settings: request.postDataJSON()});
    }
    if (path === "/api/ssh/sessions") {
      sessionCreates += 1;
      const body = request.postDataJSON();
      check(body.password === secret, "Session request uses exactly the submitted password");
      if (body.ssh.host === hostA && holdHostA) await new Promise((resolve) => { releaseHostA = resolve; });
      if (sessionOutcome === "held") await new Promise((resolve) => { releaseConnection = resolve; });
      if (sessionOutcome === "auth_failed") return json(route, {detail: {code: "auth_failed", message: secret}}, 502);
      if (sessionOutcome === "timeout") return json(route, {detail: {code: "connection_timeout", message: secret}}, 502);
      if (sessionOutcome === "malformed") return json(route, {detail: {code: secret, message: secret}}, 502);
      if (sessionOutcome === "fingerprint" && !body.ssh.host_key_fingerprint) return json(route, {detail: {code: "host_key_confirmation_required", fingerprint}}, 409);
      if (sessionOutcome === "fingerprint") check(body.ssh.host_key_fingerprint === fingerprint, "Only the explicitly confirmed fingerprint is submitted");
      const createdId = body.ssh.host === hostA ? sessionA : body.ssh.host === hostB ? sessionB : sessionId;
      return json(route, {ok: true, session_id: createdId, expires_at: new Date(Date.now() + 1800000).toISOString()});
    }
    if (path.startsWith("/api/ssh/sessions/") && request.method() === "DELETE") {
      deleted += 1;
      revokedSessionIds.push(decodeURIComponent(path.slice("/api/ssh/sessions/".length)));
      return json(route, {ok: true});
    }
    if (path === "/api/hardware/resolve") {
      const body = request.postDataJSON();
      resolveRequests.push(body);
      if (hardwareOutcome === "expired") return json(route, {detail: {code: "ssh_session_expired", password: secret, session_id: sessionId}}, 410);
      const devices = hardwareOutcome === "none" ? [] : hardwareOutcome === "multi" ? [device(0), device(1)] : [device(0)];
      const selected = body.device_id ? devices.find((item) => item.device_id === body.device_id) : devices.length === 1 ? devices[0] : null;
      const fields = Object.fromEntries(Object.entries(selected?.fields || {}).map(([field, value]) => [field, {value, source: "remote_detection", confidence: "high", unit: field.endsWith("_bytes") ? "bytes" : null}]));
      fields.wave_size = {value: null, source: "unknown", confidence: "unknown", unit: "threads"};
      fields.shared_memory_per_block_bytes = {value: selected ? 65536 + selected.runtime_index : null, source: selected ? "remote_detection" : "unknown", confidence: selected ? "high" : "unknown", unit: "bytes"};
      return json(route, {ok: true, devices, selected_device_id: selected?.device_id || null, detection_status: devices.length ? "partial" : "failed", warnings: [], hardware: {fields, unknown_fields: ["wave_size"], conservative_mode: true}});
    }
    if (path === "/api/tasks" && request.method() === "GET") return json(route, {ok: true, tasks: []});
    if (path === "/api/tasks" && request.method() === "POST") {
      taskRequests.push(request.postDataJSON());
      if (taskOutcome === "expired") return json(route, {detail: {code: "ssh_session_not_found", message: secret}}, 404);
      return json(route, {ok: true, task_id: "session-task", task: task()});
    }
    if (path === "/api/tasks/session-task") return json(route, {ok: true, task: task()});
    if (path.endsWith("/events")) return json(route, {ok: true, events: []});
    if (path.endsWith("/cancel")) {
      cancelled += 1;
      return json(route, {ok: true, task: task("cancelled")});
    }
    return json(route, {detail: "Unconfigured contract route"}, 404);
  });

  async function settingsView() {
    await page.evaluate(() => activateView("settingsView"));
  }
  async function submitConnection() {
    await settingsView();
    await page.locator("#settingSshPassword").fill(secret);
    await page.locator("#connectSshBtn").click();
  }
  async function connected() {
    await submitConnection();
    await page.waitForFunction(() => state.sshStatus === "connected" && !state.hardwareResolving);
  }
  async function runnable() {
    await page.evaluate(() => { activateView("startView"); el.v2ExecutionIntent.value = "baseline_only"; syncSourceOptimizationUi(); });
    await page.locator("#v2SampleText").fill("print('contract only')");
    const advanced = page.locator("#startView details");
    if (await advanced.getAttribute("open") === null) await advanced.locator("summary").click();
    await page.locator("#v2CorrectnessCommand").fill("python kernel.py --check");
    await page.locator("#v2BenchmarkCommand").fill("python kernel.py --benchmark");
  }
  async function safeStorage() {
    return page.evaluate(({password, id}) => {
      const storage = JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage});
      const settingsText = JSON.stringify(readSettings()) + JSON.stringify(settingsApiPayload()) + toYaml(settingsFileObject());
      return !storage.includes(password) && !storage.includes(id) && !settingsText.includes(password) && !settingsText.includes(id);
    }, {password: secret, id: sessionId});
  }

  await page.reload();
  await page.waitForFunction(() => state.apiHealthy && document.getElementById("settingSshHost").value === "fixture.invalid");
  check(await page.locator("#v2GpuModel").inputValue() === "", "GPU model is not prefilled or required before connection");
  check((await page.locator("#connectionStatus").innerText()).includes("未连接"), "Saved settings do not imply an active SSH connection");
  await runnable();
  await page.locator("#v2RunnerType").selectOption("ssh");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Remote submission is blocked without a session");
  await page.evaluate(() => startOptimization());
  check(taskRequests.length === 0, "JavaScript submission cannot bypass the session requirement");

  sessionOutcome = "auth_failed";
  await submitConnection();
  await page.waitForFunction(() => state.sshStatus === "failed");
  check((await page.locator("#sshConnectionMessage").innerText()).includes("认证失败"), "Authentication failure has an actionable Chinese reason");
  check(await page.locator("#settingSshPassword").inputValue() === "", "Password is cleared after failed submission");
  check(!(await page.locator("body").innerText()).includes(secret) && await safeStorage(), "Even an echoed backend error does not leak the password");

  sessionOutcome = "timeout";
  await submitConnection();
  await page.waitForFunction(() => state.sshStatus === "failed");
  check((await page.locator("#sshConnectionMessage").innerText()).includes("超时"), "Connection timeout is distinct from authentication failure");
  sessionOutcome = "malformed";
  await submitConnection();
  await page.waitForFunction(() => state.sshStatus === "failed");
  check(!(await page.locator("body").innerText()).includes(secret), "Unknown backend error codes are never echoed to the UI");

  sessionOutcome = "fingerprint";
  await submitConnection();
  await page.waitForFunction(() => state.sshStatus === "host_key");
  check(await page.locator("#hostKeyFingerprint").innerText() === fingerprint, "First-time host fingerprint is displayed for independent verification");
  const beforeConfirmation = sessionCreates;
  await submitConnection();
  check(sessionCreates === beforeConfirmation, "Unconfirmed host fingerprint cannot trigger another SSH request");
  await page.locator("#confirmHostKey").check();
  await connected();
  check(await safeStorage(), "Session id is kept out of browser storage and exported settings");

  sessionOutcome = "held";
  await submitConnection();
  await page.waitForFunction(() => state.sshStatus === "connecting");
  check((await page.locator("#sshStatus").innerText()).includes("连接中") && await page.locator("#connectSshBtn").isDisabled(), "Connection in progress is explicit and cannot be submitted twice");
  check(await page.locator("#settingSshPassword").inputValue() === "", "Password clears before the SSH response arrives");
  sessionOutcome = "ok";
  releaseConnection();
  await page.waitForFunction(() => state.sshStatus === "connected" && !state.hardwareResolving);
  check((await page.locator("#remoteHardwareStatus").innerText()).includes("部分参数未知"), "Partial identification is separate from SSH connection success");
  check(resolveRequests.at(-1).ssh_session_id === sessionId && Object.keys(resolveRequests.at(-1).user_overrides).length === 0, "Hardware detection uses the session without resubmitting detected fields as overrides");

  await runnable();
  const unknownInput = page.locator('[data-hardware-override="wave_size"]');
  check(await unknownInput.inputValue() === "" && await unknownInput.getAttribute("placeholder") === "未知", "Unknown hardware values remain unknown rather than zero");
  check(await page.locator('[data-hardware-override="target_name"]').count() === 0, "Actual GPU identity is read-only");
  await page.locator("#v2GpuModel").fill("Manually declared different GPU");
  check((await page.locator("#deviceIdentity").innerText()).includes("Contract GPU 0"), "Manual model declaration never hides the detected GPU");
  await unknownInput.fill("32");
  await unknownInput.blur();
  check((await page.locator('[data-hardware-override="wave_size"]').locator("xpath=ancestor::tr").innerText()).includes("用户修改"), "Edited hardware field has an explicit user-modified source");
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.activeTaskId === "session-task" && !state.taskSubmitting);
  check(taskRequests.at(-1).ssh_session_id === sessionId && taskRequests.at(-1).target.device_id === device(0).device_id, "Remote task is bound to the current session and selected device");
  check(JSON.stringify(taskRequests.at(-1).target.user_overrides) === JSON.stringify({wave_size: 32}), "Only actual user edits are submitted as hardware overrides");
  check(taskRequests.at(-1).target.gpu_model === "Contract GPU 0" && !JSON.stringify(taskRequests.at(-1)).includes(secret), "Task identity uses detection and its body never contains the password");
  await page.evaluate(() => generateRunRequest());
  check(await page.evaluate(({password, id}) => !el.runRequestOutput.value.includes(password) && !el.runRequestOutput.value.includes(id), {password: secret, id: sessionId}), "Developer task export never includes the password or session id");
  await settingsView();
  await page.locator("#saveSettingsBtn").click();
  check(savedRequests.length === 1 && !JSON.stringify(savedRequests).includes(secret) && !JSON.stringify(savedRequests).includes(sessionId), "Saving long-term settings excludes password and session id");
  await page.locator("#disconnectSshBtn").click();
  await page.waitForFunction(() => state.sshStatus === "disconnected");
  check(deleted > 0 && cancelled === 0, "Disconnect revokes the SSH session without cancelling a task");
  check(await page.evaluate(() => state.task.status) === "running", "SSH disconnect does not report a running task as completed or cancelled");
  await page.evaluate(() => activateView("taskView"));
  await page.locator("#cancelTaskBtn").click();
  check(cancelled === 1, "Task cancellation still uses the existing task cancel endpoint");

  hardwareOutcome = "none";
  await connected();
  check((await page.locator("#connectionStatus").innerText()).includes("已连接") && (await page.locator("#remoteHardwareStatus").innerText()).includes("没有可见 GPU"), "SSH success with no visible GPU is not GPU identification success");
  await runnable();
  check(await page.locator("#startOptimizationBtn").isDisabled(), "No-GPU result blocks remote execution");

  hardwareOutcome = "multi";
  await connected();
  await runnable();
  check(await page.locator("#deviceSelectionField").isVisible() && await page.locator("#gpuDeviceSelect").inputValue() === "", "Multiple GPUs require an explicit selection");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Multi-GPU hosts cannot silently submit GPU zero");
  await page.locator("#gpuDeviceSelect").selectOption(device(1).device_id);
  await page.waitForFunction(() => !state.hardwareResolving && state.resolvedHardware?.fields?.target_name?.value === "Contract GPU 1");
  check(resolveRequests.at(-1).device_id === device(1).device_id && await page.locator('[data-hardware-override="shared_memory_per_block_bytes"]').inputValue() === "65537", "Selecting another GPU fetches that device's hardware");
  check(await page.evaluate(() => Object.keys(readHardwareOverrideFields()).length) === 0, "Device switch clears overrides from the previous GPU");
  check(!(await page.locator("#startOptimizationBtn").isDisabled()), "Recognized device can start a task without a password environment variable");

  taskOutcome = "expired";
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.sshStatus === "expired");
  check(await page.evaluate(() => state.sshSession === null) && (await page.locator("#taskDetailPanel").innerText()).includes("重新连接"), "Backend-invalidated session is cleared and asks for reconnection");
  const beforeBlocked = taskRequests.length;
  await page.evaluate(() => startOptimization());
  check(taskRequests.length === beforeBlocked, "Expired session cannot be reused through JavaScript");

  taskOutcome = "ok";
  hardwareOutcome = "single";
  await connected();
  hardwareOutcome = "expired";
  await runnable();
  await page.locator("#resolveHardwareBtn").click();
  await page.waitForFunction(() => state.sshStatus === "expired");
  check((await page.locator("#sshConnectionMessage").innerText()).includes("过期"), "Hardware endpoint expiry is shown separately from GPU detection failure");
  hardwareOutcome = "single";
  await connected();
  await page.evaluate(() => { state.sshSession.expiresAt = Date.now() - 1; updateStartButtonState(); activateView("startView"); });
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Client-side expiry blocks submission before a request");

  await connected();
  await runnable();
  await page.locator("#v2RunnerType").selectOption("local");
  await page.locator("#v2RunnerType").selectOption("ssh");
  await page.waitForFunction(() => !state.hardwareResolving && state.resolvedHardware?.fields?.target_name?.value === "Contract GPU 0");
  check(resolveRequests.at(-1).device_id === device(0).device_id, "Returning to SSH reloads hardware for the selected device");
  await settingsView();
  await page.locator("#settingSshHost").fill("changed.fixture.invalid");
  await page.waitForFunction(() => state.sshStatus === "disconnected");
  check(await page.evaluate(() => state.sshSession === null && state.selectedDeviceId === ""), "Changing connection details invalidates the old session and device selection");
  await page.locator("#settingSshHost").fill("fixture.invalid");
  await connected();
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy);
  check(await page.evaluate(() => state.sshSession === null) && await page.locator("#settingSshPassword").inputValue() === "", "Page reload never restores credentials or the session");
  check((await page.locator("#sshConnectionMessage").innerText()).includes("重新连接") && await safeStorage(), "Reload explicitly explains reconnection and retains no secrets");

  async function startDelayedHostA() {
    await settingsView();
    await page.locator("#settingSshHost").fill(hostA);
    holdHostA = true;
    await submitConnection();
    await page.waitForFunction(() => state.sshStatus === "connecting");
  }

  async function loadHostB() {
    backendSettings = {...settings, ssh: {...settings.ssh, host: hostB}};
    await page.locator("#loadSettingsBtn").click();
    await page.waitForFunction((host) => document.getElementById("settingSshHost").value === host, hostB);
  }

  await startDelayedHostA();
  const resolveCountBeforeSwitch = resolveRequests.length;
  await loadHostB();
  check(await page.evaluate(() => state.sshSession === null && state.sshStatus !== "connecting"), "Reading different backend settings invalidates the in-flight host A connection");
  const staleRevocation = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/ssh/sessions/${sessionA}` && response.request().method() === "DELETE");
  releaseHostA();
  await staleRevocation;
  await runnable();
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Late host A response cannot unlock remote submission with host B displayed");
  check(await page.evaluate(() => state.sshSession === null) && resolveRequests.length === resolveCountBeforeSwitch, "Stale session is revoked without hardware lookup or page adoption");
  check(revokedSessionIds.includes(sessionA), "Late host A session is explicitly revoked through DELETE");

  await startDelayedHostA();
  await loadHostB();
  await connected();
  check(await page.evaluate((id) => state.sshSession?.id === id && state.sshSession.settingsKey === sshSettingsKey(), sessionB), "New host B session is bound to the settings used by its request");
  const resolvesAfterB = resolveRequests.length;
  const revocationsOfBBefore = revokedSessionIds.filter((id) => id === sessionB).length;
  const staleWhileB = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/ssh/sessions/${sessionA}` && response.request().method() === "DELETE");
  releaseHostA();
  await staleWhileB;
  check(await page.evaluate((id) => state.sshSession?.id === id && state.sshStatus === "connected", sessionB), "Late host A response cannot overwrite a newer host B connection");
  check(resolveRequests.length === resolvesAfterB && revokedSessionIds.filter((id) => id === sessionB).length === revocationsOfBBefore, "Stale cleanup affects only host A, not host B hardware or session");
  await runnable();
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => !state.taskSubmitting);
  check(taskRequests.at(-1).ssh_session_id === sessionB, "Remote task submitted after the race uses host B's session");

  await startDelayedHostA();
  await loadHostB();
  backendSettings = {...settings, ssh: {...settings.ssh, host: hostA}};
  await page.locator("#loadSettingsBtn").click();
  await page.waitForFunction((host) => document.getElementById("settingSshHost").value === host, hostA);
  const returnedToA = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/ssh/sessions/${sessionA}` && response.request().method() === "DELETE");
  releaseHostA();
  await returnedToA;
  check(await page.evaluate(() => state.sshSession === null && !sessionIsActive()), "Switching A to B and back to A cannot revive an invalidated connection attempt");

  holdHostA = false;
  backendSettings = settings;
  await settingsView();
  await page.locator("#loadSettingsBtn").click();
  await page.waitForFunction(() => state.sshStatus === "disconnected");

  await connected();
  await runnable();
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    await page.evaluate(() => window.scrollTo(0, 0));
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Hardware view fits ${name} viewport`);
    check(await page.evaluate(() => {
      const section = document.querySelector('.hardware-card').getBoundingClientRect();
      const table = document.getElementById('v2HardwareEditor').getBoundingClientRect();
      return Math.abs(section.width - table.width - 34) < 3;
    }), `Hardware table uses the full available section width at ${name}`);
    await page.screenshot({path: `output/playwright/ssh-session-${name}.png`, fullPage: true});
  }
  await page.setViewportSize({width: 1440, height: 1000});
  await settingsView();
  check(!(await page.locator("body").innerText()).includes(secret), "Settings screenshot contains no password value");
  await page.screenshot({path: "output/playwright/ssh-session-settings.png", fullPage: true});
  return {passed: checks.length, checks, contractOnly: true, realHardwareEvidence: false};
}
