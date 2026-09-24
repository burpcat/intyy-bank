import os
import re
import sqlite3
import subprocess
import sys
from datetime import date
from pathlib import Path

from conftest import AT_LIMIT, VALID, VAR, bank, seed
from test_accounts import app_id, review


def dump(path):
    with sqlite3.connect(path) as con:
        return list(con.iterdump())


def q(sql, *args):
    with sqlite3.connect(VAR / "live.db") as con:
        return con.execute(sql, args).fetchall()


def test_seed_categories_and_safe_formats(client):
    assert (len(VALID), len(AT_LIMIT), len(seed.MISSING)) == (20, 5, 5)
    subs = dict(q("select mem_no, sum(acct_type<>'Primary Savings') from accounts group by mem_no"))
    assert all(subs[m] < 3 for m in VALID) and all(subs[m] == 3 for m in AT_LIMIT)
    assert q(f"select count(*) from members where mem_no in ({','.join('?' * 5)})", *seed.MISSING) == [(0,)]
    for mem_no, ssn, phone, since in q("select mem_no, ssn, phone, member_since from members"):
        assert re.fullmatch(r"\d{6}", mem_no) and re.fullmatch(r"9\d\d-\d\d-\d{4}", ssn)
        assert re.fullmatch(r"\(\d{3}\) 555-01\d\d", phone)
    for acct_no, opened, since in q("select acct_no, opened_on, member_since from accounts join members using(mem_no)"):
        assert re.fullmatch(r"\d{12}", acct_no) and since <= opened <= date.today().isoformat()
    assert q("select count(*) from sessions") == [(0,)]


def test_seed_is_deterministic(tmp_path):
    seed.build(tmp_path / "a")
    seed.build(tmp_path / "b")
    assert dump(tmp_path / "a" / "seed.db") == dump(tmp_path / "b" / "seed.db") == dump(VAR / "seed.db")


def test_make_reset_restores_seed_exactly(login):
    c = login()
    c.post("/openAccountConfirm.do", data={"appId": app_id(review(c).text)})
    assert dump(VAR / "live.db") != dump(VAR / "seed.db")
    subprocess.run(["make", "-s", "reset", f"KVFCU_VAR_DIR={VAR}"], check=True, cwd=Path(__file__).parent.parent)
    assert dump(VAR / "live.db") == dump(VAR / "seed.db")
    assert "Session Expired" in c.get("/memberSearch.do").text  # sessions cleared


def test_fixed_business_date(login, monkeypatch):
    monkeypatch.setattr(bank, "FIXED_DATE", date(2026, 1, 15))
    c = login()
    assert "01/15/2026" in c.get("/memberSearch.do").text
    assert "01/15/2026" in c.post("/openAccountConfirm.do", data={"appId": app_id(review(c).text)}).text
    assert q("select opened_on from accounts where conf_no is not null") == [("2026-01-15",)]


def test_fixed_date_from_env():
    env = {**os.environ, "KVFCU_FIXED_DATE": "2026-01-15"}
    out = subprocess.run([sys.executable, "-c", "from bank import app; print(app.business_date())"],
                         env=env, capture_output=True, text=True, check=True, cwd=Path(__file__).parent.parent)
    assert out.stdout.strip() == "2026-01-15"
