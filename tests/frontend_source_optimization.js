// Browser contract fixture only. It is not hardware or optimization evidence.
// Open the same-origin frontend, then run with playwright-cli --filename.
async (page) => {
  const passed = [];
  const taskRequests = [];
  const baselineHash = "a".repeat(64);
  const acceptedHash = "b".repeat(64);
  let sourceCapability;
  let healthOk = true;
  let taskStatus = "running";
  let cancelRequested = false;

  function check(condition, label) {
    if (!condition) throw new Error(label);
    passed.push(label);
  }

  function json(route, body, status = 200) {
    return route.fulfill({status, contentType: "application/json", body: JSON.stringify(body)});
  }

  function task(taskId = "source-contract-task", mode = "baseline_only") {
    return {
      task_id: taskId,
      project_name: "source-contract-fixture",
      status: taskStatus,
      execution_mode: mode,
      current_stage: taskStatus === "cancelled" ? "task_cancelled" : "source_trial_running",
      total_trials: mode === "source_optimization" ? 2 : 1,
      baseline_latency: 1,
      best_latency: mode === "source_optimization" ? 0.9 : 1,
      improvement_percent: mode === "source_optimization" ? 10 : null,
      cancel_requested: cancelRequested,
      events: [{time: "fixture", type: "source_trial_running", message: "contract fixture event"}],
    };
  }

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = request.url().replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    if (path === "/api/health") {
      const capabilities = sourceCapability === undefined ? {} : {source_optimization: sourceCapability};
      return json(route, {ok: healthOk, service: "contract-fixture", capabilities, detail: healthOk ? undefined : "fixture health check failed"});
    }
    if (path === "/api/settings" && request.method() === "GET") {
      return json(route, {ok: true, settings: {ssh: {}, llm: {}}});
    }
    if (path === "/api/tasks" && request.method() === "GET") return json(route, {ok: true, tasks: []});
    if (path === "/api/tasks" && request.method() === "POST") {
      const body = request.postDataJSON();
      taskRequests.push(body);
      const mode = body.optimization?.enabled === true ? "source_optimization" : "baseline_only";
      const taskId = `source-contract-${taskRequests.length}`;
      return json(route, {ok: true, task_id: taskId, task: task(taskId, mode)});
    }
    const taskMatch = path.match(/^\/api\/tasks\/([^/]+)$/);
    if (taskMatch && request.method() === "GET") {
      const latest = taskRequests.at(-1);
      const mode = latest?.optimization?.enabled === true ? "source_optimization" : "baseline_only";
      return json(route, {ok: true, task: task(taskMatch[1], mode)});
    }
    if (/\/events$/.test(path)) return json(route, {ok: true, status: taskStatus, events: task().events});
    if (/\/results$/.test(path)) return json(route, {ok: true, task: task(), results: {summary_table: [], source_optimization: null}});
    if (/\/cancel$/.test(path) && request.method() === "POST") {
      cancelRequested = true;
      return json(route, {ok: true, task: task("source-contract-cancel", "source_optimization")});
    }
    return json(route, {detail: `Unhandled route ${path}`}, 404);
  });

  async function reloadHealthy() {
    taskStatus = "running";
    cancelRequested = false;
    await page.reload();
    await page.waitForFunction(() => state.apiHealthy === true);
  }

  async function fillRunnableSource() {
    await page.locator("#v2SampleText").fill("print('contract fixture')");
    const details = page.locator("#startView details");
    if ((await details.getAttribute("open")) === null) await details.locator("summary").click();
    await page.locator("#v2CorrectnessCommand").fill("python -c \"print('CORRECTNESS_RESULT status=PASS max_error=0 reason=fixture')\"");
    await page.locator("#v2BenchmarkCommand").fill("python -c \"print('BENCHMARK_RESULT latency_ms=1')\"");
  }

  function resultFixture(sourceOptimization) {
    return {
      summary_table: [
        {trial_id: "baseline", status: "benchmark_ok", latency: 1, objective_value: 1},
        {trial_id: "accepted-1", status: "benchmark_ok", latency: 0.9, objective_value: 0.9},
      ],
      best_row: {trial_id: "accepted-1", status: "benchmark_ok", latency: 0.9, objective_value: 0.9},
      best_kernel: "# Contract fixture only",
      best_config: {},
      report_markdown: "# Contract fixture only; not optimization evidence",
      evidence_summary: [{evidence_id: "ev-1", metric_name: "latency_ms", value: 1, unit: "ms", source: "benchmark/log", confidence: "medium"}],
      diagnoses: [],
      profiler_available: false,
      source_optimization: sourceOptimization,
    };
  }

  async function renderResult(sourceOptimization, executionMode = "source_optimization", overrides = {}) {
    await page.evaluate(({results, executionMode}) => {
      state.task = {
        task_id: "render-contract-task",
        project_name: "source-contract-fixture",
        status: "completed",
        execution_mode: executionMode,
        baseline_latency: 1,
        best_latency: 0.9,
        improvement_percent: 10,
      };
      state.taskResults = results;
      renderApiResults();
      activateView("resultsView");
    }, {results: {...resultFixture(sourceOptimization), ...overrides}, executionMode});
    return page.locator("#apiResultsPanel").innerText();
  }

  sourceCapability = undefined;
  await reloadHealthy();
  await fillRunnableSource();
  check(await page.locator('#v2ExecutionIntent option[value="source_optimization"]').isDisabled(), "Old backend keeps source optimization disabled");
  check(await page.locator("#v2ExecutionIntent").inputValue() === "baseline_only", "Missing capability falls back to baseline validation");
  check((await page.locator("#sourceOptimizationCapabilityHint").innerText()).includes("旧后端兼容模式"), "Missing capability is explained without claiming optimization");
  check((await page.locator("#startOptimizationBtn").innerText()) === "验证并测量", "Old backend keeps the baseline action label");
  const requestsBeforeForcedMode = taskRequests.length;
  await page.evaluate(() => {
    el.v2ExecutionIntent.value = "source_optimization";
    startOptimization();
  });
  check(taskRequests.length === requestsBeforeForcedMode, "Missing capability cannot be bypassed through JavaScript");
  check((await page.locator("#taskDetailPanel").innerText()).includes("未声明源码优化能力"), "Forced source mode shows a capability error");
  await page.evaluate(() => syncSourceOptimizationUi());
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.activeTaskId && !state.taskSubmitting);
  check(!Object.hasOwn(taskRequests.at(-1), "optimization"), "Baseline request remains backward compatible");

  healthOk = false;
  sourceCapability = true;
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy === false && state.apiError.includes("fixture health check failed"));
  check((await page.locator("#apiHealthStatus").innerText()) === "API：未启动", "Health payload with ok=false is not presented as connected");
  check(await page.locator('#v2ExecutionIntent option[value="source_optimization"]').isDisabled(), "Failed health response cannot enable source optimization");
  healthOk = true;

  sourceCapability = false;
  await reloadHealthy();
  check((await page.locator("#sourceOptimizationCapabilityHint").innerText()).includes("未启用源码优化"), "Explicit false capability keeps baseline mode");

  sourceCapability = true;
  await reloadHealthy();
  await fillRunnableSource();
  check(await page.locator('#v2ExecutionIntent option[value="source_optimization"]').isEnabled(), "Capability enables the source optimization mode");
  check(await page.locator("#v2ExecutionIntent").inputValue() === "source_optimization", "Capable backend selects the source optimization path");
  check((await page.locator("#startOptimizationBtn").innerText()) === "开始源码优化", "Capable backend exposes the real source optimization action");

  const requestsBeforeInvalid = taskRequests.length;
  await page.locator("#v2OptimizationMaxCandidates").fill("0");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Zero source candidate budget blocks submission");
  check((await page.locator("#startValidationMessage").innerText()).includes("源码候选次数"), "Invalid source candidate budget shows a Chinese error");
  await page.evaluate(() => startOptimization());
  check(taskRequests.length === requestsBeforeInvalid, "JavaScript cannot bypass the source candidate budget validation");
  await page.locator("#v2OptimizationMaxCandidates").fill("3");
  await page.locator("#v2BenchmarkRepeats").fill("0");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Zero benchmark repeats blocks submission");
  await page.locator("#v2BenchmarkRepeats").fill("5");
  await page.locator("#v2MinImprovementPercent").fill("-1");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Negative improvement threshold blocks submission");
  await page.locator("#v2MinImprovementPercent").fill("1");
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.activeTaskId && !state.taskSubmitting);
  check(JSON.stringify(taskRequests.at(-1).optimization) === JSON.stringify({enabled: true, max_candidates: 3, benchmark_repeats: 5, min_improvement_percent: 1}), "Source request uses the agreed optimization contract");
  check(taskRequests.at(-1).budget.max_iterations === 3 && taskRequests.at(-1).budget.candidates_per_iteration === 4, "Legacy budget remains separate and is not renamed as source optimization");

  let text = await renderResult({
    schema_version: "v2.source_optimization.v1",
    status: "unavailable",
    reason: "safe source boundary could not be determined",
    baseline_source_sha256: "base",
    best_source_sha256: null,
    accepted_trial_id: null,
    trials: [],
  }, "baseline_only");
  check(text.includes("保留 baseline") && text.includes("safe source boundary could not be determined"), "Unavailable optimization preserves baseline and shows the backend reason");
  check(text.includes("无法安全进入源码优化"), "Safety refusal is explicit");

  text = await renderResult({schema_version: "v2.source_optimization.v1", status: "failed", reason: "source planner failed", accepted_trial_id: null, trials: []});
  check(text.includes("失败") && text.includes("source planner failed") && text.includes("保留 baseline"), "Failed source optimization shows its reason and retains baseline");
  check((await page.locator("#apiResultsPanel > .compare-grid > .result-card").nth(1).locator(".metric-main").innerText()) === "1", "A lower summary row cannot replace baseline without accepted_trial_id");

  text = await renderResult({
    schema_version: "v2.source_optimization.v1",
    status: "completed",
    reason: "candidate failed correctness",
    baseline_source_sha256: "base",
    best_source_sha256: "base",
    accepted_trial_id: null,
    trials: [{
      trial_id: "failed-1",
      optimization_name: "unsafe candidate <img src=x onerror=alert(1)>",
      target_file: "kernel.py",
      target_function: "kernel",
      hypothesis: "<script>bad()</script>",
      evidence_ids: ["ev-1"],
      source_before_sha256: "base",
      source_after_sha256: "failed",
      status: "correctness_failed",
      decision_reason: "max error exceeded",
      diff: "<img src=x onerror=alert(1)>",
      correctness: {passed: false, max_error: 1, reason: "mismatch"},
      baseline_latency_ms: [1, 1.01],
      candidate_latency_ms: [],
      improvement_percent: null,
      rollback_verified: true,
    }],
  });
  await page.locator("#apiResultsPanel .source-trial > summary").click();
  const failedTrialText = await page.locator("#apiResultsPanel .source-trial").innerText();
  check(failedTrialText.includes("正确性失败") && failedTrialText.includes("未通过"), "Correctness failure is not shown as verified");
  check(failedTrialText.includes("source: benchmark/log") && failedTrialText.includes("confidence: medium"), "Evidence references preserve source and confidence");
  check(await page.locator("#apiResultsPanel img").count() === 0 && await page.locator("#apiResultsPanel script").count() === 0, "Diff and LLM text are HTML-escaped");

  text = await renderResult({
    schema_version: "v2.source_optimization.v1",
    status: "completed",
    reason: "no candidate met the threshold",
    baseline_source_sha256: "base",
    best_source_sha256: "base",
    accepted_trial_id: null,
    trials: [{trial_id: "no-gain", optimization_name: "coalescing attempt", status: "no_improvement", correctness: {passed: true, max_error: 0, reason: "ok"}, baseline_latency_ms: [1, 1], candidate_latency_ms: [1.01, 1.02], improvement_percent: -1.5, decision_reason: "below threshold", diff: "- old\n+ new", rollback_verified: true}],
  });
  check(text.includes("无收益") && text.includes("未接受源码修改"), "No-improvement trial keeps the baseline without a success claim");

  const acceptedSourceOptimization = {
    schema_version: "v2.source_optimization.v1",
    status: "completed",
    reason: null,
    baseline_source_sha256: baselineHash,
    best_source_sha256: acceptedHash,
    accepted_trial_id: "accepted-1",
    trials: [
      {trial_id: "rejected-1", optimization_name: "rejected layout", status: "no_improvement", correctness: {passed: true, max_error: 0, reason: "ok"}, baseline_latency_ms: [1], candidate_latency_ms: [1.02], improvement_percent: -2, decision_reason: "regressed", diff: "- a\n+ b", rollback_verified: true},
      {trial_id: "accepted-1", optimization_name: "accepted vector load", target_file: "kernel.py", target_function: "kernel", hypothesis: "reduce memory transactions", evidence_ids: ["ev-1"], source_before_sha256: baselineHash, source_after_sha256: acceptedHash, status: "accepted", decision_reason: "above threshold", diff: "- scalar\n+ vector", correctness: {passed: true, max_error: 0, reason: "ok"}, baseline_latency_ms: [1.2, 1, 1.1], candidate_latency_ms: [0.9, 0.8, 0.85], improvement_percent: 22.7, rollback_verified: null},
    ],
  };
  text = await renderResult(acceptedSourceOptimization);
  check(text.includes("已接受源码修改：accepted-1") && text.includes("22.7%"), "Accepted trial is the only source of an optimized result claim");
  const acceptedMetrics = await page.locator("#apiResultsPanel > .compare-grid > .result-card .metric-main").allInnerTexts();
  check(acceptedMetrics[0] === "1.1" && acceptedMetrics[1] === "0.85" && acceptedMetrics[2] === "22.7%", "Accepted summary uses only that trial's measurement arrays and improvement");
  check(await page.locator("#apiResultsPanel .source-trial[open]").count() === 1, "Only the accepted trial is expanded by default");
  check((await page.locator("#apiResultsPanel .source-trial[open]").innerText()).includes("accepted vector load"), "The accepted trial is the default expanded change");
  check(text.includes("回滚状态未返回") && !text.includes("回滚已验证\n"), "Null rollback state is not presented as rollback success");

  for (const partialStatus of ["running", "cancelled", "failed"]) {
    text = await renderResult({...acceptedSourceOptimization, status: partialStatus});
    check(text.includes("属于 partial 结果") && text.includes("未接受源码修改"), `${partialStatus} partial measurements cannot become an accepted result`);
    check((await page.locator("#downloadBestKernelBtn").isDisabled()), `${partialStatus} partial result cannot enable the best artifact download`);
  }

  text = await renderResult({...acceptedSourceOptimization, best_source_sha256: "c".repeat(64)});
  check(text.includes("发布源码 hash 与 accepted trial 不一致") && text.includes("未接受源码修改"), "Mismatched published source hash blocks a verified optimization claim");
  check((await page.locator("#apiResultsPanel .source-trial[open] > summary .badge").innerText()) === "接受状态待核验", "Mismatched source hash is not shown as a green accepted trial");
  check(await page.locator("#downloadBestKernelBtn").isDisabled(), "Mismatched published source hash blocks the best artifact download");

  text = await renderResult({...acceptedSourceOptimization, trials: acceptedSourceOptimization.trials.map((trial) => trial.trial_id === "accepted-1" ? {...trial, candidate_latency_ms: 0.85} : trial)});
  check(text.includes("测量数组或源码 hash 缺失") && text.includes("未接受源码修改"), "Scalar latency schema drift is not accepted as a measurement array");

  for (const invalidMeasurements of [[-1], [0.85, "bad"]]) {
    text = await renderResult({...acceptedSourceOptimization, trials: acceptedSourceOptimization.trials.map((trial) => trial.trial_id === "accepted-1" ? {...trial, candidate_latency_ms: invalidMeasurements} : trial)});
    check(text.includes("测量数组或源码 hash 缺失") && text.includes("未接受源码修改"), `Invalid measurement array ${JSON.stringify(invalidMeasurements)} is rejected in full`);
  }

  text = await renderResult({...acceptedSourceOptimization, schema_version: "future.invalid"});
  check(text.includes("schema_version 缺失或不匹配") && text.includes("未接受源码修改"), "Unknown source optimization schema cannot produce an accepted result");

  const baselineOnlySourceResult = {schema_version: "v2.source_optimization.v1", status: "completed", baseline_source_sha256: baselineHash, best_source_sha256: baselineHash, accepted_trial_id: null, trials: []};
  const verifiedBaselineRow = {trial_id: "baseline", status: "benchmark_ok", latency: 1, objective_value: 1, correctness: {passed: true}};
  text = await renderResult(baselineOnlySourceResult, "source_optimization", {summary_table: [verifiedBaselineRow], best_row: verifiedBaselineRow});
  check(!(await page.locator("#downloadBestKernelBtn").isDisabled()) && text.includes("最终 kernel（baseline）"), "Verified same-hash baseline remains downloadable when no trial is accepted");

  text = await renderResult({...baselineOnlySourceResult, best_source_sha256: acceptedHash}, "source_optimization", {summary_table: [{status: "benchmark_ok", latency: 1}], best_row: {status: "benchmark_ok", latency: 1}});
  check((await page.locator("#downloadBestKernelBtn").isDisabled()) && text.includes("baseline 产物不开放下载"), "Different-hash baseline without explicit correctness cannot enable an unrelated artifact download");

  text = await renderResult({
    schema_version: "wrong",
    status: "unexpected_status",
    reason: null,
    accepted_trial_id: "ghost",
    trials: [{trial_id: "ghost", status: "accepted", correctness: {passed: null}, baseline_latency_ms: null, candidate_latency_ms: "invalid", rollback_verified: null}],
  });
  check(text.includes("schema_version 缺失或不匹配") && text.includes("不会标记为已验证优化"), "Malformed source result is downgraded without inference");
  check(text.includes("未采集") && text.includes("回滚状态未返回"), "Missing arrays and nullable fields remain unavailable");

  text = await renderResult(null);
  check(text.includes("尚未返回 source_optimization") && text.includes("不会推断优化结果"), "Missing source result stays explicitly unavailable");

  await page.evaluate((taskValue) => {
    state.task = taskValue;
    state.activeTaskId = taskValue.task_id;
    state.taskEvents = taskValue.events;
    state.cancellationPending = false;
    el.cancelTaskBtn.disabled = false;
    renderTaskDetail();
    activateView("taskView");
  }, task("source-contract-cancel", "source_optimization"));
  await page.locator("#cancelTaskBtn").click();
  await page.waitForFunction(() => state.cancellationPending === true && document.getElementById("taskDetailPanel").innerText.includes("等待后端确认终态"));
  check((await page.locator("#taskDetailPanel").innerText()).includes("等待后端确认终态"), "Cancellation waits for a terminal API status");

  await renderResult({schema_version: "v2.source_optimization.v1", status: "cancelled", reason: "user requested cancellation", accepted_trial_id: null, trials: []});
  check((await page.locator("#apiResultsPanel").innerText()).includes("已取消"), "Cancelled source optimization uses the backend status");

  await renderResult(acceptedSourceOptimization);
  await page.evaluate(() => { state.warnings = []; renderAlerts(); });

  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    await page.evaluate(() => window.scrollTo(0, 0));
    check(!(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)), `Source optimization view has no horizontal overflow at ${name}`);
    await page.screenshot({path: `output/playwright/source-optimization-${name}.png`, fullPage: true});
  }

  return {passed: passed.length, checks: passed, contractOnly: true, hardwareEvidence: false, liveSourceOptimizationPending: true};
}
