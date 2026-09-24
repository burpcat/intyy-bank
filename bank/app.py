"""KVFCU back office. Flask, server-rendered, deliberately 2008.

Run behind the chaos proxy only: every request must carry X-KVFCU-Proxy.
"""
import json
import os
import secrets
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

from flask import Flask, g, make_response, redirect, render_template, request

ROOT = Path(__file__).resolve().parent.parent
VAR = Path(os.environ.get("KVFCU_VAR_DIR", ROOT / "var"))
PROXY_SECRET = os.environ["KVFCU_PROXY_SECRET"].encode()
IDLE_TIMEOUT = 300  # seconds, real time
ROLES = ("TELLER", "SUPERVISOR", "RESTRICTED")
USERS = {os.environ[f"KVFCU_{r}_USER"]: (os.environ[f"KVFCU_{r}_PASS"], r.lower())
         for r in ROLES if os.environ.get(f"KVFCU_{r}_USER")}

BRANDS = {
    "keystone": dict(name="Keystone Valley Federal Credit Union", short="KVFCU", css="kvfcu.css",
                     logo="kvfcu_logo.svg", lbl_mem="Member No."),
}
BRAND = BRANDS["keystone"]

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
    return dict(brand=BRAND, user=g.get("user"), today=date.today().strftime("%m/%d/%Y"))


def message(title, msg, link=None, link_text="Click here to login again"):
    return render_template("message.html", title=title, msg=msg, link=link, link_text=link_text)


@app.before_request
def _guard():
    if not secrets.compare_digest(request.headers.get("X-KVFCU-Proxy", "").encode(), PROXY_SECRET):
        return make_response("Forbidden\n", 403, {"Content-Type": "text/plain"})
    if request.path.startswith(("/static/", "/__test__/")) or request.path == "/login.do":
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
    db().execute("update sessions set last_seen=? where sid=?", (now, sid))
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
        return message("Member Detail", "No records found.", "/memberSearch.do", "Back to search")
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
