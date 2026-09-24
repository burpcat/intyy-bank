"""Chaos proxy: the only public entry point to the bank app.

Run: uv run python -m chaos.proxy   (from the repo root)

Every decision (fault or not, which style, before/after block, delay, pop-up text) is a pure
function of seed + route key + per-route counter, so the same seed and entropy replay the
same faults. Counters are global: one run at a time.
"""
import asyncio
import hashlib
import html
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import ClientSession, web
from multidict import CIMultiDict

from bank.brands import BRAND

ROOT = Path(__file__).resolve().parent.parent
VAR = Path(os.environ.get("KVFCU_VAR_DIR", ROOT / "var"))
SECRET = os.environ["KVFCU_PROXY_SECRET"]
TEST_MODE = os.environ.get("KVFCU_TEST_MODE") == "1"
PROXY_PORT = int(os.environ.get("KVFCU_PROXY_PORT", "8080"))
APP_URL = f"http://127.0.0.1:{os.environ.get('KVFCU_APP_PORT', '8081')}"
# Scales real sleeps only (delays, hang); logged delay_ms is always the nominal value. Tests use ~0.001.
DELAY_SCALE = float(os.environ.get("KVFCU_DELAY_SCALE", "1"))
HANG_MS = 45_000
LOG_PATH = VAR / "fault_log.jsonl"

STYLES = ["error_page", "blank", "hang", "unavailable", "logout", "maintenance"]
DROP_STYLES = ["error_page", "blank", "hang", "unavailable"]
NAMED_KINDS = {  # kind -> (block_point, style); None style = decided elsewhere or no page
    "session_expire": ("none", None),
    "known_popup": ("before", "known_popup"),
    "unknown_popup": ("before", "unknown_popup"),
    "supervisor_required": ("none", None),
    "server_error": ("before", "error_page"),
    "maintenance": ("before", "maintenance"),
    "drop_after_confirm": ("after", None),
}
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
               "transfer-encoding", "upgrade", "content-length", "content-encoding"}

state = {"entropy": 0.0, "seed": "0", "counters": {}, "faults": [], "seq": 0, "log": []}


# ---------- deterministic decisions ----------

def h(seed, route, n, tag):
    """Uniform float in [0, 1) from seed + route key + counter + purpose tag."""
    digest = hashlib.sha256(f"{seed}|{route}|{n}|{tag}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def choose(options, seed, route, n, tag):
    return options[int(h(seed, route, n, tag) * len(options))]


def is_counted(path):
    return not (path.startswith("/static/") or path == "/favicon.ico" or path.startswith("/__test__/"))


def delay_ms(seed, route, n, counted):
    if counted and h(seed, route, n, "slow") < 0.05:  # ~1 in 20 counted requests
        return 5000 + int(h(seed, route, n, "delay") * 3000)
    return 200 + int(h(seed, route, n, "delay") * 2800)


def fault_matches(fault, route, n):
    if fault["route"] != route or fault.get("fired"):
        return False
    if fault.get("nth") is None:
        return True
    return n == fault["nth"] if fault["repeat"] == "once" else n >= fault["nth"]


def decide(seed, entropy, faults, method, path, n):
    """Decision for the n-th request to METHOD path. Marks a matched once-fault as fired."""
    route = f"{method} {path}"
    counted = is_counted(path)
    d = dict(decision="pass", fault_kind=None, block_point="none", style=None, named_id=None,
             delay_ms=delay_ms(seed, route, n, counted))
    if not counted:
        return d
    for f in faults:
        if fault_matches(f, route, n):
            if f["repeat"] == "once":
                f["fired"] = True
            block, style = NAMED_KINDS[f["kind"]]
            if f["kind"] == "drop_after_confirm":
                style = choose(DROP_STYLES, seed, route, n, "drop_style")
            return {**d, "decision": "named", "fault_kind": f["kind"], "block_point": block,
                    "style": style, "named_id": f["id"]}
    if h(seed, route, n, "pick") < entropy:
        return {**d, "decision": "entropy", "fault_kind": "random",
                "block_point": choose(["before", "after"], seed, route, n, "block"),
                "style": choose(STYLES, seed, route, n, "style")}
    return d


# ---------- fault pages (in the bank's own style) ----------

def bank_page(title, body, status=200):
    doc = f"""<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<title>{BRAND['short']} :: {title}</title>
<link rel="stylesheet" type="text/css" href="/static/{BRAND['css']}"></head>
<body>
<table width="100%" border="0" cellspacing="0" cellpadding="3"><tr><td class="pgtitle">&raquo; {title}</td></tr></table>
<table width="70%" border="0" cellspacing="0" cellpadding="12" align="center"><tr><td>
{body}
</td></tr></table>
<table width="100%" border="0" cellspacing="0" cellpadding="4" class="footer"><tr><td align="center">
Best viewed in Internet Explorer 6.0 at 1024x768 resolution.<br>&copy; 2008 {BRAND['name']}.
</td></tr></table>
</body></html>"""
    return web.Response(status=status, text=doc, content_type="text/html")


POPUP_TITLES = ["Important Notice", "Regulatory Update", "Security Advisory", "Action Required", "System Message"]
POPUP_TEXTS = [
    "Please verify the member's mailing address before proceeding.",
    "Revised OFAC screening guidelines are effective from the 1st of next month.",
    "Your password will expire in 7 days. Please change it from the Profile menu.",
    "Dormant account review is pending for your branch. Please complete it this week.",
    "Mandatory AML refresher training must be completed by month end.",
]
POPUP_BUTTONS = [["Continue"], ["Acknowledge", "Skip"], ["Proceed", "Remind Me Tomorrow"], ["I Agree"],
                 ["OK", "Close"]]


def attr(value):
    """HTML-attribute-escape, keeping CR/LF exact through a hidden-field round trip."""
    return html.escape(value, quote=True).replace("\r", "&#13;").replace("\n", "&#10;")


def interstitial(request, body, title, text, buttons):
    """Pop-up page whose every button re-sends the original request unchanged."""
    fields = parse_qsl(request.query_string, keep_blank_values=True) if request.method == "GET" else \
        parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
    hidden = "".join(f'<input type="hidden" name="{attr(k)}" value="{attr(v)}">' for k, v in fields)
    btns = "&nbsp;".join(f'<input type="submit" class="btn" value="{html.escape(b)}">' for b in buttons)
    return bank_page(title, f"""<table width="100%" class="grid" cellspacing="0">
<tr><th align="left">{html.escape(title)}</th></tr>
<tr><td style="padding:12px">{html.escape(text)}
<form method="{request.method.lower()}" action="{attr(request.path)}">{hidden}<br>{btns}</form></td></tr>
</table>""")


async def fault_response(request, body, entry, n):
    style = entry["style"]
    route = f"{request.method} {request.path}"
    seed = state["seed"]
    if style == "error_page":
        return bank_page("500 Internal Server Error",
                         "<b>500 Internal Server Error</b><br><br>The server encountered an internal error and was "
                         "unable to complete your request. Please contact the system administrator.", 500)
    if style == "blank":
        return web.Response(status=200, text="", content_type="text/html")
    if style == "unavailable":
        return bank_page("Service Unavailable", "Service temporarily unavailable. Please try later.", 503)
    if style == "maintenance":
        return bank_page("Scheduled Maintenance", "<b>System under scheduled maintenance.</b><br><br>"
                         "Services will be restored shortly. We regret the inconvenience caused.")
    if style == "logout":
        await kill_session(request)
        raise web.HTTPFound("/login.do")
    if style == "known_popup":
        return interstitial(request, body, "KYC Update", "Please update member KYC details", ["OK", "Remind Later"])
    if style == "unknown_popup":
        return interstitial(request, body, choose(POPUP_TITLES, seed, route, n, "popup_title"),
                            choose(POPUP_TEXTS, seed, route, n, "popup_text"),
                            choose(POPUP_BUTTONS, seed, route, n, "popup_buttons"))
    if style == "hang":
        await asyncio.sleep(HANG_MS / 1000 * DELAY_SCALE)
        request.transport.close()  # no reply at all; the connection just closes
        return web.Response()
    raise AssertionError(f"unknown style {style}")


# ---------- upstream ----------

def upstream_headers(request, extra=None):
    headers = CIMultiDict((k, v) for k, v in request.headers.items()
                          if k.lower() not in HOP_HEADERS and not k.lower().startswith("x-kvfcu-"))
    headers["X-KVFCU-Proxy"] = SECRET
    headers.update(extra or {})
    return headers


async def forward(request, body, extra=None):
    async with request.app["client"].request(request.method, APP_URL + request.path_qs, data=body,
                                             headers=upstream_headers(request, extra),
                                             allow_redirects=False) as r:
        payload = await r.read()
        headers = CIMultiDict((k, v) for k, v in r.headers.items() if k.lower() not in HOP_HEADERS)
    return web.Response(status=r.status, body=payload, headers=headers)


async def kill_session(request):
    """End the caller's session in the app (the cookie stays, so the next page says 'Session expired')."""
    if "Cookie" in request.headers:
        async with request.app["client"].get(APP_URL + "/logout.do", allow_redirects=False,
                                             headers={"Cookie": request.headers["Cookie"],
                                                      "X-KVFCU-Proxy": SECRET}) as r:
            await r.read()


def write_log(entry):
    state["log"].append(entry)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def clear_log():
    state["log"].clear()
    state["seq"] = 0
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("")


# ---------- main handler ----------

async def handle(request):
    if request.path.startswith("/__test__/"):
        if not TEST_MODE:
            raise web.HTTPNotFound()
        return await test_endpoint(request)
    body = await request.read()
    route = f"{request.method} {request.path}"
    # No await between counting and deciding: the single event loop makes this atomic.
    n = state["counters"][route] = state["counters"].get(route, 0) + 1
    d = decide(state["seed"], state["entropy"], state["faults"], request.method, request.path, n)
    if is_counted(request.path):
        state["seq"] += 1
        write_log({"seq": state["seq"], "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                   "method": request.method, "path": request.path, "route_count": n, **d})
    await asyncio.sleep(d["delay_ms"] / 1000 * DELAY_SCALE)
    if d["decision"] == "pass":
        return await forward(request, body)
    kind = d["fault_kind"]
    if kind == "session_expire":
        await kill_session(request)
        return await forward(request, body)
    if kind == "supervisor_required":
        return await forward(request, body, {"X-KVFCU-Force-Override": "1"})
    if d["block_point"] == "after":
        await forward(request, body)  # the app processes and commits; the reply is dropped
    return await fault_response(request, body, d, n)


# ---------- test endpoints ----------

def bad(msg):
    return web.json_response({"error": msg}, status=400)


async def json_body(request):
    try:
        return await request.json()
    except ValueError:
        return None


def validate_fault(f):
    if not isinstance(f, dict):
        return "each fault must be an object"
    if not isinstance(f.get("id"), str) or not f["id"]:
        return "id must be a non-empty string"
    if f.get("kind") not in NAMED_KINDS:
        return f"kind must be one of {sorted(NAMED_KINDS)}"
    if not isinstance(f.get("route"), str) or not re.fullmatch(r"[A-Z]+ /\S*", f["route"]):
        return 'route must look like "POST /somePage.do"'
    nth = f.get("nth")
    if nth is not None and (not isinstance(nth, int) or isinstance(nth, bool) or nth < 1):
        return "nth must be a positive integer"
    if f.get("repeat", "once") not in ("once", "always"):
        return 'repeat must be "once" or "always"'
    return None


def public_faults():
    return [{k: f.get(k) for k in ("id", "kind", "route", "nth", "repeat", "fired")} for f in state["faults"]]


async def test_endpoint(request):
    route = f"{request.method} {request.path}"
    if route == "POST /__test__/chaos":
        body = await json_body(request)
        if not isinstance(body, dict):
            return bad('body must be {"entropy": 0..1, "seed": ...}')
        entropy = body.get("entropy", state["entropy"])
        if isinstance(entropy, bool) or not isinstance(entropy, (int, float)) or not 0 <= entropy <= 1:
            return bad("entropy must be a number from 0 to 1")
        state["entropy"], state["seed"] = float(entropy), str(body.get("seed", state["seed"]))
        return web.json_response({"entropy": state["entropy"], "seed": state["seed"]})
    if route == "POST /__test__/faults":
        body = await json_body(request)
        new = body.get("faults", [body]) if isinstance(body, dict) else body
        if not isinstance(new, list):
            return bad("body must be a fault object, a list of faults, or {\"faults\": [...]}")
        ids = {f["id"] for f in state["faults"]}
        for f in new:
            err = validate_fault(f)
            if err is None and f["id"] in ids:
                err = f"duplicate id {f['id']}"
            if err:
                return bad(err)
            ids.add(f["id"])
        state["faults"].extend({**f, "nth": f.get("nth"), "repeat": f.get("repeat", "once"), "fired": False}
                               for f in new)
        return web.json_response({"faults": public_faults()})
    if route == "DELETE /__test__/faults":
        state["faults"].clear()
        return web.json_response({"faults": []})
    if route == "GET /__test__/faultlog":
        return web.json_response({"entries": state["log"]})
    if route == "POST /__test__/reset":
        state["counters"].clear()
        state["faults"].clear()
        clear_log()
        return await forward(request, await request.read())
    if route in ("POST /__test__/clock", "GET /__test__/oracle"):
        return await forward(request, await request.read())
    raise web.HTTPNotFound()


# ---------- app ----------

async def client_ctx(app):
    async with ClientSession() as client:
        app["client"] = client
        yield


def make_app():
    clear_log()
    app = web.Application(client_max_size=10 * 1024 * 1024)
    app.cleanup_ctx.append(client_ctx)
    app.router.add_route("*", "/{tail:.*}", handle)
    return app


if __name__ == "__main__":
    print(f"chaos proxy on http://127.0.0.1:{PROXY_PORT} -> {APP_URL} (test mode {'on' if TEST_MODE else 'off'})")
    web.run_app(make_app(), host="127.0.0.1", port=PROXY_PORT, print=None)
