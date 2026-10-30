"""Loads prompts from /prompts. The markdown file is the single source of truth:
the code never contains prompt text, so reviewers read exactly what the model sees."""
from __future__ import annotations

import re
from functools import lru_cache

from disco_planner.data.catalog import REPO_ROOT

PROMPTS_DIR = REPO_ROOT / "prompts"


@lru_cache
def _sections(name: str) -> dict[str, str]:
    text = (PROMPTS_DIR / f"{name}.md").read_text()
    parts = re.split(r"^## (system|user)\s*$", text, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def render(name: str, **values: str) -> tuple[str, str]:
    """Return (system, user) with {{placeholders}} filled. Missing values raise."""
    sections = _sections(name)

    def fill(template: str) -> str:
        def sub(m: re.Match) -> str:
            return str(values[m.group(1)])

        return re.sub(r"\{\{(\w+)\}\}", sub, template)

    return fill(sections["system"]), fill(sections["user"])
