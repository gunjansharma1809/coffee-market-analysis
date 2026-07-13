"""
resolve.py — Country entity resolution
=======================================
The heart of this pipeline. Maps every USDA coffee country NAME to a canonical
ISO3 code, so all three data sources can join through a single key.

Why by name and not by code? USDA's Country_Code column is FIPS 10-4, not ISO.
Joining on it maps China->Switzerland, Nigeria->Nicaragua (proven in profiling).
So we reject that column and resolve names instead, in four tiers:

    Tier 1  normalize + exact match against the OpenDataSoft spine  (~81/94)
    Tier 2  apply hand-curated overrides from mappings/country_overrides.csv
    Tier 3  apply exclusions (aggregates like "European Union")
    Tier 4  THE GATE: if any USDA name is still unresolved, FAIL LOUDLY

The gate guarantees no country is ever silently dropped. If a future USDA file
introduces a new name, the run stops and prints it, instead of losing data.

Output: a DataFrame  (usda_name, iso3)  for all mappable countries,
plus a printed reconciliation report.
"""

import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

# --- paths (relative to project root) -------------------------------------
ROOT      = Path(__file__).resolve().parent.parent
RAW       = ROOT / "data" / "raw"
OVERRIDES = ROOT / "mappings" / "country_overrides.csv"


def normalize(name: str) -> str:
    """Lowercase, strip accents & punctuation, collapse whitespace.
    Makes matching robust to trivial formatting differences, e.g.
    'Côte d'Ivoire' and 'Cote d Ivoire' normalize to the same string."""
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def load_sources():
    """Load the two files we need for resolution: USDA names and the spine."""
    coffee = pd.read_csv(RAW / "psd_coffee.csv")
    codes  = pd.read_csv(RAW / "countries-codes.csv", sep=";")
    # Standardize the spine's column names (this export uses UPPERCASE + spaces)
    codes = codes.rename(columns={
        "ISO2 CODE": "iso2_code",
        "ISO3 CODE": "iso3_code",
        "LABEL EN":  "label_en",
    })
    # Guard: fail early if expected columns are missing (a "column contract")
    for col in ("iso3_code", "label_en"):
        if col not in codes.columns:
            sys.exit(f"FATAL: expected column '{col}' not found in countries-codes.csv")
    return coffee, codes


def resolve_countries() -> pd.DataFrame:
    coffee, codes = load_sources()

    # Build the spine lookup: normalized English name -> ISO3
    codes["norm"] = codes["label_en"].map(normalize)
    spine = dict(zip(codes["norm"], codes["iso3_code"]))
    valid_iso3 = set(codes["iso3_code"].dropna())

    usda_names = sorted(coffee["Country_Name"].unique())

    # Load overrides (hand-curated decisions)
    ov = pd.read_csv(OVERRIDES)
    override_map = {}   # usda_name -> iso3
    exclude_set  = set()  # usda_names to drop deliberately
    for _, r in ov.iterrows():
        if r["action"] == "map":
            override_map[r["usda_name"]] = r["iso3"]
        elif r["action"] == "exclude":
            exclude_set.add(r["usda_name"])

    # --- resolve each name through the tiers --------------------------------
    resolved   = {}   # usda_name -> iso3
    excluded   = []
    unresolved = []
    tier1 = tier2 = 0

    for name in usda_names:
        if name in exclude_set:                       # Tier 3: exclusions
            excluded.append(name)
            continue
        if normalize(name) in spine:                  # Tier 1: exact match
            resolved[name] = spine[normalize(name)]
            tier1 += 1
        elif name in override_map:                    # Tier 2: overrides
            iso3 = override_map[name]
            if iso3 not in valid_iso3:
                unresolved.append(f"{name} (override -> {iso3}, but {iso3} not in spine!)")
                continue
            resolved[name] = iso3
            tier2 += 1
        else:                                         # fell through all tiers
            unresolved.append(name)

    # --- Tier 4: THE GATE ---------------------------------------------------
    print("=" * 60)
    print("COUNTRY RESOLUTION — RECONCILIATION REPORT")
    print("=" * 60)
    print(f"  USDA countries in file:      {len(usda_names)}")
    print(f"  Tier 1 exact-matched:        {tier1}")
    print(f"  Tier 2 override-mapped:      {tier2}")
    print(f"  Tier 3 excluded (aggregate): {len(excluded)}  {excluded}")
    print(f"  Unresolved:                  {len(unresolved)}")
    print("=" * 60)

    if unresolved:
        print("\nFATAL: the following USDA names could not be resolved.")
        print("Add a row for each to mappings/country_overrides.csv, then re-run:")
        for n in unresolved:
            print("   -", n)
        sys.exit(1)   # stop the pipeline — never drop silently

    print(f"\nAll {len(resolved)} mappable countries resolved cleanly.\n")

    return pd.DataFrame(
        [(name, iso3) for name, iso3 in resolved.items()],
        columns=["usda_name", "iso3"],
    )


if __name__ == "__main__":
    df = resolve_countries()
    print(df.head(10).to_string(index=False))
    print(f"\n... {len(df)} rows total")
