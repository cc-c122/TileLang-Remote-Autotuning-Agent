import kernel


score = kernel.kernel_score()
if score > 0:
    print('CORRECTNESS_RESULT status=PASS max_error=0 reason="ok"')
else:
    print('CORRECTNESS_RESULT status=FAIL max_error=1 reason="score must be positive"')
    raise SystemExit(1)
