"""Build the compact reference tables used by the serving feature layer.

Run once (offline, needs pandas, requests, pyreadr):  python scripts/build_reference_tables.py

Outputs (committed):  data/reference/names.csv, data/reference/zips.csv
Downloads (gitignored): data/raw/

SOURCES
-------
Names: the `babynames` dataset (Hadley Wickham's R package, github.com/hadley/babynames,
  CC0). It is derived from the US Social Security Administration (SSA) national baby-name
  files, 1880-2017. We use it because ssa.gov blocks scripted downloads (HTTP 403) and we
  do not work around that. Only the 1880-2017 span is available here.
Zips: Census 2023 ZCTA Gazetteer (lat, lon, land area).
  ZCTA = ZIP Code Tabulation Area, the Census approximation of a ZIP code.
Income / population: the ACS 5-year API (2022) now needs a free key. If the environment
  variable CENSUS_API_KEY is set, we fetch B19013_001E (median household income) and
  B01003_001E (population) per ZCTA and add median_income, population, density,
  income_tier and density-based urbanicity. Without a key these columns are omitted and
  urbanicity falls back to land area (see below); the serving code then reports
  income_tier with confidence 0.
Hand-written tables used at serve time (data/reference/coast_points.csv, metros.csv,
  ski_areas.csv) are NOT built here; see src/disco_planner/serving/geo.py.

LIMITATIONS
-----------
* Baby-name counts are births, not living people: they ignore mortality and migration,
  which biases name->age estimates toward older cohorts. Acceptable as a prior.
* Name data ends in 2017, so people born 2009-2017 (under 18) are excluded on purpose.
"""
import io
import os
import zipfile
from pathlib import Path

import pandas as pd
import pyreadr
import requests

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw"
OUT = REPO / "data" / "reference"

BABYNAMES_URL = "https://raw.githubusercontent.com/hadley/babynames/master/data/babynames.rda"
GAZETTEER_URL = ("https://www2.census.gov/geo/docs/maps-data/data/gazetteer/"
                 "2023_Gazetteer/2023_Gaz_zcta_national.zip")

AS_OF_YEAR = 2026
MIN_NAME_COUNT = 1000
# Age bucket -> (oldest birth year, youngest birth year), computed from AS_OF_YEAR.
# 65+ is capped at birth year 1930 (older cohorts are mostly deceased).
AGE_BUCKETS = {
    "18-24": (AS_OF_YEAR - 24, AS_OF_YEAR - 18),
    "25-34": (AS_OF_YEAR - 34, AS_OF_YEAR - 25),
    "35-44": (AS_OF_YEAR - 44, AS_OF_YEAR - 35),
    "45-54": (AS_OF_YEAR - 54, AS_OF_YEAR - 45),
    "55-64": (AS_OF_YEAR - 64, AS_OF_YEAR - 55),
    "65+": (1930, AS_OF_YEAR - 65),
}

# ---- Geography tables -------------------------------------------------------

# zip3 (first three digits) -> state, as inclusive ranges. Based on the USPS
# prefix allocation; a few special cases are handled in ZIP3_OVERRIDES.
ZIP3_RANGES = [
    (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"), (39, 49, "ME"), (50, 59, "VT"),
    (60, 69, "CT"), (70, 89, "NJ"), (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"),
    (200, 205, "DC"), (206, 219, "MD"), (220, 246, "VA"), (247, 268, "WV"),
    (270, 289, "NC"), (290, 299, "SC"), (300, 319, "GA"), (320, 349, "FL"),
    (350, 369, "AL"), (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"),
    (400, 427, "KY"), (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"),
    (500, 528, "IA"), (530, 549, "WI"), (550, 567, "MN"), (570, 577, "SD"),
    (580, 588, "ND"), (590, 599, "MT"), (600, 629, "IL"), (630, 658, "MO"),
    (660, 679, "KS"), (680, 693, "NE"), (700, 714, "LA"), (716, 729, "AR"),
    (730, 749, "OK"), (750, 799, "TX"), (800, 816, "CO"), (820, 831, "WY"),
    (832, 838, "ID"), (840, 847, "UT"), (850, 865, "AZ"), (870, 884, "NM"),
    (889, 898, "NV"), (900, 961, "CA"), (967, 968, "HI"), (970, 979, "OR"),
    (980, 994, "WA"), (995, 999, "AK"),
]
ZIP3_OVERRIDES = {
    "005": "NY", "055": "MA", "063": "CT", "733": "TX", "885": "TX",
    "006": "PR", "007": "PR", "009": "PR", "008": "VI", "569": "DC",
}

# Catalog geo labels. Each state gets exactly ONE label.
#  US-Northeast: New England + NY, NJ, PA (Census Northeast).
#  US-East: the remaining Atlantic-seaboard states, DE down to FL (+ DC).
#  US-South: the non-seaboard South (WV, KY, TN, AL, MS, AR, LA, OK, TX).
#  US-Midwest: Census Midwest. US-West: Census West (incl. AK, HI).
REGION_STATES = {
    "US-Northeast": "ME NH VT MA RI CT NY NJ PA",
    "US-East": "DE MD DC VA NC SC GA FL",
    "US-South": "WV KY TN AL MS AR LA OK TX",
    "US-Midwest": "OH IN IL MI WI MN IA MO ND SD NE KS",
    "US-West": "MT ID WY CO NM AZ UT NV CA OR WA AK HI",
}
STATE_TO_REGION = {s: r for r, states in REGION_STATES.items() for s in states.split()}

# IANA timezone by state (the state's main zone), then zip3 overrides for split states.
EASTERN = "America/New_York"
CENTRAL = "America/Chicago"
MOUNTAIN = "America/Denver"
PACIFIC = "America/Los_Angeles"
STATE_TZ = {}
for _s in "ME NH VT MA RI CT NY NJ PA DE MD DC VA WV NC SC GA FL OH MI IN KY".split():
    STATE_TZ[_s] = EASTERN
for _s in "AL MS AR LA OK TX TN IL WI MN IA MO ND SD NE KS".split():
    STATE_TZ[_s] = CENTRAL
for _s in "MT ID WY CO NM UT".split():
    STATE_TZ[_s] = MOUNTAIN
for _s in "NV CA OR WA".split():
    STATE_TZ[_s] = PACIFIC
STATE_TZ.update({"AZ": "America/Phoenix", "HI": "Pacific/Honolulu",
                 "AK": "America/Anchorage", "PR": "America/Puerto_Rico",
                 "VI": "America/Puerto_Rico"})
# Approximations for states split across zones: the zip3 areas below get the other zone.
ZIP3_TZ_OVERRIDES = {
    **{z: CENTRAL for z in ["324", "325"]},                      # FL panhandle
    **{z: CENTRAL for z in ["420", "421", "422", "423", "424", "425", "426", "427"]},  # W. KY
    **{z: CENTRAL for z in ["463", "464", "475", "476", "477"]},  # NW / SW Indiana
    **{z: CENTRAL for z in ["498", "499"]},                      # Upper Peninsula MI
    **{z: EASTERN for z in ["373", "374", "377", "378", "379"]},  # E. Tennessee
    **{z: MOUNTAIN for z in ["798", "799", "885"]},              # El Paso TX
    **{z: MOUNTAIN for z in ["678", "679", "691", "692", "693"]},  # W. KS / W. NE
    **{z: PACIFIC for z in ["835", "838"]},                      # N. Idaho
}

# Urbanicity from ZCTA land area (square miles), a PROXY for density because no
# population is available: small zips are dense cities, large zips are rural.
URBAN_MAX_SQMI = 5
SUBURBAN_MAX_SQMI = 40


def zip3_to_state(zip3: str) -> str | None:
    """Look up the state for a 3-digit zip prefix (None if unallocated)."""
    if zip3 in ZIP3_OVERRIDES:
        return ZIP3_OVERRIDES[zip3]
    number = int(zip3)
    for low, high, state in ZIP3_RANGES:
        if low <= number <= high:
            return state
    return None


def urbanicity_from_area(land_sqmi: float) -> str:
    if land_sqmi < URBAN_MAX_SQMI:
        return "urban"
    if land_sqmi < SUBURBAN_MAX_SQMI:
        return "suburban"
    return "rural"


# ACS-based cutoffs (used only when CENSUS_API_KEY is set).
ACS_URL = "https://api.census.gov/data/2022/acs/acs5"
INCOME_CUTOFFS = [(45_000, "low"), (80_000, "mid"), (120_000, "mid-high")]  # else "high"
URBAN_MIN_DENSITY = 2000      # people per square mile
SUBURBAN_MIN_DENSITY = 500


def income_tier(income: float) -> str:
    """low < $45k <= mid < $80k <= mid-high < $120k <= high (median household income)."""
    if pd.isna(income):
        return ""
    for upper, label in INCOME_CUTOFFS:
        if income < upper:
            return label
    return "high"


def urbanicity_from_density(density: float) -> str:
    if density >= URBAN_MIN_DENSITY:
        return "urban"
    if density >= SUBURBAN_MIN_DENSITY:
        return "suburban"
    return "rural"


def fetch_acs(api_key: str) -> pd.DataFrame:
    """Median income and population per ZCTA from the ACS 5-year API."""
    response = requests.get(ACS_URL, timeout=120, params={
        "get": "B19013_001E,B01003_001E", "for": "zip code tabulation area:*", "key": api_key})
    response.raise_for_status()
    rows = response.json()
    acs = pd.DataFrame(rows[1:], columns=rows[0]).rename(columns={
        "zip code tabulation area": "zip", "B19013_001E": "median_income",
        "B01003_001E": "population"})
    acs["median_income"] = pd.to_numeric(acs["median_income"], errors="coerce")
    acs["population"] = pd.to_numeric(acs["population"], errors="coerce")
    acs.loc[acs["median_income"] < 0, "median_income"] = float("nan")  # -666666666 = no data
    return acs


def download(url: str, path: Path) -> Path:
    """Download a file once (plain request, no header tricks)."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        path.write_bytes(response.content)
    return path


def build_names() -> None:
    rda = download(BABYNAMES_URL, RAW / "babynames.rda")
    df = pyreadr.read_r(str(rda))["babynames"]
    df["year"] = df["year"].astype(int)
    df["name"] = df["name"].str.lower()
    first_year = min(low for low, _ in AGE_BUCKETS.values())
    last_year = max(high for _, high in AGE_BUCKETS.values())
    df = df[(df["year"] >= first_year) & (df["year"] <= last_year)]  # adults only

    by_name = pd.DataFrame({
        "n_total": df.groupby("name")["n"].sum(),
        "n_female": df[df["sex"] == "F"].groupby("name")["n"].sum(),
    }).fillna(0)
    by_name = by_name[by_name["n_total"] >= MIN_NAME_COUNT].copy()
    by_name["p_female"] = (by_name["n_female"] / by_name["n_total"]).round(4)

    for bucket, (low, high) in AGE_BUCKETS.items():
        in_bucket = df[(df["year"] >= low) & (df["year"] <= high)]
        by_name[bucket] = in_bucket.groupby("name")["n"].sum()
    by_name = by_name.fillna(0)
    bucket_cols = list(AGE_BUCKETS)
    by_name[bucket_cols] = by_name[bucket_cols].div(by_name[bucket_cols].sum(axis=1), axis=0).round(4)

    out = by_name[["p_female", "n_total"] + bucket_cols].astype({"n_total": int})
    out.index.name = "name"
    OUT.mkdir(parents=True, exist_ok=True)
    out.sort_values("n_total", ascending=False).to_csv(OUT / "names.csv")
    print(f"names.csv: {len(out)} names")


def build_zips() -> None:
    gz = download(GAZETTEER_URL, RAW / "gaz_zcta.zip")
    with zipfile.ZipFile(gz) as archive:
        with archive.open(archive.namelist()[0]) as handle:
            df = pd.read_csv(io.TextIOWrapper(handle), sep="\t", dtype={"GEOID": str})
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"GEOID": "zip", "ALAND_SQMI": "land_sqmi",
                            "INTPTLAT": "lat", "INTPTLONG": "lon"})
    df["state"] = df["zip"].str[:3].map(zip3_to_state)
    df = df.dropna(subset=["state"]).copy()
    df["region"] = df["state"].map(STATE_TO_REGION).fillna("")  # PR / VI: no region
    df["timezone"] = df["zip"].str[:3].map(ZIP3_TZ_OVERRIDES).fillna(df["state"].map(STATE_TZ))
    df["urbanicity"] = df["land_sqmi"].map(urbanicity_from_area)
    columns = ["zip", "lat", "lon", "land_sqmi", "state", "region", "timezone", "urbanicity"]
    api_key = os.environ.get("CENSUS_API_KEY")
    if api_key:
        df = df.merge(fetch_acs(api_key), on="zip", how="left")
        df["density"] = (df["population"] / df["land_sqmi"].where(df["land_sqmi"] > 0)).round(1)
        df["income_tier"] = df["median_income"].map(income_tier)
        has_density = df["density"].notna()
        df.loc[has_density, "urbanicity"] = df.loc[has_density, "density"].map(urbanicity_from_density)
        columns += ["median_income", "population", "density", "income_tier"]
        print("ACS income/population added")
    else:
        print("CENSUS_API_KEY not set: skipping ACS income/population")
    out = df[columns]
    out.to_csv(OUT / "zips.csv", index=False, float_format="%.4f")
    print(f"zips.csv: {len(out)} zips")


if __name__ == "__main__":
    build_names()
    build_zips()
