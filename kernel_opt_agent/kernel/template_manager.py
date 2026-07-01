from __future__ import annotations

import re
from pathlib import Path
from typing import Any


PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def render_value(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, str):
        return value
    return str(value)


class TemplateManager:
    def __init__(self, template_path: Path, search_space: dict[str, list[Any]]):
        self.template_path = template_path
        self.template_text = template_path.read_text(encoding="utf-8")
        self.search_space = search_space
        self.placeholders = set(PLACEHOLDER_RE.findall(self.template_text))
        if self.placeholders != set(search_space.keys()):
            raise ValueError("template placeholders must exactly match search_space keys")

    def render(self, config: dict[str, Any]) -> str:
        if set(config.keys()) != set(self.search_space.keys()):
            raise ValueError("candidate config keys must exactly match search_space keys")
        for key, value in config.items():
            if value not in self.search_space[key]:
                raise ValueError(f"candidate value outside search_space: {key}={value}")

        def replace(match: re.Match[str]) -> str:
            return render_value(config[match.group(1)])

        rendered = PLACEHOLDER_RE.sub(replace, self.template_text)
        leftover = PLACEHOLDER_RE.findall(rendered)
        if leftover:
            raise ValueError(f"unreplaced placeholders remain: {leftover}")
        return rendered

