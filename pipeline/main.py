"""
main.py — run the entire pipeline end to end, in order.
========================================================
One command rebuilds the whole database from raw files:
 
    python pipeline/main.py
 
Stages (each logged):
    1. schema   — create the core star schema (sql/01_schema.sql)
    2. load     — resolve countries, reshape, unit-convert, load (load.py)
    3. marts    — build the analytical marts (sql/02_marts.sql)
 
Idempotent: safe to re-run; each stage drops/recreates or truncates cleanly.
Fails fast: any stage error stops the run with a clear message.
"""
import sys
import time
from pathlib import Path
 
from sqlalchemy import text
 
# import the pieces we already built
sys.path.insert(0, str(Path(__file__).resolve().parent))
from load import engine, main as run_load   # noqa: E402
 
ROOT = Path(__file__).resolve().parent.parent
SQL  = ROOT / "sql"
 
 
def run_sql_file(path: Path):
    """Execute one .sql file inside a transaction."""
    with engine.begin() as conn:
        conn.execute(text(path.read_text()))
 
 
def stage(name: str, fn):
    print(f"\n{'='*60}\nSTAGE: {name}\n{'='*60}")
    t0 = time.time()
    fn()
    print(f"  -> {name} done in {time.time()-t0:.1f}s")
 
 
def main():
    print("Starting full pipeline rebuild...")
    stage("1/3 schema", lambda: run_sql_file(SQL / "01_schema.sql"))
    stage("2/3 load",   run_load)
    stage("3/3 marts",  lambda: run_sql_file(SQL / "02_marts.sql"))
 
    # quick smoke test so a green run means the data is really there
    with engine.connect() as c:
        counts = {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                  for t in ["core.dim_country", "core.fact_coffee",
                            "core.fact_population", "marts.market_scorecard"]}
    print(f"\n{'='*60}\nPIPELINE COMPLETE\n{'='*60}")
    for t, n in counts.items():
        print(f"  {t:28s} {n:>6} rows")
 
 
if __name__ == "__main__":
    main()
 
