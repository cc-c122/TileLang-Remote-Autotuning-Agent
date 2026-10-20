// Real loopback FastAPI smoke. No request interception or embedded result data.
// This checks a refused SSH connection and a measured local CPU baseline;
// successful remote GPU execution must be verified separately with real credentials.
async (page) => {
  const checks = [];
  const check = (condition, label) => {
    if (!condition) throw new Error(label);
    checks.push(label);
  };
  const api = await page.evaluate(async () => {
    const response = await fetch('/api/health');
    return {status: response.status, body: await response.json()};
  });
  check(api.status === 200 && api.body.ok === true, 'Real FastAPI health endpoint is available');
  await page.waitForFunction(() => state.apiHealthy);
  await page.evaluate(() => activateView('settingsView'));
  await page.locator('#settingSshHost').fill('127.0.0.1');
  await page.locator('#settingSshPort').fill('1');
  await page.locator('#settingSshUsername').fill('connection-refused-smoke');
  await page.locator('#settingRemoteWorkspace').fill('/tmp/frontend-live-smoke');
  await page.locator('#settingSshPassword').fill('ephemeral-refusal-check');
  const sessionResponse = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/ssh/sessions');
  await page.locator('#connectSshBtn').click();
  const refused = await sessionResponse;
  check(refused.status() === 502 && (await refused.json()).detail.code === 'ssh_connection_failed', 'Real session endpoint reports the actual SSH connection failure');
  await page.waitForFunction(() => state.sshStatus === 'failed');
  check(await page.locator('#settingSshPassword').inputValue() === '', 'Password clears after a real failed connection');
  check((await page.locator('#sshConnectionMessage').innerText()).includes('连接失败'), 'Real connection failure is visible in Chinese');
  check(!(await page.locator('body').innerText()).includes('ephemeral-refusal-check'), 'Real error response exposes no password in the page');

  await page.evaluate(() => activateView('startView'));
  await page.locator('#v2ProjectName').fill('frontend-real-api-cpu-baseline');
  await page.locator('#v2SampleText').fill([
    'import sys, time, statistics',
    'def compute():',
    '    return sum(index * index for index in range(20000))',
    'if "--correctness" in sys.argv:',
    '    actual = compute()',
    '    expected = 19999 * 20000 * 39999 // 6',
    '    error = abs(actual - expected)',
    '    passed = error == 0',
    '    print(f"CORRECTNESS_RESULT status={\'PASS\' if passed else \'FAIL\'} max_error={error} reason=cpu_reference")',
    '    raise SystemExit(0 if passed else 1)',
    'elif "--benchmark" in sys.argv:',
    '    compute()',
    '    samples = []',
    '    for repeat in range(5):',
    '        start = time.perf_counter()',
    '        compute()',
    '        samples.append((time.perf_counter() - start) * 1000)',
    '    print(f"BENCHMARK_RESULT latency_ms={statistics.median(samples)}")',
  ].join('\n'));
  const commands = page.locator('#startView details');
  if (await commands.getAttribute('open') === null) await commands.locator('summary').click();
  await page.locator('#v2CorrectnessCommand').fill('python kernel.py --correctness');
  await page.locator('#v2BenchmarkCommand').fill('python kernel.py --benchmark');
  check(await page.locator('#startOptimizationBtn').isDisabled(), 'Real SSH failure leaves remote task submission blocked');
  await page.locator('#v2RunnerType').selectOption('local');
  await page.locator('#v2ExecutionIntent').selectOption('baseline_only');
  const taskResponse = page.waitForResponse((response) => new URL(response.url()).pathname === '/api/tasks' && response.request().method() === 'POST');
  await page.locator('#startOptimizationBtn').click();
  const created = await taskResponse;
  check(created.status() === 200, 'Task is created by the real task endpoint');
  await page.waitForFunction(() => state.task && ['completed', 'failed', 'cancelled'].includes(state.task.status), {timeout: 120000});
  check(await page.evaluate(() => state.task.status) === 'completed', 'Measured local CPU baseline completes on the real backend');
  await page.waitForFunction(() => state.taskResults && !document.getElementById('downloadBestKernelBtn').disabled, {timeout: 30000});
  const evidence = await page.evaluate(() => ({
    taskId: state.activeTaskId,
    baseline: state.taskResults.summary_table?.find((row) => row.status === 'benchmark_ok'),
    report: !!state.taskResults.report_markdown,
    kernel: !!state.taskResults.best_kernel,
    sshConnected: sessionIsActive(),
    gpuIdentified: !!currentRemoteDevice(),
    events: state.taskEvents.map((event) => event.type),
  }));
  check(Number(evidence.baseline?.latency) > 0 && evidence.report && evidence.kernel, 'Results contain actual measured latency, best kernel and report');
  check(!evidence.sshConnected && !evidence.gpuIdentified, 'Task completion does not imply SSH connected or GPU identified');
  const download = page.waitForEvent('download');
  await page.locator('#downloadBestKernelBtn').click();
  check((await download).suggestedFilename() === 'best_kernel.py', 'Best kernel downloads through the real artifact endpoint');
  await page.screenshot({path: 'output/playwright/ssh-session-live-results.png', fullPage: true});
  return {passed: checks.length, checks, taskId: evidence.taskId, realHttp: true, realCpuBaseline: true, realSshSuccess: false};
}
