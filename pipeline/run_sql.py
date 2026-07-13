"""
run_sql.py — execute .sql files against the database, in order.
Usage:  python pipeline/run_sql.py sql/01_schema.sql
        python pipeline/run_sql.py            (runs all sql/*.sql in sorted order)
"""
import os, sys, glob
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
engine = create_engine(os.getenv("DATABASE_URL"))

# which files to run: an argument, or all sql/*.sql sorted by name
args = sys.argv[1:]
files = [Path(a) for a in args] if args else sorted((ROOT / "sql").glob("*.sql"))

for f in files:
    sql = Path(f).read_text()
    print(f"--- running {f} ---")
    with engine.begin() as conn:          # begin() = one transaction, auto-commit or rollback
        conn.execute(text(sql))
    print(f"    OK")

print("done.")
