"""Product taxonomy and tree similarity (D27). Data: data/taxonomy.yaml.

Wu-Palmer similarity (1994), with the top-level trees hanging from a virtual root at depth 0:
    sim(a, b) = 2 x depth(deepest shared ancestor) / (depth(a) + depth(b)),   top-level node = depth 1
    same node 1.0 · parent/child or siblings by depth · different top-level trees 0.0
No tunable constants: the numbers follow from the shape of the tree.
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from disco_planner.data.catalog import DATA_DIR
from disco_planner.domain.models import Publisher


@lru_cache
def _raw() -> dict:
    return yaml.safe_load((DATA_DIR / "taxonomy.yaml").read_text())


@lru_cache
def parents() -> dict[str, str | None]:
    return _raw()["product_tree"]


@lru_cache
def facet_group() -> dict[str, str]:
    """facet tag -> its group (audience, business_model, values, ...)."""
    return {tag: group for group, tags in _raw()["facets"].items() for tag in tags}


def is_product(tag: str) -> bool:
    return tag in parents()


def is_facet(tag: str) -> bool:
    return tag in facet_group()


def path(node: str) -> list[str]:
    """node, its parent, ..., its top-level ancestor."""
    out = [node]
    while parents()[out[-1]] is not None:
        out.append(parents()[out[-1]])
    return out


def depth(node: str) -> int:
    return len(path(node))


def similarity(a: str, b: str) -> float:
    shared = [n for n in path(a) if n in set(path(b))]
    if not shared:
        return 0.0
    return 2 * depth(shared[0]) / (depth(a) + depth(b))


def host_products(pub: Publisher) -> set[str]:
    """The host's most specific product nodes: its category is only the root of the subcategories it lists, so any
    tag that is an ancestor of another of its tags is dropped (Tailcrate: pet_supplies, toys, treats; not pet)."""
    nodes = {t for t in pub.tags if is_product(t)}
    ancestors = {a for n in nodes for a in path(n)[1:]}
    return nodes - ancestors


def host_facets(pub: Publisher) -> set[str]:
    return {t for t in pub.tags if is_facet(t)}
