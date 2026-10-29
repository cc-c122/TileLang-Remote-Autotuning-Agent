// Real loopback API and CPU subprocesses. No routes, fake metrics, or GPU claims.
// Run from the repository root with the new HTML served against the intake API.
async (page) => {
  const fs = await import("node:fs/promises");
  const crypto = await import("node:crypto");
  const output = process.env.FRONTEND_LIVE_OUTPUT || "output/file-role-intake/final";
  await fs.mkdir(output, {recursive: true});
  const sampleDirectory = output + "/sample";
  await fs.mkdir(sampleDirectory, {recursive: true});
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const record = {scope: "real HTTP and CPU execution; not SSH/GPU or speedup evidence", tasks: [], checks: []};
  const check = (condition, label) => { if (!condition) throw new Error(label); record.checks.push(label); };
  const hash = (bytes) => crypto.createHash("sha256").update(bytes).digest("hex");
  const origin = new URL(page.url()).origin;
  if (!["127.0.0.1", "localhost", "[::1]"].includes(new URL(origin).hostname)) throw new Error("Live smoke requires a loopback API");
  record.origin = origin;
  record.frontend_html_sha256 = hash(await (await page.request.get(origin + "/")).body());
  const files = [];
  for (const name of ["kernel.py", "reference.py", "correctness.py", "benchmark.py"]) {
    const source = "tests/fixtures/file_intake_cpu/" + name;
    files.push({name, mimeType: "text/x-python", buffer: await fs.readFile(source)});
    await fs.copyFile(source, sampleDirectory + "/" + name);
  }
  const screenshot = async (name) => {
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({path: `${output}/${name}.png`, fullPage: true});
  };
  const showAdvanced = async () => {
    if (await page.locator("#advancedTaskSettings").getAttribute("open") === null) await page.locator("#advancedTaskSettings > summary").click();
  };
  const create = async (purpose, selectedFiles = files, roles = {}) => {
    await page.locator('.tab-button[data-view-target="startView"]').click();
    await page.locator("#v2ProjectName").fill("CPU-browser-" + purpose);
    if (selectedFiles) {
      await page.locator("#v2SampleSourceType").selectOption("upload_multiple");
      await page.locator("#v2SampleFilesFallback").setInputFiles(selectedFiles);
      await page.waitForFunction((count) => state.sampleUploadStage === "selected" && state.sampleFiles.length === count, selectedFiles.length);
    }
    for (const [selector, value] of Object.entries(roles)) await page.locator(selector).selectOption(value);
    check(await page.locator("#startOptimizationBtn").isEnabled(), "Valid input: " + purpose);
    const responseWait = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/tasks" && response.request().method() === "POST");
    await page.locator("#startOptimizationBtn").click();
    const response = await responseWait;
    const data = await response.json();
    const id = data.task_id || data.task?.task_id;
    check(response.ok() && !!id, "Real API created " + purpose);
    const request = response.request().postDataJSON();
    check(request.runner.type === "local" && request.optimization.enabled === true && !("commands" in request), "CPU override keeps automatic commands and source intent: " + purpose);
    check(!!request.test_files && !("ssh_session_id" in request), "Exact file contract and no credentials: " + purpose);
    check(await page.locator("#taskView").isVisible(), "Creation opens task detail: " + purpose);
    record.tasks.push({task_id: id, purpose, submitted_test_files: request.test_files});
    return id;
  };
  const finish = async (id, purpose) => {
    await page.waitForFunction(() => ["completed", "failed", "cancelled"].includes(state.task?.status), null, {timeout: 60000});
    await page.locator('.tab-button[data-view-target="resultsView"]').click();
    await page.locator("#refreshTaskResultsBtn").click();
    await page.waitForFunction(() => state.taskResults && document.getElementById("apiResultsStatus").textContent !== "读取中");
    const response = await page.request.get(`${origin}/api/tasks/${id}/results`);
    const data = await response.json();
    const events = (await (await page.request.get(`${origin}/api/tasks/${id}/events`)).json()).events;
    check(data.task.task_id === id, "Displayed result belongs to the created task: " + purpose);
    const result = record.tasks.find((row) => row.task_id === id);
    Object.assign(result, {status: data.task.status, execution_mode: data.task.execution_mode, preflight: data.results.preflight, events: events.map((event) => event.type), best_button_enabled: await page.locator("#downloadBestKernelBtn").isEnabled(), report_button_enabled: await page.locator("#downloadReportBtn").isEnabled()});
    await fs.writeFile(`${output}/${purpose}-api.json`, JSON.stringify(data, null, 2));
    await page.locator('.tab-button[data-view-target="taskView"]').click();
    const taskText = await page.locator("#taskDetailPanel").innerText();
    await page.locator('.tab-button[data-view-target="resultsView"]').click();
    await screenshot(purpose);
    await fs.writeFile(`${output}/acceptance.json`, JSON.stringify(record, null, 2));
    return {data, events, result, taskText, text: await page.locator("#apiResultsPanel").innerText()};
  };

  await page.reload();
  await page.waitForFunction(() => state.apiHealthy === true);
  check(await page.locator("#v2RunnerType").inputValue() === "ssh", "Fresh page defaults to remote SSH");
  check(await page.locator("#v2ExecutionIntent").inputValue() === "source_optimization", "Fresh page defaults to source optimization");
  await page.locator("#v2SampleDirectory").setInputFiles(sampleDirectory);
  await page.waitForFunction(() => state.sampleUploadStage === "selected");
  check(await page.locator(".sample-file-row").count() === 4, "Real directory selection lists every input file");
  check(await page.locator("#v2UploadEntrySelect").inputValue() === "kernel.py" && await page.locator("#v2CorrectnessFileSelect").inputValue() === "correctness.py" && await page.locator("#v2BenchmarkFileSelect").inputValue() === "benchmark.py", "Common file roles are preselected from actual files");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Remote task requires an actual session, not saved settings alone");
  for (const [width, height, name] of [[1440, 1000, "files-desktop"], [390, 844, "files-mobile"]]) {
    await page.setViewportSize({width, height});
    check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "File selection fits " + name);
    await screenshot(name);
  }
  await page.setViewportSize({width: 1440, height: 1000});
  await showAdvanced();
  await page.locator("#v2RunnerType").selectOption("local");
  await page.locator("#advancedTaskSettings > summary").click();
  check((await page.locator("#deviceIdentity").innerText()).includes("不作为真实 GPU 提速证据"), "Explicit CPU override explains its evidence boundary");

  const mainId = await create("directory-baseline", null);
  await screenshot("task-progress");
  const normal = await finish(mainId, "cpu-results-desktop");
  check(normal.data.task.status === "completed" && normal.data.results.preflight.status === "passed", "Real Python preflight and CPU baseline completed");
  check(normal.data.results.preflight.gpu_check === "local_demo_no_gpu_validation", "No GPU validation is claimed");
  check(normal.data.results.source_optimization.status === "unavailable" && normal.data.task.execution_mode === "baseline_only", "No eligible plain-Python transform remains baseline-only");
  check(normal.text.includes("没有适用优化"), "No applicable optimization is explained");
  check(await page.locator("#taskDetailPanel .task-source-rounds").count() === 0, "Real baseline-only response does not generate source-round placeholders");
  check(Number(normal.data.results.summary_table[0].latency) > 0, "Real measured CPU latency is returned");
  check(normal.events.findIndex((event) => event.type === "correctness_started") < normal.events.findIndex((event) => event.type === "benchmark_started"), "Correctness precedes real benchmark execution");
  normal.result.downloads = [];
  for (const [kind, selector] of [["best_kernel", "#downloadBestKernelBtn"], ["report", "#downloadReportBtn"]]) {
    check(await page.locator(selector).isEnabled(), "Verified CPU artifact download enabled: " + kind);
    const downloadWait = page.waitForEvent("download");
    await page.locator(selector).click();
    const download = await downloadWait;
    await download.saveAs(`${output}/${download.suggestedFilename()}`);
    const response = await page.request.get(download.url());
    const bytes = await response.body();
    check(response.ok() && hash(bytes) === hash(await fs.readFile(`${output}/${download.suggestedFilename()}`)), "Button download equals the actual API: " + kind);
    if (kind === "best_kernel") check(hash(bytes) === hash(files.find((file) => file.name === "kernel.py").buffer), "Downloaded baseline is the original uploaded operator");
    normal.result.downloads.push({kind, status: response.status(), sha256: hash(bytes)});
  }
  await page.setViewportSize({width: 390, height: 844});
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Actual CPU result fits mobile");
  await screenshot("cpu-results-mobile");
  await page.setViewportSize({width: 1440, height: 1000});

  const shared = [...files.filter((file) => ["kernel.py", "reference.py"].includes(file.name)), {name: "checks.py", mimeType: "text/x-python", buffer: Buffer.from(files.find((file) => file.name === "correctness.py").buffer.toString() + "\n" + files.find((file) => file.name === "benchmark.py").buffer.toString())}];
  const sharedId = await create("shared-tests", shared, {"#v2CorrectnessFileSelect": "checks.py", "#v2BenchmarkFileSelect": "checks.py"});
  const sharedResult = await finish(sharedId, "shared-tests");
  check(sharedResult.data.task.status === "completed", "A real script can serve both protected test roles");

  const failures = [
    {name: "missing-dependency", source: "import file_intake_intentionally_missing_module\nfrom kernel import run\nrun([1, 2])\n", label: "环境缺依赖"},
    {name: "incompatible-script", source: "from kernel import run\nprint(run([1, 2]))\nprint('PASS')\n", label: "测试脚本不兼容", status: "script_protocol_incompatible"},
    {name: "correctness-failed", source: "from kernel import run\nvalues=list(range(64))\nerror=max(abs(a-b) for a,b in zip(run(values), [v*3 for v in values]))\nstatus='PASS' if error==0 else 'FAIL'\nprint(f'CORRECTNESS_RESULT status={status} max_error={error} reason=\"deliberately incompatible reference\"')\n", label: "正确性失败"},
    {name: "correctness-fail-exit-one", source: "import sys\nfrom kernel import run\nvalues=list(range(64))\nerror=max(abs(a-b) for a,b in zip(run(values), [v*2+7 for v in values]))\nprint(f'CORRECTNESS_RESULT status=FAIL max_error={error} reason=\"reference mismatch\"')\nsys.exit(1)\n", label: "正确性失败", status: "correctness_failed", maxError: 7},
    {name: "correctness-timeout", source: "import time\nfrom kernel import run\nrun([1, 2])\ntime.sleep(4)\n", label: "正确性检查超时", status: "correctness_timeout", timeout: "1"},
  ];
  for (const failure of failures) {
    await page.locator('.tab-button[data-view-target="startView"]').click();
    await showAdvanced();
    await page.locator("#v2TimeoutSeconds").fill(failure.timeout || "60");
    await page.locator("#advancedTaskSettings > summary").click();
    const input = files.map((file) => file.name === "correctness.py" ? {...file, buffer: Buffer.from(failure.source)} : file);
    const id = await create(failure.name, input);
    const result = await finish(id, failure.name);
    check(result.text.includes(failure.label), "Actual failure has the right Chinese category: " + failure.name);
    check(result.taskText.includes(failure.label), "Task detail shows the same actual failure category: " + failure.name);
    check(!result.result.best_button_enabled, "Invalid result does not enable best download: " + failure.name);
    check(!result.events.some((event) => event.type === "benchmark_started"), "Invalid correctness/environment never reaches benchmark: " + failure.name);
    if (failure.status) {
      const baseline = result.data.results.summary_table.find((row) => row.iteration === "0" && row.candidate_id === "0");
      const failed = result.data.results.failed_cases.find((row) => row.run_id === baseline.run_id && row.iteration === 0 && row.candidate_id === 0 && row.config_hash === baseline.config_hash);
      check(baseline.status === failure.status && failed.error.category === failure.status, "Exact real baseline and error.category retained: " + failure.name);
      check(result.text.includes("正确性验证") && result.text.includes(failed.error.message), "Stage and original failure reason remain visible: " + failure.name);
      check(failed.correctness.parse_error === (failure.status === "script_protocol_incompatible"), "FAIL, timeout and invalid protocol remain distinct: " + failure.name);
      if (failure.maxError !== undefined) check(failed.correctness.max_error === failure.maxError && result.text.includes("max_error：7"), "FAIL with exit 1 preserves the actual max_error");
      check(result.result.report_button_enabled, "Failure report remains readable: " + failure.name);
      const downloadWait = page.waitForEvent("download");
      await page.locator("#downloadReportBtn").click();
      const download = await downloadWait;
      const reportPath = `${output}/${failure.name}-report.md`;
      await download.saveAs(reportPath);
      const response = await page.request.get(download.url());
      const bytes = await response.body();
      check(response.ok() && hash(bytes) === hash(await fs.readFile(reportPath)) && bytes.toString("utf8").replace(/\r\n/g, "\n") === result.data.results.report_markdown.replace(/\r\n/g, "\n"), "Failure report button downloads the actual API report: " + failure.name);
      await page.setViewportSize({width: 390, height: 844});
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Actual failure result fits mobile: " + failure.name);
      await screenshot(failure.name + "-mobile");
      await page.setViewportSize({width: 1440, height: 1000});
    }
  }

  await page.locator('.tab-button[data-view-target="startView"]').click();
  await showAdvanced();
  await page.locator("#v2TimeoutSeconds").fill("60");
  await page.locator("#advancedTaskSettings > summary").click();
  const slowFiles = files.map((file) => file.name === "benchmark.py" ? {...file, buffer: Buffer.from("import time\ntime.sleep(8)\n" + file.buffer.toString())} : file);
  const cancelId = await create("cancel-running", slowFiles);
  await page.waitForFunction(() => state.task?.status === "running" && state.task.current_stage === "benchmark_started", null, {timeout: 60000});
  check((await page.locator("#taskDetailPanel .metric-main").first().innerText()) === "测速", "Real running stage follows the backend benchmark_started event");
  await screenshot("cancel-running");
  const cancelWait = page.waitForResponse((response) => new URL(response.url()).pathname === `/api/tasks/${cancelId}/cancel`);
  await page.locator("#cancelTaskBtn").click();
  check((await cancelWait).ok(), "Visible cancel action uses the real endpoint");
  await screenshot("cancel-requested");
  const cancelled = await finish(cancelId, "cancelled-desktop");
  check(cancelled.data.task.status === "cancelled", "Real running CPU task reaches cancelled");
  check(cancelled.text.includes("已取消，验证未完成") && !cancelled.text.includes("基线验证未通过"), "Cancelled result is not called a correctness failure");
  check(!cancelled.result.best_button_enabled, "Cancelled unverified source is not presented as verified best");
  cancelled.result.download_status = {};
  for (const kind of ["best_kernel", "report"]) cancelled.result.download_status[kind] = (await page.request.get(`${origin}/api/tasks/${cancelId}/download/${kind}`)).status();
  await page.setViewportSize({width: 390, height: 844});
  check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), "Actual cancelled result fits mobile");
  await screenshot("cancelled-mobile");
  check(errors.length === 0, "No JavaScript page errors in live acceptance");
  record.page_errors = errors;
  record.ssh_gpu_validation = "not performed; session/device/security contracts tested separately, not counted as SSH or GPU evidence";
  record.performance_rejection_validation = "contract-only; this real CPU sample has no applicable TileLang transformation";
  await fs.writeFile(`${output}/acceptance.json`, JSON.stringify(record, null, 2));
  return {passed: record.checks.length, checks: record.checks, tasks: record.tasks, liveApi: true, hardwareEvidence: false, screenshotDirectory: output};
}
