from kernel import run
from reference import reference

values = list(range(4096))
error = max(abs(a - b) for a, b in zip(run(values), reference(values)))
status = "PASS" if error == 0 else "FAIL"
print(f'CORRECTNESS_RESULT status={status} max_error={error} reason="CPU reference; tolerance=0"')
