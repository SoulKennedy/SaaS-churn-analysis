"""
Creates the churn_project database and loads the CSVs from ./data

Setup: pip install pandas sqlalchemy pymysql
Run:   python load_data.py   (after generate_data.py)
"""

import os
from getpass import getpass
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text

BASE = Path(__file__).resolve().parent
HOST, PORT, USER, DATABASE = "localhost", 3306, "root", "churn_project"
PASSWORD = os.getenv("MYSQL_PASSWORD") or getpass("MySQL password: ")
url = f"mysql+pymysql://{USER}:{quote_plus(PASSWORD)}@{HOST}:{PORT}"

# 1. Create database and tables with proper column types
server = create_engine(url)
statements = [s.strip() for s in (BASE / "schema.sql").read_text().split(";") if s.strip()]
with server.begin() as conn:
    for stmt in statements:
        conn.execute(text(stmt))

# 2. Load the CSVs (parents first)
engine = create_engine(f"{url}/{DATABASE}")
for table in ["campaigns", "leads", "customers", "monthly_activity"]:
    df = pd.read_csv(BASE / "data" / f"{table}.csv")
    df.to_sql(table, engine, if_exists="append", index=False, chunksize=1000)
    print(f"{table:<17} {len(df):>7,} rows loaded")
