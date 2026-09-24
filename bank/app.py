"""KVFCU back office. Flask, server-rendered, deliberately 2008.

Run behind the chaos proxy only: every request must carry X-KVFCU-Proxy.
"""
import json
import os
import re
import secrets
import shutil
import sqlite3
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from flask import Flask, abort, g, jsonify, make_response, redirect, render_template, request

ROOT = Path(__file__).resolve().parent.parent
VAR = Path(os.environ.get("KVFCU_VAR_DIR", ROOT / "var"))
PROXY_SECRET = os.environ["KVFCU_PROXY_SECRET"].encode()
TEST_MODE = os.environ.get("KVFCU_TEST_MODE") == "1"
IDLE_TIMEOUT = 300  # seconds, real time
# Business date only (opened/closed on, page dates). Sessions always use real time.
FIXED_DATE = date.fromisoformat(os.environ["KVFCU_FIXED_DATE"]) if os.environ.get("KVFCU_FIXED_DATE") else None
ROLES = ("TELLER", "SUPERVISOR", "RESTRICTED")
USERS = {os.environ[f"KVFCU_{r}_USER"]: (os.environ[f"KVFCU_{r}_PASS"], r.lower())
         for r in ROLES if os.environ.get(f"KVFCU_{r}_USER")}

BRANDS = {
    "keystone": dict(name="Keystone Valley Federal Credit Union", short="KVFCU", css="kvfcu.css",
                     logo="kvfcu_logo.svg", prefix="KV", lbl_mem="Member No.", lbl_deposit="Opening Deposit"),
}
BRAND = BRANDS["keystone"]

PRIMARY = "Primary Savings"
SUB_TYPES = ["Share Savings", "Money Market", "Share Certificate"]
FUNDING = ["Cash", "Transfer from primary savings", "Check"]
CLOSE_REASONS = ["Opened in error", "Duplicate account", "Member request", "Other"]
MIN_DEPOSIT, MAX_DEPOSIT, OVERRIDE_ABOVE = 25_00, 10_000_00, 5_000_00  # cents
MAX_OPEN_SUBS = 3

app = Flask(__name__)


# ---------- plumbing ----------

def db():
    if "db" not in g:
        g.db = sqlite3.connect(VAR / "live.db")
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


@app.template_filter("money")
def money(cents):
    return f"${cents / 100:,.2f}"


@app.template_filter("usdate")
def usdate(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%m/%d/%Y") if iso else ""


@app.context_processor
def _ctx():
    return dict(brand=BRAND, user=g.get("user"), today=business_date().strftime("%m/%d/%Y"),
                sub_types=SUB_TYPES, funding=FUNDING)


def message(title, msg, link=None, link_text="Click here to login again"):
    return render_template("message.html", title=title, msg=msg, link=link, link_text=link_text)


@app.before_request
def _guard():
    if not secrets.compare_digest(request.headers.get("X-KVFCU-Proxy", "").encode(), PROXY_SECRET):
        return make_response("Forbidden\n", 403, {"Content-Type": "text/plain"})
    if request.path.startswith("/__test__/"):
        return None if TEST_MODE else abort(404)
    if request.path.startswith("/static/") or request.path == "/login.do":
        return None
    sid = request.cookies.get("JSESSIONID")
    if not sid:
        return redirect("/login.do")
    row = db().execute("select * from sessions where sid=?", (sid,)).fetchone()
    now = time.time()
    if row is None or now - row["last_seen"] > IDLE_TIMEOUT:
        db().execute("delete from sessions where sid=?", (sid,))
        db().commit()
        return message("Session Expired", "Your session has expired due to inactivity.", "/login.do")
    g.sid, g.user, g.role, g.sess = sid, row["username"], row["role"], json.loads(row["data"])
    if request.headers.get("X-KVFCU-Force-Override"):  # set only by the proxy (supervisor_required fault)
        g.sess["force_override"] = True
    db().execute("update sessions set last_seen=?, data=? where sid=?", (now, json.dumps(g.sess), sid))
    db().commit()
    return None


@app.after_request
def _no_cache(resp):
    if not request.path.startswith("/static/"):
        resp.headers.update({"Cache-Control": "no-store, no-cache, must-revalidate",
                             "Pragma": "no-cache", "Expires": "0"})
    return resp


def save_sess():
    db().execute("update sessions set data=? where sid=?", (json.dumps(g.sess), g.sid))
    db().commit()


def page():
    """Bump the main-frame page sequence; forms embed it so Back/Refresh after a POST is detectable."""
    g.sess["seq"] = g.sess.get("seq", 0) + 1
    save_sess()
    return g.sess["seq"]


def stale():
    return request.form.get("pageSeq") != str(g.sess.get("seq"))


def page_expired():
    return message("Page Expired",
                   "This page has expired. Do not press Back or Refresh buttons. "
                   "Please start the transaction again from the menu.")


def member(mem_no):
    return db().execute("select * from members where mem_no=?", (mem_no,)).fetchone()


def accounts(mem_no):
    return db().execute("select * from accounts where mem_no=? order by acct_no", (mem_no,)).fetchall()


def account(acct_no):
    return db().execute("select * from accounts where acct_no=?", (acct_no,)).fetchone()


def open_subs(mem_no):
    return db().execute("select count(*) from accounts where mem_no=? and status='OPEN' and acct_type<>?",
                        (mem_no, PRIMARY)).fetchone()[0]


def primary_of(mem_no):
    return db().execute("select * from accounts where mem_no=? and acct_type=? and status='OPEN'",
                        (mem_no, PRIMARY)).fetchone()


def short_of_funds(appl):
    """True if a transfer-funded opening exceeds the primary savings balance."""
    return appl["fundSrc"] == FUNDING[1] and primary_of(appl["cifId"])["balance_cents"] < appl["cents"]


INSUFFICIENT = "Insufficient balance in Primary Savings for this transfer."


def next_seq(name):
    db().execute("update seq set value=value+1 where name=?", (name,))
    return db().execute("select value from seq where name=?", (name,)).fetchone()[0]


def business_date():
    return FIXED_DATE or date.today()


def can_transact():
    return g.role in ("teller", "supervisor")


def denied():
    return message("Permission Denied", "You are not authorised to perform this transaction.")


def no_records():
    return message("Message", "No records found.", "/memberSearch.do", "Back to search")


def not_eligible():
    return message("Member Not Eligible",
                   "Member not eligible for a new sub-account. Please contact your branch manager.")


def parse_amount(text):
    """'$1,250.5' -> 125050 cents; None if not a plain amount."""
    text = text.strip().replace(",", "").removeprefix("$")
    return int(Decimal(text) * 100) if re.fullmatch(r"\d{1,9}(\.\d{1,2})?", text) else None


# ---------- login / frames ----------

@app.route("/login.do", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    user, pwd = request.form.get("userId", ""), request.form.get("pwd", "")
    cred = USERS.get(user)
    if not cred or not secrets.compare_digest(cred[0].encode(), pwd.encode()):
        return render_template("login.html", error="Invalid User ID or Password. Please try again.")
    prev = db().execute("select last_at from logins where username=?", (user,)).fetchone()
    db().execute("insert or replace into logins values (?,?)",
                 (user, datetime.now().strftime("%m/%d/%Y %H:%M:%S")))
    sid = secrets.token_hex(16).upper()
    db().execute("insert into sessions values (?,?,?,?,?)",
                 (sid, user, cred[1], time.time(), json.dumps({"last_login": prev[0] if prev else None})))
    db().commit()
    resp = redirect("/lastLogin.do")
    resp.set_cookie("JSESSIONID", sid, httponly=True)
    return resp


@app.route("/lastLogin.do")
def last_login():
    return render_template("last_login.html", last=g.sess.get("last_login"))


@app.route("/main.do")
def main():
    return render_template("main.html")


@app.route("/banner.do")
def banner():
    return render_template("banner.html")


@app.route("/menu.do")
def menu():
    return render_template("menu.html")


@app.route("/logout.do")
def logout():
    db().execute("delete from sessions where sid=?", (g.sid,))
    db().commit()
    g.user = None
    resp = make_response(message("Logged Out", "You have been logged out successfully. "
                                 "For security reasons please close the browser window.", "/login.do"))
    resp.delete_cookie("JSESSIONID")
    return resp


# ---------- member search / view ----------

@app.route("/memberSearch.do")
def member_search():
    return render_template("member_search.html", seq=page())


@app.route("/memberSearchResult.do", methods=["POST"])
def member_search_result():
    if stale():
        return page_expired()
    mem_no, last = request.form.get("memNo", "").strip(), request.form.get("lastName", "").strip()
    if not mem_no:
        return render_template("member_search.html", seq=page(),
                               error=f"Please enter {BRAND['lbl_mem']}")
    rows = db().execute("select * from members where mem_no=? and (?='' or upper(last_name)=upper(?))",
                        (mem_no, last, last)).fetchall()
    page()
    return render_template("search_result.html", rows=rows)


@app.route("/memberDetail.do")
def member_detail():
    m = member(request.args.get("memNo", ""))
    if m is None:
        return no_records()
    page()
    return render_template("member_detail.html", m=m, accts=accounts(m["mem_no"]))


@app.route("/balanceEnquiry.do")
def balance_enquiry():
    mem_no = request.args.get("memNo", "").strip()
    m = member(mem_no) if mem_no else None
    if mem_no and m is None:
        return message("Balance Enquiry", "No records found.", "/balanceEnquiry.do", "Back")
    page()
    accts = [a for a in accounts(mem_no) if a["status"] == "OPEN"] if m else []
    return render_template("balance.html", m=m, accts=accts,
                           total=sum(a["balance_cents"] for a in accts))


# ---------- open sub-account ----------

OPEN_FIELDS = ("cifId", "acctType", "openAmt", "fundSrc", "nomName")


@app.route("/openAccount.do")
def open_account():
    if not can_transact():
        return denied()
    mem_no = request.args.get("memNo", "").strip()
    if mem_no:
        if member(mem_no) is None:
            return no_records()
        if open_subs(mem_no) >= MAX_OPEN_SUBS:
            return not_eligible()
    return render_template("open_account.html", seq=page(), f={"cifId": mem_no})


def open_form_error(f, error):
    return render_template("open_account.html", seq=page(), f=f, error=error)


@app.route("/openAccountReview.do", methods=["POST"])
def open_account_review():
    if not can_transact():
        return denied()
    if stale():
        return page_expired()
    pending = g.sess.setdefault("pending", {})
    app_id = request.form.get("appId")
    if app_id:  # re-display of an existing application (supervisor pop-up reload)
        appl = pending.get(app_id)
        if appl is None:
            return page_expired()
        return render_template("open_review.html", seq=page(), app_id=app_id, a=appl)
    f = {k: request.form.get(k, "").strip() for k in OPEN_FIELDS}
    f["notes"] = request.form.get("txtRemarks", "")  # stored and shown exactly as typed
    m = member(f["cifId"])
    if m is None:
        return no_records()
    if open_subs(m["mem_no"]) >= MAX_OPEN_SUBS:
        return not_eligible()
    if f["acctType"] not in SUB_TYPES:
        return open_form_error(f, "Please select Account Type.")
    cents = parse_amount(f["openAmt"])
    if cents is None:
        return open_form_error(f, f"Please enter a valid {BRAND['lbl_deposit']}.")
    if cents < MIN_DEPOSIT:
        return open_form_error(f, f"{BRAND['lbl_deposit']} must be at least {money(MIN_DEPOSIT)}.")
    if cents > MAX_DEPOSIT:
        return open_form_error(f, f"{BRAND['lbl_deposit']} cannot exceed {money(MAX_DEPOSIT)}.")
    if f["fundSrc"] not in FUNDING:
        return open_form_error(f, "Please select Funding Source.")
    appl = {**f, "cents": cents, "name": f"{m['first_name']} {m['last_name']}",
            "override": cents > OVERRIDE_ABOVE or g.sess.pop("force_override", False), "approved": False}
    if short_of_funds(appl):
        return open_form_error(f, INSUFFICIENT)
    app_id = secrets.token_hex(6).upper()
    pending[app_id] = appl
    return render_template("open_review.html", seq=page(), app_id=app_id, a=appl)


@app.route("/openAccountConfirm.do", methods=["POST"])
def open_account_confirm():
    # Deliberately no double-submit protection: every POST here opens another account.
    if not can_transact():
        return denied()
    appl = g.sess.get("pending", {}).get(request.form.get("appId", ""))
    if appl is None:
        return page_expired()
    if appl["override"] and not appl["approved"]:
        return message("Supervisor Approval Required",
                       "This transaction requires supervisor approval before it can be confirmed.")
    if open_subs(appl["cifId"]) >= MAX_OPEN_SUBS:
        return not_eligible()
    if short_of_funds(appl):  # re-checked: balance may have moved since review
        return message("Message", INSUFFICIENT)
    if appl["fundSrc"] == FUNDING[1]:
        db().execute("update accounts set balance_cents=balance_cents-? where acct_no=?",
                     (appl["cents"], primary_of(appl["cifId"])["acct_no"]))
    acct_no = f"{next_seq('acct'):012d}"
    conf_no = f"{BRAND['prefix']}{next_seq('conf'):08d}"
    opened = business_date().isoformat()
    db().execute(
        "insert into accounts (acct_no, mem_no, acct_type, balance_cents, status, opened_on,"
        " funding_source, nominee, notes, conf_no) values (?,?,?,?,'OPEN',?,?,?,?,?)",
        (acct_no, appl["cifId"], appl["acctType"], appl["cents"], opened,
         appl["fundSrc"], appl["nomName"], appl["notes"], conf_no))
    db().commit()
    page()
    return render_template("open_confirm.html", a=appl, acct_no=acct_no, conf_no=conf_no, opened=opened)


@app.route("/supervisorOverride.do", methods=["GET", "POST"])
def supervisor_override():
    """Pop-up window. Does not touch the page sequence, so the opener's review form stays valid."""
    app_id = request.values.get("appId", "")
    appl = g.sess.get("pending", {}).get(app_id)
    if appl is None or not appl["override"]:
        return render_template("supervisor.html", app_id=app_id, error="No application pending approval.")
    if request.method == "GET":
        return render_template("supervisor.html", app_id=app_id, a=appl)
    sup, pwd = request.form.get("supId", ""), request.form.get("supPwd", "")
    cred = USERS.get(sup)
    if not cred or not secrets.compare_digest(cred[0].encode(), pwd.encode()):
        return render_template("supervisor.html", app_id=app_id, a=appl, error="Invalid Supervisor ID or Password.")
    if cred[1] != "supervisor":
        return render_template("supervisor.html", app_id=app_id, a=appl,
                               error="User is not authorised to approve this transaction.")
    appl.update(approved=True, approved_by=sup)
    save_sess()
    return render_template("supervisor.html", app_id=app_id, a=appl, done=True)


# ---------- close sub-account ----------

@app.route("/closeAccount.do")
def close_account():
    if not can_transact():
        return denied()
    mem_no = request.args.get("memNo", "").strip()
    m = member(mem_no) if mem_no else None
    if mem_no and m is None:
        return no_records()
    accts = [a for a in accounts(mem_no) if a["status"] == "OPEN"] if m else []
    return render_template("close_account.html", seq=page(), m=m, accts=accts)


def closable(acct_no):
    """(account, None) if it may be closed, else (None, error page)."""
    a = account(acct_no)
    if a is None or a["status"] != "OPEN":
        return None, message("Message", "Account not found or already closed.")
    if a["acct_type"] == PRIMARY:
        return None, message("Message", "Primary Savings account cannot be closed.")
    return a, None


@app.route("/closeAccountReason.do", methods=["POST"])
def close_account_reason():
    if not can_transact():
        return denied()
    if stale():
        return page_expired()
    a, err = closable(request.form.get("acctNo", ""))
    if err:
        return err
    return render_template("close_reason.html", seq=page(), a=a, reasons=CLOSE_REASONS)


@app.route("/closeAccountConfirm.do", methods=["POST"])
def close_account_confirm():
    if not can_transact():
        return denied()
    a, err = closable(request.form.get("acctNo", ""))
    if err:
        return err
    reason, remarks = request.form.get("reason", ""), request.form.get("remarks", "").strip()
    if reason not in CLOSE_REASONS:
        return render_template("close_reason.html", seq=page(), a=a, reasons=CLOSE_REASONS,
                               error="Please select Reason for Closure.")
    primary = primary_of(a["mem_no"])["acct_no"]
    closure_no = f"CL{next_seq('closure'):08d}"
    closed = business_date().isoformat()
    db().execute("update accounts set balance_cents=balance_cents+? where acct_no=?",
                 (a["balance_cents"], primary))
    db().execute("update accounts set status='CLOSED', balance_cents=0, closed_on=?, closure_no=?,"
                 " close_reason=? where acct_no=?",
                 (closed, closure_no, f"{reason}: {remarks}" if remarks else reason, a["acct_no"]))
    db().commit()
    page()
    return render_template("close_confirm.html", a=a, primary=primary, closure_no=closure_no, closed=closed)


# ---------- test endpoints (KVFCU_TEST_MODE=1 only; 404 otherwise) ----------

@app.route("/__test__/reset", methods=["POST"])
def test_reset():
    """Restore seed data; seed.db holds no sessions, so this also logs everyone out."""
    shutil.copyfile(VAR / "seed.db", VAR / "live.db")
    return jsonify(ok=True)


@app.route("/__test__/clock", methods=["POST"])
def test_clock():
    """Body {"date": "YYYY-MM-DD"} fixes the business date; {"date": null} clears it."""
    global FIXED_DATE
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or "date" not in body:
        return jsonify(error='body must be {"date": "YYYY-MM-DD"} or {"date": null}'), 400
    try:
        FIXED_DATE = date.fromisoformat(body["date"]) if body["date"] is not None else None
    except (TypeError, ValueError):
        return jsonify(error="date must be YYYY-MM-DD"), 400
    return jsonify(date=business_date().isoformat(), fixed=FIXED_DATE is not None)


@app.route("/__test__/oracle")
def test_oracle():
    """Ground truth after a dropped reply: accounts whose notes match exactly."""
    notes = request.args.get("notes")
    if notes is None:
        return jsonify(error="notes query parameter is required"), 400
    found = db().execute("select acct_no, status, conf_no from accounts where notes=? order by acct_no",
                         (notes,)).fetchall()
    return jsonify(exists=bool(found), count=len(found),
                   accounts=[{"account_number": a, "status": s, "confirmation_number": c} for a, s, c in found])
