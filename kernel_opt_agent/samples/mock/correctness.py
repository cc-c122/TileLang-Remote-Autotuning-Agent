import kernel


bad = kernel.BM == 64 and kernel.BN == 64 and kernel.USE_SHARED is False
if bad:
    print('CORRECTNESS_RESULT status=FAIL max_error=0.25 reason="mock invalid tile without shared memory"')
    raise SystemExit(0)
print('CORRECTNESS_RESULT status=PASS max_error=0.00001 reason="ok"')

