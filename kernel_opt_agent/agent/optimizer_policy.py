from __future__ import annotations

import hashlib
import itertools
import random
from typing import Any


def config_hash(config: dict[str, Any]) -> str:
    parts = [f"{k}={repr(config[k])}" for k in sorted(config)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]


class OptimizerPolicy:
    def __init__(self, search_space: dict[str, list[Any]], seed: int = 20260701, planner: Any | None = None):
        self.search_space = search_space
        self.seed = seed
        self.rng = random.Random(seed)
        self.planner = planner
        self.grid_iter = self._grid_iter()

    def _grid_iter(self):
        keys = list(self.search_space.keys())
        for values in itertools.product(*(self.search_space[k] for k in keys)):
            yield dict(zip(keys, values))

    def grid(self, count: int, tried: set[str]) -> list[dict[str, Any]]:
        out = []
        for cfg in self.grid_iter:
            h = config_hash(cfg)
            if h not in tried:
                out.append(cfg)
                if len(out) >= count:
                    break
        return out

    def random_search(self, count: int, tried: set[str]) -> list[dict[str, Any]]:
        out = []
        keys = list(self.search_space.keys())
        max_attempts = max(100, count * 50)
        for _ in range(max_attempts):
            cfg = {k: self.rng.choice(self.search_space[k]) for k in keys}
            h = config_hash(cfg)
            if h not in tried and all(config_hash(x) != h for x in out):
                out.append(cfg)
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
            h = config_hash(cfg)
            if h not in tried and all(config_hash(x) != h for x in out):
                out.append(cfg)
                if len(out) >= count:
                    return out
        out.extend(self.random_search(count - len(out), tried | {config_hash(x) for x in out}))
        return out[:count]

    def llm(self, count: int, tried: set[str], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.planner is None:
            raise RuntimeError("LLM planner is unavailable")
        out = []
        for cfg in self.planner.propose(history, count):
            h = config_hash(cfg)
            if h not in tried and all(config_hash(x) != h for x in out):
                out.append(cfg)
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
