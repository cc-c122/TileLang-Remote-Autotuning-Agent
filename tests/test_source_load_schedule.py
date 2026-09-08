from __future__ import annotations

import ast
import unittest
from pathlib import Path

from kernel_opt_agent.source_optimizer.analyzer import (
    analyze_source,
    rewrite_source_target,
)
from kernel_opt_agent.source_optimizer.load_schedule import SharedLoadScheduleTarget
from kernel_opt_agent.source_optimizer.planner import choose_plan


PAGED_SOURCE = Path("kernel_opt_agent/samples/paged_attention_decode/_upstream_sparse_gqa_decode_paged.py")


def _kernel(crossed: str = "T.clear(acc)", *, before_candidate: str = "") -> str:
    return f'''import tilelang.language as T

@T.prim_func
def main(
    A: T.Tensor([16, 16], "float16"),
    B: T.Tensor([16, 16], "float16"),
    Output: T.Tensor([16, 16], "float16"),
):
    A_shared = T.alloc_shared([16, 16], "float16")
    B_shared = T.alloc_shared([16, 16], "float16")
    acc = T.alloc_fragment([16, 16], "float32")
    offset = 0
    for k in T.Pipelined(4, num_stages=2):
        T.copy(A[offset, :], A_shared)
        {crossed}
        {before_candidate}
        T.copy(B[offset, :], B_shared)
        T.gemm(acc, B_shared, acc)
'''


def _prefetch_targets(source: str) -> list[SharedLoadScheduleTarget]:
    return [
        target
        for target in analyze_source(source).targets
        if isinstance(target, SharedLoadScheduleTarget)
    ]


class SourceLoadScheduleTests(unittest.TestCase):
    def test_real_paged_attention_v_load_moves_after_k_load(self) -> None:
        source = PAGED_SOURCE.read_text(encoding="utf-8")
        targets = _prefetch_targets(source)
        self.assertEqual(len(targets), 1)
        target = targets[0]
        self.assertEqual(target.function_name, "flashattn.main")
        self.assertIn("V[physical_block_idx", target.source_expr)
        self.assertEqual(target.destination_expr, "V_shared")
        self.assertEqual(target.confidence, "low")
        rewritten = rewrite_source_target(source, target)
        ast.parse(rewritten)
        k_load = rewritten.index("T.copy(K[physical_block_idx")
        v_load = rewritten.index("T.copy(V[physical_block_idx")
        clear = rewritten.index("T.clear(acc_s)", k_load)
        self.assertLess(k_load, v_load)
        self.assertLess(v_load, clear)
        self.assertEqual(source.count("T.copy(V[physical_block_idx"), 1)
        self.assertEqual(rewritten.count("T.copy(V[physical_block_idx"), 1)

    def test_dependency_assignment_sets_a_later_safe_insertion_point(self) -> None:
        source = _kernel("offset = k\n        T.clear(acc)")
        target = _prefetch_targets(source)[0]
        rewritten = rewrite_source_target(source, target)
        self.assertLess(rewritten.index("offset = k"), rewritten.index("T.copy(B[offset"))
        self.assertLess(rewritten.index("T.copy(B[offset"), rewritten.index("T.clear(acc)"))

    def test_unknown_call_global_write_and_barrier_are_rejected(self) -> None:
        for crossed in (
            "custom_compute(acc)",
            "Output[0, 0] = acc[0, 0]",
            "T.barrier()",
            "T.async_wait()",
        ):
            with self.subTest(crossed=crossed):
                self.assertEqual(_prefetch_targets(_kernel(crossed)), [])

    def test_destination_access_and_loop_carried_use_are_rejected(self) -> None:
        self.assertEqual(_prefetch_targets(_kernel("T.clear(B_shared)")), [])
        self.assertEqual(_prefetch_targets(_kernel("T.gemm(acc, B_shared, acc)")), [])

    def test_alias_and_dsl_shadowing_are_rejected(self) -> None:
        aliased = _kernel().replace("    offset = 0", "    alias = B\n    offset = 0")
        self.assertEqual(_prefetch_targets(aliased), [])
        sliced_alias = _kernel().replace("    offset = 0", "    alias = B[:, :]\n    offset = 0")
        self.assertEqual(_prefetch_targets(sliced_alias), [])
        rebound = _kernel().replace("    offset = 0", "    B = A\n    offset = 0")
        self.assertEqual(_prefetch_targets(rebound), [])
        shadowed = _kernel().replace("    offset = 0", "    T = object()\n    offset = 0")
        self.assertEqual(_prefetch_targets(shadowed), [])

    def test_candidate_cannot_cross_a_control_domain(self) -> None:
        source = _kernel().replace(
            "        T.copy(B[offset, :], B_shared)",
            "        if k > 0:\n            T.copy(B[offset, :], B_shared)",
        ).replace(
            "        T.gemm(acc, B_shared, acc)",
            "            T.gemm(acc, B_shared, acc)",
        )
        self.assertEqual(_prefetch_targets(source), [])
        while_loop = _kernel().replace(
            "    for k in T.Pipelined(4, num_stages=2):",
            "    while offset < 4:",
        )
        self.assertEqual(_prefetch_targets(while_loop), [])

    def test_stale_target_is_rejected(self) -> None:
        source = _kernel()
        target = _prefetch_targets(source)[0]
        changed = source.replace("T.copy(B[offset, :], B_shared)", "T.copy(B[k, :], B_shared)")
        with self.assertRaisesRegex(ValueError, "stale"):
            rewrite_source_target(changed, target)

    def test_planner_only_accepts_each_targets_authorized_template(self) -> None:
        source = _kernel()
        target = _prefetch_targets(source)[0]

        class WrongTemplatePlanner:
            def __init__(self) -> None:
                self.calls = 0

            def chat_json(self, messages):
                self.calls += 1
                return {
                    "target_id": target.target_id,
                    "template": "parallel_copy_to_t_copy",
                    "hypothesis": "guaranteed speedup",
                    "evidence_ids": [],
                }

        planner = WrongTemplatePlanner()
        plan, source_name = choose_plan((target,), [], planner)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(source_name, "rule_based")
        self.assertEqual(plan.template, "prefetch_shared_load")
        self.assertIn("low-confidence", plan.hypothesis)
        self.assertIn("no speedup is assumed", plan.hypothesis)


if __name__ == "__main__":
    unittest.main()
