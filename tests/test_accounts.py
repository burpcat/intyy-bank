import re
import sqlite3

from conftest import AT_LIMIT, VALID, VAR, seq

NOTES = "  run-ref: RUN-2026-0042 é中\U0001F600 " + "x" * 600 + "\r\nline two  "


def review(c, **over):
    f = {"cifId": VALID[1], "acctType": "Money Market", "openAmt": "1,250.50",
         "fundSrc": "Cash", "nomName": "Jane Doe", "txtRemarks": NOTES}
    f.update(over)
    f["pageSeq"] = seq(c.get("/openAccount.do").text)
    return c.post("/openAccountReview.do", data=f)


def app_id(html):
    return re.search(r'name="appId" value="(\w+)"', html).group(1)


def rows(sql, *args):
    with sqlite3.connect(VAR / "live.db") as con:
        return con.execute(sql, args).fetchall()


def test_open_account_notes_exact_and_no_double_submit_guard(login):
    c = login()
    r = review(c)
    assert "$1,250.50" in r.text and "Are you sure you want to open this account?" in r.text
    aid = app_id(r.text)
    first = c.post("/openAccountConfirm.do", data={"appId": aid}).text
    assert "KV10000001" in first
    assert "KV10000002" in c.post("/openAccountConfirm.do", data={"appId": aid}).text  # refresh = 2nd account
    got = rows("select notes, balance_cents, status from accounts where conf_no like 'KV%' order by conf_no")
    assert got == [(NOTES, 125050, "OPEN")] * 2
    assert c.get(f"/memberDetail.do?memNo={VALID[1]}").text.count("Money Market") == 2


def test_open_rules(login):
    c = login()
    assert "No records found" in review(c, cifId="999001").text
    assert "Member not eligible" in c.get(f"/openAccount.do?memNo={AT_LIMIT[0]}").text
    assert "Member not eligible" in review(c, cifId=AT_LIMIT[0]).text
    assert "must be at least $25.00" in review(c, openAmt="24.99").text
    assert "cannot exceed $10,000.00" in review(c, openAmt="10000.01").text
    assert "valid Opening Deposit" in review(c, openAmt="12abc").text
    assert "Please select Account Type" in review(c, acctType="").text
    ok = review(c, openAmt="10000.00")  # above $5,000: supervisor needed, confirm disabled
    assert "Supervisor approval required" in ok.text and "disabled" in ok.text
    assert "requires supervisor approval" in c.post("/openAccountConfirm.do", data={"appId": app_id(ok.text)}).text
    assert "Supervisor approval required" not in review(c, openAmt="5000.00").text


def test_limit_reached_after_opens(login):
    c = login()  # VALID[0] starts with 1 open sub-account
    for _ in range(2):
        c.post("/openAccountConfirm.do", data={"appId": app_id(review(c, cifId=VALID[0]).text)})
    assert "Member not eligible" in review(c, cifId=VALID[0]).text


def test_back_after_review_is_page_expired(login):
    c = login()
    s = seq(c.get("/openAccount.do").text)
    f = {"cifId": VALID[1], "acctType": "Money Market", "openAmt": "100", "fundSrc": "Cash", "pageSeq": s}
    assert "Application Ref" in c.post("/openAccountReview.do", data=f).text
    assert "Page Expired" in c.post("/openAccountReview.do", data=f).text


def test_restricted_user_denied(login):
    c = login("restricted")
    assert "Permission Denied" in c.get("/openAccount.do").text
    assert "Permission Denied" in c.get("/closeAccount.do").text
    assert "Permission Denied" in c.post("/openAccountConfirm.do", data={"appId": "X"}).text
    assert "Permission Denied" in c.post("/closeAccountConfirm.do", data={"acctNo": "X"}).text
    assert "Primary Savings" in c.get(f"/memberDetail.do?memNo={VALID[0]}").text  # view still allowed


def test_close_account(login):
    c = login()
    (primary, p_bal), (sub, s_bal) = rows(
        "select acct_no, balance_cents from accounts where mem_no=? order by acct_no", VALID[0])
    page = c.get(f"/closeAccount.do?memNo={VALID[0]}").text
    assert "cannot be closed" in c.post("/closeAccountReason.do", data={"acctNo": primary, "pageSeq": seq(page)}).text
    page = c.get(f"/closeAccount.do?memNo={VALID[0]}").text
    assert "Close Account" in c.post("/closeAccountReason.do", data={"acctNo": sub, "pageSeq": seq(page)}).text
    assert "Please select Reason" in c.post("/closeAccountConfirm.do", data={"acctNo": sub}).text
    done = c.post("/closeAccountConfirm.do", data={"acctNo": sub, "reason": "Opened in error"}).text
    assert "CL20000001" in done
    assert rows("select acct_no, balance_cents, status from accounts where mem_no=? order by acct_no", VALID[0]) == [
        (primary, p_bal + s_bal, "OPEN"), (sub, 0, "CLOSED")]
    assert "already closed" in c.post("/closeAccountConfirm.do", data={"acctNo": sub, "reason": "Other"}).text
