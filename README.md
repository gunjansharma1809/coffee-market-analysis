# Coffee Consumption Around the World
### A PostgreSQL data pipeline & market-entry analysis for ACME Baristas
 
Joins three public datasets (USDA coffee supply/demand, World Bank population,
OpenDataSoft country codes) into a clean PostgreSQL star schema, transforms them
through layered SQL, and presents a market-entry recommendation via an
interactive Streamlit dashboard.
 
---
 
## Architecture at a glance
 
```
  RAW FILES (immutable)          data/raw/
      │
      ▼
  RESOLVE   country names → ISO3, 4 tiers + hard gate   pipeline/resolve.py
      │
      ▼
  LOAD      reshape + unit-convert + transactional load  pipeline/load.py
      │                                                   sql/01_schema.sql
      ▼
  SQL       core star schema → analytical marts          sql/02_marts.sql
      │
      ▼
  SERVE     Streamlit reads marts only                   dashboard/app.py
```
 
- **Star schema:** `core.dim_country` (dimension) + `core.fact_coffee`,
  `core.fact_population` (facts), joined on `iso3`.
- **Marts:** per-capita consumption, 5-yr CAGR, per-capita growth, market
  scorecard, global trend.
- **Single source of truth:** the dashboard queries `marts.*` only — never the
  raw files.
 
See `ARCHITECTURE.md` for the full set of design-decision records (ADRs) and
`ISSUES_LOG.md` for the engineering journal of bugs and resolutions.
 
---
 
## How to run
 
### Prerequisites
- Python 3.10+
- A PostgreSQL database (this project uses a free Neon instance)
 
### Setup
```bash
# 1. clone and enter
git clone <repo-url> && cd coffee-market-analysis
 
# 2. environment
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
 
# 3. configure the database connection
cp .env.example .env
#    then edit .env and set DATABASE_URL to your PostgreSQL connection string
```
 
### Get the data
Place the three source files in `data/raw/`:
- `psd_coffee.csv` — USDA PSD coffee (https://apps.fas.usda.gov/psdonline/app/index.html#/app/downloads)
- `population.csv` — World Bank SP.POP.TOTL (https://data.worldbank.org/indicator/SP.POP.TOTL)
- `countries-codes.csv` — OpenDataSoft (https://public.opendatasoft.com/explore/dataset/countries-codes/)
 
*(Data as of July 2026. Sources revise periodically.)*
 
### Run the pipeline
```bash
python pipeline/run_sql.py sql/01_schema.sql   # create the schema
python pipeline/load.py                         # resolve, reshape, load
python pipeline/run_sql.py sql/02_marts.sql     # build the analytical marts
```
 
### Launch the dashboard
```bash
streamlit run dashboard/app.py
# macOS note: if it segfaults, add  --server.fileWatcherType none
```
 
### Read-only database access (for reviewers)
A read-only `reviewer` role is provisioned (see `sql/03_reviewer_role.sql`).
The read-only connection string is provided separately with the submission.
 
---
 
## Reflection
 
### Walk us through your key design choices
 
The pipeline is **ELT-leaning**: Python handles only what it is uniquely good at
— file parsing and the human-reviewable country mapping — while all analytical
logic (joins, aggregations, window functions) lives in layered SQL inside
PostgreSQL, satisfying the brief's requirement for `.sql` transformation files
and keeping business logic auditable.
 
The data model is a **star schema**: one country dimension, two fact tables,
joined on ISO3. Facts store only keys and measures; the dimension is the single
source of country attributes. Foreign keys make orphan records impossible.
 
The **country resolution** is the centrepiece. USDA's `Country_Code` is FIPS,
not ISO — profiling proved a code-join maps only ~35/94 correctly (China→
Switzerland), so it was rejected in favour of name-based resolution in four
tiers: normalize + exact match (81), a version-controlled overrides CSV (12), an
explicit exclusion for the "European Union" aggregate (1), and a **hard gate**
that fails the pipeline if any name is unresolved — so no country is ever
silently dropped.
 
Unit conversion (1000×60kg bags → kg) happens at exactly **one choke point** in
the load, guarded by a semantic magnitude check. Loads are **idempotent**
(truncate-and-reload inside per-table transactions), so re-running is always a
safe recovery.
 
### What challenges did you face and how did you overcome them?
 
The recurring theme was **unstable source formatting**, especially the
country-codes file. It broke ingestion three times: a `ParserError` (the file is
semicolon-delimited, because a coordinate column is full of commas); a `KeyError`
(columns are UPPERCASE with spaces, not lowercase); and the same naming
instability resurfacing in pipeline code when tested against a differently-named
export. Each was resolved by making ingestion defensive — explicit delimiter,
and a header-normalizing loader that accepts any style and fails loudly if the
ISO3 column is missing. (Full log in `ISSUES_LOG.md`.)
 
The most instructive challenge was analytical, not technical: my first "top 3
markets" was **judgment disguised as data** — a hard-coded shortlist that
silently dropped higher-growth countries. I rebuilt it as a **tunable scorecard**
(weight sliders on the dashboard) and reframed it as a sensitivity analysis,
which surfaced a genuine finding: China ranks top-3 under *every* weighting
strategy, making it the pick most robust to ACME's risk appetite.
 
### What assumptions did you make?
 
- **Market year ≈ calendar year.** USDA marketing years don't align to calendar
  years for all countries; population is calendar-year. Aligning market year N to
  calendar year N is second-order for the multi-year trends the recommendations
  rest on.
- **Apparent consumption.** USDA "Domestic Consumption" is derived as a residual
  for some countries, so per-capita can be distorted for small transit economies.
- **Yemen entities.** "Yemen (Sanaa)" (1960–1990) and "Yemen" (1991+) both map to
  ISO3 YEM; verified their year ranges do not overlap, so no double-counting.
- **European Union excluded** as a non-country aggregate.
 
### If you had more time, what would you have done differently?
 
- **Dockerize** with a compose file so setup is a single command.
- Port the SQL layer to **dbt** — the staging/core/marts layering maps directly,
  adding tests, docs, and lineage for free.
- Add a **CI pipeline** (GitHub Action) spinning up Postgres and running the
  assertion suite on every push.
- Compute CAGR against an explicit **year offset** rather than a row offset, for
  full robustness to any gaps in the year sequence.
- Add a **backtest**: would the scorecard, run on data as of five years ago, have
  picked markets that then grew?
 
### What additional data would have strengthened your insights?
 
- **Coffee prices** (e.g. ICO) — the biggest blind spot; without them,
  input-cost risk from supply concentration is named but not quantified.
- **Channel/format split** — retail vs café vs at-home consumption, to
  distinguish markets ripe for a *chain* from home-brewing markets.
- **Income / GDP-per-capita** — to segment markets by purchasing power (partly
  available in the unused World Bank metadata).
- **Competitor footprint** — existing café density, which the current data cannot
  see at all.
 
