// Contract-only fixtures for PR #72 target_reports. Not hardware evidence.
async (page) => {
  const checks = [];
  const check = (condition, label) => {
    if (!condition) throw new Error(label);
    checks.push(label);
  };
  await page.route("**/api/**", (route) => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify({ok: true, tasks: [], settings: {}})}));
  await page.reload();
  const hash = "a".repeat(64);
  const collections = ["baseline", "source-001"].map((trial) => {
    const reports = Array.from({length: 9}, (_, index) => ({
      report: `${index + 1}_contract_kernel_dumped_result.json`, native_report_index: index + 1,
      kernel_name: "contract_kernel", invocation_mapping: "unknown", sha256: hash,
      artifact_id: `${trial}:report-${index + 1}`, available_metrics: {counter_0: true},
    }));
    return {
      schema_version: "v2.mcprofiler_collection.v1", mode: "remote_official_cli",
      status: "collected", reason_category: "none", case_name: `${trial}-case`,
      source_trial_id: trial, shape_id: "contract-shape", primary_report: null, target_reports: reports,
      metrics_requested: ["counter_0"], available_metrics: {counter_0: false},
      execution_manifest: {report_scope: "named_kernel_reports"},
      metric_observations: reports.flatMap((report, index) => Array.from({length: 12}, (_, metric) => ({
        artifact: report.artifact_id, artifact_id: report.artifact_id, kernel_name: report.kernel_name,
        metric_name: `counter_${metric}`, value: index * 100 + metric, unit: "count", source: "mcprofiler",
        available: true, parse_confidence: "high", confidence: "low", attribution_status: "unverified",
        attribution_confidence: "low", planning_eligible: false, eligibility_reasons: ["attribution_unverified"],
        collection_scope: {report_sha256: hash, report_layout: "named_kernel"},
      }))),
    };
  });
  const evidence = collections.flatMap((collection) => collection.metric_observations.map((row, index) => ({
    ...row, metric: row.metric_name, evidence_id: `${collection.source_trial_id}:evidence-${index}`,
    source_trial_id: collection.source_trial_id,
  })));
  const results = {profiler_collections: collections, evidence_summary: evidence, diagnoses: [], profiler_available: true};
  const render = async (value) => page.evaluate((data) => {
    state.task = {task_id: "reports-contract", status: "completed", execution_mode: "source_optimization"};
    state.taskResults = data; renderApiResults(); activateView("resultsView");
  }, value);
  await render(results);
  check(await page.locator(".profiler-report").count() === 18, "Two Cases retain all eighteen independent reports");
  check(await page.locator("#evidenceTrustTable tbody tr").count() === 216, "All 216 evidence IDs remain distinct");
  check(await page.locator("#apiResultsAdvanced").getAttribute("open") === null, "Report details stay folded by default");
  check((await page.locator("#profilerCollectionPanel").innerText()).includes("9 份报告，逐份查看"), "Main summary counts reports instead of conflating repeated metrics");
  await page.locator("#apiResultsAdvanced > summary").click();
  const firstCase = page.locator(".source-trial").filter({has: page.locator(".profiler-report")}).first();
  await firstCase.locator(":scope > summary").click();
  let text = await firstCase.innerText();
  check(text.includes("不求和或平均") && text.includes("调用轮次映射未知"), "No aggregate counters or invented warmup/benchmark mapping");
  check(!text.includes("24 个指标没有显式数值"), "Repeated per-report metrics are not reported as missing Case metrics");
  await firstCase.locator(".profiler-report > summary").first().click();
  const metricCells = firstCase.locator(".profiler-report").first().locator("tbody tr").first().locator("td");
  check(await metricCells.nth(1).innerText() === "0", "A report-specific zero survives a contradictory Case-level availability flag");
  check(await firstCase.locator(".profiler-report").first().locator("tbody tr").count() === 12, "Metrics are scoped to one report, not merged across nine reports");
  const ids = await page.locator("#evidenceTrustTable tbody tr td:first-child").allTextContents();
  check(ids[0].includes("1_contract_kernel_dumped_result.json") && ids[12].includes("2_contract_kernel_dumped_result.json"), "Same-hash reports show their distinct artifact and report identities");
  check(ids[108].includes("source-001:report-1") && !ids[108].includes("baseline:report-1"), "Candidate evidence cannot acquire baseline report identity");
  check((await page.locator("#evidenceTrustTable").innerText()).includes("仅供检查，未用于优化决策"), "Numeric values do not relax attribution or planning gates");

  const group = async (value) => page.evaluate((record) => {
    const grouped = profilerReportGroups(record);
    return grouped && {invalid: grouped.invalid, unassigned: grouped.unassigned, matches: grouped.groups.map((row) => row.observations.length)};
  }, value);
  check(JSON.stringify((await group(collections[0])).matches) === JSON.stringify(Array(9).fill(12)), "Identical hashes do not collapse distinct artifact IDs");
  for (const field of ["artifact_id", "artifact", "kernel_name", "collection_scope"]) {
    const broken = structuredClone(collections[0]);
    broken.metric_observations[0][field] = field === "collection_scope" ? {report_sha256: "b".repeat(64)} : "different";
    const grouped = await group(broken);
    check(grouped.unassigned === 1 && grouped.matches[0] === 11, `${field} mismatch stays unassigned instead of joining the wrong report`);
  }
  for (const field of ["artifact_id", "report"]) {
    const broken = structuredClone(collections[0]);
    broken.target_reports[1][field] = broken.target_reports[0][field];
    const grouped = await group(broken);
    check(grouped.invalid && grouped.matches[0] === 0 && grouped.matches[1] === 0, `Duplicate ${field} cannot associate metrics`);
  }
  for (const invalid of [null, {}, [], [null], [{report: "../outside", artifact_id: "x", sha256: hash, kernel_name: "contract_kernel"}]]) {
    const grouped = await group({...collections[0], target_reports: invalid});
    check(grouped.invalid && grouped.unassigned === 108, "Missing or malformed named report list leaves observations unassigned");
  }
  const stale = {...collections[0], primary_report: {report: "representative.json"}, execution_manifest: {report_scope: "named_kernel", target_attribution: {status: "verified"}}};
  check(await page.evaluate((row) => profilerTargetReport(row), stale) === null, "Stale primary_report cannot override a multi-report list or enable comparison");
  check(await group({metric_observations: []}) === null, "Legacy single-report rendering remains supported");

  const brokenEvidence = structuredClone(results);
  brokenEvidence.evidence_summary[0].kernel_name = "other_kernel";
  await render(brokenEvidence);
  check((await page.locator("#evidenceTrustTable tbody tr td").first().textContent()).includes("报告身份未确认"), "Evidence kernel mismatch does not claim a report association");
  const unsafe = structuredClone(collections[0]);
  unsafe.target_reports[0].report = '<img src=x onerror=alert(1)>.json';
  unsafe.target_reports[0].kernel_name = "password=contract-secret";
  await render({...results, profiler_collections: [unsafe]});
  check(await page.locator("#apiResultsPanel img, #apiResultsPanel script").count() === 0, "Report metadata cannot inject HTML");
  check(!(await page.locator("#apiResultsPanel").textContent()).includes("contract-secret"), "Sensitive report metadata is redacted");
  await render(results);
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height: 900});
    await page.locator("#apiResultsAdvanced > summary").click();
    await page.locator(".source-trial").filter({has: page.locator(".profiler-report")}).first().locator(":scope > summary").click();
    await page.locator(".profiler-report > summary").first().click();
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Report detail keeps horizontal scrolling within tables at ${width}px`);
    await page.screenshot({path: `output/playwright/profiler-reports-contract-${width}.png`, fullPage: true});
    await render(results);
  }
  return {passed: checks.length, checks, contractOnly: true, profilerEvidence: false};
}
