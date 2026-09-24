from conftest import CREDS, seq
from test_accounts import app_id, review, unlabelled


def approve(c, aid, role="supervisor", pwd=None):
    u, p = CREDS[role]
    return c.post("/supervisorOverride.do", data={"appId": aid, "supId": u, "supPwd": pwd or p}).text


def test_override_flow(login):
    c = login()
    r = review(c, openAmt="7500")
    aid, s = app_id(r.text), seq(r.text)
    assert "Supervisor approval required" in r.text and f"window.open('/supervisorOverride.do?appId={aid}'" in r.text
    popup = c.get(f"/supervisorOverride.do?appId={aid}").text
    assert "$7,500.00" in popup and unlabelled(popup) == []
    assert "Invalid Supervisor ID or Password" in approve(c, aid, pwd="wrong")
    assert "not authorised to approve" in approve(c, aid, "teller")
    assert "not authorised to approve" in approve(c, aid, "restricted")
    assert "requires supervisor approval" in c.post("/openAccountConfirm.do", data={"appId": aid}).text
    done = approve(c, aid)
    assert "forms['reloadForm'].submit()" in done and "window.close()" in done
    # the pop-up's reload of the opener: same appId + the review's pageSeq (pop-up did not bump it)
    again = c.post("/openAccountReview.do", data={"appId": aid, "pageSeq": s}).text
    assert "APPROVED" in again and "disabled" not in again
    assert "KV10000001" in c.post("/openAccountConfirm.do", data={"appId": aid}).text


def test_popup_without_pending_application(login):
    c = login()
    assert "No application pending approval" in c.get("/supervisorOverride.do?appId=NOPE").text
    aid = app_id(review(c, openAmt="100").text)  # below threshold: nothing to approve
    assert "No application pending approval" in approve(c, aid)


def test_forced_override_header(login):
    c = login()
    c.get("/menu.do", headers={"X-KVFCU-Force-Override": "1"})
    assert "Supervisor approval required" in review(c, openAmt="100").text
    assert "Supervisor approval required" not in review(c, openAmt="100").text  # consumed once
