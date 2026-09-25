# KVFCU bank (intyy test target)

Spec: `Bank App Design.md`. The intyy side may read only `CONTRACT.md`; keep intyy knowledge out of every
other file. Decision history: `docs/decisions.md`.

## Current state

**Works:** all 10 build steps in the design doc: bank app, chaos proxy, test endpoints and oracle, seed/reset/clock, strip-semantics, Lakeshore variant, label removal, CONTRACT.md 1.1.0. `make test` gives 72 passed.

**Broken / unverified:** nothing known broken. Never clicked through in an interactive browser: supervisor pop-up (open, approve, reload opener, close), `confirm()` dialogs, the 4-minute session alert.

**Commands** (this repo has no agent or replay; those live in intyy):
- Run the target: `cp .env.example .env`, fill in the credentials, `make up`, then open http://127.0.0.1:8080/login.do
- Test mode: `KVFCU_TEST_MODE=1 make up`. Variants: add `KVFCU_VARIANT=lakeshore`, `KVFCU_STRIP_SEMANTICS=1`, or `KVFCU_DROP_LABELS=0.3 KVFCU_LABEL_SEED=7`.
- Tests: `make test`. Reset data: `make reset` (or `POST /__test__/reset` in test mode).

**Gotchas:**
- Every main-frame page bumps `pageSeq`. In tests, post a form straight after loading its page, or you get "Page Expired", and a test can pass while checking nothing.
- `<input type=image>` gets an implicit "Submit" accessible name, so strip mode uses `<img>` + onclick.
- The label checker must skip `type=button` inputs, whose `value` is their name.
- New form fields must use the `lbl()` / `fid()` helpers, never a bare `<label>`.
- Proxy tests start real subprocesses on free ports with `KVFCU_DELAY_SCALE=0.001`.
- In a worktree-isolated Claude session, compound shell commands and quoted executable paths get refused. Use plain single commands, or a Python script.
- `git merge --autostash` works around uncommitted local edits in the main checkout.

**Next task:** one manual browser pass after `make up`: log in, open a sub-account over $5,000, approve in the pop-up, confirm, close it. Then hand CONTRACT.md 1.1.0 to the intyy side.
