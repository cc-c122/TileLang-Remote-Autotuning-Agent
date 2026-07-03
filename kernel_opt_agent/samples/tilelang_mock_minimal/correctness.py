import os

import kernel


print(f"CORRECTNESS_CWD {os.getcwd()}")
if kernel.BM > 64 or kernel.BN > 64 or kernel.BK > 64:
    print('CORRECTNESS_RESULT status=FAIL max_error=1.0 reason="tile too large for minimal mock"')
else:
    print('CORRECTNESS_RESULT status=PASS max_error=0.00001 reason="ok"')
