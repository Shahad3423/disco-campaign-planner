"""Regulated-claims policy (config/claims_policy.yaml), enforced in code.

The LLM is told the rules too, but this is the layer that holds when it doesn't listen:
an advertiser stating "clinically proven" is not the same as substantiating it.
"""
from __future__ import annotations

import re
from functools import lru_cache

from disco_planner.data import catalog


@lru_cache
def _rules() -> list[tuple[str, re.Pattern]]:
    policy = catalog.config("claims_policy")["blocked_until_substantiated"]
    return [(r["name"], re.compile("|".join(r["patterns"]), re.IGNORECASE)) for r in policy]


def violations(text: str) -> list[str]:
    """Names of the regulated-claim rules this text breaks."""
    return [name for name, pattern in _rules() if pattern.search(text)]
