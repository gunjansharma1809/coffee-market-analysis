-- ============================================================================
-- 02_marts.sql — Analytical tables answering the ACME business questions
-- ============================================================================
-- Reads core.* (star schema), produces marts.* that the dashboard queries.
-- Idempotent: drops and recreates the marts schema each run.
-- ============================================================================

DROP SCHEMA IF EXISTS marts CASCADE;
CREATE SCHEMA marts;

-- ----------------------------------------------------------------------------
-- MART 1: consumption per capita, per country-year
-- LEFT JOIN from coffee so missing population -> NULL per-capita (never dropped)
-- ----------------------------------------------------------------------------
CREATE TABLE marts.consumption_per_capita AS
SELECT
    d.iso3,
    d.country_name,
    d.continent,
    f.market_year AS year,
    f.domestic_consumption_kg,
    p.population,
    CASE WHEN p.population > 0
         THEN f.domestic_consumption_kg / p.population
         ELSE NULL END AS kg_per_capita
FROM core.fact_coffee f
JOIN core.dim_country d       ON d.iso3 = f.iso3
LEFT JOIN core.fact_population p ON p.iso3 = f.iso3 AND p.year = f.market_year;

-- ----------------------------------------------------------------------------
-- MART 2: market growth — 5-year CAGR of domestic consumption per country
-- Uses LAG over the year window. Guards divide-by-zero and requires both endpoints.
-- ----------------------------------------------------------------------------
CREATE TABLE marts.market_growth AS
WITH c AS (
    SELECT iso3, market_year AS year, domestic_consumption_kg AS consumption
    FROM core.fact_coffee
),
lagged AS (
    SELECT iso3, year, consumption,
           LAG(consumption, 5) OVER (PARTITION BY iso3 ORDER BY year) AS consumption_5y_ago
    FROM c
)
SELECT iso3, year, consumption, consumption_5y_ago,
       CASE
         WHEN consumption_5y_ago IS NULL OR consumption_5y_ago <= 0 THEN NULL
         ELSE power(consumption / consumption_5y_ago, 1.0/5) - 1
       END AS cagr_5y
FROM lagged;

-- ----------------------------------------------------------------------------
-- MART 3: market scorecard — the ACME shortlist inputs, latest year per country
-- Combines size, growth, per-capita headroom, population. Transparent inputs.
-- ----------------------------------------------------------------------------
CREATE TABLE marts.market_scorecard AS
WITH latest AS (   -- latest year each country has coffee data
    SELECT iso3, max(market_year) AS latest_year
    FROM core.fact_coffee
    GROUP BY iso3
),
base AS (
    SELECT
        d.iso3, d.country_name, d.continent,
        l.latest_year,
        f.domestic_consumption_kg              AS market_size_kg,
        pc.kg_per_capita,
        pc.population,
        g.cagr_5y
    FROM latest l
    JOIN core.dim_country d ON d.iso3 = l.iso3
    JOIN core.fact_coffee f ON f.iso3 = l.iso3 AND f.market_year = l.latest_year
    LEFT JOIN marts.consumption_per_capita pc ON pc.iso3 = l.iso3 AND pc.year = l.latest_year
    LEFT JOIN marts.market_growth g           ON g.iso3 = l.iso3 AND g.year = l.latest_year
)
SELECT *,
       -- simple transparent 0-1 normalized scores (min-max across countries)
       (market_size_kg - min(market_size_kg) OVER ()) /
            NULLIF(max(market_size_kg) OVER () - min(market_size_kg) OVER (), 0) AS size_score,
       (COALESCE(cagr_5y,0) - min(COALESCE(cagr_5y,0)) OVER ()) /
            NULLIF(max(COALESCE(cagr_5y,0)) OVER () - min(COALESCE(cagr_5y,0)) OVER (), 0) AS growth_score,
       (population - min(population) OVER ()) /
            NULLIF(max(population) OVER () - min(population) OVER (), 0)::numeric AS population_score
FROM base;

-- ----------------------------------------------------------------------------
-- MART 4: global timing — is coffee demand rising? (world totals per year)
-- ----------------------------------------------------------------------------
CREATE TABLE marts.global_trend AS
SELECT market_year AS year,
       sum(domestic_consumption_kg) AS world_consumption_kg,
       sum(production_kg)           AS world_production_kg
FROM core.fact_coffee
GROUP BY market_year
ORDER BY market_year;

-- ----------------------------------------------------------------------------
-- MART 5: per-capita growth — latest vs 5 years prior (TRUE demand adoption)
-- Distinguishes real per-person growth from mere population growth.
-- ----------------------------------------------------------------------------
CREATE TABLE marts.per_capita_growth AS
WITH pc AS (
    SELECT iso3, country_name, continent, year, kg_per_capita, population
    FROM marts.consumption_per_capita
    WHERE kg_per_capita IS NOT NULL
),
latest AS (SELECT iso3, max(year) AS y FROM pc GROUP BY iso3)
SELECT p2.iso3, p2.country_name, p2.continent, p2.population,
       p1.kg_per_capita AS per_capita_5y_ago,
       p2.kg_per_capita AS per_capita_now,
       CASE WHEN p1.kg_per_capita > 0
            THEN (p2.kg_per_capita / p1.kg_per_capita) - 1
            ELSE NULL END AS per_capita_growth_5y
FROM latest l
JOIN pc p2 ON p2.iso3 = l.iso3 AND p2.year = l.y
LEFT JOIN pc p1 ON p1.iso3 = l.iso3 AND p1.year = l.y - 5;
