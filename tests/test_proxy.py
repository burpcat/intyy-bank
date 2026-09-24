"""End-to-end: real bank app + real chaos proxy as subprocesses, driven over HTTP."""
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest

from conftest import CREDS, VALID, proxy, seed, seq

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def stack(test_mode=True):
    var = Path(tempfile.mkdtemp(prefix="kvfcu-stack-"))
    seed.build(var)
    app_port, proxy_port = free_port(), free_port()
    env = {**os.environ, "KVFCU_VAR_DIR": str(var), "KVFCU_APP_PORT": str(app_port),
           "KVFCU_PROXY_PORT": str(proxy_port), "KVFCU_DELAY_SCALE": "0.001",
           "KVFCU_TEST_MODE": "1" if test_mode else "0"}
    procs = [subprocess.Popen([sys.executable, "-m", "flask", "--app", "bank.app", "run", "--port", str(app_port)],
                              env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL),
             subprocess.Popen([sys.executable, "-m", "chaos.proxy"],
                              env=env, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)]
    try:
        for port in (app_port, proxy_port):
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
                    break
                except OSError:
                    time.sleep(0.05)
        yield proxy_port, app_port, var
    finally:
        for p in procs:
            p.terminate()
            p.wait()


class Browser:
    """Minimal client with a JSESSIONID jar and no redirect following."""

    def __init__(self, port):
        self.port, self.cookie = port, None

    def req(self, method, path, form=None, js=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h, body = dict(headers or {}), None
        if form is not None:
            body, h["Content-Type"] = urlencode(form), "application/x-www-form-urlencoded"
        if js is not None:
            body, h["Content-Type"] = json.dumps(js), "application/json"
        if self.cookie:
            h["Cookie"] = self.cookie
        try:
            conn.request(method, path, body, h)
            r = conn.getresponse()
            text = r.read().decode()
        except (http.client.RemoteDisconnected, ConnectionResetError):
            return None, None, None  # connection closed with no reply (hang)
        finally:
            conn.close()
        sc = r.getheader("Set-Cookie") or ""
        if sc.startswith("JSESSIONID="):
            val = sc.split(";")[0]
            self.cookie = None if val == "JSESSIONID=" else val
        return r.status, r, text

    def get(self, path, **kw):
        return self.req("GET", path, **kw)

    def post(self, path, form=None, **kw):
        return self.req("POST", path, form=form, **kw)

    def login(self, role="teller"):
        u, p = CREDS[role]
        assert self.post("/login.do", {"userId": u, "pwd": p})[0] == 302
        return self

    def search(self, mem_no):
        s = seq(self.get("/memberSearch.do")[2])
        return self.post("/memberSearchResult.do", {"memNo": mem_no, "pageSeq": s})


@pytest.fixture(scope="module")
def live():
    with stack() as ports:
        yield ports


@pytest.fixture
def b(live):
    """Fresh state per test: data, sessions, counters, faults, log; chaos back to entropy 0."""
    br = Browser(live[0])
    assert br.post("/__test__/reset")[0] == 200
    br.req("POST", "/__test__/chaos", js={"entropy": 0, "seed": "0"})
    return br


def log(br):
    return json.loads(br.get("/__test__/faultlog")[2])["entries"]


def add_faults(br, *faults):
    status, _, text = br.req("POST", "/__test__/faults", js={"faults": list(faults)})
    assert status == 200, text


def open_app(br, **over):
    f = {"cifId": VALID[1], "acctType": "Money Market", "openAmt": "150", "fundSrc": "Cash",
         "nomName": "", "txtRemarks": "run-ref RUN-77"}
    f.update(over)
    f["pageSeq"] = seq(br.get("/openAccount.do")[2])
    text = br.post("/openAccountReview.do", f)[2]
    return re.search(r'name="appId" value="(\w+)"', text).group(1), text


# ---------- pass-through, header, delays ----------

def test_pass_through_adds_secret_and_strips_client_headers(b, live):
    status, _, text = b.get("/login.do")
    assert status == 200 and "Staff Login" in text
    assert Browser(live[1]).get("/login.do")[0] == 403  # the app is unreachable around the proxy
    b.login()
    assert "BRENNEMAN" in b.search(VALID[0])[2]
    _, text = open_app(b, openAmt="100")
    assert "Supervisor approval required" not in text
    # a client cannot forge proxy-only headers
    b.get("/menu.do", headers={"X-KVFCU-Force-Override": "1", "X-KVFCU-Proxy": "forged"})
    assert "Supervisor approval required" not in open_app(b, openAmt="100")[1]


def test_log_counts_pages_not_static(b):
    b.get("/login.do")
    b.get("/static/kvfcu.css")
    b.get("/login.do")
    entries = log(b)
    assert [(e["method"], e["path"], e["route_count"], e["decision"]) for e in entries] == [
        ("GET", "/login.do", 1, "pass"), ("GET", "/login.do", 2, "pass")]
    assert [e["seq"] for e in entries] == [1, 2]
    assert set(entries[0]) == {"seq", "time", "method", "path", "route_count", "decision", "fault_kind",
                               "block_point", "style", "named_id", "delay_ms"}
    assert all(200 <= e["delay_ms"] <= 3000 or 5000 <= e["delay_ms"] <= 8000 for e in entries)


# ---------- entropy ----------

def test_entropy_one_fails_every_counted_request_never_static(b):
    b.req("POST", "/__test__/chaos", js={"entropy": 1, "seed": "e1"})
    for _ in range(12):
        status, _, text = b.get("/login.do")
        assert status is None or "Staff Login" not in text
        assert b.get("/static/kvfcu.css")[0] == 200
    assert all(e["decision"] == "entropy" for e in log(b))
    assert len(log(b)) == 12


def seed_for(route, n, want):
    """Find a seed whose entropy decision for (route, n) has the wanted style and block point."""
    method, path = route.split(" ", 1)
    for i in range(10_000):
        d = proxy.decide(str(i), 1, [], method, path, n)
        if (d["style"], d["block_point"]) == want:
            return str(i)
    raise AssertionError(want)


@pytest.mark.parametrize("style,check", [
    ("error_page", lambda s, t: s == 500 and "500 Internal Server Error" in t),
    ("blank", lambda s, t: s == 200 and t == ""),
    ("hang", lambda s, t: s is None),
    ("unavailable", lambda s, t: s == 503 and "Service temporarily unavailable. Please try later." in t),
    ("maintenance", lambda s, t: s == 200 and "System under scheduled maintenance" in t),
])
def test_each_fault_style(b, style, check):
    b.req("POST", "/__test__/chaos", js={"entropy": 1, "seed": seed_for("GET /menu.do", 1, (style, "before"))})
    status, _, text = b.get("/menu.do")
    assert check(status, text)


def test_logout_style_kills_session(b):
    b.login()
    b.req("POST", "/__test__/chaos", js={"entropy": 1, "seed": seed_for("GET /menu.do", 1, ("logout", "before"))})
    status, r, _ = b.get("/menu.do")
    assert status == 302 and r.getheader("Location").endswith("/login.do")
    b.req("POST", "/__test__/chaos", js={"entropy": 0})
    assert "Session Expired" in b.get("/menu.do")[2]


def test_entropy_after_block_commits(b):
    b.login()
    aid, _ = open_app(b)
    b.req("POST", "/__test__/chaos",
          js={"entropy": 1, "seed": seed_for("POST /openAccountConfirm.do", 1, ("error_page", "after"))})
    assert b.post("/openAccountConfirm.do", {"appId": aid})[0] == 500
    assert json.loads(b.get("/__test__/oracle?notes=" + quote("run-ref RUN-77"))[2])["count"] == 1


# ---------- reproducibility ----------

def scripted_run(b, seed_):
    b.post("/__test__/reset")
    b.req("POST", "/__test__/chaos", js={"entropy": 0.4, "seed": seed_})
    b.cookie = None
    for _ in range(3):
        b.post("/login.do", {"userId": CREDS["teller"][0], "pwd": CREDS["teller"][1]})
        for path in ("/lastLogin.do", "/main.do", "/banner.do", "/menu.do", "/memberSearch.do",
                     f"/memberDetail.do?memNo={VALID[0]}", "/static/kvfcu.css"):
            b.get(path)
    return [{k: v for k, v in e.items() if k != "time"} for e in log(b)]


def test_same_seed_and_entropy_give_identical_fault_log(b, live):
    first = scripted_run(b, "repro-1")
    assert len(first) == 21 and any(e["decision"] == "entropy" for e in first)
    assert scripted_run(b, "repro-1") == first
    assert scripted_run(b, "repro-2") != first
    on_disk = [json.loads(line) for line in (live[2] / "fault_log.jsonl").read_text().splitlines()]
    assert on_disk == log(b)


# ---------- named faults ----------

def test_named_fault_triggers_on_right_route_and_count(b):
    add_faults(b, {"id": "se2", "kind": "server_error", "route": "GET /menu.do", "nth": 2},
               {"id": "mt", "kind": "maintenance", "route": "GET /banner.do", "nth": 2, "repeat": "always"})
    assert [b.get("/menu.do")[0] for _ in range(3)] == [302, 500, 302]  # 302: not logged in
    assert ["maintenance" in (b.get("/banner.do")[2] or "") for _ in range(3)] == [False, True, True]
    named = [(e["path"], e["route_count"], e["named_id"]) for e in log(b) if e["decision"] == "named"]
    assert named == [("/menu.do", 2, "se2"), ("/banner.do", 2, "mt"), ("/banner.do", 3, "mt")]


def test_known_popup_resubmits_original_post(b):
    b.login()
    add_faults(b, {"id": "kyc", "kind": "known_popup", "route": "POST /memberSearchResult.do"})
    status, _, text = b.search(VALID[0])
    assert status == 200 and "Please update member KYC details" in text
    assert 'value="OK"' in text and 'value="Remind Later"' in text and "BRENNEMAN" not in text
    hidden = dict(re.findall(r'type="hidden" name="([^"]+)" value="([^"]*)"', text))
    assert hidden["memNo"] == VALID[0]
    assert "BRENNEMAN" in b.post("/memberSearchResult.do", hidden)[2]  # pageSeq still valid: app never saw it


def test_unknown_popup_is_seeded(b):
    add_faults(b, {"id": "u", "kind": "unknown_popup", "route": "GET /login.do", "repeat": "always"})
    pages = [b.get("/login.do")[2] for _ in range(6)]
    assert all("<form method=\"get\" action=\"/login.do\">" in p and "Staff Login" not in p for p in pages)
    assert len(set(pages)) > 1
    b.post("/__test__/reset")
    add_faults(b, {"id": "u", "kind": "unknown_popup", "route": "GET /login.do", "repeat": "always"})
    assert [b.get("/login.do")[2] for _ in range(6)] == pages


def test_session_expire_and_supervisor_required(b):
    b.login()
    add_faults(b, {"id": "sx", "kind": "session_expire", "route": "GET /balanceEnquiry.do"},
               {"id": "sv", "kind": "supervisor_required", "route": "GET /openAccount.do"})
    assert "Supervisor approval required" in open_app(b, openAmt="100")[1]
    assert "Session Expired" in b.get("/balanceEnquiry.do")[2]


def test_drop_after_confirm_commits_but_drops_reply(b):
    b.login()
    add_faults(b, {"id": "drop", "kind": "drop_after_confirm", "route": "POST /openAccountConfirm.do"})
    aid, _ = open_app(b)
    status, _, text = b.post("/openAccountConfirm.do", {"appId": aid})
    assert status is None or "KV1" not in text  # no confirmation number reaches the operator
    oracle = json.loads(b.get("/__test__/oracle?notes=" + quote("run-ref RUN-77"))[2])
    assert oracle["exists"] and oracle["accounts"][0]["confirmation_number"] == "KV10000001"
    e = [e for e in log(b) if e["decision"] == "named"][0]
    assert (e["block_point"], e["fault_kind"], e["named_id"]) == ("after", "drop_after_confirm", "drop")


# ---------- test endpoints ----------

def test_fault_validation_and_clear(b):
    for bad in ({"id": "x", "kind": "nope", "route": "GET /a.do"}, {"id": "x", "kind": "maintenance", "route": "a"},
                {"id": "x", "kind": "maintenance", "route": "GET /a.do", "nth": 0},
                {"id": "x", "kind": "maintenance", "route": "GET /a.do", "repeat": "twice"}, {"kind": "maintenance"}):
        assert b.req("POST", "/__test__/faults", js=bad)[0] == 400
    assert b.req("POST", "/__test__/chaos", js={"entropy": 2})[0] == 400
    assert b.req("POST", "/__test__/faults", headers={"Content-Type": "application/json"})[0] == 400
    add_faults(b, {"id": "a", "kind": "maintenance", "route": "GET /login.do"})
    assert b.req("POST", "/__test__/faults", js={"id": "a", "kind": "maintenance", "route": "GET /x.do"})[0] == 400
    assert json.loads(b.req("DELETE", "/__test__/faults")[2]) == {"faults": []}
    assert "Staff Login" in b.get("/login.do")[2]


def test_reset_clears_counters_faults_and_log(b):
    add_faults(b, {"id": "m", "kind": "maintenance", "route": "GET /login.do", "nth": 3})
    b.get("/login.do"), b.get("/login.do")
    b.post("/__test__/reset")
    assert log(b) == []
    b.get("/login.do"), b.get("/login.do"), b.get("/login.do")
    assert [e["route_count"] for e in log(b)] == [1, 2, 3] and all(e["decision"] == "pass" for e in log(b))


def test_clock_and_oracle_forwarded(b):
    assert json.loads(b.req("POST", "/__test__/clock", js={"date": "2026-01-15"})[2])["date"] == "2026-01-15"
    assert json.loads(b.req("POST", "/__test__/clock", js={"date": None})[2])["fixed"] is False
    assert json.loads(b.get("/__test__/oracle?notes=zzz")[2])["exists"] is False


def test_test_endpoints_404_when_test_mode_off():
    with stack(test_mode=False) as (port, _, _):
        br = Browser(port)
        for method, path in (("POST", "/__test__/chaos"), ("POST", "/__test__/faults"), ("DELETE", "/__test__/faults"),
                             ("GET", "/__test__/faultlog"), ("POST", "/__test__/reset"), ("POST", "/__test__/clock"),
                             ("GET", "/__test__/oracle")):
            assert br.req(method, path, js={})[0] == 404
        assert "Staff Login" in br.get("/login.do")[2]  # normal traffic still flows (with baseline delays)
