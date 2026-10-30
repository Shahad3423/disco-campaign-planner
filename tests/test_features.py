from datetime import datetime, timezone

from disco_planner.serving import calendar
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


# ---- Geography ---------------------------------------------------------------

def test_age_confidence_is_very_low():
    for name in ["Emma", "Michael", "Jordan", "Linda"]:
        assert features(name=name).age_bucket.confidence <= 0.15


def test_ski_country_80435():
    f = features(zip="80435")
    assert f.near_ski_area.value is True and f.ski_distance_km.value < 150
    assert f.mountain.value is True and f.cold_or_mountain.value is True
    assert f.is_coastal.value is False


def test_miami_33139_coastal_hurricane_hot_humid():
    f = features(zip="33139")
    assert f.is_coastal.value is True and f.coast_distance_km.value < 50
    assert f.hurricane_coast.value is True
    assert f.climate_band.value == "hot_humid_sunbelt" and f.sun_belt.value is True
    assert f.near_ski_area.value is False and f.cold_or_mountain.value is False


def test_new_york_is_metro_near_with_fast_delivery():
    f = features(zip="10001")
    assert f.nearest_metro.value == "New York" and f.metro_distance_km.value < 15
    assert f.urbanicity_proxy.value == "urban"
    assert f.delivery_speed_proxy.value == "same_or_next_day_likely"


def test_rural_montana_is_far_from_metro():
    f = features(zip="59001")
    assert f.metro_distance_km.value > 300
    assert f.urbanicity_proxy.value == "rural"
    assert f.delivery_speed_proxy.value == "standard"


def test_climate_bands():
    assert features(zip="85004").climate_band.value == "arid_desert"        # Phoenix
    assert features(zip="98101").climate_band.value == "marine_pacific_nw"  # Seattle
    assert features(zip="55402").climate_band.value == "cold_snow_belt"     # Minneapolis


def test_income_tier_unknown_without_acs():
    assert features(zip="10001").income_tier.confidence == 0


def test_geo_extras_zero_confidence_for_bad_zip():
    f = features(zip="abc")
    for feature in (f.climate_band, f.is_coastal, f.nearest_metro, f.near_ski_area,
                    f.delivery_speed_proxy, f.sun_belt):
        assert feature.confidence == 0


# ---- Calendar ----------------------------------------------------------------

def test_nov_20_black_friday_and_gifting():
    moments = features(now=datetime(2026, 11, 20, 18, tzinfo=timezone.utc)).active_moments
    assert moments.distribution["Black Friday / Cyber Monday"] > 0
    assert "persona_010" in [p for m in calendar.active_moments(datetime(2026, 11, 20))
                             if m.name.startswith("Black") for p in m.boosts_personas]


def test_jan_3_new_year_fitness_and_dry_january():
    names = calendar.active_moments(datetime(2027, 1, 3, 12))
    names = {m.name: m for m in names}
    assert "New Year resolutions (fitness and wellness)" in names
    assert "Dry January" in names and "Ski season" in names


def test_intensity_ramps_up_before_event():
    early = {m.name: m.intensity for m in calendar.active_moments(datetime(2026, 11, 14))}
    late = {m.name: m.intensity for m in calendar.active_moments(datetime(2026, 11, 25))}
    assert 0 < early["Black Friday / Cyber Monday"] < late["Black Friday / Cyber Monday"] < 1
    inside = {m.name: m.intensity for m in calendar.active_moments(datetime(2026, 11, 28))}
    assert inside["Black Friday / Cyber Monday"] == 1.0


def test_late_night_uses_local_time():
    # 03:30 UTC in July = 23:30 EDT in New York
    f = features(zip="10001", now=datetime(2026, 7, 16, 3, 30, tzinfo=timezone.utc))
    assert f.local_hour.value == 23 and f.daypart.value == "late_night"
    assert f.day_of_week.value == "Wednesday"  # local date is Wed Jul 15


def test_weekend_flag_and_utc_fallback():
    f = features(zip="bad", now=datetime(2026, 7, 18, 10, tzinfo=timezone.utc))  # a Saturday
    assert f.is_weekend.value is True and f.daypart.value == "morning"
    assert f.daypart.confidence == 0 and f.daypart.source == "utc_fallback"


def test_gift_signal_is_calendar_driven():
    quiet = features(pub="pub_001", now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    xmas = features(pub="pub_001", now=datetime(2026, 12, 20, tzinfo=timezone.utc))
    assert xmas.gift_signal.value > quiet.gift_signal.value
