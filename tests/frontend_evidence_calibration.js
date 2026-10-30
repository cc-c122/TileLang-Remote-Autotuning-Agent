// CONTRACT ONLY: #79 additive validation and main's optimization_rounds contract.
// Constructed positive records exercise rendering, not calibration or GPU performance.
// Do not take success/demo screenshots from these fixtures.
async (page) => {
  const checks = [];
  const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const errors = [];
  const onError = (error) => errors.push(error.message);
  page.on("pageerror", onError);
  await page.route("**/api/**", (route) => route.fulfill({contentType: "application/json", body: JSON.stringify(
    new URL(route.request().url()).pathname === "/api/health" ? {ok: true, capabilities: {source_optimization: true}}
      : {ok: true, settings: {ssh: {}, llm: {}}, tasks: []})}));
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy);
  const a = "a".repeat(64), b = "b".repeat(64);
  const metric = {
    evidence_id: "CONTRACT_ONLY-cited", metric: "mma_duty", value: 17, unit: "percent", source: "mcprofiler",
    source_trial_id: "source-baseline", source_field: "AP MMA Duty ratio", artifact_id: "CONTRACT_ONLY-report",
    available: true, confidence: "high", parse_confidence: "high", attribution_status: "verified",
    attribution_confidence: "high", scope: "kernel", kernel_name: "CONTRACT_ONLY-kernel", device_id: "CONTRACT_ONLY-device",
    tool_version: "CONTRACT_ONLY-tool", planning_eligible: true, eligibility_reasons: [],
    collection_scope: {source_sha256: a, shape_id: "CONTRACT_ONLY-shape", dtype: "float16"},
    attribution_validation: {schema_version: "v2.mcprofiler_metric_validation.v1", metric: "mma_duty", status: "verified", reason_codes: [],
      validation_id: "CONTRACT_ONLY-receipt", collection_id: "CONTRACT_ONLY-collection", definition: {
        numerator: "CONTRACT_ONLY-numerator", denominator: "CONTRACT_ONLY-denominator", unit: "percent", counter_scope: "kernel",
        tool_version: "CONTRACT_ONLY-tool", tool_build: "CONTRACT_ONLY-build", gpu_model: "CONTRACT_ONLY-GPU",
        reference_url: "https://example.invalid/contract-only", reference_sha256: "c".repeat(64),
      }},
  };
  const fixture = () => ({
    profiler_available: true, profiler_status: "profiler_metrics_available",
    evidence_summary: [structuredClone(metric), {...structuredClone(metric), evidence_id: "CONTRACT_ONLY-uncited"}],
    diagnoses: [],
    profiler_collections: [{schema_version: "v2.mcprofiler_collection.v1", status: "collected", mode: "remote_official_cli"}],
    source_optimization: {schema_version: "v2.source_optimization.v1", status: "completed", trials: []},
    optimization_rounds: [
      {schema_version: "v2.optimization_round.v1", round_index: 0, trial_id: "source-baseline", parent_source_sha256: a,
        evidence_ids: [], analysis: {}, plan: {}, validation: {}, acceptance: {decision: "baseline_verified"}, artifacts: {}},
      {schema_version: "v2.optimization_round.v1", round_index: 1, trial_id: "CONTRACT_ONLY-stop", parent_source_sha256: a,
        candidate_source_sha256: null, evidence_ids: [metric.evidence_id],
        analysis: {basis: "eligible_metrics_and_source", bottleneck_type: "insufficient_evidence", confidence: "low"},
        plan: {source: "llm", decision: "stop"}, validation: {}, acceptance: {decision: "stop", reason: "CONTRACT_ONLY stop"},
        artifacts: {evidence_binding: {device_id: "CONTRACT_ONLY-device", shape_id: "CONTRACT_ONLY-shape", dtype: "float16", kernel_names: ["CONTRACT_ONLY-kernel"]}, excluded_evidence: []}},
    ],
  });
  async function render(results) {
    await page.evaluate((results) => {
      stopTaskPolling(); state.task = {task_id: "CONTRACT_ONLY", project_name: "仅契约测试，不是真实结果", status: "completed", execution_mode: "source_optimization"};
      state.taskResults = results; state.taskEvents = []; state.warnings = [];
      renderAlerts(); renderTaskDetail(); renderApiResults(); activateView("resultsView");
    }, results);
  }
  const summary = () => page.locator("#evidenceTrustSummary").innerText();
  const roundEvidence = () => page.locator('#apiResultsPanel .optimization-round[data-round-index="1"] [data-round-part="evidence"]').textContent();
  async function details() {
    await page.locator("#apiResultsAdvanced").evaluate((el) => { el.open = true; });
    await page.locator("#evidenceTrustTable details").evaluateAll((rows) => rows.forEach((row) => { row.open = true; }));
    return page.locator("#evidenceTrustTable").innerText();
  }
  const actualUse = () => page.locator('#apiResultsPanel [data-round-part="evidence"] .badge.best').count();
  let data = fixture();
  await render(data);
  let text = await summary();
  for (const label of ["采集成功", "目标归因通过", "可用于规划", "性能改善通过"]) check(text.includes(label), `Four distinct states: ${label}`);
  check(text.includes("2 条可用于优化分析") && text.includes("未确认已验证提速"), "Eligible metrics cannot establish performance improvement");
  text = await details();
  check(text.includes("CONTRACT_ONLY-kernel") && text.includes("mcprofiler") && text.includes("17 percent"), "Each metric retains actual kernel, source, raw value and unit");
  check(text.includes("解析置信度") && text.includes("归因状态 / 置信度"), "Parsing and attribution confidence remain separate");
  for (const field of ["numerator", "denominator", "tool", "build", "GPU", "contract-only"]) check(text.includes("CONTRACT_ONLY-" + field) || text.includes("example.invalid/" + field), `Backend calibration definition: ${field}`);
  check(text.includes("未被这些轮次引用，不是决策依据"), "Eligible but unreferenced metrics are not a decision basis");
  text = await roundEvidence();
  check(text.includes("模型实际引用") && !text.includes("CONTRACT_ONLY-uncited"), "A stop round without a trial renders only its actual evidence_ids");
  check(await actualUse() === 1, "Only the explicitly cited, bound metric receives the model-reference badge");
  check((await page.locator("#apiResultsPanel .optimization-round").count()) === 2, "Baseline and analysis-only stop rounds both survive");

  for (const [label, mutate, expected] of [
    ["source", (x) => { x.evidence_summary[0].collection_scope.source_sha256 = b; }, "不属于本轮 parent 源码"],
    ["trial", (x) => { x.evidence_summary[0].source_trial_id = "wrong-trial"; }, "不属于本轮 parent trial"],
    ["device", (x) => { x.evidence_summary[0].device_id = "wrong-device"; }, "设备身份不匹配"],
    ["shape", (x) => { x.evidence_summary[0].collection_scope.shape_id = "wrong-shape"; }, "shape 不匹配"],
    ["dtype", (x) => { x.evidence_summary[0].collection_scope.dtype = "float32"; }, "dtype 不匹配"],
    ["kernel", (x) => { x.evidence_summary[0].kernel_name = "wrong-kernel"; }, "目标 kernel 不匹配"],
    ["excluded", (x) => { x.optimization_rounds[1].artifacts.excluded_evidence = [{evidence_id: metric.evidence_id, reasons: ["current_parent_source_or_trial_mismatch"]}]; }, "本轮已排除"],
    ["unknown schema", (x) => { x.optimization_rounds[1].schema_version = "future.round"; }, "轮次契约未识别"],
  ]) {
    data = fixture(); mutate(data); await render(data);
    check(await actualUse() === 0, `${label}: no verified reference badge`);
    check((await details()).includes(expected), `${label}: precise conflict reason survives`);
  }

  for (const field of ["planning_eligible", "parse_confidence", "confidence", "available", "artifact_id", "kernel_name", "attribution_status", "attribution_confidence", "attribution_validation", "eligibility_reasons"]) {
    data = fixture(); data.evidence_summary = [data.evidence_summary[0]]; delete data.evidence_summary[0][field]; await render(data);
    check(!(await summary()).includes("1 条可用于优化分析") && await actualUse() === 0, `Legacy missing ${field} never defaults to trusted`);
  }
  for (const reason of ["calibration_repeatability_failed", "idle_control_not_quiet", "native_artifact_parse_failed", "report_hash_mismatch", "validation_identity_mismatch", "backend_validation_unrecognized"]) {
    data = fixture(); data.evidence_summary = [data.evidence_summary[0]];
    Object.assign(data.evidence_summary[0], {planning_eligible: false, attribution_status: "unverified", attribution_confidence: "low", confidence: "low", eligibility_reasons: [reason]});
    Object.assign(data.evidence_summary[0].attribution_validation, {status: "blocked", reason_codes: [reason]});
    await render(data);
    check((await summary()).includes("1 条采集成功") && (await summary()).includes("0 条可用于优化分析"), `${reason}: collection success is not calibration success`);
    check((await details()).includes(await page.evaluate((reason) => evidenceReasonLabel(reason), reason)) && await actualUse() === 0, `${reason}: blocked reason is visible without promoting evidence`);
  }
  for (const [source, name] of [["synthetic", "mma_duty"], ["unknown", "mma_duty"], ["benchmark", "mma_duty"], ["compiler_log", "latency_ms"]]) {
    data = fixture(); data.evidence_summary = [data.evidence_summary[0]];
    Object.assign(data.evidence_summary[0], {source, metric: name}); await render(data);
    check(!(await summary()).includes("1 条可用于优化分析") && await actualUse() === 0, `${source}/${name}: incompatible provenance cannot become a decision basis`);
  }
  data = fixture(); data.evidence_summary.push(structuredClone(data.evidence_summary[0])); await render(data);
  check((await details()).includes("证据标识重复") && await actualUse() === 0, "Duplicate evidence IDs are ambiguous, never last-wins references");
  data = fixture(); data.evidence_summary[0].value = 0; await render(data);
  check((await details()).includes("0 percent"), "A real numeric zero is preserved, not treated as missing");
  data = fixture(); data.evidence_summary[0].value = null; data.evidence_summary[0].planning_eligible = false; await render(data);
  check((await details()).includes("null") && await actualUse() === 0, "Null stays unavailable, never zero or estimated");
  data = fixture(); data.optimization_rounds[1].plan.source = "rule_based"; await render(data);
  check((await roundEvidence()).includes("规则引用") && !(await roundEvidence()).includes("模型实际引用"), "Rule fallback cannot masquerade as model usage");
  data = fixture(); data.optimization_rounds[1].evidence_ids = []; data.optimization_rounds[1].analysis.basis = "source_and_logs_only";
  await render(data);
  check((await roundEvidence()).includes("源码、benchmark 与日志回退") && await actualUse() === 0, "Source/log-only round has no fabricated metric basis");
  data = fixture(); data.optimization_rounds[1].analysis.basis = "source_and_logs_only"; await render(data);
  check(await actualUse() === 0 && (await roundEvidence()).includes("与指标引用冲突"), "A source-only basis cannot simultaneously claim metric-driven planning");
  data = fixture(); data.optimization_rounds[1].evidence_ids.push(metric.evidence_id); await render(data);
  check(await actualUse() === 0 && (await roundEvidence()).includes("引用缺失或重复"), "Duplicate cited IDs are not counted as verified model use");
  data = fixture();
  data.source_optimization.trials = [{trial_id: "prior-accepted", status: "accepted", source_after_sha256: b}];
  data.optimization_rounds[1].parent_source_sha256 = b;
  data.evidence_summary[0].source_trial_id = "prior-accepted";
  data.evidence_summary[0].collection_scope.source_sha256 = b;
  await render(data);
  check((await roundEvidence()).includes("模型实际引用") && await actualUse() === 1, "A later round resolves the accepted parent trial instead of reusing original-baseline evidence");
  data.evidence_summary[0].source_trial_id = "source-baseline"; await render(data);
  check(await actualUse() === 0, "Stale original-baseline trial identity cannot authorize a later parent");
  data = fixture();
  data.evidence_summary = Array.from({length: 5}, (_, index) => ({...structuredClone(metric), evidence_id: `CONTRACT_ONLY-many-${index}`}));
  data.optimization_rounds[1].evidence_ids = data.evidence_summary.map((row) => row.evidence_id);
  await render(data);
  check((await roundEvidence()).includes("另有 2 条证据引用"), "Compact round evidence discloses truncation instead of silently dropping references");
  const complete = page.locator('#apiResultsPanel .optimization-round[data-round-index="1"] .source-trial-details');
  await page.locator('#apiResultsPanel .optimization-round[data-round-index="1"]').evaluate((el) => { el.open = true; });
  await complete.evaluate((el) => { el.open = true; });
  check((await complete.innerText()).includes("CONTRACT_ONLY-many-4"), "All actually cited IDs remain available in folded round details");
  data = fixture(); data.evidence_summary[0].attribution_validation = {schema_version: "future.validation", status: "verified"}; await render(data);
  check(await actualUse() === 0 && (await details()).includes("校准依据未返回或契约不完整"), "Unknown validation schema cannot authorize evidence");
  for (const [field, value] of [["metric", "other_metric"], ["unit", "other_unit"], ["tool_version", "other_version"]]) {
    data = fixture();
    if (field === "metric") data.evidence_summary[0].attribution_validation.metric = value;
    else data.evidence_summary[0].attribution_validation.definition[field] = value;
    await render(data);
    check(await actualUse() === 0, `Validation ${field} mismatch cannot promote a metric`);
  }
  data = fixture();
  data.evidence_summary[0].attribution_validation.reason_codes = ["password=CONTRACT_ONLY-secret <img src=x onerror=alert(1)>"];
  await render(data); text = await details();
  check(text.includes("[REDACTED]") && !text.includes("CONTRACT_ONLY-secret") && await page.locator("#evidenceTrustTable img").count() === 0, "New calibration fields retain credential redaction and HTML escaping");
  await render({});
  check((await page.locator("#profilerCollectionPanel").innerText()).includes("当前没有可信且可用于规划的指标") && await page.locator("#evidenceTrustTable").count() === 0, "Empty results have an honest fallback and no empty metric table");
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    data = fixture(); data.evidence_summary[0].planning_eligible = false;
    data.evidence_summary[0].attribution_validation.status = "blocked";
    await render(data); await page.setViewportSize({width, height}); await details();
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${name}: advanced evidence is scrollable without page overflow`);
    check(await page.locator("#evidenceTrustTable").evaluate((el) => el.parentElement.scrollWidth >= el.parentElement.clientWidth), `${name}: evidence uses the existing horizontal table container`);
  }
  check(errors.length === 0, `No browser JS errors: ${errors.join("; ")}`);
  page.off("pageerror", onError);
  return {passed: checks.length, checks, contractOnly: true, realGpuValidation: false, successScreenshots: false};
}
