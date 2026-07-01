from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class LLMError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, base_url: str, api_key_env: str, model: str, temperature: float, max_tokens: int):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise LLMError(f"LLM API key env var is not set: {self.api_key_env}")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        content = body.get("choices", [{}])[0].get("message", {}).get("content")
        if not content:
            raise LLMError("LLM response did not contain message content")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMError("LLM response content was not strict JSON") from exc

