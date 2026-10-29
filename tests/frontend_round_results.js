// main's source_optimization.trials contract; fixtures are not performance evidence.
async (page) => {
  const checks = [];
  const check = (condition, label) => { if (!condition) throw new Error(label); checks.push(label); };
  const baseHash = "a".repeat(64), bestHash = "b".repeat(64);
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/health" ? {ok: true, capabilities: {source_optimization: true}}
      : path === "/api/settings" ? {ok: true, settings: {ssh: {}, llm: {}}} : {ok: true, tasks: []};
    return route.fulfill({contentType: "application/json", body: JSON.stringify(body)});
  });
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy);
  const gate = {
    schema_version: "v2.performance_gate.v1", decision: "accepted", reason: "contract-only gate",
    baseline_recheck_passed: true, codegen_status: "changed",
    baseline_codegen_hash: "c".repeat(64), candidate_codegen_hash: "d".repeat(64),
    baseline_samples_ms: [1, 1.1, 1.2], candidate_samples_ms: [0.8, 0.85, 0.9],
    conservative_improvement_percent: 10, median_improvement_percent: 20,
  };
  const accepted = {
    trial_id: "accepted-trial", optimization_name: "parallel_copy_to_t_copy", target_file: "kernel.py", target_function: "copy",
    hypothesis: "Contract-only source-structure hypothesis", evidence_ids: ["pending-evidence"], status: "accepted",
    source_before_sha256: baseHash, source_after_sha256: bestHash, execution_source_sha256: bestHash,
    diff: "- elementwise copy\n+ T.copy\n", correctness: {passed: true, max_error: 0, reason: "contract correct"},
    baseline_latency_ms: [1, 1.1, 1.2], candidate_latency_ms: [0.8, 0.85, 0.9], improvement_percent: 20,
    decision_reason: "contract acceptance", rollback_verified: true,
    artifacts: {planner: {source: "llm", confidence: "medium"}, candidate_logs: {correctness: "contract/correctness.log"}, performance_gate: gate},
  };
  const pendingEvidence = {
    evidence_id: "pending-evidence", metric: "memory_bandwidth", value: 73, unit: "GB/s", source: "mcprofiler",
    confidence: "high", parse_confidence: "high", attribution_status: "unverified", attribution_confidence: "low",
    planning_eligible: false, eligibility_reasons: ["target_attribution_unverified"],
  };
  async function render(trials, options = {}) {
    await page.evaluate(({trials, options, baseHash, bestHash, pendingEvidence}) => {
      stopTaskPolling();
      state.task = {task_id: "round-contract", status: "completed", execution_mode: "source_optimization",
        source_best_verified: true, baseline_latency: 1.1, ...options.task};
      state.taskEvents = state.task.current_stage ? [{type: state.task.current_stage, message: "contract event"}] : [];
      const acceptedId = options.acceptedId === undefined ? "accepted-trial" : options.acceptedId;
      state.taskResults = {
        source_best_verified: true,
        summary_table: [{run_id: "contract-run", iteration: "0", candidate_id: "0", status: "benchmark_ok", latency: 1.1, correctness: {passed: true}}],
        best_kernel: "# contract fixture, not optimization evidence", report_markdown: "# Contract-only result",
        evidence_summary: [pendingEvidence], diagnoses: [], profiler_available: false,
        source_optimization: {schema_version: "v2.source_optimization.v1", status: "completed", baseline_verified: true,
          baseline_source_sha256: baseHash, best_source_sha256: acceptedId ? bestHash : baseHash,
          accepted_trial_id: acceptedId, trials, ...options.source}, ...options.results,
      };
      state.warnings = []; renderAlerts(); renderTaskDetail(); renderApiResults(); activateView("taskView");
    }, {trials, options, baseHash, bestHash, pendingEvidence});
  }
  for (const [stage, label] of [
    ["profiling_started", "采集性能"], ["profiler_parsed", "分析证据"], ["diagnosis_completed", "分析证据"],
    ["source_trial_started", "生成修改"], ["correctness_started", "正确性验证"], ["benchmark_started", "测速"],
    ["baseline_recheck_started", "复测原版"], ["source_trial_completed", "采纳或回滚"],
  ]) {
    await render([], {acceptedId: null, task: {status: "running", current_stage: stage}, source: {status: "running"}});
    check((await page.locator("#taskDetailPanel .metric-main").first().innerText()).includes(label), `${stage}: current stage follows the actual backend event`);
    check(await page.locator(".task-source-rounds").count() === 0, `${stage}: events do not manufacture candidate records`);
  }
  await render([{...accepted, status: "proposed", diff: "", source_after_sha256: null,
    correctness: {passed: null}, candidate_latency_ms: [], artifacts: {planner: {source: "llm"}, source_was_modified: false}}],
  {acceptedId: null, source: {status: "running"}, task: {status: "running", current_stage: "source_optimization_started"}});
  const proposal = page.locator("#taskDetailPanel .source-trial");
  await proposal.locator(":scope > summary").click();
  check((await proposal.innerText()).includes("LLM 建议") && (await proposal.innerText()).includes("未实际修改"), "LLM proposal alone is not an actual source modification");
  check((await proposal.locator('[data-round-part="validation"]').innerText()).includes("正确性待验证") && !(await proposal.innerText()).includes("性能改善已验证"), "A proposal does not imply correctness or performance success");
  check((await proposal.locator('[data-round-part="decision"]').innerText()).includes("等待"), "Running proposal has no final decision");
  await render([accepted], {acceptedId: null, source: {status: "running"}, task: {status: "running"}});
  await page.locator("#taskDetailPanel .source-trial > summary").click();
  check((await page.locator('#taskDetailPanel [data-round-part="decision"]').innerText()).includes("最终选择待确认"), "An accepted candidate during the loop is not prematurely rejected or published as final best");
  await render([{...accepted, status: "future_status"}], {acceptedId: null});
  await page.locator("#taskDetailPanel .source-trial > summary").click();
  check((await page.locator('#taskDetailPanel [data-round-part="decision"]').innerText()).includes("最终决定未返回"), "Unknown future statuses never fabricate a final rejection");

  const failed = {...accepted, trial_id: "failed-before-best", optimization_name: "rejected-change", status: "correctness_failed",
    correctness: {passed: false, max_error: 7, reason: "reference mismatch"}, candidate_latency_ms: [],
    artifacts: {planner: {source: "rule_based"}}, decision_reason: "reference mismatch"};
  await render([failed, accepted]);
  for (const panel of ["#taskDetailPanel", "#apiResultsPanel"]) {
    const best = page.locator(`${panel} .source-trial[open]`);
    check(await best.locator("[data-round-part]").count() === 5, `${panel}: each round has exactly five readable parts`);
    check((await best.innerText()).includes("LLM 建议") && (await best.innerText()).includes("实际已修改") && (await best.innerText()).includes("正确性通过") && (await best.innerText()).includes("性能改善已验证"), `${panel}: proposal, modification, correctness and verified speedup are distinct`);
    check((await best.locator('[data-round-part="decision"]').innerText()).includes("最终采纳") && !(await best.locator(":scope > summary").innerText()).includes("已回滚"), `${panel}: restoring the candidate workspace does not reverse final best publication`);
    const evidence = await best.locator('[data-round-part="evidence"]').innerText();
    check(evidence.includes("73 GB/s source: mcprofiler") && evidence.includes("high") && evidence.includes("待核验证据") && evidence.includes("仅供检查，未用于优化决策"), `${panel}: high parse confidence cannot verify target attribution`);
    check(await best.locator(".source-trial-details[open]").count() === 0 && await best.locator("pre").first().isHidden(), `${panel}: raw metrics, diff and logs default to collapsed`);
  }
  check(await page.locator("#fileTaskFeedback, #taskIntakeFeedback").count() === 0 && await page.locator("#downloadBestKernelBtn").isEnabled(), "Historical failure cannot override a fully verified final best");
  const best = page.locator("#taskDetailPanel .source-trial[open]");
  await best.locator(".source-trial-details > summary").click();
  await best.getByText("原始测量、哈希与日志", {exact: true}).click();
  check((await best.innerText()).includes("contract/correctness.log") && (await best.innerText()).includes("baseline_latency_ms"), "Actual fold controls reveal raw metrics and log paths");

  await render([accepted], {results: {source_best_verified: undefined}, task: {source_best_verified: undefined}});
  check(!(await page.locator('#taskDetailPanel [data-round-part="validation"]').innerText()).includes("性能改善已验证"), "Legacy accepted results do not acquire verified speedup by default");
  await render([accepted], {results: {source_best_verified: false}, task: {source_best_verified: false}});
  check((await page.locator('#taskDetailPanel [data-round-part="decision"]').innerText()).includes("待核验") && await page.locator("#downloadBestKernelBtn").isDisabled(), "Artifact verification failure overrides accepted records in both views");
  await render([{...accepted, artifacts: {...accepted.artifacts, performance_gate: {...gate, decision: "inconclusive", reason: "contract uncertainty"}}}]);
  check((await page.locator('#taskDetailPanel [data-round-part="validation"]').innerText()).includes("测量证据不足") && await page.locator("#downloadBestKernelBtn").isDisabled(), "Positive measured improvement cannot override an inconclusive gate");
  await render([{...failed, correctness: {passed: false, reason: "correctness_timeout: timed out after 1s"}}], {acceptedId: null});
  await page.locator("#taskDetailPanel .source-trial > summary").click();
  check((await page.locator('#taskDetailPanel [data-round-part="validation"]').innerText()).includes("正确性检查超时"), "Source trial timeout remains distinguishable under the unchanged schema");
  await render([{...accepted, hypothesis: '<img src=x onerror="alert(1)"> password=contract-secret'}], {
    results: {evidence_summary: [{...pendingEvidence, source: "synthetic"}]},
  });
  check((await page.locator('#taskDetailPanel [data-round-part="evidence"]').innerText()).includes("仅解析测试，不是性能证据"), "Synthetic evidence is explicitly non-performance data");
  check(await page.locator("#taskDetailPanel img").count() === 0 && !(await page.locator("#taskDetailPanel").innerText()).includes("contract-secret"), "Round summaries escape markup and redact accidental credentials");
  await render([]);
  check(await page.locator(".task-source-rounds").count() === 0, "Missing trials do not produce a placeholder round UI");
  await render([failed, accepted]);
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    for (const [view, target] of [["task", "taskView"], ["results", "resultsView"]]) {
      await page.locator(`.tab-button[data-view-target="${target}"]`).click();
      await page.evaluate(() => scrollTo(0, 0));
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${name} ${view}: no page-wide overflow`);
      check(await page.locator(`#${target} .source-trial > summary .badge`).evaluateAll((badges) => badges.every((badge) => {
        const box = badge.getBoundingClientRect(), frame = badge.closest(".source-trial").getBoundingClientRect();
        return box.width === 0 || box.left >= frame.left && box.right <= frame.right + 1;
      })), `${name} ${view}: long candidate names cannot clip status badges`);
      await page.screenshot({path: `output/playwright/round-results-${view}-${name}.png`, fullPage: true});
    }
  }
  return {passed: checks.length, checks, contractOnly: true, hardwareEvidence: false};
}
