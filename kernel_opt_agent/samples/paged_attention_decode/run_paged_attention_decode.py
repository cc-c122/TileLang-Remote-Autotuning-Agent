from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from dataclasses import dataclass

import torch


@dataclass
class ShapeConfig:
    batch: int
    query_heads: int
    kv_heads: int
    head_dim: int
    page_size: int
    context_length: int
    dtype: str


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lo = int(index)
    hi = min(lo + 1, len(ordered) - 1)
    frac = index - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _synchronize() -> None:
    torch.cuda.synchronize()


def _measure_once(fn) -> float:
    _synchronize()
    start = time.perf_counter()
    fn()
    _synchronize()
    return (time.perf_counter() - start) * 1000.0


def _make_inputs(shape: ShapeConfig):
    import _upstream_sparse_gqa_decode_paged as upstream

    if shape.dtype != "fp16":
        raise ValueError("only fp16 is supported by the baseline sample")
    torch.manual_seed(42)
    torch.cuda.manual_seed_all(42)
    dtype = torch.float16
    device = "cuda"
    block_n = shape.page_size
    num_pages = max(1, math.ceil(shape.context_length / shape.page_size) * shape.batch)
    max_selected_blocks = math.ceil(shape.context_length / block_n)

    query = torch.randn((shape.batch, shape.query_heads, shape.head_dim), dtype=dtype, device=device)
    cache_seqlens = torch.full((shape.batch,), shape.context_length, dtype=torch.int32, device=device)
    key = torch.randn((shape.batch, shape.context_length, shape.kv_heads, shape.head_dim), dtype=dtype, device=device)
    value = torch.randn((shape.batch, shape.context_length, shape.kv_heads, shape.head_dim), dtype=dtype, device=device)
    key_cache = torch.zeros((num_pages, shape.page_size, shape.kv_heads, shape.head_dim), dtype=dtype, device=device)
    value_cache = torch.zeros((num_pages, shape.page_size, shape.kv_heads, shape.head_dim), dtype=dtype, device=device)
    max_num_blocks_per_seq = math.ceil(shape.context_length / shape.page_size)
    block_table = torch.zeros((shape.batch, max_num_blocks_per_seq), dtype=torch.int32, device=device)
    block_indices = torch.full((shape.batch, shape.kv_heads, max_selected_blocks), -1, dtype=torch.int32, device=device)

    available_blocks = list(range(shape.batch * max_num_blocks_per_seq))
    random.seed(42)
    random.shuffle(available_blocks)
    block_assignment = {}
    cursor = 0
    for batch_idx in range(shape.batch):
        for block_idx in range(max_num_blocks_per_seq):
            physical = available_blocks[cursor]
            block_table[batch_idx, block_idx] = physical
            block_assignment[(batch_idx, block_idx)] = physical
            cursor += 1

    for batch_idx in range(shape.batch):
        for block_idx in range(max_num_blocks_per_seq):
            physical = block_assignment[(batch_idx, block_idx)]
            start = block_idx * shape.page_size
            end = min(start + shape.page_size, shape.context_length)
            key_cache[physical, : end - start, :, :] = key[batch_idx, start:end, :, :]
            value_cache[physical, : end - start, :, :] = value[batch_idx, start:end, :, :]

    for batch_idx in range(shape.batch):
        for kv_head in range(shape.kv_heads):
            for item in range(max_selected_blocks):
                block_indices[batch_idx, kv_head, item] = max_selected_blocks - 1 - item

    sparse_attn = upstream.SparseFlashAttn(
        shape.batch,
        shape.query_heads,
        shape.kv_heads,
        shape.head_dim,
        shape.head_dim,
        shape.page_size,
        block_n,
        num_pages,
    )
    return upstream, sparse_attn, query, key_cache, value_cache, block_indices, cache_seqlens, block_table


def run_baseline(shape: ShapeConfig, warmup: int, repeat: int, atol: float, rtol: float) -> dict:
    upstream, sparse_attn, query, key_cache, value_cache, block_indices, cache_seqlens, block_table = _make_inputs(shape)
    output = sparse_attn.forward(query, key_cache, value_cache, block_indices, cache_seqlens, block_table)
    reference = upstream.ref_program_torch_paged(
        query,
        key_cache,
        value_cache,
        block_indices,
        cache_seqlens,
        block_table,
        shape.page_size,
        shape.page_size,
    )
    max_error = float(torch.max(torch.abs(output - reference)).item())
    passed = bool(torch.allclose(output, reference, atol=atol, rtol=rtol))
    if not passed:
        raise AssertionError(f"correctness failed: max_error={max_error} atol={atol} rtol={rtol}")

    def kernel_once() -> None:
        sparse_attn.forward(query, key_cache, value_cache, block_indices, cache_seqlens, block_table)

    for _ in range(warmup):
        kernel_once()
    samples = [_measure_once(kernel_once) for _ in range(repeat)]
    return {
        "operator": "Paged Attention Decode",
        "dtype": shape.dtype,
        "shape": {
            "batch": shape.batch,
            "query_heads": shape.query_heads,
            "kv_heads": shape.kv_heads,
            "head_dim": shape.head_dim,
            "page_size": shape.page_size,
            "context_length": shape.context_length,
        },
        "correctness": {
            "reference": "torch_scaled_dot_product_attention_over_paged_kv_cache",
            "tolerance": {"atol": atol, "rtol": rtol},
            "passed": passed,
            "max_error": max_error,
        },
        "benchmark": {
            "warmup_runs": warmup,
            "measurement_runs": repeat,
            "raw_samples_ms": samples,
            "median_ms": statistics.median(samples),
            "p90_ms": _percentile(samples, 0.9),
            "stddev_ms": statistics.stdev(samples) if len(samples) > 1 else 0.0,
            "measurement_protocol": "one sparse_attn.forward call per sample after fixed warmup",
        },
    }


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
    try:
        payload = run_baseline(
            ShapeConfig(
                batch=args.batch,
                query_heads=args.query_heads,
                kv_heads=args.kv_heads,
                head_dim=args.head_dim,
                page_size=args.page_size,
                context_length=args.context_length,
                dtype=args.dtype,
            ),
            args.warmup,
            args.repeat,
            args.tolerance_atol,
            args.tolerance_rtol,
        )
    except Exception as exc:
        print(f"PAGED_ATTENTION_BASELINE_ERROR {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("PAGED_ATTENTION_BASELINE_RESULT " + json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
