"""
load.py — Transform the raw files and load them into PostgreSQL.
================================================================
Pipeline stage that fills the empty tables created by 01_schema.sql.

Steps:
  1. Resolve USDA country names -> ISO3 (reuses resolve.py, incl. the gate)
  2. Build dim_country from the spine (only countries that appear in our data)
  3. Reshape + convert coffee: long -> wide, 1000x60kg bags -> kilograms
  4. Reshape population: wide (year columns) -> long (iso3, year, population)
  5. Load all three into Postgres, one transaction per table (truncate+insert)

Design notes:
  - Unit conversion happens ONCE here (the single choke point). 1 bag = 60 kg,
    values are in thousands of bags, so multiply by 1000 * 60 = 60000.
  - Load order respects FKs: dim_country first, then the facts.
  - Idempotent: truncates each table before loading, so re-running is safe.
"""

import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

from resolve import resolve_countries, normalize   # reuse the resolver + normalizer

load_dotenv()
ROOT   = Path(__file__).resolve().parent.parent
RAW    = ROOT / "data" / "raw"
engine = create_engine(os.getenv("DATABASE_URL"))

BAGS_TO_KG = 1000 * 60   # values are in "1000 60-KG BAGS"

# Map USDA attribute names -> our clean column names (only the ones we keep)
COFFEE_ATTRS = {
    "Production":           "production_kg",
    "Imports":              "imports_kg",
    "Exports":              "exports_kg",
    "Domestic Consumption": "domestic_consumption_kg",
    "Beginning Stocks":     "beginning_stocks_kg",
    "Ending Stocks":        "ending_stocks_kg",
}


def _load_spine() -> pd.DataFrame:
    """Load the country-codes spine, normalizing column names to a canonical set.
    Handles both export styles seen in the wild: 'ISO3 CODE' and 'iso3_code'."""
    codes = pd.read_csv(RAW / "countries-codes.csv", sep=";")
    # normalize headers: upper, strip, collapse spaces/underscores -> canonical
    canon = {}
    for c in codes.columns:
        key = c.strip().upper().replace("_", " ")
        canon[c] = {
            "ISO2 CODE": "iso2", "ISO2CODE": "iso2",
            "ISO3 CODE": "iso3", "ISO3CODE": "iso3",
            "LABEL EN":  "country_name",
            "CONTINENT": "continent", "REGION": "region",
        }.get(key, c)
    codes = codes.rename(columns=canon)
    if "iso3" not in codes.columns:
        raise SystemExit("FATAL: could not find an ISO3 column in countries-codes.csv")
    return codes


def build_dim_country(resolved: pd.DataFrame) -> pd.DataFrame:
    """dim_country = the spine, filtered to iso3 codes that appear in our data."""
    codes = _load_spine()
    keep_iso3 = set(resolved["iso3"])
    dim = codes[codes["iso3"].isin(keep_iso3)].copy()
    # keep only the columns the table has (continent/region may not exist in export)
    for col in ("continent", "region"):
        if col not in dim.columns:
            dim[col] = None
    dim = dim[["iso3", "country_name", "iso2", "continent", "region"]].drop_duplicates("iso3")
    return dim


def build_fact_coffee(resolved: pd.DataFrame) -> pd.DataFrame:
    """Long USDA -> wide per (iso3, market_year), converted to kg."""
    coffee = pd.read_csv(RAW / "psd_coffee.csv")
    name_to_iso3 = dict(zip(resolved["usda_name"], resolved["iso3"]))

    coffee = coffee[coffee["Country_Name"].isin(name_to_iso3)].copy()
    coffee["iso3"] = coffee["Country_Name"].map(name_to_iso3)
    coffee = coffee[coffee["Attribute_Description"].isin(COFFEE_ATTRS)]

    wide = coffee.pivot_table(
        index=["iso3", "Market_Year"],
        columns="Attribute_Description",
        values="Value",
        aggfunc="sum",
    ).reset_index().rename(columns={"Market_Year": "market_year"})

    wide = wide.rename(columns=COFFEE_ATTRS)
    # convert every measure column from 1000-60kg-bags to kg (the ONE choke point)
    for col in COFFEE_ATTRS.values():
        if col in wide.columns:
            wide[col] = wide[col] * BAGS_TO_KG

    keep = ["iso3", "market_year"] + [c for c in COFFEE_ATTRS.values() if c in wide.columns]
    return wide[keep]


def build_fact_population(resolved: pd.DataFrame) -> pd.DataFrame:
    """Wide World Bank -> long (iso3, year, population), country rows only."""
    pop = pd.read_csv(RAW / "population.csv", skiprows=4)
    keep_iso3 = set(resolved["iso3"])
    pop = pop[pop["Country Code"].isin(keep_iso3)].copy()

    year_cols = [c for c in pop.columns if c.strip().isdigit()]
    long = pop.melt(
        id_vars=["Country Code"], value_vars=year_cols,
        var_name="year", value_name="population",
    ).rename(columns={"Country Code": "iso3"})

    long["year"] = long["year"].astype(int)
    long = long.dropna(subset=["population"])
    long["population"] = long["population"].astype("int64")
    return long[["iso3", "year", "population"]]


def load_table(df: pd.DataFrame, table: str):
    """Truncate + insert inside one transaction (idempotent, atomic)."""
    schema, name = table.split(".")
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {table} CASCADE;"))
        df.to_sql(name, conn, schema=schema, if_exists="append", index=False)
    print(f"   loaded {len(df):>6} rows into {table}")


def main():
    print("Resolving countries...")
    resolved = resolve_countries()          # runs the gate; exits if unresolved

    print("Building tables...")
    dim  = build_dim_country(resolved)
    cof  = build_fact_coffee(resolved)
    pop  = build_fact_population(resolved)

    print("Loading (dimension first, then facts)...")
    load_table(dim, "core.dim_country")     # FK parent first
    load_table(cof, "core.fact_coffee")
    load_table(pop, "core.fact_population")
    print("Load complete.")


if __name__ == "__main__":
    main()
