from datetime import datetime, timezone

from disco_planner.serving.features import ShopperSignals, build_features, sanitize_name

JULY = datetime(2026, 7, 15, 18, 0, tzinfo=timezone.utc)
NOVEMBER = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)


def features(zip="10001", name="Emma", pub="pub_001", source="billing", now=JULY):
    return build_features(ShopperSignals(publisher_id=pub, zip=zip, first_name=name,
                                         zip_source=source), now)


def test_unisex_name_is_mid_p_female_with_low_confidence():
    unisex, clear = features(name="Jordan"), features(name="Emma")
    assert 0.2 < unisex.p_female.value < 0.8
    assert unisex.p_female.confidence < 0.6
    assert clear.p_female.value > 0.95
    assert clear.p_female.confidence > unisex.p_female.confidence


def test_name_age_distribution_sums_to_one():
    dist = features(name="Emma").age_bucket.distribution
    assert abs(sum(dist.values()) - 1) < 0.01


def test_junk_names_rejected():
    for junk in ["test", "asdf", "J", "12345", "a@b.com", "x" * 21, "fuck", "", "   ", "aaaa"]:
        assert sanitize_name(junk) is None
        result = features(name=junk)
        assert result.display_name.value is None
        assert result.p_female.confidence == 0


def test_name_title_casing():
    assert sanitize_name("JOHN") == "John"
    assert sanitize_name("  mary-kate ") == "Mary-Kate"
    assert sanitize_name("o'neil") == "O'Neil"
    assert sanitize_name("McDonald") == "McDonald"


def test_malformed_zip_has_zero_confidence_and_does_not_raise():
    for bad in ["", "abc", "1234", "00000", "99999999"]:
        result = features(zip=bad)
        assert result.state.confidence == 0
        assert result.timezone.confidence == 0
        assert result.local_hour.confidence == 0


def test_known_zips():
    ny = features(zip="10001")
    assert ny.state.value == "NY" and ny.region.value == "US-Northeast"
    assert ny.timezone.value == "America/New_York"
    assert ny.local_hour.value == 14  # 18:00 UTC in July is 14:00 EDT
    sf = features(zip="94103")
    assert sf.state.value == "CA" and sf.region.value == "US-West"
    assert sf.timezone.value == "America/Los_Angeles"
    co = features(zip="80435")
    assert co.state.value == "CO" and co.cold_or_mountain.value is True


def test_zip_plus_four_is_accepted():
    assert features(zip="10001-1234").state.value == "NY"


def test_shipping_zip_lowers_geo_confidence():
    billing, shipping = features(source="billing"), features(source="shipping")
    assert shipping.state.confidence < billing.state.confidence
    assert shipping.timezone.confidence < billing.timezone.confidence


def test_heartfoot_in_november_has_higher_gift_signal():
    nov_heartfoot = features(pub="pub_010", now=NOVEMBER).gift_signal.value
    assert nov_heartfoot > features(pub="pub_010", now=JULY).gift_signal.value
    assert nov_heartfoot > features(pub="pub_001", now=NOVEMBER).gift_signal.value


def test_cart_prior_and_unknown_publisher():
    assert features(pub="pub_010").cart_prior.category == "apparel"
    assert features(pub="nope").cart_prior.confidence == 0
