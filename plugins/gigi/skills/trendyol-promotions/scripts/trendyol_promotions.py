# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""trendyol_promotions.py — înscrie/scoate produsele Trendyol în PROMOȚII (campaniile de reduceri).

Folosește API-ul INTERN al panelului partener (NU API-ul oficial de integrare — ăla n-are campanii).
Auth = token scurt din SESIUNEA panelului (login-ul are reCAPTCHA → nu 100% headless): îl iei o dată din
DevTools → Network → orice request către apigw → header `authorization` → îl pui în KB/env `TRENDYOL_PANEL_TOKEN`
(valabil ~15-20 min; când pică, iei altul). storeFront implicit 29=RO.

  trendyol_promotions.py campaigns                              # campaniile disponibile (+ câte produse eligibile)
  trendyol_promotions.py eligible --campaign 371508            # câte/ce produse sunt eligibile
  trendyol_promotions.py enroll   --campaign 371508 --all      # înscrie TOATE eligibile (preț max, discount minim)
  trendyol_promotions.py enroll   --campaign 371508 --listing h1,h2   # doar anumite listingId-uri
  trendyol_promotions.py enrolled --campaign 371508           # ce produse sunt deja înscrise + status
  trendyol_promotions.py remove   --campaign 371508 --status APPROVED,PENDING   # UNDO: scoate produsele
"""
import argparse, json, os, subprocess, sys, urllib.request, urllib.error

BASE = "https://apigw.trendyol.com/partner/sellereng-campaign-scw-campaign-bff"
STOREFRONT = os.environ.get("TRENDYOL_STOREFRONT_ID", "29")   # 29=RO, (BG/GR au alt id)
COUNTRY = os.environ.get("TRENDYOL_SC_COUNTRY", "RO")
LANG = os.environ.get("TRENDYOL_SC_LANG", "ro-RO")


def _kb_path():
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.normpath(os.path.join(here, "..", "..", "..", "..", "core", "scripts", "kb.py"))
    if os.path.exists(cand):
        return cand
    for p in (os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
              "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py"):
        if os.path.exists(p):
            return p
    return None


def _token():
    t = os.environ.get("TRENDYOL_PANEL_TOKEN")
    if not t:
        kb = _kb_path()
        if kb:
            t = subprocess.run(["/bin/zsh", "-lc", f"uv run '{kb}' secret-get TRENDYOL_PANEL_TOKEN"],
                               capture_output=True, text=True).stdout.strip()
    if not t or "negăsit" in t.lower() or "not found" in t.lower():
        sys.exit("Lipsește TRENDYOL_PANEL_TOKEN. Ia-l din panel (DevTools→Network→orice request apigw→"
                 "header 'authorization') și pune-l: kb.py secret-set TRENDYOL_PANEL_TOKEN '<jwt>' (fără 'Bearer ').")
    return t.replace("Bearer ", "").strip()


def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={
        "Authorization": f"Bearer {_token()}",
        "x-sc-store-front-id": STOREFRONT, "x-sc-country": COUNTRY, "x-sc-language": LANG,
        "content-type": "application/json", "accept": "application/json, text/plain, */*",
        "origin": "https://partner.trendyol.com", "user-agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        if e.code in (401, 403):
            sys.exit(f"AUTH {e.code} — tokenul panelului a expirat. Ia altul din DevTools. {raw[:120]}")
        return e.code, (raw or None)


def cmd_campaigns(a):
    st, d = api("GET", "/campaigns/available/grouped?page=0&size=50&orderByField=SUGGESTED_SORTING"
                       "&listingType=ALL&visibilityType=ALL&countryGroup=ALL")
    if st >= 400 or not d:
        sys.exit(f"eroare {st}: {d}")
    print("📣 Campanii disponibile:")
    for grp in d.get("content", []):
        for c in grp.get("campaigns", []):
            reg = "✅ înscris" if c.get("hasBeenRegistered") else "—"
            pd = c.get("promotionDetail") or {}
            print(f"  [{c['id']}] {c['title'][:70]}")
            print(f"       reducere: {pd.get('name','?')} · Trendyol acoperă {pd.get('tyCoverage','?')}% · "
                  f"eligibile: {c.get('availableProductQuantity','?')} · {reg} · grup={grp.get('id')}")


def cmd_eligible(a):
    st, cnt = api("GET", f"/campaign-products/count?campaignId={a.campaign}")
    st2, d = api("GET", f"/campaign-products/available?campaignId={a.campaign}&size=20&page=0&brandIds=&approved=true&categoryIds=&sortBy=BEST_SELLER")
    print(f"📦 Campania {a.campaign}: eligibile ≈ {cnt}")
    if isinstance(d, dict):
        for p in (d.get("content") or [])[:20]:
            print(f"  {p.get('barcode','?'):<16} {str(p.get('title',''))[:50]:<50} listing={p.get('listingId')}")


def cmd_enroll(a):
    # 1) join ca supplier (idempotent)
    api("POST", f"/campaigns/{a.campaign}/suppliers")
    listing = [] if a.all else [x.strip() for x in (a.listing or "").split(",") if x.strip()]
    if not a.all and not listing:
        sys.exit("dă --all (toate eligibile) sau --listing h1,h2,...")
    st, d = api("POST", "/campaign-products/approvable/max-price/batch",
                {"campaignId": str(a.campaign), "listingIds": listing})
    if st in (200, 201):
        who = "TOATE eligibile" if a.all else f"{len(listing)} produs(e)"
        print(f"✅ Trimis pt aprobare: {who} în campania {a.campaign} la preț max (discount minim).")
        print("   Procesare ASYNC — verifică cu: enrolled --campaign %s (status Aprobat)." % a.campaign)
    else:
        sys.exit(f"eroare {st}: {d}")


def cmd_enrolled(a):
    st, d = api("GET", f"/campaign-products?campaignId={a.campaign}&statuses=PENDING,REJECTED,APPROVED")
    items = d.get("content", d) if isinstance(d, dict) else d
    n = len(items) if isinstance(items, list) else "?"
    print(f"📋 Înscrise în campania {a.campaign}: {n}")
    if isinstance(items, list):
        from collections import Counter
        by = Counter((p.get("status") or p.get("approvalStatus")) for p in items)
        print("   pe status:", dict(by))
        for p in items[:15]:
            print(f"   {p.get('barcode','?'):<16} {str(p.get('title',''))[:44]:<44} {p.get('status','')}")


def cmd_remove(a):
    statuses = [s.strip().upper() for s in a.status.split(",") if s.strip()]
    if not a.apply:
        print(f"[dry-run] aș șterge din campania {a.campaign} produsele cu status {statuses} "
              f"(reason={a.reason}). Adaugă --apply ca să execuți.")
        return
    st, d = api("DELETE", "/campaign-products/bulk",
                {"campaignId": str(a.campaign), "reason": a.reason, "statuses": statuses})
    print(f"{'✅ șters' if st in (200,204) else 'eroare '+str(st)}: campania {a.campaign}, status {statuses}. {d or ''}")


def main():
    p = argparse.ArgumentParser(description="Trendyol promoții/campanii prin API-ul intern al panelului")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("campaigns").set_defaults(func=cmd_campaigns)
    e = sub.add_parser("eligible"); e.add_argument("--campaign", required=True); e.set_defaults(func=cmd_eligible)
    en = sub.add_parser("enroll"); en.add_argument("--campaign", required=True)
    en.add_argument("--all", action="store_true"); en.add_argument("--listing"); en.set_defaults(func=cmd_enroll)
    ed = sub.add_parser("enrolled"); ed.add_argument("--campaign", required=True); ed.set_defaults(func=cmd_enrolled)
    r = sub.add_parser("remove"); r.add_argument("--campaign", required=True)
    r.add_argument("--status", default="APPROVED,PENDING,REJECTED"); r.add_argument("--reason", default="WRONG_JOIN")
    r.add_argument("--apply", action="store_true"); r.set_defaults(func=cmd_remove)
    a = p.parse_args(); a.func(a)


if __name__ == "__main__":
    main()
