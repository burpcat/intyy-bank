"""Per-tenant branding, shared by the bank app and the chaos proxy's fault pages.

KVFCU_VARIANT picks the tenant (one process = one bank). Unknown values fail at startup.
"""
import os

KEYSTONE_COLS = ["acct_no", "acct_type", "balance", "status", "opened"]
BRANDS = {
    "keystone": dict(name="Keystone Valley Federal Credit Union", short="KVFCU", css="kvfcu.css",
                     logo="kvfcu_logo.svg", prefix="KV", lbl_mem="Member No.", lbl_deposit="Opening Deposit",
                     branches=[], cols=KEYSTONE_COLS),
    "lakeshore": dict(name="Lakeshore Members Credit Union", short="LMCU", css="lmcu.css",
                      logo="lmcu_logo.svg", prefix="LM", lbl_mem="Member ID", lbl_deposit="Initial Funding Amount",
                      branches=["LS01 - Harborview", "LS02 - Pier Street", "LS03 - Dunmore Point", "LS04 - Gull Rock"],
                      cols=["acct_type", "acct_no", "status", "opened", "balance"]),
}
VARIANT = os.environ.get("KVFCU_VARIANT", "keystone")
if VARIANT not in BRANDS:
    raise SystemExit(f"KVFCU_VARIANT must be one of {sorted(BRANDS)}, got {VARIANT!r}")
BRAND = BRANDS[VARIANT]
