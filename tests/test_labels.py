"""Step 10: seeded removal of HTML <label> associations. Visible field names must stay on screen."""
import re

import pytest

from conftest import VALID, bank, seq
from test_accounts import app_id, review, rows, unlabelled
from test_variants import run_py

NAMES = {  # page -> visible field names that must always be on screen
    "login": ["User ID", "Password"],
    "search": ["Member No.", "Last Name"],
    "balance": ["Member No."],
    "open": ["A/c Holder ID", "Type of A/c", "Opening Deposit ($)", "Funding Source", "Nominee Name", "Notes"],
    "close": ["Member No."],
    "reason": ["Reason for Closure", "Remarks"],
    "supervisor": ["Supervisor ID", "Password"],
}


def pages(c):
    sub = rows("select acct_no from accounts where mem_no=? and acct_type<>'Primary Savings'", VALID[0])[0][0]
    p = {"login": c.get("/login.do").text, "search": c.get("/memberSearch.do").text,
         "balance": c.get(f"/balanceEnquiry.do?memNo={VALID[0]}").text, "open": c.get("/openAccount.do").text,
         "close": c.get(f"/closeAccount.do?memNo={VALID[0]}").text}
    # must directly follow the close page: any page in between would make its pageSeq stale
    p["reason"] = c.post("/closeAccountReason.do", data={"acctNo": sub, "pageSeq": seq(p["close"])}).text
    p["supervisor"] = c.get(f"/supervisorOverride.do?appId={app_id(review(c, openAmt='6000').text)}").text
    assert "Account Closure" in p["reason"] and "Authorisation Required" in p["supervisor"]
    return p


def labelled(html):
    return re.findall(r'<label for="([^"]+)">', html)


def drop(monkeypatch, fraction, seed="0"):
    monkeypatch.setattr(bank, "DROP_LABELS", fraction)
    monkeypatch.setattr(bank, "LABEL_SEED", seed)


def test_default_keeps_every_label(login):
    for name, html in pages(login()).items():
        assert unlabelled(html) == [], name


def test_drop_all_removes_labels_but_keeps_field_names(login, monkeypatch):
    drop(monkeypatch, 1.0)
    c = login()
    for name, html in pages(c).items():
        assert "<label" not in html, name
        for text in NAMES[name]:
            assert text in html, (name, text)
    # everything still works without labels
    assert "BRENNEMAN" in c.post("/memberSearchResult.do", data={"memNo": VALID[0],
                                                                 "pageSeq": seq(c.get("/memberSearch.do").text)}).text
    assert "KV10000001" in c.post("/openAccountConfirm.do", data={"appId": app_id(review(c).text)}).text


def labelled_set(c):
    return {(name, f) for name, html in pages(c).items() for f in labelled(html)}


def test_seed_picks_a_fixed_set(login, monkeypatch):
    c = login()
    everything = labelled_set(c)
    drop(monkeypatch, 0.5, "seed-a")
    a = labelled_set(c)
    assert a == labelled_set(c)  # same seed: same labels on every render
    assert 0 < len(a) < len(everything)
    drop(monkeypatch, 0.5, "seed-b")
    assert labelled_set(c) != a
    # a field without its label also loses its id; one with its label keeps both halves of the pair
    for name, html in pages(c).items():
        for f in labelled(html):
            assert f'id="{f}"' in html, (name, f)


def test_each_page_decides_independently(login, monkeypatch):
    c = login()
    for s in range(50):
        drop(monkeypatch, 0.5, str(s))
        p = pages(c)
        if ("memNo" in labelled(p["search"])) != ("memNo" in labelled(p["balance"])):
            return
    pytest.fail("memNo was labelled identically on search and balance for 50 seeds")


def test_drop_labels_env_validated():
    assert run_py("from bank import app; print(app.DROP_LABELS)", KVFCU_DROP_LABELS="0.3").stdout.strip() == "0.3"
    bad = run_py("from bank import app", KVFCU_DROP_LABELS="2")
    assert bad.returncode != 0 and "KVFCU_DROP_LABELS must be from 0 to 1" in bad.stderr
