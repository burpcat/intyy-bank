"""Unit tests for the proxy's pure decision function (no network)."""
from conftest import proxy


def run(seed, entropy, faults, route, count):
    method, path = route.split(" ", 1)
    return [proxy.decide(seed, entropy, faults, method, path, n) for n in range(1, count + 1)]


def test_same_inputs_same_decisions():
    a = run("42", 0.5, [], "POST /openAccountConfirm.do", 200)
    assert a == run("42", 0.5, [], "POST /openAccountConfirm.do", 200)
    assert a != run("43", 0.5, [], "POST /openAccountConfirm.do", 200)


def test_entropy_bounds_and_rate():
    assert all(d["decision"] == "pass" for d in run("s", 0, [], "GET /menu.do", 500))
    every = run("s", 1, [], "GET /menu.do", 500)
    assert all(d["decision"] == "entropy" and d["fault_kind"] == "random" for d in every)
    assert {d["style"] for d in every} == set(proxy.STYLES) and {d["block_point"] for d in every} == {"before", "after"}
    rate = sum(d["decision"] == "entropy" for d in run("s", 0.3, [], "GET /menu.do", 2000)) / 2000
    assert 0.26 < rate < 0.34


def test_static_and_test_endpoints_never_fail_but_static_is_delayed():
    for route in ("GET /static/kvfcu.css", "GET /favicon.ico", "POST /__test__/reset"):
        ds = run("s", 1, [{"id": "x", "kind": "server_error", "route": route, "nth": None, "repeat": "always"}],
                 route, 50)
        assert all(d["decision"] == "pass" for d in ds)
    assert all(200 <= d["delay_ms"] <= 3000 for d in run("s", 0, [], "GET /static/kvfcu.css", 500))


def test_delays():
    ds = run("s", 0, [], "GET /memberSearch.do", 4000)
    slow = [d["delay_ms"] for d in ds if d["delay_ms"] > 3000]
    assert all(200 <= d["delay_ms"] <= 3000 or 5000 <= d["delay_ms"] <= 8000 for d in ds)
    assert 0.035 < len(slow) / len(ds) < 0.065  # about 1 in 20


def fault(kind, route, nth=None, repeat="once"):
    return {"id": kind, "kind": kind, "route": route, "nth": nth, "repeat": repeat, "fired": False}


def test_named_fault_matching():
    r = "GET /memberSearch.do"
    assert [d["decision"] for d in run("s", 0, [fault("server_error", r, 3)], r, 5)] == \
        ["pass", "pass", "named", "pass", "pass"]
    assert [d["decision"] for d in run("s", 0, [fault("server_error", r, 3, "always")], r, 5)] == \
        ["pass", "pass", "named", "named", "named"]
    assert [d["decision"] for d in run("s", 0, [fault("server_error", r)], r, 3)] == ["named", "pass", "pass"]
    assert [d["decision"] for d in run("s", 0, [fault("server_error", r, None, "always")], r, 3)] == ["named"] * 3
    assert all(d["decision"] == "pass" for d in run("s", 0, [fault("server_error", "GET /other.do")], r, 3))
    # named beats entropy
    assert run("s", 1, [fault("maintenance", r)], r, 1)[0]["fault_kind"] == "maintenance"


def test_named_kinds_map_to_block_and_style():
    r = "POST /openAccountConfirm.do"
    got = {k: run("s", 0, [fault(k, r)], r, 1)[0] for k in proxy.NAMED_KINDS}
    assert got["server_error"]["style"] == "error_page" and got["server_error"]["block_point"] == "before"
    assert got["maintenance"]["style"] == "maintenance"
    assert got["drop_after_confirm"]["block_point"] == "after"
    assert got["drop_after_confirm"]["style"] in proxy.DROP_STYLES
    assert got["session_expire"]["block_point"] == got["supervisor_required"]["block_point"] == "none"
    assert all(d["named_id"] == k for k, d in got.items())
