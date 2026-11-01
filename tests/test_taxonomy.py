"""Product taxonomy (D27): coverage, similarity, host products, and the experimental tree scoring method."""
import contextlib
from datetime import date

from disco_planner.data import catalog
from disco_planner.planning import taxonomy
from disco_planner.planning.publisher_scoring import rank_publishers
from tests.test_publisher_scoring import dog_food_profile

TODAY = date(2026, 10, 30)


@contextlib.contextmanager
def method(name: str):
    cfg = catalog.config("scoring")["category"]
    saved = cfg["method"]
    cfg["method"] = name
    try:
        yield
    finally:
        cfg["method"] = saved


def test_every_catalog_tag_is_exactly_one_of_product_or_facet():
    vocab = set(catalog.publisher_tag_vocabulary())
    products = {t for t in taxonomy.parents() if t not in taxonomy._raw()["synthetic"]}
    facets = set(taxonomy.facet_group())
    assert vocab == products | facets and not products & facets


def test_similarity_follows_the_tree():
    s = taxonomy.similarity
    assert s("pet_food", "pet_food") == 1.0
    assert abs(s("pet_food", "treats") - 0.8) < 1e-9        # treats are a kind of pet food
    assert abs(s("activewear", "workwear") - 0.5) < 1e-9    # siblings under apparel
    assert s("bedding", "pet_food") == 0.0                  # different top-level trees share nothing
    assert s("pet_food", "pet_supplies") == s("pet_supplies", "pet_food")


def test_host_products_drop_the_hosts_own_ancestor_tags():
    tailcrate = catalog.publisher_by_id()["pub_018"]         # pet, pet_supplies, subscription, toys, treats
    assert taxonomy.host_products(tailcrate) == {"toys", "treats"}
    assert taxonomy.host_facets(tailcrate) == {"subscription"}


def test_tree_method_gives_treats_hosts_credit_for_dog_food_and_unrelated_hosts_none():
    with method("tree"):
        ranked = {s.publisher_id: s for s in rank_publishers(dog_food_profile(), TODAY)}
    assert ranked["pub_007"].components[0].value == 1.0                 # same product
    assert abs(ranked["pub_018"].components[0].value - 0.8) < 1e-9      # Tailcrate sells treats
    assert ranked["pub_015"].components[0].value == 0.0                 # Kitchenly: only a facet in common (subscription)
