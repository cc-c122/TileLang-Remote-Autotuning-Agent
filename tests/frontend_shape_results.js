// Open the frontend, then: playwright-cli run-code --filename tests/frontend_shape_results.js
async (page) => {
  const checks = await page.evaluate(() => {
    const passed = [];
    function check(condition, label) {
      if (!condition) throw new Error(label);
      passed.push(label);
    }
    const result = {
      summary_table: [{status: "benchmark_ok", latency: 1, trial_id: "context_128"}],
      best_row: {status: "benchmark_ok", latency: 1, trial_id: "context_128"},
      best_kernel: "# Browser contract fixture, not a hardware measurement",
      best_config: {BM: 16},
      report_markdown: "# Browser contract fixture",
      profiler_available: true,
      diagnoses: [{bottleneck_type: "task_level_only", confidence: "low"}],
      evidence_summary: [{metric_name: "context_128", value: 999}],
    };
    check(renderShapeResults(result) === "", "No shape inferred from task-level evidence or trial names");
    for (const value of [true, false, null, [], {}, -1, 0, 1.5, "", "1e3", "128.5"]) {
      check(normalizeContextLength(value) === "", `Reject invalid context ${JSON.stringify(value)}`);
    }
    check(renderShapeResults({shape_results: {context_length: 128}}) === "", "Require an array contract");
    check(normalizeContextLength(" 1024 ") === "1024", "Accept explicit integer context strings");
    result.shape_results = [
      {context_length: 128, provenance: "benchmark_log", latency_stats: {median_ms: 1}},
      {context_length: 512, provenance: "synthetic", profiler_available: true,
        correctness: {status: "PASS", max_error: 0}, latency_stats: {median_ms: 0.5},
        evidence: [{metric_name: "<img src=x onerror=alert(1)>", value: 0}]},
      {context_length: 2048, provenance: "real_profiler", profiler_available: true,
        correctness: {status: "FAIL", max_error: 0.25}, latency_stats: {median_ms: 2}},
      {context_length: 4096, provenance: "unknown", profiler_available: false},
    ];
    const container = document.createElement("div");
    container.innerHTML = renderShapeResults(result);
    const cards = [...container.querySelectorAll(".diagnosis-card")];
    check(cards.length === 4, "Render all and only explicit contexts, including non-default lengths");
    check(!cards[0].textContent.includes("PASS"), "Benchmark success does not imply correctness");
    check(!cards[0].querySelector("h3").textContent.includes("profiler 可用"), "No task-level profiler availability inheritance");
    check(cards[0].textContent.includes("未采集"), "Missing fields remain uncollected");
    check(cards[1].textContent.includes("synthetic：仅解析测试，不是性能证据"), "Synthetic warning is explicit");
    check(!cards[1].querySelector("h3 .ok"), "Synthetic profiler is never given a green availability badge");
    check(cards[2].textContent.includes("real_profiler") && !cards[2].textContent.includes("synthetic"), "Provenance is isolated per context");
    check(cards[2].textContent.includes("FAIL"), "Explicit correctness failures are preserved");
    check(cards[3].textContent.includes("profiler 不可用"), "Explicit profiler unavailability is preserved");
    check(!container.querySelector("img"), "Evidence text is HTML-escaped");
    const duplicate = renderShapeResults({shape_results: [
      {context_length: 128, correctness: {status: "PASS"}},
      {context_length: 128, correctness: {status: "FAIL"}},
    ]});
    check(duplicate.includes("返回多条记录") && !duplicate.includes("PASS") && !duplicate.includes("FAIL"), "Ambiguous duplicate contexts do not silently select a result");
    state.task = {task_id: "browser-contract-fixture", status: "completed"};
    state.taskResults = {...result, shape_results: undefined};
    renderApiResults();
    check(!el.apiResultsPanel.textContent.includes("按 context length"), "Generic tasks do not show Paged Attention placeholders");
    check(el.apiResultsPanel.textContent.includes("task_level_only"), "Existing global evidence remains visible without shapes");
    state.taskResults = result;
    renderApiResults();
    activateView("resultsView");
    return passed;
  });
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    await page.evaluate(() => window.scrollTo(0, 0));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    if (overflow) throw new Error(`Horizontal overflow at ${name}`);
    await page.screenshot({path: `output/playwright/pr55-${name}.png`, fullPage: true});
  }
  return {passed: checks.length, checks, screenshots: ["desktop", "mobile"]};
}
