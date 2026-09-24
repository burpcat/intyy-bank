import sqlite3

from conftest import CREDS, VALID, VAR, seq


def test_no_proxy_header_is_403(client):
    del client.environ_base["HTTP_X_KVFCU_PROXY"]
    r = client.get("/login.do")
    assert r.status_code == 403 and r.text == "Forbidden\n"
    client.environ_base["HTTP_X_KVFCU_PROXY"] = "wrong"
    assert client.get("/static/kvfcu.css").status_code == 403


def test_login_and_frameset(client):
    u, _ = CREDS["teller"]
    assert "Invalid User ID" in client.post("/login.do", data={"userId": u, "pwd": "nope"}).text
    assert client.get("/memberSearch.do").headers["Location"].endswith("/login.do")
    r = client.post("/login.do", data={"userId": u, "pwd": CREDS["teller"][1]})
    assert r.headers["Location"].endswith("/lastLogin.do")
    assert "JSESSIONID" in r.headers["Set-Cookie"]
    assert "first login" in client.get("/lastLogin.do").text
    main = client.get("/main.do").text
    assert main.count("<frame ") == 3 and 'name="mainFrame"' in main
    assert "<marquee" in client.get("/banner.do").text
    assert "new.svg" in client.get("/menu.do").text


def test_search_hit_miss_and_page_expired(login):
    c = login()
    s = seq(c.get("/memberSearch.do").text)
    assert "BRENNEMAN" in c.post("/memberSearchResult.do", data={"memNo": VALID[0], "pageSeq": s}).text
    s = seq(c.get("/memberSearch.do").text)
    r = c.post("/memberSearchResult.do", data={"memNo": "999001", "pageSeq": s})
    assert r.status_code == 200 and "No records found" in r.text
    # Back/Refresh re-sends the same POST: page expired
    assert "Page Expired" in c.post("/memberSearchResult.do", data={"memNo": "999001", "pageSeq": s}).text


def test_member_detail_and_balance(login):
    c = login()
    d = c.get(f"/memberDetail.do?memNo={VALID[0]}").text
    assert "CIF No." in d and "Primary Savings" in d
    assert "No records found" in c.get("/memberDetail.do?memNo=999001").text
    assert "Total" in c.get(f"/balanceEnquiry.do?memNo={VALID[0]}").text


def test_idle_session_expires(login):
    c = login()
    with sqlite3.connect(VAR / "live.db") as con:
        con.execute("update sessions set last_seen = last_seen - 301")
    r = c.get("/memberSearch.do")
    assert r.status_code == 200 and "Session Expired" in r.text
    assert "Session Expired" in c.get("/memberSearch.do").text  # stays expired


def test_session_warning_and_logout(login):
    c = login()
    assert "Your session will expire in 1 minute." in c.get("/memberSearch.do").text
    assert "logged out" in c.get("/logout.do").text
    assert c.get("/memberSearch.do").headers["Location"].endswith("/login.do")
