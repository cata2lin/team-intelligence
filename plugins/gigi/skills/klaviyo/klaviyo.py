# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31", "google-auth>=2.0"]
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
import argparse, json, os, subprocess, sys
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
    # flows often handled OUTSIDE Klaviyo (don't count as a Klaviyo gap)
    EXTERNAL = {"Review request": "de regulă prin Judge.me (are flow propriu — nu Klaviyo)"}
    have = miss = 0
    for label, kws in STD_FLOWS.items():
        is_live = any(any(k in n for k in kws) for n in live)
        exists = any(any(k in n for k in kws) for n in allnames)
        if is_live: mark, note = "✅", "live"; have += 1
        elif label in EXTERNAL: mark, note = "↗️", EXTERNAL[label]
        elif exists: mark, note = "🟡", "EXISTĂ dar nu e live (draft/manual)"
        else: mark, note = "❌", "LIPSEȘTE — bani lăsați pe masă"; miss += 1
        print(f"  {mark} {label:<24} {note}")
    print(f"\n  {have} active în Klaviyo · {miss} lipsesc · ↗️ = acoperit extern. Flow-urile aduc tipic 30-40%+ din venitul de email.")

def cmd_campaigns(args):
    j = _get(args.store, "/campaigns/", {"filter": "equals(messages.channel,'email')",
                                         "sort": "-created_at", "page[size]": 20})
    print(f"\nEmail campaigns — {args.store} (recente)")
    for c in j.get("data", [])[:20]:
        a = c.get("attributes", {})
        print(f"  {(a.get('send_time') or a.get('created_at') or '')[:10]}  {a.get('status',''):<10} {a.get('name','')[:50]}")

# GA4 property per store (for the inflation cross-check)
GA4_PROP = {"esteban": "510626424", "grandia": "510760223", "nubra": "541249929",
            "george-talent": "541255080", "gt": "541255080", "belasil": "487042770"}

def _kb_get(name):
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
    return ""

def _placed_order_id(store):
    for m in _all(store, "/metrics/"):
        if (m.get("attributes", {}).get("name") or "") == "Placed Order":
            return m["id"]
    return None

def _values_report(store, kind, metric_id):
    body = {"data": {"type": f"{kind}-values-report", "attributes": {
        "statistics": ["conversion_value", "conversions"], "timeframe": {"key": "last_30_days"},
        "conversion_metric_id": metric_id}}}
    r = requests.post(f"{BASE}/{kind}-values-reports/", headers={**_h(store), "content-type": "application/json"},
                      json=body, timeout=120)
    if r.status_code != 200:
        print(f"  ({kind}-report indisponibil: {r.status_code} {r.text[:120]})"); return 0.0, {}
    results = (r.json().get("data", {}).get("attributes", {}) or {}).get("results", []) or []
    total = 0.0; per = {}
    for res in results:
        cv = (res.get("statistics", {}) or {}).get("conversion_value") or 0
        gid = (res.get("groupings", {}) or {}).get(f"{kind}_id")
        total += cv;  per[gid] = cv
    return total, per

def _ga4_email_revenue(store, days=30):
    raw = _kb_get("GA4_SA_JSON")
    pid = GA4_PROP.get(store.lower())
    if not raw or not pid: return None
    import datetime as _dt
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/analytics.readonly"])
    creds.refresh(Request())
    end = _dt.date.today() - _dt.timedelta(days=1); start = end - _dt.timedelta(days=days - 1)
    r = requests.post(f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport",
        headers={"Authorization": f"Bearer {creds.token}"},
        json={"dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
              "dimensions": [{"name": "sessionDefaultChannelGroup"}], "metrics": [{"name": "purchaseRevenue"}]},
        timeout=120)
    if r.status_code != 200: return None
    for row in r.json().get("rows", []):
        if row["dimensionValues"][0]["value"] == "Email":
            return float(row["metricValues"][0]["value"])
    return 0.0

def cmd_report(args):
    mid = _placed_order_id(args.store)
    if not mid: sys.exit("Nu găsesc metrica 'Placed Order' în Klaviyo.")
    fl_total, fl_per = _values_report(args.store, "flow", mid)
    cp_total, cp_per = _values_report(args.store, "campaign", mid)
    klav = fl_total + cp_total
    ga4 = _ga4_email_revenue(args.store, 30)
    print(f"\nKlaviyo vs GA4 — {args.store} (ultimele ~30 zile)")
    print(f"  Klaviyo flows:      {fl_total:>12,.0f} RON")
    print(f"  Klaviyo campanii:   {cp_total:>12,.0f} RON")
    print(f"  Klaviyo email TOTAL:{klav:>12,.0f} RON   (atribuit de Klaviyo)")
    if ga4 is None:
        print("  GA4 'Email': indisponibil (verifică GA4_SA_JSON / property).")
    else:
        print(f"  GA4 canal 'Email':  {ga4:>12,.0f} RON   (atribuit last-click GA4)")
        if ga4 > 0:
            print(f"  → Klaviyo raportează de {klav/ga4:.1f}x ({100*(klav-ga4)/ga4:+.0f}%) față de GA4")
        print("  (atribuiri diferite: Klaviyo ~5z click/1z open vs GA4 last-click → un gap e normal,")
        print("   dar mărimea lui = cât de mult umflă Klaviyo. Adevărul e între ele, mai aproape de GA4.)")
    if fl_per:
        flows = {f["id"]: f.get("attributes", {}).get("name", "") for f in _all(args.store, "/flows/")}
        print("\n  Top flows după venit (Klaviyo):")
        for fid, cv in sorted(fl_per.items(), key=lambda x: -(x[1] or 0))[:8]:
            print(f"    {cv:>10,.0f} RON  {flows.get(fid, fid)[:48]}")

def main():
    ap = argparse.ArgumentParser(description="Klaviyo email/SMS analyst (read-only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("account", cmd_account), ("flows", cmd_flows), ("gap", cmd_gap), ("campaigns", cmd_campaigns), ("report", cmd_report)]:
        p = sub.add_parser(name); p.add_argument("--store", default="esteban"); p.set_defaults(fn=fn)
    args = ap.parse_args(); args.fn(args)

if __name__ == "__main__":
    main()
