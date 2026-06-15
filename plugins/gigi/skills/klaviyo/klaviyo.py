# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31"]
# ///
"""
Klaviyo email/SMS analyst — audit what email marketing we have and what's missing
(money left on the table). Lists flows + campaigns, runs the 10-flow lifecycle GAP
audit, and pulls flow revenue. Read-only.

Key in KB secret KLAVIYO_<STORE>_PRIVATE_KEY (e.g. KLAVIYO_ESTEBAN_PRIVATE_KEY).
Default store: esteban.

Usage:
    KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
    export KLAVIYO_ESTEBAN_PRIVATE_KEY="$(uv run "$KB" secret-get KLAVIYO_ESTEBAN_PRIVATE_KEY)"
    uv run klaviyo.py account  --store esteban
    uv run klaviyo.py flows    --store esteban      # all flows + status + trigger
    uv run klaviyo.py gap      --store esteban      # which of the 10 standard lifecycle flows are MISSING
    uv run klaviyo.py campaigns --store esteban     # recent email campaigns
"""
import argparse, os, subprocess, sys
import requests

REVISION = os.environ.get("KLAVIYO_REVISION", "2024-10-15")
BASE = "https://a.klaviyo.com/api"

# 10-flow ecommerce lifecycle checklist: label -> match keywords (EN + RO)
STD_FLOWS = {
    "Welcome series": ["welcome", "bun venit", "newsletter"],
    "Abandoned cart": ["abandoned cart", "cart", "cos abandonat", "cos parasit"],
    "Abandoned checkout": ["checkout", "abandoned checkout", "comanda neterminata"],
    "Browse abandonment": ["browse", "viewed product", "produs vizualizat"],
    "Post-purchase": ["post purchase", "post-purchase", "thank you", "dupa achizitie", "multumim", "post achizitie"],
    "Winback / Sunset": ["winback", "win back", "win-back", "sunset", "lapsed", "recastigare", "reactivare"],
    "Review request": ["review", "recenzie", "feedback", "parere"],
    "Replenishment": ["replenish", "reorder", "reaprovizionare", "reachizitie"],
    "Birthday / Anniversary": ["birthday", "anniversary", "zi de nastere", "aniversare"],
    "VIP / Loyalty": ["vip", "loyalty", "fidel", "loial"],
}

def _key(store):
    name = f"KLAVIYO_{store.upper()}_PRIVATE_KEY"
    v = os.environ.get(name)
    if v: return v
    for c in [os.environ.get("KB_PY"),
              os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")]:
        if c and os.path.exists(c):
            try:
                return subprocess.run(["uv", "run", os.path.abspath(c), "secret-get", name],
                                      capture_output=True, text=True, timeout=60).stdout.strip()
            except Exception:
                pass
    sys.exit(f"Missing {name} (set it or kb.py secret-get {name}).")

def _h(store):
    return {"Authorization": f"Klaviyo-API-Key {_key(store)}", "revision": REVISION, "accept": "application/json"}

def _get(store, path, params=None):
    url = path if path.startswith("http") else f"{BASE}{path}"
    r = requests.get(url, headers=_h(store), params=params, timeout=60)
    if r.status_code != 200:
        sys.exit(f"Klaviyo {r.status_code}: {r.text[:300]}")
    return r.json()

def _all(store, path, params=None):
    out = []; url = f"{BASE}{path}"
    while url:
        j = _get(store, url, params); params = None
        out += j.get("data", [])
        url = (j.get("links") or {}).get("next")
    return out

def cmd_account(args):
    j = _get(args.store, "/accounts/")
    a = (j.get("data") or [{}])[0].get("attributes", {})
    print(f"Klaviyo account — {args.store}")
    print(f"  {a.get('contact_information',{}).get('organization_name','?')} | {a.get('industry','')} | tz {a.get('timezone','')}")

def cmd_flows(args):
    flows = _all(args.store, "/flows/")
    print(f"\nFlows — {args.store} ({len(flows)})")
    print(f"  {'status':<10}{'trigger':<16} name")
    for f in flows:
        a = f.get("attributes", {})
        print(f"  {a.get('status',''):<10}{(a.get('trigger_type') or '')[:15]:<16} {a.get('name','')[:54]}")

def cmd_gap(args):
    flows = _all(args.store, "/flows/")
    live = [(f.get('attributes',{}).get('name','') or '').lower()
            for f in flows if f.get('attributes',{}).get('status') == 'live']
    allnames = [(f.get('attributes',{}).get('name','') or '').lower() for f in flows]
    print(f"\nLifecycle flow GAP audit — {args.store}  ({len(flows)} flows, {len(live)} live)")
    have = miss = 0
    for label, kws in STD_FLOWS.items():
        is_live = any(any(k in n for k in kws) for n in live)
        exists = any(any(k in n for k in kws) for n in allnames)
        if is_live: mark, note = "✅", "live"; have += 1
        elif exists: mark, note = "🟡", "EXISTĂ dar nu e live (draft/manual)"
        else: mark, note = "❌", "LIPSEȘTE — bani lăsați pe masă"; miss += 1
        print(f"  {mark} {label:<24} {note}")
    print(f"\n  {have}/10 active. {miss} lipsesc complet. Flow-urile aduc tipic 30-40%+ din venitul de email — fiecare lipsă = pierdere.")

def cmd_campaigns(args):
    j = _get(args.store, "/campaigns/", {"filter": "equals(messages.channel,'email')",
                                         "sort": "-created_at", "page[size]": 20})
    print(f"\nEmail campaigns — {args.store} (recente)")
    for c in j.get("data", [])[:20]:
        a = c.get("attributes", {})
        print(f"  {(a.get('send_time') or a.get('created_at') or '')[:10]}  {a.get('status',''):<10} {a.get('name','')[:50]}")

def main():
    ap = argparse.ArgumentParser(description="Klaviyo email/SMS analyst (read-only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("account", cmd_account), ("flows", cmd_flows), ("gap", cmd_gap), ("campaigns", cmd_campaigns)]:
        p = sub.add_parser(name); p.add_argument("--store", default="esteban"); p.set_defaults(fn=fn)
    args = ap.parse_args(); args.fn(args)

if __name__ == "__main__":
    main()
