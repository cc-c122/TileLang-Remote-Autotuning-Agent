// #77 contract fixtures. TEST_DATA latency is synthetic, not GPU evidence.
async (page) => {
  const checks = [];
  const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  await page.route("**/api/**", (route) => route.fulfill({contentType: "application/json", body: JSON.stringify(
    new URL(route.request().url()).pathname === "/api/health" ? {ok: true, capabilities: {source_optimization: true}}
      : {ok: true, settings: {ssh: {}, llm: {}}, tasks: []})}));
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy);
  const a = "a".repeat(64), b = "b".repeat(64), c = "c".repeat(64);
  const trial = (id, before, after, parent, candidate) => ({
    trial_id: id, optimization_name: "parallel_copy_to_t_copy", target_file: "kernel.py", target_function: id,
    hypothesis: "TEST_DATA structural hypothesis", evidence_ids: [], status: "accepted",
    source_before_sha256: before, source_after_sha256: after, execution_source_sha256: after,
    diff: "- old copy\n+ T.copy\n", correctness: {passed: true, max_error: 0, reason: "reference checked"},
    baseline_latency_ms: [parent, parent, parent], candidate_latency_ms: [candidate, candidate, candidate],
    improvement_percent: (parent - candidate) / parent * 100, rollback_verified: true,
    artifacts: {planner: {source: "rule_based"}, performance_gate: {
      schema_version: "v2.performance_gate.v1", decision: "accepted", reason: "TEST_DATA gate accepted",
      codegen_status: "unavailable", baseline_codegen_hash: null, candidate_codegen_hash: null,
      baseline_recheck_passed: true, conservative_improvement_percent: (parent - candidate) / parent * 100,
      baseline_samples_ms: [parent, parent, parent], candidate_samples_ms: [candidate, candidate, candidate],
    }},
  });
  const first = trial("round-one", a, b, 10, 7), second = trial("round-two", b, c, 7, 4);
  const round = (trial, index) => ({
    schema_version: "v2.optimization_round.v1", round_index: index, trial_id: trial.trial_id,
    parent_source_sha256: trial.source_before_sha256, candidate_source_sha256: trial.source_after_sha256,
    evidence_ids: [], analysis: {bottleneck_type: "source_structure", confidence: "low", hypothesis: trial.hypothesis,
      expected_effect: "unmeasured copy change", risk: "may regress", validation_plan: ["fixed reference", "independent repeats"], basis: "source_and_logs_only"},
    plan: {decision: "attempt", source: "rule_based", template: trial.optimization_name}, diff: trial.diff,
    validation: {status: trial.status, correctness: trial.correctness, benchmark_samples_ms: trial.candidate_latency_ms,
      performance_gate: trial.artifacts.performance_gate, rollback_verified: trial.rollback_verified},
    acceptance: {decision: trial.status, reason: "TEST_DATA parent accepted", scope: "validated_parent_selection_not_final_publication"}, artifacts: {},
  });
  const fixture = () => ({
    task: {task_id: "TEST_DATA-multi-round", project_name: "TEST_DATA, not GPU performance", status: "completed", execution_mode: "source_optimization", source_best_verified: true, baseline_latency: 10, best_latency: 4, improvement_percent: 60},
    results: {source_best_verified: true, improvement_percent: 60, best_kernel: "# TEST_DATA kernel", report_markdown: "# TEST_DATA, not GPU evidence",
      summary_table: [{run_id: "contract", iteration: "0", candidate_id: "0", status: "benchmark_ok", latency: 10, correctness: {passed: true}}],
      source_optimization: {schema_version: "v2.source_optimization.v1", status: "completed", baseline_verified: true,
        baseline_source_sha256: a, best_source_sha256: c, accepted_trial_id: second.trial_id,
        baseline: {source_sha256: a, status: "benchmark_ok", correctness: {passed: true}, benchmark_samples_ms: [10, 10, 10]}, trials: [structuredClone(first), structuredClone(second)]},
      optimization_rounds: [round(first, 1), round(second, 2)], evidence_summary: [], diagnoses: [], profiler_available: false},
  });
  const render = async (data) => {
    await page.evaluate(({task, results}) => {
      stopTaskPolling(); state.task = task; state.taskResults = results; state.taskEvents = [];
      state.warnings = []; renderAlerts(); renderTaskDetail(); renderApiResults(); activateView("resultsView");
    }, data);
  };
  let data = fixture();
  await render(data);
  check(await page.evaluate(() => acceptedSourceTrialVerified(state.taskResults.source_optimization, state.taskResults, state.task)), "A fully verified two-parent chain is accepted");
  check(await page.evaluate(() => sourceHashVerification(state.taskResults.source_optimization)) === "verified", "Published hash is checked against the full parent chain");
  check((await page.locator("#apiResultsPanel > .compare-grid .metric-main").allInnerTexts()).join("|") === "10|4|60%", "Overview is original baseline -> final best, cumulative 60%, not last-round 42.857%");
  check((await page.locator("#taskDetailPanel").textContent()).includes("60%") && await page.locator("#downloadBestKernelBtn").isEnabled(), "Task detail agrees on cumulative gain and verified download is enabled");
  check(await page.locator("#fileTaskFeedback, #taskIntakeFeedback").count() === 0, "Earlier history cannot fabricate a failed final result");
  const firstCard = page.locator('#apiResultsPanel .source-trial').filter({hasText: "round-one"}).first();
  await firstCard.locator(":scope > summary").click();
  check((await firstCard.innerText()).includes("已包含在最终 best"), "Earlier accepted ancestor remains part of the final kernel");
  const lastCard = page.locator('#apiResultsPanel .source-trial').filter({hasText: "round-two"}).first();
  await lastCard.locator(".source-trial-details > summary").click();
  check((await lastCard.innerText()).includes("相对 parent") && (await lastCard.innerText()).includes("42.8571%"), "Per-round gain keeps its parent-relative label");
  for (const [label, mutate] of [
    ["broken parent hash", (x) => { x.results.source_optimization.trials[1].source_before_sha256 = a; }],
    ["failed ancestor correctness", (x) => { x.results.source_optimization.trials[0].correctness.passed = false; }],
    ["ancestor execution mismatch", (x) => { x.results.source_optimization.trials[0].execution_source_sha256 = c; }],
    ["parent measurements mismatch", (x) => { x.results.source_optimization.trials[1].baseline_latency_ms = [10, 10, 10]; }],
    ["gate measurements mismatch", (x) => { x.results.source_optimization.trials[0].artifacts.performance_gate.candidate_samples_ms = [8, 8, 8]; }],
    ["ancestor rejected gate", (x) => { x.results.source_optimization.trials[0].artifacts.performance_gate.decision = "inconclusive"; }],
    ["missing original measurements", (x) => { x.results.source_optimization.baseline.benchmark_samples_ms = []; }],
    ["duplicate accepted id", (x) => { x.results.source_optimization.trials.push(structuredClone(x.results.source_optimization.trials[1])); }],
    ["published hash mismatch", (x) => { x.results.source_optimization.best_source_sha256 = b; }],
    ["publication failed", (x) => { x.results.source_best_verified = false; }],
    ["cancelled", (x) => { x.task.status = "cancelled"; x.results.source_optimization.status = "cancelled"; x.results.source_best_verified = false; }],
  ]) {
    data = fixture(); mutate(data); await render(data);
    check(await page.locator("#downloadBestKernelBtn").isDisabled(), `${label}: chain/download gate remains closed`);
    check(!(await page.locator("#apiResultsPanel .metric-main").allInnerTexts()).includes("60%"), `${label}: invalid chain cannot claim cumulative success`);
  }
  data = fixture();
  const rejected = data.results.source_optimization.trials[1];
  rejected.status = "no_improvement"; rejected.artifacts.performance_gate.decision = "no_improvement";
  data.results.source_optimization.accepted_trial_id = first.trial_id; data.results.source_optimization.best_source_sha256 = b;
  data.results.optimization_rounds[1] = round(rejected, 2);
  data.results.improvement_percent = 30;
  await render(data);
  check((await page.locator("#apiResultsPanel > .compare-grid .metric-main").allInnerTexts()).join("|") === "10|7|30%" && await page.locator("#downloadBestKernelBtn").isEnabled(), "Acceptance then regression retains the last verified parent");
  for (const decision of ["stop", "insufficient_evidence"]) {
    data = fixture();
    const stop = {schema_version: "v2.optimization_round.v1", round_index: 3, trial_id: "analysis-only-" + decision,
      parent_source_sha256: c, candidate_source_sha256: null, evidence_ids: [], diff: "",
      analysis: {bottleneck_type: decision, confidence: "low", hypothesis: "No eligible evidence", expected_effect: "unknown effect", risk: "speculative hardware claim", validation_plan: ["obtain attributed evidence"], basis: "source_and_logs_only"},
      plan: {decision, source: "llm", target_id: null, template: null}, validation: {},
      acceptance: {decision, reason: "No safe next change", scope: "validated_parent_selection_not_final_publication"}, artifacts: {}};
    data.results.optimization_rounds.push(stop); await render(data);
    for (const panel of ["#taskDetailPanel", "#apiResultsPanel"]) {
      const card = page.locator(`${panel} .optimization-round[data-round-index="3"]`);
      await card.locator(":scope > summary").evaluate((el) => { el.parentElement.open = true; });
      const text = await card.textContent();
      check(text.includes("No safe next change") && text.includes("speculative hardware claim") && text.includes("obtain attributed evidence") && text.includes("unknown effect"), `${decision} ${panel}: analysis, confidence, risk, plan and stop reason survive without a candidate trial`);
      check(!text.includes("实际已修改") && text.includes("未执行"), `${decision} ${panel}: stop never becomes an executed source change`);
    }
    check(await page.locator("#downloadBestKernelBtn").isEnabled(), `${decision}: analysis-only stop does not invalidate an existing verified best`);
  }
  data = fixture(); delete data.results.optimization_rounds; delete data.results.source_optimization.baseline;
  data.results.source_optimization.trials = [first]; data.results.source_optimization.accepted_trial_id = first.trial_id; data.results.source_optimization.best_source_sha256 = b;
  await render(data);
  check(await page.locator("#downloadBestKernelBtn").isEnabled(), "Legacy single-round artifacts retain their prior integrity checks");
  data = fixture(); data.results.optimization_rounds[1].candidate_source_sha256 = a;
  await render(data);
  check((await page.locator("#apiResultsPanel").textContent()).includes("轮次与候选身份未能核对"), "A round cannot borrow another source version's validation");
  data = fixture(); data.results.optimization_rounds.push({schema_version: "future.round.v9", round_index: 3, trial_id: "future"});
  await render(data);
  check((await page.locator("#apiResultsPanel").textContent()).includes("未知轮次契约"), "Unknown schemas stay inspectable without trusted interpretation");
  data = fixture(); data.results.optimization_rounds[0].analysis.risk = '<img src=x onerror="alert(1)"> password=contract-only-secret';
  await render(data);
  const risk = await page.locator("#apiResultsPanel").textContent();
  check(!risk.includes("contract-only-secret") && await page.locator("#apiResultsPanel img").count() === 0, "New analysis fields retain HTML escaping and basic credential redaction");
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: 1000});
    for (const target of ["taskView", "resultsView"]) {
      await page.locator(`.tab-button[data-view-target="${target}"]`).click();
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${width}px ${target}: multiround view has no page overflow`);
    }
  }
  return {passed: checks.length, checks, contractOnly: true, hardwareEvidence: false};
}
