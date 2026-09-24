from datetime import date
from urllib.parse import quote

import pytest

from conftest import VAR, bank
from test_accounts import NOTES, app_id, review, rows
from test_seed import dump

ENDPOINTS = [("post", "/__test__/reset"), ("post", "/__test__/clock"), ("get", "/__test__/oracle?notes=x")]


@pytest.fixture
def test_mode(monkeypatch):
    monkeypatch.setattr(bank, "TEST_MODE", True)
    monkeypatch.setattr(bank, "FIXED_DATE", None)  # restored after the test


def test_endpoints_404_when_test_mode_off(client):
    assert not bank.TEST_MODE
    for method, url in ENDPOINTS:
        assert getattr(client, method)(url).status_code == 404


def test_endpoints_still_need_proxy_header(client, test_mode):
    del client.environ_base["HTTP_X_KVFCU_PROXY"]
    for method, url in ENDPOINTS:
        assert getattr(client, method)(url).status_code == 403


def test_reset(login, test_mode):
    c = login()
    c.post("/openAccountConfirm.do", data={"appId": app_id(review(c).text)})
    assert c.post("/__test__/reset").json == {"ok": True}
    assert dump(VAR / "live.db") == dump(VAR / "seed.db")
    assert "Session Expired" in c.get("/memberSearch.do").text


def test_clock(login, test_mode):
    c = login()
    assert c.post("/__test__/clock", json={"date": "2026-01-15"}).json == {"date": "2026-01-15", "fixed": True}
    c.post("/openAccountConfirm.do", data={"appId": app_id(review(c).text)})
    assert rows("select opened_on from accounts where conf_no is not null") == [("2026-01-15",)]
    assert c.post("/__test__/clock", json={"date": None}).json == {"date": date.today().isoformat(), "fixed": False}
    assert c.post("/__test__/clock", json={"date": "01/15/2026"}).status_code == 400
    assert c.post("/__test__/clock", data="nope").status_code == 400


def test_oracle(login, test_mode):
    c = login()
    url = "/__test__/oracle?notes=" + quote(NOTES)
    assert c.get(url).json == {"exists": False, "count": 0, "accounts": []}
    aid = app_id(review(c).text)
    c.post("/openAccountConfirm.do", data={"appId": aid})
    c.post("/openAccountConfirm.do", data={"appId": aid})  # double submit: oracle sees both
    got = c.get(url).json
    assert got["exists"] and got["count"] == 2
    assert [a["confirmation_number"] for a in got["accounts"]] == ["KV10000001", "KV10000002"]
    assert all(a["status"] == "OPEN" and len(a["account_number"]) == 12 for a in got["accounts"])
    assert c.get("/__test__/oracle?notes=" + quote(NOTES.strip())).json["exists"] is False  # exact match only
    assert c.get("/__test__/oracle").status_code == 400
