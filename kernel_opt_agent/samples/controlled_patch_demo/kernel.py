BM = {{BM}}
BN = {{BN}}
BK = {{BK}}
NUM_THREADS = {{NUM_THREADS}}
NUM_STAGES = {{NUM_STAGES}}
VECTOR_WIDTH = {{VECTOR_WIDTH}}


def kernel_score():
    # BEGIN_AGENT_PATCH: compute
    value = BM + BN + BK + NUM_THREADS + NUM_STAGES + VECTOR_WIDTH
    # END_AGENT_PATCH
    return value


if __name__ == "__main__":
    print(kernel_score())
