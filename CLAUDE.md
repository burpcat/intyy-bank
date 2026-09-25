# KVFCU bank (intyy test target)

Spec: `Bank App Design.md`. The intyy side may read only `CONTRACT.md`; keep intyy knowledge out of every
other file. Decision history: `docs/decisions.md`.

## Current state

**Works:** all 10 build steps in the design doc: bank app, chaos proxy, test endpoints and oracle, seed/reset/clock, strip-semantics, Lakeshore variant, label removal, CONTRACT.md 1.1.0. `make test` passes.

**Broken / unverified:** nothing known broken. Never clicked through in an interactive browser: supervisor pop-up (open, approve, reload opener, close), `confirm()` dialogs, the 4-minute session alert.

**Commands** (this repo has no agent or replay):
- Run the target: `cp .env.example .env`, fill in the credentials, `make up`, then open http://127.0.0.1:8080/login.do
- Test mode: `KVFCU_TEST_MODE=1 make up`. Variants: add `KVFCU_VARIANT=lakeshore`, `KVFCU_STRIP_SEMANTICS=1`, or `KVFCU_DROP_LABELS=0.3 KVFCU_LABEL_SEED=7`.
- Tests: `make test`.
- Reset: `POST /__test__/reset` (test mode) restores data and ends sessions, and also clears the proxy's counters, named faults, and fault log. `make reset` restores data and sessions only; restart `make up` to clear chaos state.

**Gotchas:**
- Every main-frame page bumps `pageSeq`. In tests, post a form straight after loading its page, or you get "Page Expired", and a test can pass while checking nothing.
- Label checkers must skip `hidden`, `submit`, `reset`, `image`, and `button` inputs: those have no visible field to label, or they carry their own name in `value`.
- New form fields must use the `lbl()` / `fid()` helpers, never a bare `<label>`.
- Proxy tests start real subprocesses on free ports with `KVFCU_DELAY_SCALE=0.001`.

**Next task:** one manual browser pass after `make up`:
- log in as the teller;
- open a Money Market sub-account for a valid member, $6,000 funded by **Cash** (seeded primary balances are too small for Transfer);
- approve with supervisor credentials in the pop-up, then confirm;
- close that new sub-account.

After that, CONTRACT.md 1.1.0 is ready to hand over.
