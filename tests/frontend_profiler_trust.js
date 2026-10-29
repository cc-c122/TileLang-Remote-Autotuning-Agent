// Contract rendering only, not a live GPU validation. The initial record is the
// sanitized backend #70 example at b34edce: docs/examples/mcprofiler_attribution_result.json.
async (page) => {
  const checks = [];
  function check(condition, label) {
    if (!condition) throw new Error(label);
    checks.push(label);
  }
  const record = {
    evidence_id: "ev_example_unverified", metric: "global_read_instructions", value: 851256, unit: "instructions",
    source: "mcprofiler", source_trial_id: null, source_field: "Global Read Instructions",
    artifact_id: "c500-native-profiler-smoke-20261021-final:45c6178421a6cd66", available: true, confidence: "low",
    parse_confidence: "high", attribution_status: "unverified", attribution_confidence: "low", scope: "unknown",
    kernel_name: "profiler_vector_axpby", device_id: null, tool_version: "3.9.0.29",
    metric_definition: {name: "Global Read Instructions", unit: "instructions", status: "unconfirmed", source: "report_field_mapping"},
    collection_scope: {report_layout: "named_kernel", counter_scope: "unknown", requested_kernel: null,
      configured_kernels: ["profiler_vector_axpby"], profile_from_start_actual: true, counts_actual: 100,
      background_workload_excluded: null},
    planning_eligible: false,
    eligibility_reasons: ["confidence_insufficient", "target_attribution_unverified", "kernel_scope_unconfirmed",
      "target_kernel_unconfirmed", "counter_scope_unknown", "background_workload_not_excluded", "device_binding_unknown",
      "metric_definition_unconfirmed", "metric_attribution_not_validated"],
    parse_warnings: ["mcProfiler target attribution is unverified; counters may include other device activity. Excluded from optimization planning."],
  };
  const contract = {evidence_summary: [record], diagnoses: [{bottleneck_type: "insufficient_evidence", confidence: "low",
    evidence_ids: [record.evidence_id], counter_evidence: [], uncertainty: ["Target-only counter attribution has not been established."],
    recommended_actions: ["Validate metric scope before using counters for optimization planning."]}]};
  const base = {...contract, profiler_available: true, profiler_status: "profiler_metrics_available",
    profiler_collections: [{schema_version: "v2.mcprofiler_collection.v1", status: "collected", mode: "remote_official_cli",
      source_trial_id: "contract-baseline", shape_id: "contract-shape", metric_observations: [{...record, metric_name: record.metric, source_field_name: record.source_field}]}]};
  const copy = () => JSON.parse(JSON.stringify(base));
  async function render(value) {
    await page.evaluate((payload) => {
      state.task = {task_id: "trust-contract-only", status: "completed", execution_mode: "source_optimization"};
      state.taskResults = payload;
      renderApiResults();
      renderTaskDetail();
      activateView("resultsView");
    }, value);
  }
  async function details() {
    await page.locator("#apiResultsAdvanced > summary").click();
    return page.locator("#evidenceTrustTable").innerText();
  }
  await render(base);
  let main = await page.locator("#evidenceTrustSummary").innerText();
  check(main.includes("1 条采集成功") && main.includes("1 条归因未验证"), "Collection success and unverified attribution coexist as distinct backend states");
  check(main.includes("0 条可用于优化分析") && main.includes("1 条被后端排除"), "Numeric observations do not create planning eligibility");
  check(main.includes("未确认已验证提速"), "Collection or profiler availability cannot imply verified speedup");
  check((await page.locator("#profilerCollectionPanel").innerText()).includes("仅供检查，未用于优化决策"), "Unverified evidence has a visible inspection-only disclaimer");
  check(!main.includes("851256"), "Raw values stay in advanced details");
  check(await page.locator("#apiResultsAdvanced").getAttribute("open") === null, "Trust details are folded initially");
  let text = await details();
  check(text.includes("851256") && text.includes("instructions") && text.includes("high") && text.includes("low"), "Raw values, parse confidence and attribution confidence remain separate");
  check(text.includes("未用于规划") && text.includes("设备绑定未确认") && text.includes("指标口径未确认"), "Planning exclusion and backend reasons are displayed in Chinese");

  let value = copy();
  value.evidence_summary[0].planning_eligible = true;
  await render(value);
  check((await page.locator("#evidenceTrustSummary").innerText()).includes("0 条可用于优化分析"), "An eligibility flag conflicting with attribution does not promote evidence");
  check((await details()).includes("资格与归因字段冲突"), "Contradictory backend metadata remains visible as a conflict");
  value = copy();
  Object.assign(value.evidence_summary[0], {source: "compiler_log", metric: "register_count", attribution_status: "verified",
    attribution_confidence: "medium", scope: "kernel", planning_eligible: true, eligibility_reasons: []});
  await render(value);
  check((await page.locator("#evidenceTrustSummary").innerText()).includes("1 条可用于优化分析"), "Explicit backend analysis eligibility is shown independently of profiler collection");
  check((await details()).includes("未返回规划使用记录"), "Eligibility alone is not a claim that a planner used the evidence");
  value.source_optimization = {schema_version: "v2.source_optimization.v1", status: "completed",
    trials: [{trial_id: "contract-trial", status: "no_improvement", evidence_ids: [record.evidence_id], rollback_verified: true}]};
  await render(value);
  check((await details()).includes("后端记录规划引用：contract-trial"), "Only an explicit trial evidence reference records planning use");
  value.evidence_summary[0].planning_eligible = false;
  value.evidence_summary[0].eligibility_reasons = ["confidence_insufficient"];
  await render(value);
  check((await details()).includes("规划引用与排除决定冲突"), "An excluded evidence reference is flagged rather than trusted");
  for (const source of ["synthetic", "unknown"]) {
    value = copy();
    Object.assign(value.evidence_summary[0], {source, attribution_status: "verified", attribution_confidence: "high",
      scope: "kernel", planning_eligible: true, eligibility_reasons: []});
    await render(value);
    main = await page.locator("#evidenceTrustSummary").innerText();
    check(main.includes("0 条归因已验证") && main.includes("0 条可用于优化分析"), `${source} cannot become trusted evidence by claiming verification`);
  }

  for (const field of ["planning_eligible", "parse_confidence", "attribution_status", "attribution_confidence", "eligibility_reasons"]) {
    value = copy();
    delete value.evidence_summary[0][field];
    await render(value);
    check(!(await page.locator("#evidenceTrustSummary").innerText()).includes("1 条可用于优化分析"), `Legacy missing ${field} is not promoted`);
  }
  value = copy();
  delete value.evidence_summary[0].planning_eligible;
  delete value.evidence_summary[0].eligibility_reasons;
  await render(value);
  text = await details();
  check(text.includes("分析资格未返回") && text.includes("后端未返回原因"), "Legacy records stay unknown, not silently excluded or authorized");
  value = copy();
  value.evidence_summary[0].value = 0;
  await render(value);
  check(await page.locator("#apiResultsAdvanced > summary").isVisible(), "Zero-valued evidence still renders");
  await details();
  check((await page.locator("#evidenceTrustTable tbody tr td").nth(1).innerText()).startsWith("0 "), "Real numeric zero is not replaced with an unavailable value");
  value = copy();
  value.evidence_summary[0].value = null;
  value.evidence_summary[0].eligibility_reasons = ["future_reason", "password=contract-secret <img src=x onerror=alert(1)>"];
  await render(value);
  text = await details();
  check(text.includes("future_reason") && text.includes("[REDACTED]") && !text.includes("contract-secret"), "Unknown reason codes are preserved and secrets are redacted");
  check(await page.locator("#evidenceTrustTable img").count() === 0, "Reason text remains HTML-escaped");

  for (const [status, label] of [["build_failed", "编译失败"], ["correctness_failed", "正确性失败"], ["no_improvement", "无明显改善"]]) {
    value = copy();
    value.source_optimization = {schema_version: "v2.source_optimization.v1", status: "completed", accepted_trial_id: null,
      trials: [{trial_id: "contract-trial", optimization_name: "contract-change", status, rollback_verified: true,
        correctness: {passed: status !== "correctness_failed"}, diff: "- old\n+ new", decision_reason: "Backend decision"}]};
    await render(value);
    const sourceTrial = page.locator("#apiResultsPanel > section .source-trial").first();
    check((await sourceTrial.innerText()).includes(label) && (await sourceTrial.innerText()).includes("已回滚"), `${status} is distinguished from completion and shows verified rollback`);
    await sourceTrial.locator(":scope > summary").click();
    await sourceTrial.locator(".source-trial-details > summary").click();
    check((await sourceTrial.innerText()).includes("- old") && (await sourceTrial.innerText()).includes("Backend decision"), `${status} retains the actual diff and decision reason`);
  }
  value = copy();
  value.source_optimization = {schema_version: "v2.source_optimization.v1", status: "unavailable", trials: []};
  await render(value);
  check((await page.locator("#apiResultsPanel").innerText()).includes("没有适用变换"), "No applicable transform is distinct from validation failures");
  await render({});
  check((await page.locator("#evidenceTrustSummary").innerText()).includes("归因状态未返回"), "Empty results retain an honest unknown state");
  await page.locator("#apiResultsAdvanced > summary").click();
  check(await page.locator("#evidenceTrustTable").count() === 0, "Missing evidence does not produce an empty table");

  await render(base);
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    await page.locator("#profilerCollectionPanel").scrollIntoViewIfNeeded();
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name} trust summary has no page-wide overflow`);
    await page.screenshot({path: `output/playwright/profiler-trust-${name}.png`});
  }
  return {passed: checks.length, checks, contractOnly: true, realGpuValidation: false};
}
