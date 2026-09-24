"""Build var/seed.db (schema + deterministic fake members) and copy it to var/live.db.

Run: uv run python seed/seed.py
"""
import os
import shutil
import sqlite3
from pathlib import Path

VAR = Path(os.environ.get("KVFCU_VAR_DIR", Path(__file__).resolve().parent.parent / "var"))

SCHEMA = """
create table members(
  mem_no text primary key, first_name text, last_name text, ssn text, dob text,
  phone text, street text, city text, state text, zip text, member_since text);
create table accounts(
  acct_no text primary key, mem_no text not null references members, acct_type text not null,
  balance_cents integer not null, status text not null, opened_on text not null, closed_on text,
  funding_source text, nominee text, notes text, branch_code text,
  conf_no text, closure_no text, close_reason text);
create table seq(name text primary key, value integer not null);
create table sessions(sid text primary key, username text, role text, last_seen real, data text);
create table logins(username text primary key, last_at text);
"""

# (first, last, street, city, zip)
VALID = [
    ("Harold", "Brenneman", "114 Quarry Ridge Rd", "Millbrook", "17901"),
    ("Doris", "Kettleworth", "27 Old Tannery Ln", "Ashvale", "17922"),
    ("Marcus", "Oyelaran", "3810 Birchfold Ave", "Keystone Falls", "17960"),
]
AT_LIMIT = [
    ("Eugene", "Stoltzfaber", "9 Covered Bridge Way", "Millbrook", "17901"),
]
MISSING = ["999001"]  # member numbers guaranteed not to exist

SUB_TYPES = ["Share Savings", "Money Market", "Share Certificate"]


def member_numbers():
    """(valid, at_limit) member numbers, in seed order."""
    nums = [f"{100100 + (i + 1) * 7:06d}" for i in range(len(VALID) + len(AT_LIMIT))]
    return nums[: len(VALID)], nums[len(VALID):]


def build(var=VAR):
    var.mkdir(parents=True, exist_ok=True)
    path = var / "seed.db"
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    acct = 400100000000
    valid, limit = member_numbers()
    for i, ((first, last, street, city, zip_), mem_no) in enumerate(zip(VALID + AT_LIMIT, valid + limit)):
        n = i + 1
        con.execute(
            "insert into members values (?,?,?,?,?,?,?,?,?,?,?)",
            (mem_no, first, last, f"9{n:02d}-{40 + n:02d}-{1000 + n * 37:04d}",
             f"{1950 + n * 2}-{n % 12 + 1:02d}-{n * 3 % 28 + 1:02d}", f"(570) 555-{100 + n:04d}",
             street, city, "PA", zip_, f"{1995 + n}-03-01"),
        )
        subs = SUB_TYPES if mem_no in limit else SUB_TYPES[: n % 2]  # at-limit members hold 3 open subs
        for j, t in enumerate(["Primary Savings", *subs]):
            acct += 1
            con.execute(
                "insert into accounts (acct_no, mem_no, acct_type, balance_cents, status, opened_on)"
                " values (?,?,?,?,'OPEN',?)",
                (f"{acct:012d}", mem_no, t, 25000 + n * 13100 + j * 50000, f"{2005 + n}-0{j + 1}-15"),
            )
    con.executemany("insert into seq values (?,?)",
                    [("acct", acct + 99), ("conf", 10000000), ("closure", 20000000)])
    con.commit()
    con.close()
    shutil.copyfile(path, var / "live.db")
    return path


if __name__ == "__main__":
    print(f"built {build()}")
