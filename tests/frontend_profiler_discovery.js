// Contract fixtures for backend PR #72, not hardware or performance evidence.
// Run in Chromium with the same-origin frontend loaded.
async (page) => {
  const checks = [];
  const check = (condition, label) => {
    if (!condition) throw new Error(label);
    checks.push(label);
  };
  const json = (route, body, status = 200) => route.fulfill({status, contentType: "application/json", body: JSON.stringify(body)});
  const automatic = {mcprofiler_executable: null, mcprofiler_server_executable: null};
  const found = {
    available: true, status: "available", version: "3.9.0.29", server_version: null,
    discovery_source: "allowlist", executable: "/opt/metax/mcProfiler/mcProfiler",
    server_executable: "/opt/metax/mcProfiler/profiler_server", server_mode: "embedded",
    reason_category: "none", warnings: ["independent server version is not reported"], logs: {},
  };
  let settings = {
    ssh: {host: "contract.invalid", port: 22, username: "contract", remote_workspace: "/tmp/contract", auth_type: "password"},
    llm: {base_url: "https://example.invalid/v1", model: "contract", api_key_env: "OPENAI_API_KEY"},
    profiler: {...automatic},
  };
  let discovery = {...found};
  let saveMode = "success";
  let heldResolve = false;
  let releaseResolve;
  let requestCount = 0;
  const posted = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/health") return json(route, {ok: true, capabilities: {source_optimization: true, mcprofiler_auto_collection: true}});
    if (path === "/api/settings" && request.method() === "GET") return json(route, {ok: true, settings});
    if (path === "/api/settings" && request.method() === "POST") {
      posted.push(request.postDataJSON());
      if (saveMode === "failure") return json(route, {detail: "contract save denied"}, 500);
      if (saveMode === "ignored") return json(route, {ok: true, settings});
      settings = request.postDataJSON();
      return json(route, {ok: true, settings});
    }
    if (path === "/api/ssh/sessions") return json(route, {ok: true, session_id: "discovery-contract-session", expires_at: new Date(Date.now() + 1800000).toISOString()});
    if (path.startsWith("/api/ssh/sessions/") && request.method() === "DELETE") return json(route, {ok: true});
    if (path === "/api/hardware/resolve") {
      requestCount += 1;
      const snapshot = discovery ? structuredClone(discovery) : null;
      if (heldResolve) await new Promise((resolve) => {releaseResolve = resolve;});
      return json(route, {
        ok: true, devices: [{device_id: "contract-device", identity_verified: true, backend: "mxmaca", fields: {target_name: "Contract GPU", backend: "mxmaca"}}],
        selected_device_id: "contract-device", detection_status: "partial", hardware: {fields: {}}, warnings: [],
        ...(snapshot ? {profiler_capabilities: {mcprofiler: snapshot}} : {}),
      });
    }
    if (path === "/api/tasks" && request.method() === "GET") return json(route, {ok: true, tasks: []});
    return json(route, {detail: "Unhandled contract route"}, 404);
  });
  await page.reload();
  await page.waitForFunction(() => state.profilerSettingsSupported);
  await page.evaluate(() => activateView("settingsView"));
  check(await page.locator("#advancedProfilerSettings").isVisible(), "Explicit settings contract enables the real advanced path inputs");
  await page.locator("#advancedProfilerSettings > summary").click();
  await page.locator("#settingSshPassword").fill("contract-password-only");
  await page.locator("#connectSshBtn").click();
  await page.waitForFunction(() => sessionIsActive() && !state.hardwareResolving);
  let text = await page.locator("#remoteProfilerStatus").innerText();
  check(text.includes("已发现 mcProfiler") && text.includes("3.9.0.29"), "Connection resolves the actual discovery status and version");
  check(text.includes("分别核验"), "Tool discovery is not collection, attribution or speedup success");
  check(await page.locator("#settingSshPassword").inputValue() === "", "Connection still clears the one-time password");

  await page.locator("#settingMcProfilerPath").fill("/opt/contract-tools/mcProfiler");
  check((await page.locator("#remoteProfilerStatus").innerText()).includes("尚未保存"), "Editing a path clears the previous discovery claim");
  check((await page.evaluate(() => remoteRunnerBlocker("ssh"))).includes("工具路径尚未保存"), "An unsaved path blocks remote submission with an actionable reason");
  check(!(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage) + toYaml(settingsFileObject()))).includes("/opt/contract-tools"), "Tool paths are not silently saved to browser storage or exported files");
  saveMode = "failure";
  await page.locator("#saveSettingsBtn").click();
  await page.waitForFunction(() => !state.profilerSettingsSaving);
  check((await page.locator("#profilerSettingsStatus").innerText()).includes("保存失败"), "A failed POST is not presented as saved");
  check((await page.evaluate(() => profilerSettingsPending())), "Failed persistence keeps the path unsaved");
  saveMode = "ignored";
  await page.locator("#saveSettingsBtn").click();
  await page.waitForFunction(() => !state.profilerSettingsSaving);
  check((await page.locator("#profilerSettingsStatus").innerText()).includes("未确认本次工具路径"), "A server ignoring new fields cannot falsely confirm saving");
  saveMode = "success";
  await page.locator("#settingProfilerServerPath").fill("/opt/contract-tools/profiler_server");
  await page.locator("#saveSettingsBtn").click();
  await page.waitForFunction(() => !state.profilerSettingsSaving);
  check(posted.at(-1).profiler.mcprofiler_executable === "/opt/contract-tools/mcProfiler" && posted.at(-1).profiler.mcprofiler_server_executable === "/opt/contract-tools/profiler_server", "POST persists both exact backend path fields");
  check((await page.locator("#profilerSettingsStatus").innerText()).includes("已保存到后端"), "Saved status requires the backend echo");
  check(!JSON.stringify(posted).includes("contract-password-only"), "Settings requests never include the transient password");
  await page.locator("#loadSettingsBtn").click();
  check(await page.locator("#settingMcProfilerPath").inputValue() === "/opt/contract-tools/mcProfiler", "GET reads the saved path back into the form");
  await page.locator("#settingMcProfilerPath").fill("");
  await page.locator("#settingProfilerServerPath").fill("");
  await page.locator("#saveSettingsBtn").click();
  await page.waitForFunction(() => !state.profilerSettingsSaving);
  check(JSON.stringify(posted.at(-1).profiler) === JSON.stringify(automatic), "Clearing paths sends null for automatic discovery");

  for (const [status, reason, label] of [
    ["unavailable", "client_unavailable", "未找到"], ["unavailable", "server_unavailable", "profiler_server"],
    ["failed", "timeout", "超时"], ["failed", "guard_denied", "安全规则"],
    ["cancelled", "cancelled", "已取消"], ["not_checked", "ssh_session_required", "SSH 会话"],
  ]) {
    discovery = {...found, available: false, status, version: null, reason_category: reason};
    await page.evaluate(() => resolveHardwareProfile());
    text = await page.locator("#remoteProfilerStatus").innerText();
    check(text.includes(label) && text.includes("版本：未返回") && !text.includes("已发现"), `${status}/${reason} retains its explicit reason without stale version or success`);
    if (["unavailable", "failed"].includes(status)) check(text.includes("benchmark 与日志分析"), `${reason} explains the genuine fallback`);
  }
  discovery = {...found, available: false};
  await page.evaluate(() => resolveHardwareProfile());
  check((await page.locator("#remoteProfilerStatus").innerText()).includes("字段不一致"), "Contradictory discovery fields remain unconfirmed");
  discovery = null;
  await page.evaluate(() => resolveHardwareProfile());
  check((await page.locator("#remoteProfilerStatus").innerText()).includes("后端未返回"), "Legacy hardware payload does not become tool available");

  discovery = {...found};
  heldResolve = true;
  const before = requestCount;
  await page.evaluate(() => { void resolveHardwareProfile(); });
  await page.waitForFunction(() => state.hardwareResolving);
  while (requestCount === before || !releaseResolve) await new Promise((resolve) => setTimeout(resolve, 10));
  await page.locator("#settingMcProfilerPath").fill("/opt/changed/mcProfiler");
  releaseResolve();
  heldResolve = false;
  await page.waitForTimeout(100);
  check(await page.evaluate(() => state.remoteProfilerCapabilities === null), "An in-flight probe cannot restore discovery from before the path edit");
  check((await page.locator("#remoteProfilerStatus").innerText()).includes("尚未保存"), "The stale probe cannot hide the unsaved-path warning");
  await page.locator("#loadSettingsBtn").click();
  await page.waitForFunction(() => !profilerSettingsPending());
  await page.evaluate(() => resolveHardwareProfile());
  await page.evaluate(() => disconnectSshSession());
  check(!(await page.locator("#remoteProfilerStatus").innerText()).includes("已发现"), "Disconnect removes discovery tied to the old session");

  delete settings.profiler;
  await page.reload();
  await page.waitForFunction(() => document.getElementById("settingSshUsername").value === "contract");
  await page.evaluate(() => activateView("settingsView"));
  check(await page.locator("#advancedProfilerSettings").isHidden(), "Legacy settings contract does not expose an unpersistable control");
  check(await page.evaluate(() => !("profiler" in settingsApiPayload())), "Legacy settings POST omits unrecognized profiler fields");

  const results = {
    profiler_capabilities: {mcprofiler: {...found, available: false, status: "unavailable", version: null, reason_category: "client_unavailable"}},
    profiler_available: false, profiler_collections: [], evidence_summary: [], source_best_verified: false,
    source_optimization: {schema_version: "v2.source_optimization.v1", status: "cancelled", trials: []},
  };
  await page.evaluate((result) => {
    state.remoteProfilerCapabilities = {mcprofiler: {available: true, status: "available", version: "other-session-version"}};
    state.task = {task_id: "contract-cancelled", status: "cancelled", execution_mode: "source_optimization"};
    state.taskResults = result;
    renderTaskDetail(); renderApiResults(); activateView("resultsView");
  }, results);
  check(await page.locator("#apiResultsPanel").isVisible(), "Discovery contract is rendered in the visible results view");
  text = await page.locator("#taskProfilerDiscovery").innerText();
  check(text.includes("未找到") && !text.includes("other-session-version"), "Task results use task discovery, never a newer connection's tool state");
  text = await page.locator("#apiResultsPanel").innerText();
  check(text.includes("已取消，best 未发布") && !text.includes("当前 best 验证失败"), "Cancellation explains withheld best publication without claiming corruption");
  check(await page.locator("#downloadBestKernelBtn").isDisabled(), "Cancelled unpublished best remains non-downloadable");
  for (const label of ["采集状态", "指标归属", "是否可用于优化", "性能验证结果"]) check(text.includes(label), `Trust layer remains separate: ${label}`);

  const outcome = async (status, decision, median) => page.evaluate(({status, decision, median}) => {
    const trial = {status, artifacts: decision ? {performance_gate: {
      schema_version: "v2.performance_gate.v1", decision, reason: "contract reason", median_improvement_percent: median,
      codegen_status: "unavailable", baseline_recheck_passed: false, baseline_codegen_hash: null, candidate_codegen_hash: null,
    }} : {}, rollback_verified: true};
    const source = {schema_version: "v2.source_optimization.v1", status: "completed", trials: [trial]};
    return sourceOutcomeLabel(source, {source_optimization: source}, {status: "completed"});
  }, {status, decision, median});
  check((await outcome("no_improvement", "no_improvement", -2)).includes("候选变慢"), "Backend negative median and rejected gate explain slower candidates");
  check((await outcome("no_improvement", "inconclusive", 5)).includes("收益不确定"), "Inconclusive gate overrides a positive measured median");
  check((await outcome("no_improvement", "inconclusive", -5)).includes("收益不确定"), "Inconclusive gate is not relabeled slower from its median");
  check((await outcome("no_improvement", "no_improvement", 0.1)).includes("收益门槛"), "Below-threshold benefit is not mislabeled slower");
  check((await outcome("correctness_failed")).includes("正确性失败"), "Correctness rejection explains retaining the baseline");
  check((await outcome("build_failed")).includes("编译失败"), "Build rejection remains distinct from correctness");
  check((await page.evaluate(() => sourceOutcomeLabel({status: "unavailable"}, {}, {status: "completed"}))).includes("没有适用变换"), "No applicable change is not an optimization success");
  check((await page.evaluate(() => sourceOutcomeLabel({status: "completed", trials: [{status: "correctness_failed"}]}, {}, {status: "running"}))).includes("等待结果核验"), "Running task does not prematurely announce terminal rejection");
  return {passed: checks.length, checks};
}
