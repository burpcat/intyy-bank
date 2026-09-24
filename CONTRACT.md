# KVFCU test target: contract

**Version 1.0.0** (semantic versioning: a breaking change to anything below bumps the major version).

This is the only document the automation side may rely on. Everything not written here (screens, page
layouts, labels, markup, URLs, flows, business rules and their limits) must be discovered from the live app.

---

## 1. Starting it

```
make up
```

- Base URL: **`http://127.0.0.1:8080`**. This is the only entry point. Nothing else is reachable.
- Stop with Ctrl-C.
- Credentials and options are read from environment variables. A `.env` file in the repo root is loaded
  automatically (copy `.env.example`).

## 2. One run at a time

Fault counters, named faults, the fault log, and the data are **global** to the running instance.
Two runs in parallel would disturb each other and break reproducibility. Run one at a time.

## 3. Test users

| Role | Rights | Username variable | Password variable |
|---|---|---|---|
| Teller | Search, view, open and close sub-accounts | `KVFCU_TELLER_USER` | `KVFCU_TELLER_PASS` |
| Supervisor | Everything a teller can do, plus approve overrides | `KVFCU_SUPERVISOR_USER` | `KVFCU_SUPERVISOR_PASS` |
| Restricted | Search and view only | `KVFCU_RESTRICTED_USER` | `KVFCU_RESTRICTED_PASS` |

Values are never stored in the repo. Whoever runs the instance sets them.

## 4. Options

| Variable | Values | Effect |
|---|---|---|
| `KVFCU_TEST_MODE` | `1` or unset | `1` enables the test endpoints (section 8), entropy, and named faults. Otherwise those are off and every test endpoint returns 404. Baseline slowness (section 6) applies either way. |
| `KVFCU_VARIANT` | `keystone` (default), `lakeshore` | Which bank this instance is. `lakeshore` is a second credit union on the same vendor software. It differs in branding and in small screen details. One instance is one bank; restart to switch. |
| `KVFCU_STRIP_SEMANTICS` | `1` or unset | Some buttons render as images with no text, no alt text, no title, and no accessible name. |
| `KVFCU_FIXED_DATE` | `YYYY-MM-DD` or unset | Fixes the business date at startup (section 7). |
| `KVFCU_DELAY_SCALE` | number, default `1` | Multiplies the real waiting time of delays and hangs, for fast harness runs. The fault log always records the nominal, unscaled `delay_ms`, so logs stay comparable. |

Both variants start from the same seed data (section 5).

## 5. Seed data

Fake people only. After every reset the data is exactly the seed. The same sequence of actions after a
reset produces the same account and reference numbers.

| Category | Member numbers |
|---|---|
| Valid, with room for new sub-accounts (20) | `100107` `100114` `100121` `100128` `100135` `100142` `100149` `100156` `100163` `100170` `100177` `100184` `100191` `100198` `100205` `100212` `100219` `100226` `100233` `100240` |
| At the sub-account limit (5) | `100247` `100254` `100261` `100268` `100275` |
| Do not exist (5) | `999001` `100101` `100199` `100250` `123456` |

## 6. Chaos

Every request passes through a chaos layer before the bank.

**Route key.** `METHOD` + space + URL path, without the query string. Example: `POST /example/path`.
Each route key has its own counter. It starts at 1 for the first request after a reset.

**Counted requests.** HTML pages and form posts. Static files (anything under `/static/`, and
`/favicon.ico`) and `/__test__/` endpoints are never failed and never logged. Static files are still delayed.

**Decision per counted request**, in this order:

1. If a named fault matches (section 6.2), apply it.
2. Otherwise, if entropy picks this request, apply a random fault style.
3. Otherwise, pass the request through.
4. In every case, wait the seeded delay first.

Every choice comes from a hash of **seed + route key + counter**. The same seed, entropy, and named faults
with the same sequence of requests give the same faults, styles, block points, pop-up texts, and delays,
every time.

**Delay.** Normally 200 ms to 3 s. About 1 in 20 counted requests is slow: 5 to 8 s. This applies even when
test mode is off.

**Entropy.** A number from 0 to 1. At 0 nothing is injected. At 1 every counted request fails. A
random fault picks a style from the table below (not the pop-up styles), and a block point:

- `before`: the request never reaches the bank. Nothing happens.
- `after`: the bank processes the request and commits any change. The reply is then thrown away and the
  operator sees the fault instead.

### 6.1 Fault styles (what the operator sees)

| Style | Operator sees |
|---|---|
| `error_page` | A generic "500 Internal Server Error" page in the bank's style (status 500) |
| `blank` | An empty white page (status 200) |
| `hang` | No reply for 45 s, then the connection closes |
| `unavailable` | "Service temporarily unavailable. Please try later." (status 503) |
| `logout` | The session is ended; a redirect to the login page |
| `maintenance` | A "System under scheduled maintenance" page (status 200) |
| `known_popup` | An interstitial: "Please update member KYC details", buttons OK and Remind Later |
| `unknown_popup` | An interstitial with seeded, varied title, text, and buttons |

On both interstitials, every button continues by re-sending the original request unchanged. The
interstitial itself counts as a blocked (`before`) request.

### 6.2 Named faults

A named fault is an object:

| Field | Type | Meaning |
|---|---|---|
| `id` | non-empty string, unique | Chosen by the caller. Appears in the fault log as `named_id`. |
| `kind` | see below | What happens |
| `route` | route key, e.g. `"GET /example/path"` | Which route it applies to |
| `nth` | positive integer, optional | Which request to that route triggers it (the route counter value) |
| `repeat` | `"once"` (default) or `"always"` | See below |

| `nth` | `repeat` | Fires on |
|---|---|---|
| set, e.g. 3 | `once` | only request 3 |
| set, e.g. 3 | `always` | request 3 and every later one |
| unset | `once` | the next request to the route |
| unset | `always` | every request to the route |

A `once` fault whose `nth` has already passed never fires. Reset clears counters and named faults, so the
usual order is: reset, add faults, run.

| `kind` | Effect | Block point | Style |
|---|---|---|---|
| `session_expire` | The session ends just before the request; the bank then shows its own session-expired page | `none` | none |
| `known_popup` | `known_popup` interstitial | `before` | `known_popup` |
| `unknown_popup` | `unknown_popup` interstitial | `before` | `unknown_popup` |
| `supervisor_required` | The request passes through. The next sub-account application submitted in that session requires supervisor approval, whatever its values. | `none` | none |
| `server_error` | `error_page` | `before` | `error_page` |
| `maintenance` | `maintenance` | `before` | `maintenance` |
| `drop_after_confirm` | After-endpoint block. Intended for a confirming action: the change is committed, the reply is lost. | `after` | seeded: one of `error_page`, `blank`, `hang`, `unavailable` |

If several named faults match one request, the first one added wins. A named fault beats entropy.

### 6.3 Fault log

JSON Lines at `var/fault_log.jsonl`, also served by `GET /__test__/faultlog`. One line per counted
request, including pass-throughs, in arrival order. This is the ground truth for what was injected.

| Field | Type | Values |
|---|---|---|
| `seq` | int | 1, 2, 3, … since the last reset |
| `time` | string | UTC ISO 8601 with milliseconds. The only field that differs between identical runs. |
| `method` | string | `GET`, `POST`, … |
| `path` | string | URL path, no query string |
| `route_count` | int | This route's counter for this request |
| `decision` | string | `pass`, `entropy`, `named` |
| `fault_kind` | string or null | null for `pass`; `"random"` for `entropy`; the named `kind` for `named` |
| `block_point` | string | `before`, `after`, `none` |
| `style` | string or null | A style from section 6.1, or null |
| `named_id` | string or null | The named fault's `id` |
| `delay_ms` | int | The nominal delay applied |

The log is cleared on reset and whenever the instance starts.

## 7. Business date

Only business dates (for example the date an account was opened or closed) follow the clock. Session
timeouts always use real time.

- At startup: `KVFCU_FIXED_DATE=2026-01-15`.
- At runtime (test mode): `POST /__test__/clock` (section 8).
- Unset: today's real date.

## 8. Test endpoints

Only when `KVFCU_TEST_MODE=1`; otherwise 404. All take and return JSON. A malformed request gets 400 with
`{"error": "<message>"}`.

### `POST /__test__/chaos`
Set entropy and/or seed. Omitted fields keep their current value. Defaults at startup: entropy `0`,
seed `"0"`. Not changed by reset.
```json
request:  {"entropy": 0.25, "seed": "run-42"}
response: {"entropy": 0.25, "seed": "run-42"}
```
`seed` may be any JSON value; it is used as its string form.

### `POST /__test__/faults`
Add named faults (section 6.2). The body may be one fault object, a list of them, or `{"faults": [...]}`.
If any fault is invalid or reuses an `id`, the whole request is rejected with 400 and nothing is added.
```json
request:  {"faults": [{"id": "drop1", "kind": "drop_after_confirm", "route": "POST /example/path", "nth": 1}]}
response: {"faults": [{"id": "drop1", "kind": "drop_after_confirm", "route": "POST /example/path",
                       "nth": 1, "repeat": "once", "fired": false}]}
```
The response lists all active faults. `fired` becomes true once a `once` fault has triggered.

### `DELETE /__test__/faults`
Remove all named faults.
```json
response: {"faults": []}
```

### `GET /__test__/faultlog`
```json
response: {"entries": [ { ...fault log line, section 6.3... } ]}
```

### `POST /__test__/reset`
Restores the seed data exactly, ends all sessions, zeroes every route counter, removes named faults, and
clears the fault log. It does not change entropy, seed, or the business date.
```json
response: {"ok": true}
```

### `POST /__test__/clock`
```json
request:  {"date": "2026-01-15"}         response: {"date": "2026-01-15", "fixed": true}
request:  {"date": null}                 response: {"date": "<today>", "fixed": false}
```

### `GET /__test__/oracle?notes=<text>`
Ground truth after a lost reply. `notes` is the exact free-form notes text entered when a sub-account was
opened, URL-encoded. It is compared exactly: whitespace, case, and line breaks all count.
```json
response: {"exists": true, "count": 1,
           "accounts": [{"account_number": "400100000159", "status": "OPEN", "confirmation_number": "KV10000001"}]}
```
- `count` can be above 1 if the same application was confirmed more than once.
- `status` is `OPEN` or `CLOSED`.
- `exists: false` gives `count: 0` and an empty list.

The oracle is for the test harness only. The automation under test must not call it during normal runs.

## 9. Resetting outside test mode

`make reset` restores the seed data and ends all sessions, with or without test mode. It does not touch
chaos counters or the fault log; restart the instance to clear those, or use `POST /__test__/reset` in
test mode.

## 10. Outside link

The footer carries one external link, to **`https://www.ncua.gov`**. It is outside the system under test.
It must never be followed.
