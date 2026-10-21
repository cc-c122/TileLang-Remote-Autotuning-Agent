// Browser contract tests only, not real profiler measurements or optimization evidence.
async (page) => {
  const passed = [];
  function check(condition, label) {
    if (!condition) throw new Error(label);
    passed.push(label);
  }
  const baselineHash = "a".repeat(64);
  const bestHash = "b".repeat(64);
  const commandHash = "c".repeat(64);
  function collection(trialId, shape, value) {
    return {
      schema_version: "v2.mcprofiler_collection.v1", status: "collected", mode: "remote_official_cli",
      collection_id: `${trialId}-${shape}`, case_name: `${trialId}-${shape}`, source_trial_id: trialId,
      source_sha256: trialId === "source-baseline" ? baselineHash : bestHash, shape_id: shape,
      reason_category: "none", message: "Backend collection completed",
      metrics_requested: ["hbm_read_bandwidth", "warp_active_ratio", "boolean_only"],
      available_metrics: {hbm_read_bandwidth: true, warp_active_ratio: false, boolean_only: true},
      metric_observations: [
        {metric_name: "hbm_read_bandwidth", value, unit: "GB/s", source: "mcprofiler", available: true, confidence: "low"},
        {metric_name: "warp_active_ratio", value: null, unit: "%", source: "mcprofiler", available: false},
      ],
      kernel_names: ["contract_kernel"], warnings: ["Counter attribution is not verified"],
      primary_report: "report_dumped_result.json", case_dir: "raw/contract_case",
      artifact_manifest: [{relative_path: "report_dumped_result.json", source: "mcprofiler_auto_collection"}],
      execution_manifest: {tool: "mcProfiler", target_command_sha256: commandHash, returncode: 0, guard_denied: false},
      fallback: "benchmark_log",
    };
  }
  const results = {
    summary_table: [{trial_id: "source-baseline", latency: 1, status: "benchmark_ok"}],
    best_kernel: "# contract only", best_config: {}, report_markdown: "# Contract only",
    profiler_status: "profiler_metrics_available", profiler_available: true,
    evidence_summary: [], diagnoses: [],
    source_optimization: {
      schema_version: "v2.source_optimization.v1", status: "completed",
      baseline_source_sha256: baselineHash, best_source_sha256: bestHash, accepted_trial_id: "source-trial-1",
      trials: [{trial_id: "source-trial-1", status: "accepted", correctness: {passed: true},
        source_before_sha256: baselineHash, source_after_sha256: bestHash,
        baseline_latency_ms: [1, 1, 1], candidate_latency_ms: [0.9, 0.9, 0.9], improvement_percent: 10}],
    },
    profiler_collections: [collection("source-baseline", "context-128", 8), collection("source-trial-1", "context-128", 12)],
  };

  async function render(value) {
    return page.evaluate((payload) => {
      state.task = {task_id: "contract-task", status: "completed", execution_mode: "source_optimization"};
      state.taskResults = payload;
      renderApiResults();
      activateView("resultsView");
      return profilerEvidenceComparison(payload);
    }, value);
  }
  const copy = () => JSON.parse(JSON.stringify(results));

  let comparison = await render(results);
  check(comparison.pairs.length === 1 && comparison.pairs[0].metrics.filter((metric) => metric.comparable).length === 1, "Same shape, trial binding, source hash and command produce one explicit metric pair");
  let main = await page.locator("#profilerCollectionPanel").innerText();
  check(main.includes("已尝试执行工具") && main.includes("已采集") && main.includes("1 / 3"), "Tool execution, collection state and numeric availability are separate");
  check(main.includes("baseline") && main.includes("source-trial-1") && main.includes("1 个指标"), "Main region summarizes the verified before/after evidence");
  check(!main.includes("report_dumped_result.json") && !main.includes("hbm_read_bandwidth"), "Raw reports and detailed metric names stay out of the main summary");
  check(await page.locator("#apiResultsAdvanced").getAttribute("open") === null, "Advanced evidence is folded initially");
  await page.locator("#apiResultsAdvanced > summary").click();
  const detailText = await page.locator("#apiResultsAdvanced").innerText();
  check(detailText.includes("8 GB/s") && detailText.includes("12 GB/s") && detailText.includes("未采集"), "Advanced comparison shows only returned numbers and explicit missing values");
  check(detailText.includes("不推算收益"), "Metric comparison does not invent a performance benefit");

  let value = copy();
  value.profiler_collections.forEach((row) => {
    row.metrics_requested = ["Global Read Instructions"];
    row.available_metrics = {global_read_instructions: true};
    row.metric_observations = [{metric_name: "global_read_instructions", source_field_name: "Global Read Instructions", value: 851256,
      unit: "instructions", source: "mcprofiler", available: true, confidence: "low", parse_warnings: ["Attribution unverified"]}];
  });
  comparison = await render(value);
  check(comparison.pairs[0].metrics.length === 1 && comparison.pairs[0].metrics[0].comparable, "Native requested name maps to the canonical metric only through source_field_name");
  check((await page.locator("#profilerCollectionPanel").innerText()).includes("1 / 1"), "Native metric aliases do not inflate missing or requested counts");
  await page.locator("#apiResultsAdvanced > summary").click();
  await page.locator("#apiResultsAdvanced .source-trial > summary").first().click();
  const nativeRow = page.locator("#apiResultsAdvanced .source-trial table tbody tr").first();
  check((await nativeRow.innerText()).includes("Global Read Instructions") && (await nativeRow.innerText()).includes("851256") && (await nativeRow.innerText()).includes("low") && (await nativeRow.innerText()).includes("Attribution unverified"), "Native name, value, confidence and parse warning stay in one detail row");
  value.profiler_collections.forEach((row) => { delete row.metric_observations[0].source_field_name; });
  comparison = await render(value);
  check(comparison.pairs[0].metrics.length === 2 && comparison.pairs[0].metrics.filter((metric) => metric.comparable).length === 1, "Unmapped vendor names are never guessed from spelling");

  value = copy();
  value.profiler_collections[1].metric_observations[0].value = 0;
  comparison = await render(value);
  check(comparison.pairs[0].metrics[0].after.value === 0 && comparison.pairs[0].metrics[0].comparable, "Explicit numeric zero remains a real value");
  for (const invalid of [null, true, false, "12", "", {}]) {
    value = copy();
    value.profiler_collections[1].metric_observations[0].value = invalid;
    comparison = await render(value);
    check(!comparison.pairs[0].metrics[0].comparable && !comparison.pairs[0].metrics[0].after.hasValue, `Non-numeric or absent observation is not a measured value: ${JSON.stringify(invalid)}`);
  }
  value = copy();
  value.profiler_collections[1].available_metrics.hbm_read_bandwidth = false;
  comparison = await render(value);
  check(!comparison.pairs[0].metrics[0].after.hasValue, "Explicit unavailable flag overrides a stale numeric observation");
  value = copy();
  value.profiler_collections[1].metric_observations.push({...value.profiler_collections[1].metric_observations[0], value: 99});
  comparison = await render(value);
  check(!comparison.pairs[0].metrics[0].after.hasValue && comparison.pairs[0].metrics[0].after.ambiguous, "Duplicate observations cannot silently choose a kernel or sample");
  for (const [field, changed] of [["unit", "MB/s"], ["source", "benchmark/log"], ["unit", null], ["source", null]]) {
    value = copy();
    value.profiler_collections[1].metric_observations[0][field] = changed;
    comparison = await render(value);
    check(!comparison.pairs[0].metrics[0].comparable, `Mismatched or absent ${field} is not compared: ${changed}`);
  }
  value = copy();
  value.profiler_collections.forEach((row) => { row.metric_observations[0].source = "unknown"; });
  comparison = await render(value);
  check(!comparison.pairs[0].metrics[0].comparable, "Two unknown source labels are not treated as verified same-source evidence");
  for (const [field, changed] of [["shape_id", "context-512"], ["shape_id", null], ["source_trial_id", "other-trial"], ["source_sha256", "d".repeat(64)], ["kernel_names", ["other_kernel"]], ["mode", "report_import"], ["schema_version", "future.schema"]]) {
    value = copy();
    value.profiler_collections[1][field] = changed;
    comparison = await render(value);
    check(!comparison.pairs.length, `Incompatible collection binding is never joined: ${field}`);
  }
  value = copy();
  value.profiler_collections[1].execution_manifest.target_command_sha256 = "d".repeat(64);
  check(!(await render(value)).pairs.length, "Different target commands are not compared");
  value = copy();
  value.profiler_collections.push({...value.profiler_collections[1], collection_id: "retry"});
  check(!(await render(value)).pairs.length, "Repeated collection attempts are ambiguous, not auto-selected");
  value = copy();
  value.profiler_collections.push(collection("source-baseline", "context-512", 20), collection("source-trial-1", "context-512", 30));
  comparison = await render(value);
  check(comparison.pairs.length === 2 && comparison.pairs[1].metrics[0].before.value === 20 && comparison.pairs[0].metrics[0].after.value === 12, "Different shapes retain independent before/after evidence");

  value = copy();
  value.profiler_collections[1].metric_observations[0].source = "synthetic";
  check(!(await render(value)).pairs.length, "Synthetic records never enter performance evidence comparisons");
  check((await page.locator("#profilerCollectionPanel").innerText()).includes("仅解析测试，不是性能证据"), "Synthetic disclaimer is visible before opening advanced details");
  value = copy();
  value.source_best_verified = false;
  check(!(await render(value)).pairs.length && await page.locator("#downloadBestKernelBtn").isDisabled(), "Profiler evidence cannot bypass failed best artifact verification");

  for (const [category, message] of [["tool_unavailable", "远端未找到"], ["timeout", "采集超时"], ["guard_denied", "安全检查"], ["report_not_found", "未生成原始报告"], ["parse_failed", "报告解析失败"]]) {
    value = copy();
    value.profiler_available = false;
    value.profiler_status = "collection_failed";
    value.profiler_collections = [{...collection("source-baseline", "context-128", null), status: "failed", reason_category: category, message: "Backend failure reason", metric_observations: [], execution_manifest: {guard_denied: true, returncode: -1}}];
    await render(value);
    main = await page.locator("#profilerCollectionPanel").innerText();
    check(main.includes(message) && main.includes("置信度较低") && !main.includes("已尝试执行工具"), `${category} shows a real missing reason without inventing tool execution`);
  }
  value = copy();
  value.profiler_collections = [{...collection("source-baseline", "context-128", null), status: "imported"}];
  await render(value);
  check((await page.locator("#profilerCollectionPanel").innerText()).includes("仅导入报告，未检查工具"), "Imported report is not proof that a remote tool is installed");
  value = copy();
  value.profiler_collections[0].message = "token=contract-secret <img src=x onerror=alert(1)>";
  value.profiler_collections[0].reason_category = "tool_unavailable";
  await render(value);
  main = await page.locator("#profilerCollectionPanel").innerText();
  check(main.includes("token=[REDACTED]") && !main.includes("contract-secret") && await page.locator("#apiResultsPanel img").count() === 0, "Failure reasons remain redacted and HTML escaped");
  await render({});
  main = await page.locator("#profilerCollectionPanel").innerText();
  check(main.includes("等待后端采集记录") && main.includes("后端未返回采集状态"), "Legacy or pending results stay unknown rather than inferring success");
  check(await page.locator("#profilerCollectionPanel table").count() === 0 && await page.locator("#apiResultsAdvanced table").count() === 0, "Empty results do not create empty metric tables");

  await render(results);
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    await page.locator("#profilerCollectionPanel").scrollIntoViewIfNeeded();
    check(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${name} layout has no page-wide horizontal overflow`);
    check(await page.locator("#profilerCollectionPanel .table-wrap").evaluate((node) => node.scrollWidth > 0 && node.clientWidth > 0), `${name} collection table remains visible and scrollable`);
    await page.screenshot({path: `output/playwright/profiler-evidence-${name}.png`});
  }
  return {passed: passed.length, checks: passed, contractOnly: true, profilerEvidence: false};
}
