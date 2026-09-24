import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from bank.brands import BRANDS
from conftest import VALID, bank, seq
from test_accounts import app_id, review, rows

ROOT = Path(__file__).resolve().parent.parent


def img_tags(html):
    return re.findall(r"<img [^>]*bx\d\.svg[^>]*>", html)


def assert_nameless(tag):
    assert not re.search(r"\b(alt|title|aria-[a-z]+|value)=", tag), tag


def test_strip_semantics_buttons_are_nameless_images(login, monkeypatch):
    monkeypatch.setattr(bank, "STRIP", True)
    c = login()
    search = c.get("/memberSearch.do").text
    assert 'value="Search"' not in search and len(img_tags(search)) == 1
    r = review(c)
    assert 'value="Confirm"' not in r.text and "Are you sure you want to open this account?" in r.text
    over = review(c, openAmt="7000")
    assert "opacity:0.4" in img_tags(over.text)[0] and "onclick" not in img_tags(over.text)[0]
    sub = rows("select acct_no from accounts where mem_no=? and acct_type<>'Primary Savings'", VALID[0])[0][0]
    page = c.get(f"/closeAccount.do?memNo={VALID[0]}").text
    reason = c.post("/closeAccountReason.do", data={"acctNo": sub, "pageSeq": seq(page)}).text
    assert 'value="Close Account"' not in reason
    tags = img_tags(search) + img_tags(r.text) + img_tags(reason)
    assert [re.search(r"bx\d", t).group() for t in tags] == ["bx1", "bx2", "bx3"]
    for t in tags:
        assert_nameless(t)
    # the image files carry no text either
    assert all("<text" not in (ROOT / f"bank/static/bx{i}.svg").read_text() for i in (1, 2, 3))
    # forms still submit with the same fields
    assert "KV10000001" in c.post("/openAccountConfirm.do", data={"appId": app_id(r.text)}).text


def test_normal_mode_keeps_text_buttons(login):
    c = login()
    assert 'value="Search"' in c.get("/memberSearch.do").text and not img_tags(c.get("/memberSearch.do").text)


@pytest.fixture
def lakeshore(monkeypatch):
    monkeypatch.setattr(bank, "BRAND", BRANDS["lakeshore"])


def test_lakeshore_labels_branding_and_columns(login, lakeshore):
    c = login()
    search = c.get("/memberSearch.do").text
    assert "Member ID" in search and "Member No." not in search
    assert "Lakeshore Members Credit Union" in search and "lmcu.css" in search and "kvfcu" not in search
    form = c.get("/openAccount.do").text
    assert "Initial Funding Amount" in form and "Opening Deposit" not in form and 'name="brCode"' in form
    heads = re.findall(r"<th>([^<]+)</th>", c.get(f"/memberDetail.do?memNo={VALID[0]}").text)
    assert heads == ["Account Type", "Account No.", "Status", "Opened On", "Balance"]
    assert "Lakeshore" in c.get("/banner.do").text and "lmcu_logo.svg" in c.get("/banner.do").text


def test_lakeshore_branch_code_required(login, lakeshore):
    c = login()
    assert "Please select Branch Code" in review(c).text
    assert "Please select Branch Code" in review(c, brCode="LS99 - Nowhere").text
    assert "Initial Funding Amount must be at least" in review(c, openAmt="1", brCode="LS02 - Pier Street").text
    r = review(c, brCode="LS02 - Pier Street")
    assert "LS02 - Pier Street" in r.text
    done = c.post("/openAccountConfirm.do", data={"appId": app_id(r.text)}).text
    assert "LM10000001" in done and "LS02 - Pier Street" in done
    assert rows("select conf_no, branch_code from accounts where conf_no is not null") == [
        ("LM10000001", "LS02 - Pier Street")]


def test_keystone_has_no_branch_field(login):
    c = login()
    assert 'name="brCode"' not in c.get("/openAccount.do").text
    heads = re.findall(r"<th>([^<]+)</th>", c.get(f"/memberDetail.do?memNo={VALID[0]}").text)
    assert heads == ["Account No.", "Account Type", "Balance", "Status", "Opened On"]


def run_py(code, **env):
    return subprocess.run([sys.executable, "-c", code], env={**os.environ, **env}, cwd=ROOT,
                          capture_output=True, text=True)


def test_variant_env_selects_tenant_for_app_and_proxy():
    out = run_py("from bank import app; from chaos import proxy; print(app.BRAND['short'], proxy.BRAND['css'])",
                 KVFCU_VARIANT="lakeshore")
    assert out.stdout.split() == ["LMCU", "lmcu.css"]
    bad = run_py("from bank import app", KVFCU_VARIANT="acme")
    assert bad.returncode != 0 and "KVFCU_VARIANT must be one of" in bad.stderr
    strip = run_py("from bank import app; print(app.STRIP)", KVFCU_STRIP_SEMANTICS="1")
    assert strip.stdout.strip() == "True"
