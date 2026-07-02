from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from .llm_client import LLMError, OpenAICompatibleClient
from .prompt_templates import SYSTEM_PROMPT, build_user_prompt


class CandidateAdvice(BaseModel):
    config: dict[str, Any]
    hypothesis: str = ""
    expected_improvement: str = ""
    risk: str = ""


class LLMPlan(BaseModel):
    candidates: list[CandidateAdvice] = Field(default_factory=list)

    @model_validator(mode="after")
    def non_empty(self) -> "LLMPlan":
        if not self.candidates:
            raise ValueError("LLM returned no candidates")
        return self


class Planner:
    def __init__(self, client: OpenAICompatibleClient, search_space: dict[str, list[Any]], safe_probe_results: list[dict[str, Any]] | None = None):
        self.client = client
        self.search_space = search_space
        self.safe_probe_results = safe_probe_results or []

    def propose(self, history: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
        raw = self.client.chat_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(self.search_space, history, count, self.safe_probe_results)},
            ]
        )
        plan = LLMPlan.model_validate(raw)
        configs: list[dict[str, Any]] = []
        expected_keys = set(self.search_space)
        for cand in plan.candidates:
            if set(cand.config) != expected_keys:
                raise LLMError("LLM candidate config keys do not match search_space")
            for key, value in cand.config.items():
                if value not in self.search_space[key]:
                    raise LLMError(f"LLM candidate outside search_space: {key}={value}")
            configs.append(cand.config)
        return configs[:count]
