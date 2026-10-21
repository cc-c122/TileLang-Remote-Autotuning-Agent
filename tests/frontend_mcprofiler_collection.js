// Browser contract fixture only. It is not profiler or optimization evidence.
// Open the same-origin frontend, then run with playwright-cli --filename.
async (page) => {
  const passed = [];
  let mcProfilerCapability;

  function check(condition, label) {
    if (!condition) throw new Error(label);
    passed.push(label);
  }

  function json(route, body, status = 200) {
    return route.fulfill({status, contentType: "application/json", body: JSON.stringify(body)});
  }

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = request.url().replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    if (path === "/api/health") {
      const capabilities = {source_optimization: true};
      if (typeof mcProfilerCapability === "boolean") capabilities.mcprofiler_auto_collection = mcProfilerCapability;
      return json(route, {ok: true, service: "mcprofiler-contract-fixture", capabilities});
    }
    if (path === "/api/settings" && request.method() === "GET") {
      return json(route, {ok: true, settings: {
        ssh: {host: "127.0.0.1", port: 22, username: "fixture", auth_type: "password", password_env: "KERNEL_AGENT_SSH_PASSWORD", password_env_exists: true, remote_workspace: "/tmp/kernel_opt_workspace"},
        llm: {provider: "openai_compatible", base_url: "https://example.invalid/v1", model: "fixture", api_key_env: "OPENAI_API_KEY", api_key_env_exists: true},
      }});
    }
    if (path === "/api/tasks" && request.method() === "GET") return json(route, {ok: true, tasks: []});
    return json(route, {detail: `Unhandled fixture route: ${path}`}, 404);
  });

  async function reloadHealthy() {
    await page.reload();
    await page.waitForFunction(() => state.apiHealthy === true);
  }

  mcProfilerCapability = undefined;
  await reloadHealthy();
  check(await page.locator("#mcProfilerAutoCollectionField").isHidden(), "Missing capability keeps the mcProfiler control hidden");
  let request = await page.evaluate(() => buildApiTaskRequest());
  check(JSON.stringify(request.profiler) === JSON.stringify({enabled: true, type: "dummy"}), "Missing capability preserves the default dummy profiler request");

  mcProfilerCapability = true;
  await reloadHealthy();
  check(await page.locator("#mcProfilerAutoCollectionField").isHidden(), "Local runner does not expose remote mcProfiler collection");
  await page.evaluate(() => { el.v2McProfilerAutoCollect.checked = true; });
  request = await page.evaluate(() => buildApiTaskRequest());
  check(JSON.stringify(request.profiler) === JSON.stringify({enabled: true, type: "dummy"}), "Local runner cannot force auto collection through JavaScript");

  await page.locator("#v2RunnerType").selectOption("ssh");
  check(await page.locator("#mcProfilerAutoCollectionField").getAttribute("hidden") === null, "Capable remote SSH mode enables the optional collection control");
  const advancedSettings = page.locator("#mcProfilerAutoCollectionField").locator("xpath=ancestor::details");
  if ((await advancedSettings.getAttribute("open")) === null) await advancedSettings.locator("summary").click();
  check(await page.locator("#mcProfilerAutoCollectionField").isVisible(), "The optional collection control is usable after opening advanced settings");
  check(!(await page.locator("#v2McProfilerAutoCollect").isChecked()), "Remote auto collection remains opt-in");
  request = await page.evaluate(() => buildApiTaskRequest());
  check(JSON.stringify(request.profiler) === JSON.stringify({enabled: true, type: "dummy"}), "Unchecked remote collection preserves the dummy profiler request");
  await page.locator("#v2McProfilerAutoCollect").check();
  request = await page.evaluate(() => buildApiTaskRequest());
  check(JSON.stringify(request.profiler) === JSON.stringify({enabled: true, type: "mxmaca", auto_collect: true}), "Explicit remote opt-in sends only the agreed auto collection fields");
  check((await page.locator("#mcProfilerAutoCollectionHint").innerText()).includes("未安装或采集不可用") && (await page.locator("#mcProfilerAutoCollectionHint").innerText()).includes("benchmark + 日志"), "Collection hint explains the honest fallback");

  await page.locator("#v2RunnerType").selectOption("local");
  check(await page.locator("#mcProfilerAutoCollectionField").isHidden() && !(await page.locator("#v2McProfilerAutoCollect").isChecked()), "Leaving remote mode clears the collection opt-in");

  mcProfilerCapability = false;
  await reloadHealthy();
  await page.locator("#v2RunnerType").selectOption("ssh");
  check(await page.locator("#mcProfilerAutoCollectionField").isHidden(), "Explicit false capability does not advertise collection support");
  await page.evaluate(() => { el.v2McProfilerAutoCollect.checked = true; });
  request = await page.evaluate(() => buildApiTaskRequest());
  check(JSON.stringify(request.profiler) === JSON.stringify({enabled: true, type: "dummy"}), "False capability cannot be bypassed through JavaScript");

  const baselineHash = "a".repeat(64);
  const acceptedHash = "b".repeat(64);
  const sourceOptimization = {
    schema_version: "v2.source_optimization.v1",
    status: "completed",
    baseline_source_sha256: baselineHash,
    best_source_sha256: acceptedHash,
    accepted_trial_id: "accepted-1",
    trials: [{
      trial_id: "accepted-1",
      optimization_name: "historical accepted trial",
      status: "accepted",
      correctness: {passed: true, max_error: 0},
      source_before_sha256: baselineHash,
      source_after_sha256: acceptedHash,
      baseline_latency_ms: [1.2, 1.1, 1.0],
      candidate_latency_ms: [0.9, 0.8, 0.85],
      improvement_percent: 22.7,
      diff: "- old\n+ new",
    }],
  };
  const profilerCollections = [
    {
      schema_version: "v2.mcprofiler_collection.v1",
      status: "collected",
      reason_category: "none",
      message: "collection completed <img src=x onerror=alert(1)> password=fixture-secret",
      case_name: "ctx128",
      collection_id: "collection-1",
      source_trial_id: "accepted-1<script>bad()</script>",
      source_sha256: acceptedHash,
      shape_id: "context-128",
      metrics_requested: ["latency_ms", "warp_active_ratio", "boolean_only", "missing_metric"],
      available_metrics: {latency_ms: true, warp_active_ratio: true, boolean_only: true, missing_metric: false},
      metric_observations: [
        {metric_name: "latency_ms", value: 0.85, unit: "ms", source: "mcprofiler"},
        {metric_name: "warp_active_ratio", value: null, unit: "%", source: "mcprofiler"},
      ],
      artifact_manifest: [{relative_path: "case_report/<script>.csv", size_bytes: 42, sha256: "c".repeat(64), source: "mcprofiler_auto_collection"}],
      fallback: "benchmark_log",
      logs: {stdout: "logs/collection.stdout.log", stderr: "logs/collection.stderr.log"},
      execution_manifest: {target_command_sha256: "d".repeat(64), credentials_forwarded_to_vendor: false},
    },
    {schema_version: "v2.mcprofiler_collection.v1", status: "imported", case_name: "imported-case", metrics_requested: [], available_metrics: {}, fallback: "benchmark_log"},
    {schema_version: "v2.mcprofiler_collection.v1", status: "unsupported", case_name: "unsupported-case", reason_category: "tool_unavailable", message: "mcProfiler unavailable", fallback: "benchmark_log"},
    {schema_version: "v2.mcprofiler_collection.v1", status: "failed", case_name: "failed-case", reason_category: "parse_failed", message: "report parse failed", fallback: "benchmark_log"},
    {schema_version: "v2.mcprofiler_collection.v1", status: "cancelled", case_name: "cancelled-case", reason_category: "cancelled", message: "task cancelled", fallback: "benchmark_log"},
    {schema_version: "v2.mcprofiler_collection.v1", status: "future_success", case_name: "future-status", message: "unknown status", fallback: "benchmark_log"},
    {schema_version: "future.schema", status: "collected", case_name: "future-schema", message: "unknown schema", fallback: "benchmark_log"},
  ];
  const results = {
    summary_table: [{trial_id: "baseline", status: "benchmark_ok", latency: 1.1, objective_value: 1.1}],
    best_row: {trial_id: "accepted-1", status: "benchmark_ok", latency: 0.85, objective_value: 0.85},
    best_kernel: "# historical artifact",
    best_config: {},
    report_markdown: "# Fixture report",
    evidence_summary: [],
    diagnoses: [],
    profiler_available: true,
    profiler_collections: profilerCollections,
    source_optimization: sourceOptimization,
    source_best_verified: false,
    source_best_verification_error: "published artifact verification failed",
  };
  await page.evaluate((resultsValue) => {
    state.task = {task_id: "collection-result", project_name: "fixture", status: "completed", execution_mode: "source_optimization", source_best_verified: false};
    state.taskResults = resultsValue;
    renderApiResults();
    activateView("resultsView");
  }, results);
  const resultText = await page.locator("#apiResultsPanel").innerText();
  check(resultText.includes("mcProfiler 采集") && resultText.includes("7 条记录"), "Result view renders the backend collection array");
  const collectionSummaryClass = await page.locator("#apiResultsPanel section").filter({hasText: "mcProfiler 采集"}).locator(".section-head .badge").getAttribute("class");
  check(collectionSummaryClass.includes("warn"), "Mixed collection outcomes do not receive an all-success summary color");
  check(resultText.includes("0.85") && resultText.includes("latency_ms") && resultText.includes("mcprofiler"), "Only an explicit observation value is rendered as a metric");
  check(resultText.includes("可用标记，数值未返回") && resultText.includes("缺失") && resultText.includes("未返回数值"), "Boolean availability and missing metric values remain distinct");
  check(resultText.includes("采集完成不代表所有指标可用") && resultText.includes("benchmark + 日志分析"), "Collected status retains incomplete-metric and fallback caveats");
  check(resultText.includes("当前环境不支持") && resultText.includes("采集失败") && resultText.includes("已取消") && resultText.includes("已导入"), "Known collection statuses remain distinct");
  check(resultText.includes("未知状态：future_success") && resultText.includes("schema 未识别"), "Unknown status and schema are rendered conservatively");
  const unknownBadges = await page.locator("#apiResultsPanel .source-trial > summary .badge").all();
  check((await unknownBadges.at(-1).getAttribute("class")).includes("warn") && (await unknownBadges.at(-2).getAttribute("class")).includes("warn"), "Unknown schema and status never receive success styling");
  check(resultText.includes("password=[REDACTED]") && await page.locator("#apiResultsPanel img, #apiResultsPanel script").count() === 0, "Collection messages and artifact names are redacted and HTML-escaped");
  check(await page.locator("#downloadBestKernelBtn").isDisabled() && resultText.includes("当前 best 验证失败"), "Collected profiler data cannot bypass the source best verification gate");

  await page.evaluate((resultsValue) => {
    const withoutCollections = {...resultsValue};
    delete withoutCollections.profiler_collections;
    state.taskResults = withoutCollections;
    renderApiResults();
  }, results);
  check((await page.locator("#apiResultsPanel").innerText()).includes("本次任务未采集 mcProfiler；这不是任务错误"), "Missing collection array is a neutral uncollected state");

  return {passed: passed.length, checks: passed, contractOnly: true, profilerEvidence: false};
}
