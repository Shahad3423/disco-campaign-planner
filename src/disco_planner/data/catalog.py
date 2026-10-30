"""Loads the mock catalog and config. Cached: files are read once per process."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml

from disco_planner.domain.models import Persona, Publisher

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = REPO_ROOT / "config"


@lru_cache
def publishers() -> list[Publisher]:
    raw = json.loads((DATA_DIR / "publishers.json").read_text())
    return [Publisher(**p) for p in raw]


@lru_cache
def personas() -> list[Persona]:
    raw = json.loads((DATA_DIR / "shopper_personas.json").read_text())
    return [Persona(**p) for p in raw]


def publisher_by_id() -> dict[str, Publisher]:
    return {p.id: p for p in publishers()}


def persona_by_id() -> dict[str, Persona]:
    return {p.id: p for p in personas()}


@lru_cache
def config(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / f"{name}.yaml").read_text())


def publisher_tag_vocabulary() -> list[str]:
    """Closed vocabulary the LLM must choose from, so it can't invent categories."""
    return sorted({t for p in publishers() for t in p.tags})


def persona_tag_vocabulary() -> list[str]:
    return sorted({t for p in personas() for t in p.category_affinities})
