from __future__ import annotations

import hashlib
import itertools
import random
from typing import Any


def config_hash(config: dict[str, Any]) -> str:
    parts = [f"{k}={repr(config[k])}" for k in sorted(config)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


class OptimizerPolicy:
    def __init__(
        self,
        search_space: dict[str, list[Any]],
        seed: int = 20260701,
        planner: Any | None = None,
        safe_probe_results: list[dict[str, Any]] | None = None,
        hardware_info: Any | None = None,
        conservative_mode: bool | None = None,
    ):
        self.search_space = search_space
        self.seed = seed
        self.rng = random.Random(seed)
        self.planner = planner
        self.hardware_info = hardware_info
        self.conservative_mode = bool(conservative_mode) if conservative_mode is not None else bool(getattr(hardware_info, "conservative_mode", False))
        self.safe_probe_results = safe_probe_results or []
        self.unavailable_values = self._unavailable_values()
        self.grid_iter = self._grid_iter()

    def _unavailable_values(self) -> dict[str, set[Any]]:
        failed_statuses = {"failed", "timeout", "guard_denied", "exception"}
        failed: dict[str, set[Any]] = {}
        passed: dict[str, set[Any]] = {}
        for record in self.safe_probe_results:
            param_name = record.get("param_name")
            if param_name not in self.search_space:
                continue
            value = record.get("candidate_value")
            if value not in self.search_space[param_name]:
                continue
            if record.get("status") == "pass":
                passed.setdefault(param_name, set()).add(value)
            elif record.get("status") in failed_statuses:
                failed.setdefault(param_name, set()).add(value)

        unavailable: dict[str, set[Any]] = {}
        for param_name, values in failed.items():
            blocked = values - passed.get(param_name, set())
            if blocked and len(blocked) < len(self.search_space[param_name]):
                unavailable[param_name] = blocked
        return unavailable

    def _allowed_by_probe(self, config: dict[str, Any]) -> bool:
        for param_name, values in self.unavailable_values.items():
            if config.get(param_name) in values:
                return False
        return True

    def _append_if_new(self, out: list[dict[str, Any]], cfg: dict[str, Any], tried: set[str]) -> bool:
        h = config_hash(cfg)
        if h in tried or any(config_hash(x) == h for x in out):
            return False
        if not self._allowed_by_probe(cfg):
            return False
        if self.conservative_mode and not self._allowed_by_conservative_mode(cfg):
            return False
        out.append(cfg)
        return True

    def _allowed_by_conservative_mode(self, config: dict[str, Any]) -> bool:
        for key in ("BM", "BN", "BK"):
            values = self.search_space.get(key)
            if values and len(values) > 1 and config.get(key) == values[-1]:
                return False
        threads = self.search_space.get("NUM_THREADS")
        if threads and config.get("NUM_THREADS") == threads[-1] and len(threads) > 2:
            return False
        stages = self.search_space.get("NUM_STAGES")
        if stages and config.get("NUM_STAGES") == stages[-1] and len(stages) > 2:
            return False
        if config.get("USE_DOUBLE_BUFFER") is True:
            for key in ("BM", "BN", "BK"):
                values = self.search_space.get(key)
                if values and config.get(key) == values[-1]:
                    return False
        return True

    def _grid_iter(self):
        keys = list(self.search_space.keys())
        for values in itertools.product(*(self.search_space[k] for k in keys)):
            yield dict(zip(keys, values))

    def grid(self, count: int, tried: set[str]) -> list[dict[str, Any]]:
        out = []
        for cfg in self.grid_iter:
            self._append_if_new(out, cfg, tried)
            if len(out) >= count:
                break
        return out

    def random_search(self, count: int, tried: set[str]) -> list[dict[str, Any]]:
        out = []
        keys = list(self.search_space.keys())
        max_attempts = max(100, count * 50)
        for _ in range(max_attempts):
            cfg = {k: self.rng.choice(self.search_space[k]) for k in keys}
            self._append_if_new(out, cfg, tried)
            if len(out) >= count:
                break
        return out

    def rule_based(self, count: int, tried: set[str]) -> list[dict[str, Any]]:
        keys = list(self.search_space.keys())
        mid = {k: self.search_space[k][len(self.search_space[k]) // 2] for k in keys}
        small = {k: self.search_space[k][0] for k in keys}
        large = {k: self.search_space[k][-1] for k in keys}
        bool_flipped = dict(mid)
        for k, values in self.search_space.items():
            if all(isinstance(v, bool) for v in values):
                bool_flipped[k] = values[-1]
        candidates = [mid, small, large, bool_flipped]
        out = []
        for cfg in candidates:
            self._append_if_new(out, cfg, tried)
            if len(out) >= count:
                return out
        out.extend(self.random_search(count - len(out), tried | {config_hash(x) for x in out}))
        return out[:count]

    def llm(self, count: int, tried: set[str], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.planner is None:
            raise RuntimeError("LLM planner is unavailable")
        out = []
        for cfg in self.planner.propose(history, count):
            self._append_if_new(out, cfg, tried)
        return out

    def propose(self, strategy: str, count: int, tried: set[str], history: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
        if strategy == "grid":
            return self.grid(count, tried), "grid"
        if strategy == "random":
            return self.random_search(count, tried), "random"
        if strategy == "rule_based":
            return self.rule_based(count, tried), "rule_based"
        if strategy == "llm":
            try:
                candidates = self.llm(count, tried, history)
                source = "llm"
            except Exception:
                candidates = self.rule_based(count, tried)
                source = "llm_rule_based_fallback"
            seen = tried | {config_hash(c) for c in candidates}
            if len(candidates) < count:
                candidates.extend(self.grid(count - len(candidates), seen))
            seen = tried | {config_hash(c) for c in candidates}
            if len(candidates) < count:
                candidates.extend(self.random_search(count - len(candidates), seen))
            return candidates[:count], source
        if strategy == "hybrid":
            candidates: list[dict[str, Any]] = []
            source = "hybrid"
            try:
                candidates.extend(self.llm(count, tried, history))
                source = "llm"
            except Exception:
                candidates.extend(self.rule_based(count, tried))
                source = "rule_based_fallback"
            seen = tried | {config_hash(c) for c in candidates}
            if len(candidates) < count:
                candidates.extend(self.grid(count - len(candidates), seen))
            seen = tried | {config_hash(c) for c in candidates}
            if len(candidates) < count:
                candidates.extend(self.random_search(count - len(candidates), seen))
            return candidates[:count], source
        raise ValueError(f"unsupported strategy: {strategy}")
