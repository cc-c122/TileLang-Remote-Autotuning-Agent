// Real #77 HTTP + local subprocesses. Synthetic timing/DSL, NOT GPU or LLM evidence.
// Requires FEEDBACK_BACKEND_ROOT and an isolated API with no configured LLM credentials.
async (page) => {
  const fs = await import("node:fs/promises");
  const path = await import("node:path");
  const crypto = await import("node:crypto");
  const {execFileSync} = await import("node:child_process");
  const output = process.env.FEEDBACK_LIVE_OUTPUT || "output/playwright/multiround-live-final-20261029";
  const origin = new URL(page.url()).origin;
  if (!process.env.FEEDBACK_BACKEND_ROOT || new URL(origin).hostname !== "127.0.0.1") throw new Error("Isolated backend checkout and loopback API required");
  const python = `import json, tempfile\nfrom pathlib import Path\nfrom tests.test_file_task_intake import FileTaskTests\nfrom tests.test_optimization_rounds import two_copies\nwith tempfile.TemporaryDirectory() as temp:\n f=FileTaskTests(); f.sample=Path(temp)/'sample'; files=f.fixture(); files['ops/kernel.py']=two_copies(); print(json.dumps(files))`;
  const baseFiles = JSON.parse(execFileSync(process.env.PYTHON || "python", ["-B", "-c", python], {cwd: process.env.FEEDBACK_BACKEND_ROOT, encoding: "utf8"}));
  await fs.mkdir(output, {recursive: true});
  const hash = (value) => crypto.createHash("sha256").update(value).digest("hex");
  const receipt = {scope: "real HTTP/local execution; synthetic DSL and latency; rule fallback, NOT GPU speedup or real LLM inference", checks: [], tasks: [], page_errors: []};
  const check = (value, label) => { if (!value) throw new Error(label); receipt.checks.push(label); };
  page.on("pageerror", (error) => receipt.page_errors.push(error.message));
  receipt.html_sha256 = hash(await (await page.request.get(origin)).body());
  const advanced = async () => { if (await page.locator("#advancedTaskSettings").getAttribute("open") === null) await page.locator("#advancedTaskSettings > summary").click(); };
  const shot = async (name) => { await page.evaluate(() => scrollTo(0, 0)); await page.screenshot({path: `${output}/${name}.png`, fullPage: true}); };
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy);

  const create = async (purpose, cancel = false) => {
    const files = {...baseFiles};
    const latency = purpose === "accepted-then-regressed" ? "{0:10,1:7,2:8,3:9}" : "{0:10,1:7,2:4,3:3}";
    files["checks/benchmark check.py"] = files["checks/benchmark check.py"].replace("latency = 5 if changed else 10", `latency = ${latency}[Path('ops/kernel.py').read_text().count('TL.copy(')]`);
    if (cancel) files["checks/benchmark check.py"] = "import time\n" + files["checks/benchmark check.py"].replace("print('TEST_DATA:", "if changed: time.sleep(5)\nprint('TEST_DATA:");
    const folder = path.resolve(output, "samples", purpose);
    for (const [name, source] of Object.entries(files)) { await fs.mkdir(path.dirname(path.join(folder, name)), {recursive: true}); await fs.writeFile(path.join(folder, name), source); }
    await page.locator('.tab-button[data-view-target="startView"]').click();
    await page.locator("#v2ProjectName").fill("TEST_DATA-not-GPU-" + purpose);
    await page.locator("#v2SampleSourceType").selectOption("upload_directory");
    await page.locator("#v2SampleDirectory").setInputFiles(folder);
    await page.waitForFunction(() => state.sampleFiles.length === 6 && state.sampleUploadStage === "selected");
    await page.locator("#v2UploadEntrySelect").selectOption("ops/kernel.py");
    await page.locator("#v2CorrectnessFileSelect").selectOption("checks/correctness check.py");
    await page.locator("#v2BenchmarkFileSelect").selectOption("checks/benchmark check.py");
    await advanced();
    await page.locator("#v2RunnerType").selectOption("local");
    await page.locator("#v2OptimizationMaxCandidates").fill(purpose === "stop" ? "4" : "2");
    await page.locator("#v2BenchmarkRepeats").fill("3");
    await page.locator("#v2TimeoutSeconds").fill("15");
    await page.locator("#advancedTaskSettings > summary").click();
    const responseWait = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/tasks" && response.request().method() === "POST");
    await page.locator("#startOptimizationBtn").click();
    const response = await responseWait, body = await response.json();
    check(response.ok() && !!body.task_id, purpose + ": actual task created through normal Web file intake");
    check(!Object.hasOwn(response.request().postDataJSON(), "commands"), purpose + ": no user command override");
    check(await page.locator("#taskView").isVisible(), purpose + ": creation navigates to task detail");
    receipt.tasks.push({purpose, task_id: body.task_id});
    if (cancel) {
      await page.waitForFunction(() => state.task?.status === "running" && state.task.current_stage === "benchmark_started"
        && state.taskEvents.some((event) => event.type === "source_trial_started"), null, {timeout: 120000});
      await shot("candidate-running-before-cancel");
      await page.locator("#cancelTaskBtn").click();
    }
    await page.waitForFunction(() => ["completed", "failed", "cancelled"].includes(state.task?.status), null, {timeout: 120000});
    await page.locator('.tab-button[data-view-target="resultsView"]').click();
    await page.locator("#refreshTaskResultsBtn").click();
    await page.waitForFunction(() => state.taskResults && document.getElementById("apiResultsStatus").textContent !== "读取中");
    const data = await (await page.request.get(`${origin}/api/tasks/${body.task_id}/results`)).json();
    await fs.writeFile(`${output}/${purpose}-results.json`, JSON.stringify(data, null, 2));
    const source = data.results.source_optimization;
    Object.assign(receipt.tasks.at(-1), {status: data.task.status, source_best_verified: data.results.source_best_verified,
      trial_statuses: source.trials.map((row) => row.status), rounds: data.results.optimization_rounds.map((row) => ({index: row.round_index, decision: row.acceptance.decision})),
      cumulative_improvement: data.results.improvement_percent});
    return data;
  };
  const download = async (data, kind) => {
    const selector = kind === "best_kernel" ? "#downloadBestKernelBtn" : "#downloadReportBtn";
    check(await page.locator(selector).isEnabled(), data.task.task_id + ": " + kind + " download enabled");
    const pending = page.waitForEvent("download"); await page.locator(selector).click(); const file = await pending;
    const target = `${output}/${data.task.task_id}-${file.suggestedFilename()}`; await file.saveAs(target);
    const response = await page.request.get(file.url()), bytes = await response.body();
    check(response.ok() && hash(bytes) === hash(await fs.readFile(target)), data.task.task_id + ": " + kind + " button/API bytes match");
    if (kind === "best_kernel") check(hash(bytes) === data.results.source_optimization.best_source_sha256, "Published kernel matches the verified final chain hash");
  };

  const accepted = await create("two-accepted");
  check(accepted.results.source_optimization.trials.map((row) => row.status).join() === "accepted,accepted", "Two actual source candidates are accepted by the unchanged backend gate");
  check(accepted.results.source_best_verified === true && accepted.results.improvement_percent === 60, "Backend verifies original 10 -> 7 -> 4 chain, cumulative synthetic 60%");
  check((await page.locator("#apiResultsPanel > .compare-grid .metric-main").allInnerTexts()).join("|") === "10|4|60%", "Real API overview uses original baseline and cumulative gain");
  check(await page.evaluate(() => sourceHashVerification(state.taskResults.source_optimization)) === "verified", "Browser verifies the same real accepted parent chain");
  await page.locator('.tab-button[data-view-target="taskView"]').click();
  check((await page.locator("#taskDetailPanel").textContent()).includes("60%"), "Real task detail agrees with the result cumulative gain");
  await page.locator('.tab-button[data-view-target="resultsView"]').click();
  for (const kind of ["best_kernel", "report"]) await download(accepted, kind);
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    for (const [view, target] of [["task", "taskView"], ["results", "resultsView"]]) {
      await page.locator(`.tab-button[data-view-target="${target}"]`).click();
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), name + " " + view + ": no overflow");
      await shot("two-accepted-" + view + "-" + name);
    }
  }
  await page.setViewportSize({width: 1440, height: 1000});
  const regressed = await create("accepted-then-regressed");
  check(regressed.results.source_optimization.trials.map((row) => row.status).join() === "accepted,no_improvement", "Later regressed candidate does not replace the accepted parent");
  check((await page.locator("#apiResultsPanel > .compare-grid .metric-main").allInnerTexts()).join("|") === "10|7|30%", "Regression retains original baseline -> verified parent and cumulative 30%");
  await download(regressed, "best_kernel"); await shot("accepted-then-regressed");
  const stopped = await create("stop");
  const stop = stopped.results.optimization_rounds.find((row) => row.plan.decision === "stop");
  check(!!stop && !stop.candidate_source_sha256 && !stopped.results.source_optimization.trials.some((trial) => trial.trial_id === stop.trial_id), "True API retains a rule-based analysis-only stop without manufacturing a source trial");
  const card = page.locator(`#apiResultsPanel .optimization-round[data-round-index="${stop.round_index}"]`); await card.locator(":scope > summary").click();
  check((await card.innerText()).includes("未执行源码修改") && (await card.innerText()).includes(stop.analysis.risk), "Real stop analysis/risk/plan rendered without inventing a modification");
  await download(stopped, "best_kernel"); await download(stopped, "report"); await shot("stop-analysis-only");
  const cancelled = await create("cancel-candidate", true);
  check(cancelled.task.status === "cancelled" && await page.locator("#downloadBestKernelBtn").isDisabled(), "Actual candidate cancellation is not published as verified best");
  await download(cancelled, "report"); await shot("cancelled");
  check(receipt.page_errors.length === 0, "No JavaScript page errors in the real API run");
  receipt.contract_only = ["insufficient_evidence", "publication failure", "malformed chains"];
  receipt.integration_limit = "#77 local runner does not apply saved LLM settings; no real insufficient_evidence or LLM invocation is credited";
  await fs.writeFile(output + "/acceptance.json", JSON.stringify(receipt, null, 2));
  return {passed: receipt.checks.length, tasks: receipt.tasks, realHttp: true, syntheticTiming: true, realLlm: false, gpuEvidence: false};
}
