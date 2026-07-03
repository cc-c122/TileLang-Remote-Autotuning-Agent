BM = {{BM}}
BN = {{BN}}
BK = {{BK}}
NUM_THREADS = {{NUM_THREADS}}
NUM_STAGES = {{NUM_STAGES}}
VECTOR_WIDTH = {{VECTOR_WIDTH}}


def score():
    tile_score = (BM * BN * BK) / (16 * 32 * 32)
    thread_score = NUM_THREADS / 128
    stage_score = NUM_STAGES / 2
    vector_score = VECTOR_WIDTH
    return tile_score + thread_score + stage_score + vector_score
