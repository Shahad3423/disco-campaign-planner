"""Build data/reference/ruca.csv (zip -> 2020 RUCA code) from USDA ERS.

Source: USDA Economic Research Service, 2020 Rural-Urban Commuting Area Codes, ZIP code file
https://www.ers.usda.gov/data-products/rural-urban-commuting-area-codes/ (public domain, no key).
RUCA: 1 metro core, 2-3 metro high/low commuting (suburban/exurban), 4-6 micropolitan,
7-9 small town, 10 rural.
"""
import csv
import io
import urllib.request
from pathlib import Path

URL = "https://www.ers.usda.gov/media/5444/2020-rural-urban-commuting-area-codes-zip-codes.csv?v=57102"
OUT = Path(__file__).resolve().parents[1] / "data" / "reference" / "ruca.csv"

with urllib.request.urlopen(URL, timeout=60) as resp:
    rows = list(csv.DictReader(io.StringIO(resp.read().decode("utf-8-sig"))))
with OUT.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["zip", "ruca"])
    for r in rows:
        code = r["PrimaryRUCA"].strip()
        if code and code not in ("99",):
            w.writerow([r["ZIPCode"].zfill(5), int(float(code))])
print(f"wrote {OUT} ({len(rows)} source rows)")
