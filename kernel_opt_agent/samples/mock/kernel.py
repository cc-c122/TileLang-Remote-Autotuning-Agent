BM = {{BM}}
BN = {{BN}}
BK = {{BK}}
NUM_THREADS = {{NUM_THREADS}}
NUM_STAGES = {{NUM_STAGES}}
VECTOR_WIDTH = {{VECTOR_WIDTH}}
UNROLL_FACTOR = {{UNROLL_FACTOR}}
USE_SHARED = {{USE_SHARED}}
USE_DOUBLE_BUFFER = {{USE_DOUBLE_BUFFER}}


def score():
    reuse = (BM * BN) / max(BK, 1)
    thread_penalty = abs(NUM_THREADS - 128) / 256.0
    stage_penalty = max(NUM_STAGES - 3, 0) * 0.15
    bool_bonus = 0.2 if USE_SHARED else 0.0
    db_bonus = 0.1 if USE_DOUBLE_BUFFER else 0.0
    return reuse / 64.0 + VECTOR_WIDTH * 0.25 + UNROLL_FACTOR * 0.1 + bool_bonus + db_bonus - thread_penalty - stage_penalty

