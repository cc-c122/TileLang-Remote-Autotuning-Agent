// Contract fixture only. Open the same-origin frontend, then:
// playwright-cli run-code --filename tests/frontend_sample_upload.js
async (page) => {
  const passed = [];
  const uploads = [];
  const taskRequests = [];
  const downloads = [];
  let uploadFailure = null;
  let healthAvailable = true;
  let taskStatus = "running";
  let cancelRequested = false;

  function check(condition, label) {
    if (!condition) throw new Error(label);
    passed.push(label);
  }

  function json(route, body, status = 200, headers = {}) {
    return route.fulfill({
      status,
      contentType: "application/json",
      headers,
      body: JSON.stringify(body),
    });
  }

  function taskPayload(taskId = "fixture-task") {
    return {
      task_id: taskId,
      project_name: "browser-contract-fixture",
      status: taskStatus,
      execution_mode: "baseline_only",
      current_stage: taskStatus === "completed" ? "task_completed" : "benchmark_started",
      total_trials: taskStatus === "completed" ? 1 : 0,
      baseline_latency: taskStatus === "completed" ? 1.25 : null,
      best_latency: taskStatus === "completed" ? 1.25 : null,
      improvement_percent: taskStatus === "completed" ? 0 : null,
      cancel_requested: cancelRequested,
      events: [],
    };
  }

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = request.url().replace(/^https?:\/\/[^/]+/, "").split("?")[0];
    if (path === "/api/health") {
      return healthAvailable
        ? json(route, {ok: true, service: "fixture", mode: "contract"})
        : json(route, {detail: "fixture API offline"}, 503);
    }
    if (path === "/api/settings" && request.method() === "GET") {
      return json(route, {ok: true, settings: {
        ssh: {host: "127.0.0.1", port: 22, username: "", auth_type: "password", password_env: "KERNEL_AGENT_SSH_PASSWORD", password_env_exists: false, key_path: null, remote_workspace: "/tmp/kernel_opt_workspace"},
        llm: {provider: "openai_compatible", base_url: "https://api.openai.com/v1", model: "fixture", api_key_env: "OPENAI_API_KEY", api_key_env_exists: false},
      }});
    }
    if (path === "/api/tasks" && request.method() === "GET") return json(route, {ok: true, tasks: []});
    if (path === "/api/samples/upload" && request.method() === "POST") {
      const body = request.postDataBuffer()?.toString("utf8") || "";
      uploads.push(body);
      if (uploadFailure) return json(route, {detail: uploadFailure.message}, uploadFailure.status);
      const uploadId = `fixture-upload-${uploads.length}`;
      const entryMatch = body.match(/name="entry_file"\r\n\r\n([^\r]+)/);
      const entryFile = entryMatch?.[1] || "kernel.py";
      const filePaths = [...body.matchAll(/name="files"; filename="([^"]+)"/g)].map((match) => match[1]);
      return json(route, {ok: true, upload_id: uploadId, entry_file: entryFile, files: filePaths.map((item) => ({path: item, size_bytes: 1}))});
    }
    if (path === "/api/tasks" && request.method() === "POST") {
      taskRequests.push(request.postDataJSON());
      return json(route, {ok: true, task_id: `fixture-task-${taskRequests.length}`, task: taskPayload(`fixture-task-${taskRequests.length}`)});
    }
    const taskMatch = path.match(/^\/api\/tasks\/([^/]+)$/);
    if (taskMatch && request.method() === "GET") return json(route, {ok: true, task: taskPayload(taskMatch[1])});
    if (/\/events$/.test(path)) {
      return json(route, {ok: true, status: taskStatus, events: [{time: "fixture", type: taskStatus === "completed" ? "task_completed" : "benchmark_started", message: "fixture event"}]});
    }
    if (/\/results$/.test(path)) {
      return json(route, {ok: true, task: taskPayload(), results: {
        best_kernel: "# Baseline fixture, not hardware evidence",
        best_config: {},
        report_markdown: "# Fixture report",
        summary_table: [{status: "benchmark_ok", latency: 1.25, objective_value: 1.25}],
        best_row: {status: "benchmark_ok", latency: 1.25, objective_value: 1.25},
        improvement_percent: 0,
        failed_cases: [],
        evidence_summary: [],
        diagnoses: [],
        profiler_available: false,
      }});
    }
    if (/\/cancel$/.test(path) && request.method() === "POST") {
      cancelRequested = true;
      return json(route, {ok: true, task: taskPayload()});
    }
    const downloadMatch = path.match(/\/download\/(best_kernel|report)$/);
    if (downloadMatch) {
      downloads.push(downloadMatch[1]);
      const filename = downloadMatch[1] === "best_kernel" ? "best_kernel.py" : "report.md";
      return route.fulfill({status: 200, headers: {"Content-Type": "text/plain", "Content-Disposition": `attachment; filename="${filename}"`}, body: "fixture artifact"});
    }
    return json(route, {detail: `Unhandled fixture route: ${path}`}, 404);
  });

  async function resetPage() {
    taskStatus = "running";
    cancelRequested = false;
    uploadFailure = null;
    await page.reload();
    await page.waitForFunction(() => state.apiHealthy === true);
  }

  async function setRunnableCommands() {
    const details = page.locator("#startView details");
    if ((await details.getAttribute("open")) === null) await details.locator("summary").click();
    await page.locator("#v2CorrectnessCommand").fill("python -c \"print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')\"");
    await page.locator("#v2BenchmarkCommand").fill("python -c \"print('BENCHMARK_RESULT latency_ms=1.25')\"");
  }

  async function selectSingle(name = "kernel.py", body = "print('sample')") {
    await page.locator("#v2SampleSourceType").selectOption("upload_single");
    await page.evaluate(({name, body}) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([body], name, {type: "text/x-python"}));
      const input = document.getElementById("v2SampleFile");
      Object.defineProperty(input, "files", {value: transfer.files, configurable: true});
      input.dispatchEvent(new Event("change", {bubbles: true}));
    }, {name, body});
    await page.waitForFunction(() => state.sampleUploadStage === "selected");
  }

  async function selectDirectory() {
    await page.locator("#v2SampleSourceType").selectOption("upload_directory");
    await page.evaluate(() => {
      const entries = [
        ["sample/kernel.py", "print('kernel')"],
        ["sample/correctness.py", "print('CORRECTNESS_RESULT status=PASS max_error=0 reason=ok')"],
        ["sample/benchmark.py", "print('BENCHMARK_RESULT latency_ms=1.25')"],
        ["sample/lib/helper.py", "VALUE = 1"],
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
    await page.waitForFunction(() => state.sampleUploadStage === "selected" && state.sampleFiles.length === 4);
  }

  await resetPage();
  const safetyChecks = await page.evaluate(() => {
    const fake = (name, size = 1, relative = "") => ({name, size, webkitRelativePath: relative});
    const rejected = (files, mode) => {
      try {
        normalizeSelectedSampleFiles(files, mode);
        return false;
      } catch (error) {
        return true;
      }
    };
    return {
      traversal: rejected([fake("kernel.py", 1, "sample/../kernel.py")], "directory"),
      mixedSeparator: rejected([fake("kernel.py", 1, "sample\\kernel.py")], "directory"),
      controlCharacter: rejected([fake("kernel.py", 1, "sample/bad\u0001.py")], "directory"),
      windowsInvalidCharacter: rejected([fake("bad?.py")], "single"),
      sensitive: rejected([fake("id_rsa")], "single") && rejected([fake("token", 1, "sample/.ssh/token")], "directory"),
      duplicateCase: rejected([fake("Kernel.py"), fake("kernel.py")], "fallback"),
      fileDirectoryPrefix: rejected([fake("a", 1, "sample/a"), fake("kernel.py", 1, "sample/a/kernel.py")], "directory"),
      reverseFileDirectoryPrefix: rejected([fake("kernel.py", 1, "sample/A/kernel.py"), fake("a", 1, "sample/a")], "directory"),
      tooMany: rejected(Array.from({length: 101}, (_, index) => fake(`file-${index}.py`)), "fallback"),
      totalTooLarge: rejected(Array.from({length: 6}, (_, index) => fake(`file-${index}.py`, 2 * 1024 * 1024)), "fallback"),
      reservedName: rejected([fake("con.py")], "single"),
    };
  });
  Object.entries(safetyChecks).forEach(([name, value]) => check(value, `Client upload safety rejects ${name}`));
  const fallbackView = await page.evaluate(() => {
    const original = directoryPickerSupported;
    directoryPickerSupported = () => false;
    document.getElementById("v2SampleSourceType").value = "upload_directory";
    handleSampleSourceChange();
    const result = {
      fallbackVisible: !document.getElementById("fallbackSamplePicker").hidden,
      directoryHidden: document.getElementById("directorySamplePicker").hidden,
      note: document.getElementById("directorySupportHint").textContent,
    };
    directoryPickerSupported = original;
    document.getElementById("v2SampleSourceType").value = "inline";
    handleSampleSourceChange();
    return result;
  });
  check(fallbackView.fallbackVisible && fallbackView.directoryHidden && fallbackView.note.includes("回退"), "Unsupported directory browsers get a working multi-file fallback message");

  await page.locator("#v2SampleText").fill("print('inline sample')");
  await setRunnableCommands();
  check(await page.locator("#startOptimizationBtn").isEnabled(), "Inline source becomes runnable with explicit verification commands");
  check((await page.locator("#startValidationMessage").innerText()).includes("build、correctness 和 benchmark"), "Ready message follows the backend stage order");
  const emptyCommandTasksBefore = taskRequests.length;
  await page.locator("#v2BuildCommand").fill("");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Empty build command disables submission");
  check((await page.locator("#startValidationMessage").innerText()).includes("build 命令不能为空"), "Empty build command shows a Chinese error");
  await page.evaluate(() => startOptimization());
  check(taskRequests.length === emptyCommandTasksBefore, "Empty build command cannot bypass validation through JavaScript");
  await page.locator("#v2BuildCommand").fill("python -m py_compile kernel.py");
  await page.locator("#v2CorrectnessCommand").fill("");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Empty correctness command disables submission");
  check((await page.locator("#startValidationMessage").innerText()).includes("correctness 命令不能为空"), "Empty correctness command shows a Chinese error");
  await page.evaluate(() => startOptimization());
  check(taskRequests.length === emptyCommandTasksBefore, "Empty correctness command cannot bypass validation through JavaScript");
  await page.locator("#v2CorrectnessCommand").fill("python correctness.py");
  await page.locator("#v2BenchmarkCommand").fill("");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Empty benchmark command disables submission");
  check((await page.locator("#startValidationMessage").innerText()).includes("benchmark 命令不能为空"), "Empty benchmark command shows a Chinese error");
  await setRunnableCommands();
  const inlineTasksBefore = taskRequests.length;
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.activeTaskId && state.task);
  check(taskRequests.length === inlineTasksBefore + 1, "Inline flow creates exactly one task");
  check(taskRequests.at(-1).sample.source_type === "inline" && !Object.hasOwn(taskRequests.at(-1).sample, "path"), "Inline task uses the nested API schema without client paths");

  await resetPage();
  await selectSingle();
  await setRunnableCommands();
  check(await page.locator("#v2SampleText").inputValue() === "print('sample')", "Single-file upload reads content into the editor");
  check(await page.locator("#v2EntryFile").inputValue() === "kernel.py", "Single-file upload updates entry_file");
  const singleUploadsBefore = uploads.length;
  const singleTasksBefore = taskRequests.length;
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction((count) => state.activeTaskId && state.sampleUploadStage === "uploaded", singleTasksBefore);
  check(uploads.length === singleUploadsBefore + 1, "Single-file flow calls the real multipart upload endpoint once");
  check(taskRequests.length === singleTasksBefore + 1, "Single-file flow creates a task after upload");
  check(taskRequests.at(-1).sample.source_type === "upload" && /^fixture-upload-/.test(taskRequests.at(-1).sample.upload_id), "Task receives the server upload_id");
  check(!Object.hasOwn(taskRequests.at(-1).sample, "path") && !Object.hasOwn(taskRequests.at(-1).sample, "inline_text"), "Upload task sends neither fake path nor inline fallback");

  await resetPage();
  await selectDirectory();
  check((await page.locator("#sampleFileList").innerText()).includes("lib/helper.py"), "Directory selection preserves nested paths after stripping one root");
  check(!(await page.locator("#sampleFileList").innerText()).includes("sample/lib/helper.py"), "Common outer directory is stripped exactly once");
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    await page.evaluate(() => window.scrollTo(0, 0));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    check(!overflow, `Selected directory has no horizontal overflow at ${name}`);
    await page.screenshot({path: `output/playwright/sample-upload-selected-${name}.png`, fullPage: true});
  }
  await page.setViewportSize({width: 1440, height: 1000});
  const directoryUploadsBefore = uploads.length;
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.sampleUploadStage === "uploaded");
  check(uploads.length === directoryUploadsBefore + 1 && uploads.at(-1).includes('filename="lib/helper.py"'), "Directory multipart payload keeps nested POSIX filenames");

  await resetPage();
  await selectDirectory();
  const invalidUploadsBefore = uploads.length;
  await page.locator("#v2EntryFile").fill("missing.py");
  check(await page.locator("#startOptimizationBtn").isDisabled(), "Missing entry_file blocks task submission");
  check((await page.locator("#startValidationMessage").innerText()).includes("必须与上传文件列表"), "Missing entry_file shows a Chinese validation error");
  check(uploads.length === invalidUploadsBefore, "Invalid entry_file does not call upload API");

  await resetPage();
  await page.locator("#v2SampleSourceType").selectOption("upload_single");
  await page.evaluate(() => {
    const transfer = new DataTransfer();
    transfer.items.add(new File([new Uint8Array(2 * 1024 * 1024 + 1)], "large.py", {type: "text/x-python"}));
    const input = document.getElementById("v2SampleFile");
    Object.defineProperty(input, "files", {value: transfer.files, configurable: true});
    input.dispatchEvent(new Event("change", {bubbles: true}));
  });
  await page.waitForFunction(() => state.sampleUploadStage === "error");
  check((await page.locator("#sampleFileList").innerText()).includes("超过 2 MiB"), "Oversized files are rejected before network upload");

  await resetPage();
  await selectSingle();
  await setRunnableCommands();
  uploadFailure = {status: 413, message: "fixture total upload too large"};
  const failedTasksBefore = taskRequests.length;
  await page.locator("#startOptimizationBtn").click();
  await page.waitForFunction(() => state.sampleUploadStage === "error");
  check(taskRequests.length === failedTasksBefore, "Upload failure prevents task creation");
  check((await page.locator("#sampleFileList").innerText()).includes("fixture total upload too large"), "Backend upload errors are shown to the user");
  check(await page.locator("#startOptimizationBtn").isEnabled(), "Upload errors remain retryable");

  uploadFailure = null;
  const firstUploadId = await page.evaluate(() => uploadSelectedSample());
  const staleUploadsBefore = uploads.length;
  await page.locator("#v2EntryFile").fill("other.py");
  await page.locator("#v2EntryFile").fill("kernel.py");
  const secondUploadId = await page.evaluate(() => uploadSelectedSample());
  check(firstUploadId !== secondUploadId && uploads.length === staleUploadsBefore + 1, "Editing entry_file invalidates and replaces the old upload_id");

  const doubleTasksBefore = taskRequests.length;
  const expectedTaskId = `fixture-task-${doubleTasksBefore + 1}`;
  await page.evaluate(() => { startOptimization(); startOptimization(); });
  await page.waitForFunction((taskId) => (
    state.activeTaskId === taskId
    && state.task?.task_id === taskId
    && !state.taskSubmitting
    && document.getElementById("taskDetailPanel").textContent.includes("已尝试 trial")
  ), expectedTaskId);
  check(taskRequests.length === doubleTasksBefore + 1, "Double submission creates only one task");
  check((await page.locator("#taskDetailPanel").innerText()).includes("仅基线测量，未执行源码优化"), "baseline_only is presented as measurement, not source optimization");
  check((await page.locator("#taskDetailPanel").textContent()).includes("已尝试 trial"), "Task detail does not label failed attempts as verified trials");

  await page.locator("#cancelTaskBtn").click();
  await page.waitForFunction(() => state.cancellationPending === true && document.getElementById("taskDetailPanel").innerText.includes("等待后端确认终态"));
  check((await page.locator("#taskDetailPanel").innerText()).includes("等待后端确认终态"), "Cancel stays pending until the backend confirms a terminal state");

  taskStatus = "completed";
  cancelRequested = false;
  await page.evaluate(() => pollTask());
  await page.waitForFunction(() => state.task?.status === "completed" && state.taskResults);
  check((await page.locator("#apiResultsPanel").innerText()).includes("源码优化结果\n未执行"), "Completed baseline result suppresses a fictional optimized best");
  const downloadCount = downloads.length;
  await page.locator("#downloadBestKernelBtn").click();
  await page.waitForTimeout(100);
  check(downloads.length === downloadCount + 1 && downloads.at(-1) === "best_kernel", "Best artifact button calls the download API");

  healthAvailable = false;
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy === false && state.apiError);
  check((await page.locator("#apiHealthStatus").innerText()).includes("未启动"), "API outage is shown as a real error state");

  healthAvailable = true;
  await page.reload();
  await page.waitForFunction(() => state.apiHealthy === true);
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    await page.setViewportSize({width, height});
    await page.evaluate(() => window.scrollTo(0, 0));
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    check(!overflow, `No horizontal overflow at ${name}`);
    await page.screenshot({path: `output/playwright/sample-upload-fixture-${name}.png`, fullPage: true});
  }

  return {passed: passed.length, checks: passed, fixtureOnly: true};
}
