// Contract fixtures only. Live CPU/API acceptance is recorded separately.
async (page) => {
  const checks = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  const json = (route, data, status = 200) => route.fulfill({status, contentType: "application/json", body: JSON.stringify(data)});
  const posted = [];
  let sessions = 0, uploads = 0, deviceCount = 1, releaseUpload;
  let holdUpload = false, rejectUpload = false, rejectTask = false, offline = false;
  const secret = "contract-only-transient-password";
  const sessionId = "contract-only-memory-session";
  const task = {task_id: "file-contract-task", status: "running", current_stage: "preflight_started", execution_mode: "source_optimization"};
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/health") return json(route, {ok: !offline, capabilities: {source_optimization: true}}, offline ? 503 : 200);
    if (path === "/api/settings") return json(route, {ok: true, settings: {ssh: {host: "contract.invalid", username: "contract", port: 22, auth_type: "password", remote_workspace: "/tmp/contract"}, llm: {}}});
    if (path === "/api/ssh/sessions") {
      sessions += 1;
      check(request.postDataJSON().password === secret, "Connection uses the one-time password only in the session request");
      return json(route, {ok: true, session_id: sessionId, expires_at: new Date(Date.now() + 1800000).toISOString()});
    }
    if (path.startsWith("/api/ssh/sessions/")) return json(route, {ok: true});
    if (path === "/api/hardware/resolve") {
      const devices = Array.from({length: deviceCount}, (_, index) => ({device_id: `contract-gpu-${index}`, runtime_index: index, identity_verified: true, backend: "mxmaca", fields: {target_name: `Contract GPU ${index}`, backend: "mxmaca"}}));
      const chosen = request.postDataJSON().device_id || (devices.length === 1 ? devices[0].device_id : null);
      return json(route, {ok: true, devices, selected_device_id: chosen, hardware: {fields: {}}, detection_status: "partial"});
    }
    if (path === "/api/samples/upload") {
      uploads += 1;
      if (rejectUpload) return json(route, {detail: "temporary upload failure"}, 503);
      if (holdUpload) await new Promise((resolve) => { releaseUpload = resolve; });
      return json(route, {ok: true, upload_id: `contract-upload-${uploads}`, entry_file: "kernel.py"});
    }
    if (path === "/api/tasks" && request.method() === "GET") return json(route, {ok: true, tasks: []});
    if (path === "/api/tasks" && request.method() === "POST") {
      posted.push(request.postDataJSON());
      return rejectTask ? json(route, {detail: {code: "ssh_session_expired", message: secret}}, 410) : json(route, {ok: true, task_id: task.task_id, task});
    }
    if (path.endsWith("/events")) return json(route, {events: [{type: "preflight_started", message: "environment preflight"}]});
    if (path === `/api/tasks/${task.task_id}`) return json(route, {task});
    return json(route, {detail: "Unhandled contract endpoint"}, 404);
  });
  const selectFiles = async (paths) => {
    await page.evaluate(async (paths) => {
      const files = paths.map((path) => {
        const file = new File(["print('contract input')"], path.split("/").pop());
        Object.defineProperty(file, "webkitRelativePath", {value: `sample/${path}`});
        return file;
      });
      await selectSampleFiles(files, "directory");
    }, paths);
  };
  const common = ["kernel.py", "correctness.py", "benchmark.py", "reference.py"];
  const advanced = async () => {
    const panel = page.locator("#advancedTaskSettings");
    if (await panel.getAttribute("open") === null) await panel.locator(":scope > summary").click();
  };
  const startView = () => page.locator('.tab-button[data-view-target="startView"]').click();
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy && document.getElementById("settingSshUsername").value === "contract");
  check(await page.locator("#v2SampleSourceType").inputValue() === "upload_directory", "Default intake is a directory, not configuration or command files");
  check(await page.locator("#v2RunnerType").inputValue() === "ssh", "SSH is the default runner");
  check(await page.locator("#v2ExecutionIntent").inputValue() === "source_optimization", "Source optimization is the default intent");
  check(await page.locator("#startOptimizationBtn").innerText() === "开始优化", "Primary action is the real optimization flow");
  for (const selector of ["#v2GpuModel", "#v2Backend", "#v2HardwareEditor", "#v2BuildCommand", "#v2CorrectnessCommand", "#v2BenchmarkCommand"]) check(await page.locator(selector).isHidden(), `Ordinary page hides advanced input ${selector}`);
  check(await page.locator("#deviceSelectionField").isHidden(), "No card selector is shown before multiple actual devices are returned");
  await selectFiles(common);
  check(await page.locator("#v2UploadEntrySelect").inputValue() === "kernel.py", "Unique kernel.py is preselected");
  check(await page.locator("#v2CorrectnessFileSelect").inputValue() === "correctness.py", "Unique correctness.py is preselected");
  check(await page.locator("#v2BenchmarkFileSelect").inputValue() === "benchmark.py", "Unique benchmark.py is preselected");
  check(await page.locator(".sample-file-row").count() === 4, "File list retains auxiliary files");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Files alone do not imply an active SSH connection");
  await page.evaluate(() => startOptimization());
  check(posted.length === 0, "JavaScript cannot bypass the SSH requirement");

  await page.locator('.tab-button[data-view-target="settingsView"]').click();
  await page.locator("#settingSshPassword").fill(secret);
  await page.locator("#connectSshBtn").click();
  await page.waitForFunction(() => sessionIsActive() && !state.hardwareResolving);
  check(await page.locator("#settingSshPassword").inputValue() === "", "Password is cleared after submission");
  await startView();
  check(await page.locator("#deviceSelectionField").isHidden(), "Single discovered GPU binds without a card-selection step");
  let request = await page.evaluate(() => buildApiTaskRequest());
  check(request.runner.type === "ssh" && request.optimization.enabled === true, "Default request keeps SSH and source optimization");
  check(!Object.hasOwn(request, "commands"), "Automatic mode omits commands entirely");
  check(request.ssh_session_id === sessionId && request.target.device_id === "contract-gpu-0", "Task reuses the current real-interface session/device contract");
  check(JSON.stringify(request.test_files) === JSON.stringify({correctness: "correctness.py", benchmark: "benchmark.py"}), "Request uses exactly the backend test_files fields");
  check(Object.keys(request.target.user_overrides).length === 0, "Detected hardware is not resubmitted as user overrides");
  check(!JSON.stringify(request).includes(secret), "Task body never contains the password");
  check(await page.evaluate(({secret, sessionId}) => { const stored = JSON.stringify(localStorage) + JSON.stringify(sessionStorage) + JSON.stringify(settingsFileObject()); return !stored.includes(secret) && !stored.includes(sessionId); }, {secret, sessionId}), "Password and session ID stay out of browser storage and exported settings");

  await page.locator("#v2BenchmarkFileSelect").selectOption("correctness.py");
  request = await page.evaluate(() => buildApiTaskRequest());
  check(request.test_files.correctness === request.test_files.benchmark, "Correctness and performance may share a protected script");
  await page.locator("#v2CorrectnessFileSelect").selectOption("kernel.py");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Operator/test role collision is blocked before POST");
  check((await page.locator("#sampleRoleMessage").innerText()).includes("算子入口与测试文件分开"), "Role collision explains the backend protection boundary");
  const beforeInvalid = posted.length;
  await page.evaluate(() => startOptimization());
  check(posted.length === beforeInvalid, "Role validation is repeated in JavaScript");

  await selectFiles(["a/kernel.py", "b/kernel.py", "correctness.py", "benchmark.py"]);
  check(await page.locator("#v2UploadEntrySelect").inputValue() === "", "Multiple kernel candidates are never silently selected");
  await page.locator("#v2UploadEntrySelect").selectOption("b/kernel.py");
  await page.waitForFunction(() => document.getElementById("v2SampleText").value === "print('contract input')");
  check(await page.locator("#v2SampleText").inputValue() === "print('contract input')", "Explicit entry choice reads that file into the preview");
  await selectFiles(["operator.py", "check.py", "bench.py"]);
  check(await page.locator("#v2UploadEntrySelect").inputValue() === "" && await page.locator("#v2CorrectnessFileSelect").inputValue() === "" && await page.locator("#v2BenchmarkFileSelect").inputValue() === "", "Unknown filenames require explicit confirmation for every role");
  await page.locator("#v2UploadEntrySelect").selectOption("operator.py");
  await page.locator("#v2CorrectnessFileSelect").selectOption("check.py");
  await page.locator("#v2BenchmarkFileSelect").selectOption("bench.py");
  check(await page.locator("#startOptimizationBtn").isEnabled(), "Confirmed nonstandard filenames are supported");
  await page.locator('[aria-label="移除 check.py"]').click();
  await page.waitForFunction(() => document.getElementById("v2CorrectnessFileSelect").value === "");
  check(await page.locator("#v2CorrectnessFileSelect").inputValue() === "" && await page.locator("#startOptimizationBtn").isDisabled(), "Removing a role file clears that role without silently replacing it");

  await selectFiles(common);
  await advanced();
  await page.locator("#v2CustomCommands").check();
  for (const [selector, text] of [["#v2BuildCommand", "python -m py_compile kernel.py"], ["#v2CorrectnessCommand", "python correctness.py --strict"], ["#v2BenchmarkCommand", "python benchmark.py --repeat 5"]]) await page.locator(selector).fill(text);
  request = await page.evaluate(() => buildApiTaskRequest());
  check(request.commands.correctness_command.endsWith("--strict"), "Explicit advanced commands are passed unchanged");
  await page.locator("#v2CustomCommands").uncheck();
  check(!Object.hasOwn(await page.evaluate(() => buildApiTaskRequest()), "commands"), "Returning to automatic mode omits retained command text");
  for (const value of ["0", ""]) {
    await page.locator("#v2TimeoutSeconds").fill(value);
    check(await page.locator("#startOptimizationBtn").isDisabled(), `Invalid timeout is blocked: ${value || "empty"}`);
  }
  await page.locator("#v2TimeoutSeconds").fill("60");
  await page.locator("#advancedTaskSettings > summary").click();

  deviceCount = 2;
  await page.evaluate(() => { state.selectedDeviceId = ""; return resolveHardwareProfile(); });
  check(await page.locator("#deviceSelectionField").isVisible() && await page.locator("#startOptimizationBtn").isDisabled(), "Multiple GPUs require explicit device selection");
  await page.locator("#gpuDeviceSelect").selectOption("contract-gpu-1");
  await page.waitForFunction(() => !state.hardwareResolving && currentRemoteDevice()?.device_id === "contract-gpu-1");
  check((await page.evaluate(() => buildApiTaskRequest())).target.device_id === "contract-gpu-1", "Selected device is used without overwriting its identity");

  holdUpload = true;
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.sampleUploadStage === "uploading");
  const beforeRace = posted.length;
  await page.locator("#v2BenchmarkFileSelect").selectOption("correctness.py");
  while (!releaseUpload) await new Promise((resolve) => setTimeout(resolve, 10));
  releaseUpload(); holdUpload = false;
  await page.waitForFunction(() => !state.taskSubmitting);
  check(posted.length === beforeRace, "Changing roles during upload invalidates the pending submission");
  check((await page.locator("#startValidationMessage").innerText()).includes("发生变化"), "Stale upload gives a Chinese retry message");
  await selectFiles(common);
  rejectUpload = true;
  const beforeUploadFailure = posted.length;
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => !state.taskSubmitting && state.sampleUploadStage === "error");
  check(posted.length === beforeUploadFailure, "Failed upload never creates a task");
  check(await page.locator("#startOptimizationBtn").isEnabled() && (await page.locator("#sampleFileList").innerText()).includes("可以保留当前文件并重试"), "Transient upload failure has a Chinese error and remains retryable");
  rejectUpload = false;
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.activeTaskId === "file-contract-task" && !state.taskSubmitting);
  check(await page.locator("#taskView").isVisible(), "Successful creation navigates to task detail");
  check(sessions === 1, "Task submission does not create another SSH session");
  await startView();
  rejectTask = true;
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => !state.taskSubmitting && state.sshStatus === "expired");
  check((await page.locator("#startValidationMessage").innerText()).includes("重新连接"), "Expired task session requires reconnection");
  check(!(await page.locator("#startValidationMessage").innerText()).includes(secret), "Private API failure never echoes a server-provided password");

  const outcomes = await page.evaluate(() => {
    state.taskEvents = [];
    return ["dependency_missing", "script_protocol_incompatible", "correctness_failed"].map((status) => fileTaskOutcome({preflight: {error_category: status}}, {status: "failed"})?.label);
  });
  check(outcomes.join("/") === "环境缺依赖/测试脚本不兼容/正确性失败", "Known backend failure categories remain distinct");
  check(await page.evaluate(() => fileTaskOutcome({source_optimization: {status: "completed", trials: [{status: "no_improvement"}]}}, {status: "completed"}).label) === "收益不足或不确定", "Rejected performance is not optimization success");
  check(await page.evaluate(() => fileTaskOutcome({source_optimization: {status: "unavailable", trials: []}}, {status: "completed"}).label) === "没有适用优化", "Unavailable transformation remains distinct from failure or speedup");
  check(await page.evaluate(() => fileTaskOutcome({source_optimization: {status: "completed", trials: [{status: "no_improvement"}]}}, {status: "running"})) === null, "Running task does not prematurely declare insufficient benefit");
  check(await page.evaluate(() => fileTaskOutcome({failed_cases: [{status: "correctness_failed"}], source_optimization: {status: "running", trials: [{status: "correctness_failed"}]}}, {status: "running"})) === null, "Running task with a rejected candidate does not declare a final correctness failure");
  check(await page.evaluate(() => fileTaskOutcome({summary_table: [{trial_id: "baseline", status: "correctness_failed", correctness: {passed: false}}]}, {status: "failed"})?.label) === "正确性失败", "An actual baseline correctness failure still has a task-level explanation");
  for (const [status, label, reason, maxError] of [
    ["correctness_failed", "正确性失败", "reference mismatch", 7],
    ["correctness_timeout", "正确性检查超时", "command timed out after 1s", null],
    ["script_protocol_incompatible", "测试脚本不兼容", "missing structured result", null],
  ]) {
    await page.evaluate(({status, reason, maxError}) => {
      stopTaskPolling();
      const baseline = {run_id: "20261029-181526", iteration: "0", candidate_id: "0", config_hash: "baseline-hash", status,
        correctness: {passed: false, max_error: maxError, reason}};
      const failure = {...baseline, iteration: 0, candidate_id: 0, trial_id: "20261029-181526:iter000_cand000", error: {category: status, stage: "correctness", message: reason}};
      state.task = {status: "failed", execution_mode: "baseline_only", error: reason};
      state.taskResults = {summary_table: [{...baseline, iteration: "1", candidate_id: "1", status: "correctness_failed"}, baseline],
        failed_cases: [{...failure, run_id: "another-run", error: {category: "dependency_missing", message: "unrelated failure"}}, failure],
        source_optimization: {status: "failed", reason, trials: []}, best_kernel: "# unverified", report_markdown: "# Real response contract"};
      renderTaskDetail(); renderApiResults();
    }, {status, reason, maxError});
    for (const selector of ["#taskIntakeFeedback", "#fileTaskFeedback"]) {
      const feedback = await page.locator(selector).textContent();
      check(feedback.includes(label) && feedback.includes(reason) && feedback.includes("正确性验证"), `${status}: ${selector} uses the real CSV identity and structured failure stage/reason`);
      check(!feedback.includes("unrelated failure") && (maxError === null || feedback.includes("max_error：7")), `${status}: baseline details never borrow another run's failure`);
    }
    check(await page.locator("#downloadBestKernelBtn").isDisabled() && await page.locator("#downloadReportBtn").isEnabled(), `${status}: invalid best stays disabled while report remains readable`);
  }
  check(await page.evaluate(() => taskBaselineRow({summary_table: [{iteration: "1", candidate_id: "1", status: "correctness_failed"}]})) === null, "A source candidate is never guessed to be a missing baseline");
  check(await page.evaluate(() => {
    state.taskEvents = [{type: "preflight_failed", message: "dependency_missing"}];
    return fileTaskOutcome({preflight: {status: "passed"}, failed_cases: [null, "malformed row"], source_optimization: {status: "unavailable", trials: []}}, {status: "completed"}).label;
  }) === "没有适用优化", "Previous-task events and malformed rows cannot fabricate a current-task dependency failure");
  await page.evaluate(() => {
    stopTaskPolling();
    state.task = {status: "cancelled", execution_mode: "baseline_only"};
    state.taskResults = {summary_table: [], source_optimization: {status: "unavailable", trials: []}};
    renderApiResults();
  });
  check((await page.locator("#apiResultsPanel").textContent()).includes("已取消，验证未完成") && !(await page.locator("#apiResultsPanel").textContent()).includes("基线验证未通过"), "Cancellation is incomplete verification, not a correctness failure");
  check(await page.locator("#downloadBestKernelBtn").isDisabled(), "Cancelled unverified source is not published as best");
  check(await page.locator("#apiResultsStatus").textContent() === "已取消", "Task status is Chinese rather than the raw backend token");
  await startView();
  await page.evaluate(async () => {
    const files = ["kernel.py", "other.py", "correctness.py", "benchmark.py"].map((name) => new File(["# readable"], name));
    files[0].text = async () => { throw new Error("unreadable file"); };
    await selectSampleFiles(files, "fallback");
  });
  check((await page.locator("#sampleFileList").textContent()).includes("读取入口文件失败"), "Unreadable operator has an actionable file error");
  await page.locator("#v2UploadEntrySelect").selectOption("other.py");
  await page.waitForFunction(() => !state.sampleReadError && document.getElementById("v2SampleText").value === "# readable");
  check(await page.evaluate(() => uploadSelectionValidationError()) === "", "Selecting a readable replacement recovers from a local read error");

  const unsafe = await page.evaluate(() => {
    const fake = (name, size = 1, webkitRelativePath = "") => ({name, size, webkitRelativePath});
    return [[fake(".env")], [fake("id_rsa")], [fake("token.py", 1, "sample/.ssh/token.py")], [fake("kernel.py", 1, "sample/../kernel.py")], [fake("a.py"), fake("A.py")], [fake("con.py")], [fake("big.py", 3 * 1024 * 1024)]].map((files) => { try { normalizeSelectedSampleFiles(files, "directory"); return false; } catch { return true; } });
  });
  check(unsafe.every(Boolean), "Existing sensitive-file, path, duplicate and size safeguards remain enforced");
  await startView();
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: 900});
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `File-role UI has no page overflow at ${width}px`);
    check(await page.locator("#sampleRoles select").count() === 3, "All three role controls remain present on the viewport");
  }
  offline = true;
  await page.reload();
  await page.waitForFunction(() => state.apiError);
  check((await page.locator("#apiHealthStatus").innerText()).includes("未启动"), "Unavailable API is not presented as a running task");
  return {passed: checks.length, checks, contractOnly: true, hardwareEvidence: false};
}
