// Takes unmodified backend result-file rows. This is artifact replay, not a live API/GPU run.
async (page, payload) => {
  if (!payload?.evidence_summary?.length || !payload?.profiler_collections?.length) throw new Error("Provide real backend artifact rows; no demo fallback is allowed");
  const checks = [];
  const check = (value, label) => { if (!value) throw new Error(label); checks.push(label); };
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.evaluate((results) => {
    stopTaskPolling(); state.task = null; state.taskResults = results;
    renderApiResults(); activateView("resultsView");
    const note = document.createElement("div");
    note.className = "alert warn";
    note.textContent = "真实 C500 校准产物回放（2026-10-30），不是实时任务 API 联调，也不是性能提速证据。";
    document.getElementById("profilerCollectionPanel").prepend(note);
  }, payload);
  check(await page.evaluate((expected) => JSON.stringify(state.taskResults) === JSON.stringify(expected), payload), "Backend artifact rows reach the renderer unchanged");
  const collected = payload.profiler_collections.filter((row) => row.status === "collected").length;
  let text = await page.locator("#evidenceTrustSummary").innerText();
  check(text.includes(`${collected} 条采集成功`) && text.includes("0 条可用于优化分析"), "Real collection success is independent from planning exclusion");
  check(text.includes("未确认已验证提速") && text.includes("0 条归因已验证"), "Real controls never establish a verified optimization result");
  check(await page.locator("#downloadBestKernelBtn").isDisabled() && await page.locator("#downloadReportBtn").isDisabled(), "Calibration-only artifacts cannot unlock best/report downloads");
  check((await page.locator("#profilerCollectionPanel").innerText()).includes("当前没有可信且可用于规划的指标"), "The no-trusted-metrics fallback is visible for the real controls");
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    const alignBelowHeader = (el) => window.scrollTo({top: el.getBoundingClientRect().top + scrollY - document.querySelector(".topbar").getBoundingClientRect().height - 12, behavior: "instant"});
    await page.locator("#profilerCollectionPanel").evaluate(alignBelowHeader);
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${name}: real artifact summary has no page overflow`);
    if (name === "desktop") await page.locator("#profilerCollectionPanel").screenshot({path: `output/playwright/real-calibration-blocked-${name}.png`});
    else await page.screenshot({path: `output/playwright/real-calibration-blocked-${name}.png`});
    await page.locator("#apiResultsAdvanced").evaluate((el) => { el.open = true; });
    text = await page.locator("#evidenceTrustTable").innerText();
    check(await page.locator("#evidenceTrustTable tbody tr").count() === payload.evidence_summary.length, `${name}: one row per real metric, without combining reports`);
    for (const record of payload.evidence_summary) {
      check(text.includes(String(record.value)) && text.includes(record.source_trial_id), `${name}: exact value retained for ${record.source_trial_id}`);
    }
    check(text.includes("校准或归因核验未通过") && text.includes("mcprofiler"), `${name}: real blocked validation and source remain visible`);
    await page.locator("#evidenceTrustTable").evaluate(alignBelowHeader);
    await page.screenshot({path: `output/playwright/real-calibration-blocked-${name}-details.png`});
    await page.locator("#apiResultsAdvanced").evaluate((el) => { el.open = false; });
  }
  check(errors.length === 0, "Real artifact replay has no JS errors");
  return {passed: checks.length, checks, artifactReplayOnly: true, liveApiValidation: false, realGpuValidation: false};
}
