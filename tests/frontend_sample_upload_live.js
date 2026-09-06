// Live same-origin smoke. No request interception or synthetic result injection.
// Start the FastAPI server, open its root page, then run this script with playwright-cli.
async (page) => {
  const passed = [];
  const consoleErrors = [];
  function check(condition, label) {
    if (!condition) throw new Error(label);
    passed.push(label);
  }

  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy === true);
  check((await page.locator("#apiHealthStatus").innerText()).includes("已连接"), "Same-origin API health is connected");

  await page.locator("#v2ProjectName").fill("frontend-live-upload-smoke");
  await page.locator("#v2SampleSourceType").selectOption("upload_directory");
  await page.evaluate(() => {
    const entries = [
      ["live-sample/kernel.py", "from lib.helper import VALUE\n"],
      ["live-sample/correctness.py", "from kernel import VALUE\nprint(f'CORRECTNESS_RESULT status=PASS max_error=0 reason=ok value={VALUE}')\n"],
      ["live-sample/benchmark.py", "from kernel import VALUE\nprint(f'BENCHMARK_RESULT latency_ms=1.25 tflops=2.5 bandwidth_gbps=3.75 value={VALUE}')\n"],
      ["live-sample/lib/__init__.py", ""],
      ["live-sample/lib/helper.py", "VALUE = 7\n"],
    ];
    const transfer = new DataTransfer();
    entries.forEach(([path, body]) => {
      const file = new File([body], path.split("/").pop(), {type: "text/x-python"});
      Object.defineProperty(file, "webkitRelativePath", {value: path});
      transfer.items.add(file);
    });
    const input = document.getElementById("v2SampleDirectory");
    Object.defineProperty(input, "files", {value: transfer.files, configurable: true});
    input.dispatchEvent(new Event("change", {bubbles: true}));
  });
  await page.waitForFunction(() => state.sampleUploadStage === "selected" && state.sampleFiles.length === 5);
  check(await page.locator("#v2EntryFile").inputValue() === "kernel.py", "Directory entry_file is selected from the real FileList");
  check((await page.locator("#sampleFileList").innerText()).includes("lib/helper.py"), "Nested dependency remains in the upload list");
  check(await page.locator("#startOptimizationBtn").isEnabled(), "Live task is valid before submission");

  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.activeTaskId && state.sampleUploadStage === "uploaded", null, {timeout: 30000});
  const taskId = await page.evaluate(() => state.activeTaskId);
  check(/^[0-9a-f-]+$/i.test(taskId), "Server returned a task_id after multipart upload");

  await page.waitForFunction(() => ["completed", "failed", "cancelled"].includes(state.task?.status), null, {timeout: 60000});
  const terminal = await page.evaluate(() => ({status: state.task.status, executionMode: state.task.execution_mode, totalTrials: state.task.total_trials}));
  check(terminal.status === "completed", "Real local baseline task completed");
  check(terminal.executionMode === "baseline_only", "Plain source is reported as baseline_only");
  check(terminal.totalTrials === 1, "baseline_only executes exactly one trial");

  await page.waitForFunction(() => state.taskResults && document.getElementById("apiResultsPanel").innerText.includes("仅基线测量，未执行源码优化"), null, {timeout: 30000});
  const resultsText = await page.locator("#apiResultsPanel").innerText();
  check(resultsText.includes("1.25"), "Real parsed baseline latency is rendered");
  check(resultsText.includes("Baseline") || resultsText.includes("基线"), "Baseline report and artifacts are rendered");
  check(!resultsText.includes("0.00%"), "baseline_only does not show a fabricated zero-percent improvement");

  const origin = await page.evaluate(() => location.origin);
  const taskResponse = await page.request.get(`${origin}/api/tasks/${encodeURIComponent(taskId)}`);
  const resultsResponse = await page.request.get(`${origin}/api/tasks/${encodeURIComponent(taskId)}/results`);
  check(taskResponse.ok() && resultsResponse.ok(), "Task and results endpoints return real JSON");
  const rawTask = await taskResponse.json();
  const rawResults = await resultsResponse.json();
  check(rawTask.task.execution_mode === "baseline_only", "Raw task JSON carries execution_mode");
  check(rawResults.results.improvement_percent === null, "Raw results JSON preserves null improvement");
  check(rawResults.results.report_markdown && rawResults.results.best_kernel, "Raw results include report and kernel artifacts");

  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    await page.evaluate(() => window.scrollTo(0, 0));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    check(!overflow, `Live results have no horizontal overflow at ${name}`);
    await page.screenshot({path: `output/playwright/sample-upload-live-${name}.png`, fullPage: true});
  }

  const [bestDownload] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#downloadBestKernelBtn").click(),
  ]);
  check(bestDownload.suggestedFilename() === "best_kernel.py", "Live best kernel download returns the expected filename");
  check(consoleErrors.length === 0, `Live browser console stays clean: ${consoleErrors.join(" | ")}`);

  return {
    passed: passed.length,
    checks: passed,
    taskId,
    backendResponses: {task: rawTask, results: rawResults},
    liveApi: true,
    hardwareEvidence: false,
  };
}
