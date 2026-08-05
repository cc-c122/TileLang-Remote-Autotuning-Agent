from __future__ import annotations

import argparse
import json
import statistics
from types import SimpleNamespace


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lo = int(index)
    hi = min(lo + 1, len(ordered) - 1)
    frac = index - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _upstream_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        batch=args.batch,
        heads=args.query_heads,
        heads_kv=args.kv_heads,
        max_cache_seqlen=args.context_length,
        dim=args.head_dim,
        dim_v=args.head_dim,
        sparse_ratio=0.0,
        block_N=args.page_size,
        page_block_size=args.page_size,
        num_pages=max(1, (args.context_length + args.page_size - 1) // args.page_size * args.batch),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--context-length", type=int, required=True)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--dtype", choices=["fp16"], default="fp16")
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--query-heads", type=int, default=32)
    parser.add_argument("--kv-heads", type=int, default=8)
    parser.add_argument("--head-dim", type=int, default=128)
    parser.add_argument("--page-size", type=int, default=16)
    parser.add_argument("--tolerance-atol", type=float, default=2e-3)
    parser.add_argument("--tolerance-rtol", type=float, default=2e-3)
    args = parser.parse_args()

    import _upstream_sparse_gqa_decode_paged as upstream

    upstream_args = _upstream_args(args)
    upstream.main(upstream_args)
    for _ in range(args.warmup):
        upstream.run_regression_perf(upstream_args)
    samples = [float(upstream.run_regression_perf(upstream_args)) for _ in range(args.repeat)]
    payload = {
        "operator": "Paged Attention Decode",
        "dtype": args.dtype,
        "shape": {
            "batch": args.batch,
            "query_heads": args.query_heads,
            "kv_heads": args.kv_heads,
            "head_dim": args.head_dim,
            "page_size": args.page_size,
            "context_length": args.context_length,
        },
        "correctness": {
            "reference": "upstream TileLang sample reference path",
            "tolerance": {"atol": args.tolerance_atol, "rtol": args.tolerance_rtol},
            "status": "see upstream correctness output",
        },
        "benchmark": {
            "warmup_runs": args.warmup,
            "measurement_runs": args.repeat,
            "raw_samples_ms": samples,
            "median_ms": statistics.median(samples),
            "p90_ms": _percentile(samples, 0.9),
            "stddev_ms": statistics.stdev(samples) if len(samples) > 1 else 0.0,
        },
    }
    print("PAGED_ATTENTION_BASELINE_RESULT " + json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
