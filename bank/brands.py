"""Per-tenant branding, shared by the bank app and the chaos proxy's fault pages."""
BRANDS = {
    "keystone": dict(name="Keystone Valley Federal Credit Union", short="KVFCU", css="kvfcu.css",
                     logo="kvfcu_logo.svg", prefix="KV", lbl_mem="Member No.", lbl_deposit="Opening Deposit"),
}
BRAND = BRANDS["keystone"]
