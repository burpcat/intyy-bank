# Decisions log

## 2026-09-24: initial build (design doc steps 1-10)

### Decisions (rejected options in brackets)
- Repo root is the doc's `kvfcu/`. [Nested `kvfcu/` folder: no benefit.]
- Password field is `type="password"`. [Visible text: would put the secret in screenshots.]
- Back/Refresh after a POST is caught by a per-session `pageSeq`. The two confirm POSTs are exempt, so a refresh creates a second account, as the spec requires. [One-time tokens: would add double-submit protection, which the spec forbids.]
- The supervisor pop-up re-submits a hidden `reloadForm` in its opener. [`opener.location.reload()`: triggers the browser's resubmit prompt.]
- Transfer funding debits primary savings. Too little balance gives a validation error, checked on review and again on confirm. (User decision.)
- The close page has no radio button for primary savings; the server still refuses it. (User decision.)
- Label removal takes away only the HTML `<label>` association, from a seeded fixed set. Visible names stay. (User decision.) [Removing visible text: stops looking like a bank. New random set each page load: not reproducible.]
- A named fault with `nth` + `always` fires from request n onwards. [Every nth request.]
- `drop_after_confirm` style is seeded from error_page / blank / hang / unavailable. The maintenance page returns 200. Every pop-up button continues. Reset keeps entropy, seed and clock.
- Strip mode uses `<img>` + onclick, with labels drawn as pixel rectangles in SVG. [`<input type=image>`: browsers give it the name "Submit". SVG `<text>`: leaks the label.]
- `bank/brands.py` is shared by the app and the proxy's fault pages.

### Cut / stubbed / mocked (seams)
- No real money movement. Cash and Check funding are only recorded (`bank/app.py` open_account_confirm).
- Fault pages are rendered by the proxy (`chaos/proxy.py` bank_page), not by app templates.
- Both variants use the same seed data. Branch codes are made up (`bank/brands.py`).
- Parts not yet tried in an interactive browser: see CLAUDE.md "Current state".

### Artifact schema changes
- CONTRACT.md 1.0.0 -> 1.1.0: added `KVFCU_DROP_LABELS` and `KVFCU_LABEL_SEED`. Additive, not breaking.
- Fault log JSONL: 11 fields, defined in CONTRACT §6.3 (first version).
- DB schema in `seed/seed.py`: no version number; reset replaces the whole file.

### Error cases (how detected, class)
| Case | Detection | Class |
|---|---|---|
| No records found | member lookup is empty | business outcome |
| Member not eligible | open sub-accounts >= limit (on form, review, confirm) | business outcome |
| Deposit invalid / too low / too high | `parse_amount` + limits on review | business outcome |
| Insufficient primary balance | `short_of_funds` on review and confirm | business outcome |
| Permission denied | `can_transact()` on every open/close route | business outcome |
| Primary savings cannot be closed / already closed | `closable()` | business outcome |
| Supervisor approval required | over limit or forced flag; confirm refuses until approved | business outcome |
| Page Expired | stale `pageSeq` on a non-confirm POST | recoverable |
| Session Expired | idle > 300 s, or unknown session id | recoverable |
| Proxy faults (8 styles) | fault log entry; oracle resolves a lost reply | recoverable |
| 403 Forbidden | missing or wrong proxy header | hard failure |
| Startup exit | missing secret, bad variant, `KVFCU_DROP_LABELS` outside 0-1, bad fixed date | hard failure |

### Safety
- No bypass: the app listens on 127.0.0.1:8081 only and needs the secret header (constant-time compare). The proxy strips `X-KVFCU-*` headers sent by clients.
- Test endpoints return 404 unless `KVFCU_TEST_MODE=1`. The oracle is for the harness only.
- Credentials come from env vars only. `.env` is gitignored; `.env.example` holds names only.
- Fake PII only: SSNs start with 9, phones are 555-01xx.
- Outside link (NCUA) must not be followed (CONTRACT §10).
- Known gaps: Flask dev server; no CSRF protection (deliberate legacy); the secret is regenerated on each `make up` unless set; dev env vars (`KVFCU_VAR_DIR`, ports) are not guarded.

### Handoff / control model
- Not applicable to this repo, which has no agent. The only interface is CONTRACT.md. No change.

### Reuse ideas
- Multi-tenant: `brands.py` can grow more banks (labels, column order, extra required fields).
- Legacy web: the pure seeded `decide()` chaos proxy can sit in front of any HTTP app.
- Semantic degradation: label removal and strip mode as general-purpose switches.
- Desktop: not covered; it would need something other than an HTTP proxy.

### Evidence runs
- None; this repo has no `/evidence/`. Test result: 72 passed on master `2a49a9f`.
