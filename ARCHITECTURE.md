# Architecture & Design Decisions
### Coffee Consumption Around the World — Data Pipeline

This document explains the architecture of the pipeline: what each decision is,
*why* it was made, what alternatives existed, and why they were rejected. It is
organized as a series of Architecture Decision Records (ADRs) — the format used
to capture significant technical choices along with their trade-offs.

---

## 1. System Overview

The pipeline turns three public datasets into a queryable analytical database
answering business questions about global coffee markets.

```
  RAW FILES (immutable)
    psd_coffee.csv        (USDA — coffee supply/demand, by country-year)
    population.csv        (World Bank — population, by country-year)
    countries-codes.csv   (OpenDataSoft — ISO country codes = the "spine")
        │
        ▼
  EXTRACT      read + validate each file against a column contract
        │
        ▼
  RESOLVE      map USDA country names → ISO3 (4 tiers + hard gate)
        │
        ▼
  TRANSFORM    reshape (melt/pivot), convert units (bags→kg), one choke point
        │
        ▼
  LOAD         write into PostgreSQL, per-table transactions, truncate-reload
        │
        ▼
  SQL LAYERS   staging → core (star schema) → marts (analytics)
        │
        ▼
  SERVE        Streamlit dashboard reads marts only
```

**Guiding principles** (each expanded in its own ADR):
- The database is the single source of truth.
- Nothing enters a fact table unless it maps to a known country (allowlist).
- Every stage fails fast, fails loud, and is safe to re-run (idempotent).
- Each pattern is right-sized, with a documented path to scale.

---

## 2. ADR-01 — ELT over ETL; split work between Python and SQL

**Decision.** Light cleaning and entity resolution happen in Python (pandas).
All analytical transformation — joins, aggregations, window functions — happens
in SQL, inside PostgreSQL, *after* loading.

**Why.**
- String normalization and the human-reviewable country mapping are natural in
  pandas and awkward in SQL.
- Joins and aggregations are the database's home turf, and keeping them in SQL
  means the business logic is reviewable as plain `.sql` files.
- The brief explicitly requires transformation logic as separate `.sql` files —
  ELT satisfies this directly.

**Alternatives considered.**
- *Do everything in pandas, load final tables.* Rejected: hides all business
  logic inside Python, produces no SQL artifacts, and doesn't scale — pandas
  holds everything in memory.
- *Do everything in SQL, including name cleaning.* Rejected: string
  normalization and the iterative human-in-the-loop mapping are painful in SQL.

**Trade-off accepted.** Two languages in one pipeline. Justified because each is
used only where it is strongest.

---

## 3. ADR-02 — Star schema for the data model

**Decision.** A small star schema: one dimension (`dim_country`) and two fact
tables (`fact_coffee`, `fact_population`), joined on `iso3`.

**Why.**
- The country dimension is the *one* place naming decisions live; facts store
  only the key, never duplicated country names.
- Foreign keys from facts to the dimension make orphan records impossible.
- The shape is instantly readable — a reviewer sees the model at a glance.

**Alternatives considered.**
- *One big denormalized table* (country name, region, population, and every
  coffee measure in each row). Rejected: duplicates country attributes across
  thousands of rows, invites update anomalies, and obscures the grain.
- *Full snowflake schema* (separate continent, region, income dimensions).
  Rejected: over-engineered for this data volume; the extra joins buy nothing
  here.
- *Full Kimball machinery* — surrogate keys, slowly-changing dimensions (SCD).
  Rejected: nothing in this data changes slowly over time; ISO3 is already a
  stable natural key. Building SCD infrastructure would be ceremony without
  benefit.

**Trade-off accepted.** We call it a "star-shaped schema" — we use the pattern
(dimension + facts + FKs) but skip surrogate keys and SCDs because the data
doesn't need them. Right-sizing is the point.

---

## 4. ADR-03 — Wide fact tables (pivot attributes into columns)

**Decision.** `fact_coffee` stores one row per (country, year) with each
attribute as its own column: `production_kg`, `imports_kg`,
`domestic_consumption_kg`, etc. The raw USDA "long" format (one row per
country-year-attribute) is pivoted during transformation.

**Why.**
- The USDA attribute set is small and fixed.
- Every analysis touches several attributes at once (e.g. per-capita needs
  consumption *and* population; import-dependence needs imports *and*
  consumption). Wide makes each of these a simple SELECT instead of a self-join.

**Alternatives considered.**
- *Keep it long* — `(iso3, year, attribute, value)`. Rejected as the core model:
  every multi-attribute query becomes a self-join or pivot, and it's easy to
  accidentally SUM a rollup total (`Production`) together with its components
  (`Arabica Production` + `Robusta Production`) and double-count. (We *do* keep a
  faithful long representation in staging, so the pivot is an inspectable
  transformation, not an ingestion assumption.)

**Trade-off accepted.** Wide is less flexible if new attributes appear (a schema
change vs a new row). Acceptable because the attribute set is stable. If this
fed a generic multi-commodity platform, long with an attribute dimension would
win.

---

## 5. ADR-04 — Country resolution: name-based, 4-tier, with a hard gate

**Decision.** USDA countries are resolved to ISO3 by NAME, not by USDA's
`Country_Code`. Resolution proceeds in tiers: (1) normalize + exact match,
(2) hand-curated overrides CSV, (3) explicit exclusions, (4) a gate that FAILS
the pipeline if any name is left unresolved.

**Why.**
- USDA's `Country_Code` is FIPS 10-4, not ISO. Profiling proved a code-join
  maps only ~35/94 correctly — China→Switzerland, Nigeria→Nicaragua. It is a
  decoy and was rejected with evidence.
- The override CSV is version-controlled and carries a `note` per row, so every
  judgment call is auditable.
- The gate guarantees no country is *ever* silently dropped. A new USDA name in
  a future file stops the run and prints the offender, instead of vanishing.

**Alternatives considered.**
- *Trust USDA's code column.* Rejected — proven wrong in profiling.
- *Fuzzy string matching (edit distance) as the primary resolver.* Rejected as
  an unattended decision-maker: it maps Niger→Nigeria, Guinea→Guinea-Bissau with
  high confidence — silent corruption. (Fuzzy matching *was* used as a
  suggestion generator to pre-fill the override file for human review.)
- *Inner join and ignore misses.* Rejected outright: this is exactly the silent
  data loss the assignment is testing for.

**Trade-off accepted.** ~13 names require manual curation. At ~100 entities this
is minutes of work and buys a zero-surprise, fully auditable mapping — the
correct trade at this cardinality.

---

## 6. ADR-05 — Dimension as allowlist (enforced by foreign keys)

**Decision.** Nothing enters a fact table unless its `iso3` exists in
`dim_country`. Enforced structurally by a foreign-key constraint.

**Why.**
- The World Bank file contains ~54 aggregate rows ("World", "European Union",
  income groups) carrying real-looking ISO3-style codes. A naive load would let
  "World" appear as the largest "country."
- A filter can be forgotten; a foreign key cannot. The FK makes the aggregate
  physically un-insertable — a guarantee, not a hope.

**Alternatives considered.**
- *Filter aggregates in Python only.* Rejected as the sole defense: relies on a
  maintained exclusion list; a new aggregate slips through. (We filter *and*
  constrain — filter as first line, FK as guarantee.)

**Trade-off accepted.** Countries present in coffee data but absent from the
spine would be rejected too — but profiling confirmed the spine is a superset,
so this cannot happen, and if it did, failing loudly is the desired behavior.

---

## 7. ADR-06 — Unit conversion at a single choke point

**Decision.** The conversion from USDA's "1000 × 60 kg bags" to kilograms
happens exactly once, in the staging transformation. Every table downstream is
already in kg (signalled by the `_kg` column suffix).

**Why.**
- Scattered conversions cause version skew — one table in bags, one in kg, both
  internally plausible, silently inconsistent.
- One location means one place to test. A single semantic assertion (world
  consumption must land near the known ~10M-tonne magnitude) catches any
  conversion error immediately.

**Alternatives considered.**
- *Convert in the dashboard / in each query as needed.* Rejected: guarantees the
  factor-60 bug will eventually appear somewhere and be hard to trace.

**Trade-off accepted.** None material — this is strictly safer.

---

## 8. ADR-07 — Truncate-and-reload (idempotent), not incremental

**Decision.** Each load truncates its table and reloads it fully, inside a
transaction. Re-running the pipeline yields an identical database.

**Why.**
- Data volume is tens of thousands of rows from annual snapshot files; a full
  refresh takes seconds and guarantees consistency with zero merge logic.
- Idempotency makes "run it again" the universal, always-safe recovery
  procedure.

**Alternatives considered.**
- *Incremental upsert (MERGE / INSERT ... ON CONFLICT) with a watermark.*
  Rejected *for now* as premature: it adds real merge-logic bug surface to save
  time that doesn't need saving at this scale.

**Trade-off accepted.** Doesn't scale to large daily-volume sources. The
migration path is documented: switch to `INSERT ... ON CONFLICT DO UPDATE` keyed
on the natural key with a year watermark when volume or always-on consumers
demand it.

---

## 9. ADR-08 — Per-table transactions

**Decision.** Each table load is wrapped in its own transaction: truncate +
insert either commits whole or rolls back whole.

**Why.**
- A crash mid-load never leaves a half-populated table. Combined with
  idempotency, the recovery procedure is simply "re-run."
- Constraint violations (duplicate keys, orphan FKs) abort the transaction with
  a named error, so the database is the last line of defense against bad data.

**Alternatives considered.**
- *One giant transaction for the whole pipeline.* Rejected: longer locks, and a
  failure in the last table needlessly rolls back the first — no benefit at this
  scale.
- *No transactions (autocommit each row).* Rejected: permits partial loads.

---

## 10. ADR-09 — Schemas as layers (raw / staging / core / marts)

**Decision.** Use PostgreSQL schemas to separate pipeline layers rather than
table-name prefixes.

**Why.**
- Namespacing plus a security boundary: the read-only reviewer role can be
  granted SELECT on `core` and `marts` specifically.
- The layer of any table is unambiguous from its qualified name
  (`marts.market_scorecard` announces its maturity level).

**Alternatives considered.**
- *Prefixes (`stg_`, `core_`)* — simulate layers but don't enforce a security or
  namespace boundary. Rejected as weaker.

---

## 11. ADR-10 — Orchestration: a single entry point, not Airflow

**Decision.** A `main.py` runs stages in order (extract → resolve → load → run
SQL → checks), with per-stage logging. No workflow orchestrator.

**Why.**
- Orchestrators (Airflow, Dagster) earn their complexity through scheduling,
  retries, backfills, and cross-team dependencies — none of which exist for
  three annual files rebuilt in seconds.
- The orchestrator's *concepts* are kept: staged DAG-ordered execution, logging,
  gates, idempotent retries.

**Alternatives considered.**
- *Airflow / Dagster.* Rejected as over-engineering here. Migration path is
  clean: each stage is already a function with explicit inputs/outputs, liftable
  into an operator with little rework.

**Trade-off accepted.** No scheduling or automatic retries — acceptable for a
manually-run analytical build.

---

## 12. ADR-11 — Streamlit reads marts only

**Decision.** The dashboard queries the `marts` tables in PostgreSQL — never the
raw CSVs, never recomputing logic in Python.

**Why.**
- Proves the database is the single source of truth and closes the loop
  (raw → pipeline → db → dashboard).
- Business logic lives in one place (SQL marts); the dashboard is pure
  presentation.

**Alternatives considered.**
- *Dashboard reads CSVs directly / recomputes metrics in pandas.* Rejected:
  duplicates logic, and a number on the dashboard could then disagree with the
  database.

---

## 13. Failure Handling Summary

| Failure mode | Defense |
|---|---|
| Source file missing / renamed | Existence + column-contract check at extract; loud error |
| Delimiter / encoding surprise | Explicit `sep`/`encoding` per source (learned in profiling) |
| New unmapped country | Hard gate in resolve: run fails, prints offender |
| Aggregate row ("World") sneaks in | Dimension allowlist enforced by FK |
| Duplicate rows | PK constraint rejects them at load |
| Crash mid-load | Per-table transaction rolls back whole |
| Unit-conversion bug | Single choke point + semantic magnitude assertion |
| Missing population for a country-year | LEFT JOIN → NULL per-capita (visible), never dropped |
| DB unreachable (dashboard) | try/except → friendly message, not a stack trace |

---

## 14. What Would Change at Production Scale

Named upgrade paths (each pattern above is deliberately right-sized):
- **Orchestration:** Airflow/Dagster for scheduling, retries, alerting, backfills.
- **Loading:** incremental MERGE/upsert with a year watermark instead of
  truncate-reload.
- **Transformations:** dbt — the existing staging/core/marts layering maps
  directly onto it, adding tests, docs, and lineage for free.
- **Data quality:** Great Expectations / Soda replacing hand-rolled assertions.
- **CI:** a GitHub Action spinning up a Postgres service container, running the
  pipeline on fixture data and the assertion suite on every PR.
- **Secrets:** a managed secrets store and per-service least-privilege roles
  instead of a local `.env`.

---

## 15. Known Limitations & Assumptions

- **Market year ≈ calendar year.** USDA marketing years don't align to calendar
  years for all countries; population is calendar-year. We align market year N to
  calendar year N — second-order for multi-year trends, documented as an
  assumption.
- **Apparent consumption.** USDA "Domestic Consumption" is partly derived as a
  residual (Supply − Exports − Ending Stocks), so small transit economies can
  show distorted per-capita figures. Flagged in the risk analysis.
- **No price data.** The provided datasets have no prices, so input-cost risk
  from supply concentration is named but not quantified.
- **Yemen entities.** "Yemen (Sanaa)" (1960–1990) and "Yemen" (1991+) both map to
  YEM; verified their year ranges do not overlap, so no double-counting.

