// Read-only HTTP verification against the backend-provided receipt recovery service
// for completed C500 tasks. No API interception or fixture injection in this test.
// This is not live SSH/GPU validation, new task submission or live progress polling.
async (page) => {
  const checks = [];
  const writes = [];
  page.on("request", (request) => { if (request.method() !== "GET") writes.push(request.method()); });
  function check(condition, label) {
    if (!condition) throw new Error(label);
    checks.push(label);
  }
  await page.waitForFunction(() => state.apiHealthy && state.taskList.length);
  const taskIds = ["363df1fb4e9b442c9813738d8f516cca", "1a9eed7c98104ed38b78a942c268537b"];
  const snapshots = [];
  for (const [index, taskId] of taskIds.entries()) {
    await page.setViewportSize({width: 1440, height: 1000});
    const resultResponse = await page.request.get(`/api/tasks/${taskId}/results`);
    const eventResponse = await page.request.get(`/api/tasks/${taskId}/events`);
    check(resultResponse.ok() && eventResponse.ok(), `${taskId}: receipt-backed result and event endpoints respond`);
    const payload = await resultResponse.json();
    const events = await eventResponse.json();
    snapshots.push({taskId, payload, events});
    await page.locator('[data-view-target="taskView"]').first().click();
    await page.locator(`[data-task-id="${taskId}"]`).click();
    await page.waitForFunction((id) => state.task?.task_id === id && state.taskResults && document.getElementById("resultsView").classList.contains("active"), taskId);
    check(await page.evaluate((expected) => JSON.stringify(state.taskResults) === JSON.stringify(expected), payload.results), `${taskId}: UI receives the unchanged receipt-backed results payload`);
    check(await page.evaluate((expected) => JSON.stringify(state.taskEvents) === JSON.stringify(expected), events.events), `${taskId}: progress history matches the recorded API events, not live progress`);
    const source = payload.results.source_optimization;
    check(payload.task.status === "completed" && source.accepted_trial_id === null && source.trials.some((trial) => trial.status === "no_improvement" && trial.rollback_verified === true), `${taskId}: backend confirms no accepted candidate and verified rollback`);
    const main = await page.locator("#apiResultsPanel").innerText();
    const trust = await page.locator("#evidenceTrustSummary").innerText();
    check(main.includes("无明显改善") && main.includes("已回滚") && main.includes("保留 baseline"), `${taskId}: completion is not presented as optimization success`);
    check(trust.includes("归因未验证") && trust.includes("0 条可用于优化分析") && trust.includes("未确认已验证提速"), `${taskId}: collected unverified counters are excluded from analysis and speedup claims`);
    check(!(await page.evaluate(() => profilerEvidenceComparison(state.taskResults).pairs.length)), `${taskId}: unsupported profiler evidence is never paired`);
    check(payload.results.evidence_summary.every((row) => row.planning_eligible === false), `${taskId}: planning eligibility comes from backend exclusions`);
    const shape = index ? "ctx512" : "ctx128";
    const otherShape = index ? "ctx128" : "ctx512";
    const collectionText = await page.locator("#profilerCollectionPanel").innerText();
    check(collectionText.includes(shape) && !collectionText.includes(otherShape), `${taskId}: shape/trial data does not leak from the other task`);
    await page.locator("#profilerCollectionPanel").scrollIntoViewIfNeeded();
    await page.screenshot({path: `output/playwright/trust-real-${shape}-desktop.png`});
    await page.locator("#apiResultsAdvanced > summary").click();
    const evidenceRows = page.locator("#evidenceTrustTable tbody tr");
    check(await evidenceRows.count() === payload.results.evidence_summary.length, `${taskId}: all real evidence rows are retained`);
    const firstEvidence = payload.results.evidence_summary[0];
    check((await evidenceRows.first().innerText()).includes(String(firstEvidence.value))
      && (await evidenceRows.first().innerText()).includes(firstEvidence.parse_confidence)
      && (await evidenceRows.first().innerText()).includes(firstEvidence.attribution_confidence), `${taskId}: raw numbers and both confidence levels match the API`);
    check((await evidenceRows.first().innerText()).includes("未用于规划"), `${taskId}: unverified evidence is inspection-only`);
    await evidenceRows.first().evaluate((row) => row.scrollIntoView({block: "center"}));
    await page.screenshot({path: `output/playwright/trust-real-${shape}-advanced.png`});
    await page.locator("#apiResultsAdvanced > summary").click();
    await page.locator('[data-view-target="taskView"]').first().click();
    const detail = await page.locator("#taskDetailPanel").innerText();
    check(detail.includes(taskId) && detail.includes("无明显改善") && detail.includes("已回滚"), `${taskId}: task detail agrees with the final source result`);
    await page.screenshot({path: `output/playwright/trust-real-${shape}-task.png`});
    await page.locator('[data-view-target="resultsView"]').first().click();
    for (const [button, kind, expected] of [["downloadBestKernelBtn", "best_kernel", payload.results.best_kernel], ["downloadReportBtn", "report", payload.results.report_markdown]]) {
      check(!(await page.locator(`#${button}`).isDisabled()), `${taskId}: verified ${kind} download is enabled`);
      const downloaded = page.waitForEvent("download");
      await page.locator(`#${button}`).click();
      const download = await downloaded;
      check(new URL(download.url()).pathname === `/api/tasks/${taskId}/download/${kind}`, `${taskId}: download stays bound to the selected task`);
      const stream = await download.createReadStream();
      const chunks = [];
      for await (const chunk of stream) chunks.push(chunk);
      const bytes = Buffer.concat(chunks);
      // Python read_text normalizes CRLF in report_markdown; the download preserves original file bytes.
      check(kind === "report" ? bytes.toString("utf8").replace(/\r\n/g, "\n") === expected
        : bytes.equals(Buffer.from(expected, "utf8")), `${taskId}: downloaded ${kind} matches API content (report line endings normalized only)`);
      if (kind === "best_kernel") {
        const hash = await page.evaluate(async (data) => Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(data)))).map((value) => value.toString(16).padStart(2, "0")).join(""), Array.from(bytes));
        check(hash === source.baseline_source_sha256 && hash === source.best_source_sha256, `${taskId}: downloaded final kernel is the retained baseline, not the rejected candidate`);
      }
    }
    await page.setViewportSize({width: 390, height: 844});
    await page.locator("#profilerCollectionPanel").scrollIntoViewIfNeeded();
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${taskId}: real results fit the mobile viewport`);
    await page.screenshot({path: `output/playwright/trust-real-${shape}-mobile.png`});
    await page.locator("#apiResultsAdvanced > summary").click();
    await evidenceRows.first().evaluate((row) => row.scrollIntoView({block: "center"}));
    check(await page.locator("#evidenceTrustTable").evaluate((table) => table.parentElement.scrollWidth > table.parentElement.clientWidth)
      && await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${taskId}: metric details scroll inside their container on mobile`);
    check((await page.locator("#evidenceTrustTable tbody tr").first().boundingBox()).height < 260, `${taskId}: long eligibility reasons retain a readable column width`);
    await page.screenshot({path: `output/playwright/trust-real-${shape}-mobile-advanced.png`});
    await page.locator("#evidenceTrustTable").evaluate((table) => { table.parentElement.scrollLeft = table.parentElement.scrollWidth; });
    check(await page.locator("#evidenceTrustTable").evaluate((table) => table.parentElement.scrollLeft > 0), `${taskId}: mobile users can reach eligibility reasons by scrolling`);
    await page.screenshot({path: `output/playwright/trust-real-${shape}-mobile-reasons.png`});
  }
  check(writes.length === 0, "Browser only reads tasks and downloads artifacts; no POST/DELETE requests");
  return {passed: checks.length, checks, snapshots, realHttp: true, apiMode: "receipt_recovery_read_only",
    liveBackendValidation: false, realGpuValidation: false, newGpuExecution: false, observedRunningTask: false};
}
