# Issues & Resolutions Log
### Coffee Consumption Pipeline — Engineering Journal

A running record of every bug, data quirk, and design snag encountered while
building this pipeline, why it happened, and how it was resolved. Kept as
evidence of process and as the basis for the "what challenges did you face?"
reflection.

Format per entry: **Symptom → Root cause → Resolution → Lesson.**

---

## #1 — `.DS_Store` files committed to git

**Phase:** Setup
**Symptom.** The first `git commit` included `.DS_Store` and `data/.DS_Store` in
the file list.
**Root cause.** macOS Finder silently creates `.DS_Store` files in every folder
it opens (to remember icon positions). The initial `.gitignore` didn't list
them, so `git add .` swept them in.
**Resolution.** Added `.DS_Store` to `.gitignore`, then ran
`git rm -r --cached .` to untrack everything and re-added with the updated rules.
The junk files remain on disk (harmless) but are no longer tracked.
**Lesson.** On macOS, always add `.DS_Store` to `.gitignore` from the very first
commit. Inspect what a commit actually contains (`git ls-files`) rather than
assuming.

---

## #2 — `ParserError: Expected N fields, saw M` on the country-codes file

**Phase:** Profiling
**Symptom.** `pd.read_csv("countries-codes.csv")` failed with
`ParserError: Error tokenizing data. C error: Expected 3132 fields in line 3,
saw 3288`.
**Root cause.** The file is **semicolon-delimited**, not comma-delimited. It
contains a `geo_shape` column full of geographic coordinates that themselves
contain thousands of commas. With the default comma separator, pandas counted
those internal commas as column boundaries, so different rows appeared to have
different column counts.
**Resolution.** Loaded with `sep=";"`. Semicolons cleanly separate columns while
the commas inside coordinate strings stay harmlessly inside their cell.
**Lesson.** "CSV" does not guarantee commas. European open-data platforms
(OpenDataSoft) and any file with comma-heavy content commonly use semicolons.
Always inspect the raw first line and set the delimiter explicitly rather than
letting pandas guess.

---

## #3 — `KeyError: 'iso2_code'` after fixing the delimiter

**Phase:** Profiling
**Symptom.** Cells referencing `iso2_code` / `iso3_code` / `label_en` failed with
`KeyError`.
**Root cause.** The OpenDataSoft export names its columns in **UPPERCASE with
spaces** (`ISO2 CODE`, `ISO3 CODE`, `LABEL EN`), not the lowercase-underscore
names the code assumed. Column naming is not stable across exports of the same
dataset.
**Resolution.** Renamed the columns to canonical lowercase names immediately
after loading, so every downstream reference is consistent.
**Lesson.** Public-source column names are unstable. Normalize/rename columns at
the point of ingestion and validate that expected columns exist ("column
contract"), failing loudly if one is missing.

---

## #4 — `KeyError: 'iso3'` in `build_dim_country` (caught in testing)

**Phase:** Load (Phase 3)
**Symptom.** `load.py` crashed at `codes[codes["iso3"]...]` with `KeyError:
'iso3'` when tested against a country-codes file that used lowercase column
names.
**Root cause.** The first version of `build_dim_country` only renamed the
UPPERCASE style (`ISO3 CODE` -> `iso3`). When the file instead used
`iso3_code`, no rename matched, so the `iso3` column never existed. This is the
same instability as issue #3, resurfacing in pipeline code because the fix in
the notebook hadn't been generalized.
**Resolution.** Replaced the brittle rename with a `_load_spine()` helper that
normalizes ANY header style (uppercase-with-spaces OR lowercase-with-underscores)
to a canonical set, and raises a clear fatal error if no ISO3 column can be
found at all.
**Lesson.** A quirk fixed once in exploration must be generalized in production
code. Testing against a differently-formatted file exposed a latent assumption
that the notebook had masked. This is exactly why testing against real (and
varied) data matters more than code that merely looks correct.

---

## #5 — CAGR window function: LAG counts rows, not years

**Phase:** Marts (Phase 4)
**Symptom.** Risk of a subtly-wrong 5-year growth rate if a country has gaps in
its year sequence.
**Root cause.** `LAG(consumption, 5)` steps back 5 *rows*, not 5 *years*. If a
country is missing a year, "5 rows back" is not "5 years back," silently
producing a wrong CAGR span.
**Resolution.** The fact table is dense per country (annual rows), and the CASE
guard requires a non-null, positive 5-years-prior value before computing CAGR;
divide-by-zero is guarded with the endpoint check. For full safety at scale the
next step is to compute the lag against an explicit year offset (self-join on
year = year-5) rather than a row offset.
**Lesson.** Window offsets are positional. When the intent is temporal, either
guarantee dense rows or join on the explicit time key. A classic silent-bug trap.

---

## #6 — World consumption magnitude lower than headline global figure

**Phase:** Verification
**Symptom.** Summed world consumption (~7.25M tonnes for 2020) is below the
commonly cited ~9-10M tonnes.
**Root cause.** Not a bug. USDA reports the European Union as a single aggregate
row, which we deliberately EXCLUDE (it's not a country and would break
per-capita joins). EU consumption is therefore absent from the country-level
sum. The magnitude is otherwise correct — confirming no unit-conversion error.
**Resolution.** Documented as expected behavior. The magnitude check still
serves its purpose (catching factor-of-60 unit bugs); the ~25% gap is the known
EU-aggregate exclusion, noted in the assumptions.
**Lesson.** A sanity check failing your first expectation isn't always a bug —
trace it to a known modeling decision before "fixing" it. Distinguish a real
anomaly from a documented exclusion.

## #7 — "Top 3 markets" was judgment disguised as data

**Phase:** Analysis / Dashboard (Phase 5)
**Symptom.** An initial hard-coded "top 3" (Vietnam/Turkey/Egypt) presented as if
the data determined it, when in fact unstated judgment picked and dropped
countries (e.g. silently excluding Uganda despite its higher growth).
**Root cause.** A composite score's weights ARE a strategy. A size-biased weight
vector returns China/India/US; a growth-biased vector returns
Vietnam/Turkey/Egypt. Hard-coding one answer hides that the choice of weights —
not the data alone — drives the shortlist.
**Resolution.** Replaced the hard-coded shortlist with a TUNABLE scorecard on the
dashboard: sliders for growth / headroom / size / population weights, recomputing
the ranking live. Reframed the recommendation as a sensitivity analysis. Verified
across three weightings that **China ranks top-3 under all of them** — a genuine
robustness finding surfaced only by varying the weights.
**Lesson.** Analytics informs a decision; it does not make it. Make the judgment
(the weights) explicit and tunable rather than baking one opinion into a single
number. Robustness across weightings is itself the strongest insight.

