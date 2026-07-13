-- ============================================================================
-- 01_schema.sql — Data model (the "clean PostgreSQL data model" the brief asks for)
-- ============================================================================
-- A small STAR SCHEMA: one dimension (countries) + two fact tables (coffee, pop).
-- Everything joins through iso3. Constraints make bad data impossible to load.
--
-- Layers as schemas:  core = the star schema.  (marts come later in phase 4.)
-- Idempotent: safe to re-run — it drops and recreates cleanly every time.
-- ============================================================================

DROP SCHEMA IF EXISTS core CASCADE;
CREATE SCHEMA core;

-- ----------------------------------------------------------------------------
-- DIMENSION: one row per country. The single source of truth for "what is a
-- country" — the allowlist that facts must join against.
-- ----------------------------------------------------------------------------
CREATE TABLE core.dim_country (
    iso3          CHAR(3)      PRIMARY KEY,          -- canonical key, e.g. 'BRA'
    country_name  TEXT         NOT NULL,             -- display name
    iso2          CHAR(2),                           -- 2-letter code (nullable: some spine rows lack it)
    continent     TEXT,
    region        TEXT
);

-- ----------------------------------------------------------------------------
-- FACT: coffee. One row per country per market year. Attributes are PIVOTED
-- into columns (wide) because the set is small and fixed. All values in KG.
-- ----------------------------------------------------------------------------
CREATE TABLE core.fact_coffee (
    iso3                 CHAR(3)   NOT NULL,
    market_year          SMALLINT  NOT NULL,
    production_kg        NUMERIC,
    imports_kg           NUMERIC,
    exports_kg           NUMERIC,
    domestic_consumption_kg  NUMERIC,
    beginning_stocks_kg  NUMERIC,
    ending_stocks_kg     NUMERIC,

    -- grain: exactly one row per (country, year)
    PRIMARY KEY (iso3, market_year),

    -- every coffee row must reference a real country (dimension allowlist)
    FOREIGN KEY (iso3) REFERENCES core.dim_country (iso3),

    -- sanity guards: years plausible, quantities never negative
    CHECK (market_year BETWEEN 1960 AND 2035),
    CHECK (production_kg IS NULL OR production_kg >= 0),
    CHECK (domestic_consumption_kg IS NULL OR domestic_consumption_kg >= 0)
);

-- ----------------------------------------------------------------------------
-- FACT: population. One row per country per calendar year.
-- ----------------------------------------------------------------------------
CREATE TABLE core.fact_population (
    iso3        CHAR(3)   NOT NULL,
    year        SMALLINT  NOT NULL,
    population  BIGINT,                              -- BIGINT: populations exceed INT range

    PRIMARY KEY (iso3, year),
    FOREIGN KEY (iso3) REFERENCES core.dim_country (iso3),
    CHECK (year BETWEEN 1960 AND 2035),
    CHECK (population IS NULL OR population >= 0)
);
