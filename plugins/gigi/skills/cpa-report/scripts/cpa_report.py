# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client","google-auth","requests"]
# ///
"""Audit + operare a raportului „CPA și financiar 2025" (Google Sheet + Apps Script, alimentat de un conector 3rd-party).

De ce există: un brand apare pe 0 / lipsește din raport aproape MEREU pt că lipsesc DATE ÎN FILELE SURSĂ
(conectorul FB/Shopify a oprit un feed, un cont nou nu e tras, un magazin nou a ratat prima zi) — NU pt vreun bug
de formulă. Auditul arată exact ce dată lipsește în ce filă, pt fiecare brand/cont/magazin.

  audit                       # toate brandurile: feed FB/Shopify/Google, ultima dată, gap fața de azi
  audit --brand Covoria       # un brand, detaliat (fiecare cont FB din Mapping verificat separat)
  shopify-pull --brand LABNOIR --store "Lab Noir" --from 2026-07-04 --to 2026-07-06   # daily Shopify în formatul filei

Filele: sheet `1IVg0fI-...` — Mapping (brand→FB/TT/Shopify/Google), `Curs valutar`, sursele
`Facebook Ads`(+azi)/`Shopify`(+azi)/`Google Ads`(+azi)/`Tiktok Ads`(+azi), rapoartele `Raport azi`/`Raport Zilnic 2`.
Vezi memoria [[labnoir-cpa-sheet-add]] + [[raport-zilnic2-optimizare]]. audit = read-only. shopify-pull nu scrie (doar printează rândul).
"""
import os, sys, json, argparse, subprocess, re, datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SS = "1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo"
KB = os.environ.get("KB_PY") or os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
def kb(key):
    v = os.environ.get(key)
    if v: return v
    try: return subprocess.run(["uv","run",KB,"secret-get",key], capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception: return ""
norm = lambda s: re.sub(r'[^A-Z0-9]', '', str(s).upper())
def s2d(v):
    try: return (datetime.date(1899,12,30)+datetime.timedelta(days=int(float(v)))).isoformat()
    except: return str(v) if v not in (None,"") else ""

def sheets_api(write=False):
    import socket; socket.setdefaulttimeout(180)
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    scope = "https://www.googleapis.com/auth/spreadsheets" if write else "https://www.googleapis.com/auth/spreadsheets.readonly"
    info = json.loads(kb("GA4_SA_JSON"))
    creds = service_account.Credentials.from_service_account_info(info, scopes=[scope])
    return build("sheets","v4",credentials=creds).spreadsheets()

def get(api, rng):
    try: return api.values().get(spreadsheetId=SS, range=rng, valueRenderOption="UNFORMATTED_VALUE").execute().get("values",[])
    except Exception as e: return []

def latest_by_account(rows, want_norms):
    """rows = [[date, account, ...]]. Return {want_norm: (latest_iso, count)} for accounts matching."""
    best = {}
    for r in rows[1:]:
        a = r[1] if len(r)>1 else ""
        na = norm(a)
        key = next((w for w in want_norms if w==na), None)
        if not key: continue
        d = s2d(r[0]) if r and isinstance(r[0],(int,float)) else str(r[0] if r else "")
        cur = best.get(key, ("", 0))
        best[key] = (max(cur[0], d), cur[1]+1)
    return best

def cmd_audit(brand_filter):
    api = sheets_api()
    today = datetime.date.today().isoformat()
    yest = (datetime.date.today()-datetime.timedelta(days=1)).isoformat()
    mapping = get(api, "Mapping!A1:G60")
    MAP = {}
    for r in mapping[1:]:
        b = str(r[0]).strip() if r else ""
        if not b: continue
        MAP[b] = {"fb": str(r[1]) if len(r)>1 else "", "shop": str(r[3]) if len(r)>3 else "", "google": str(r[4]) if len(r)>4 else ""}
    # source tabs
    fb   = get(api, "'Facebook Ads'!A1:C100000");      fba  = get(api, "'Facebook Ads azi'!A1:C100000")
    shp  = get(api, "'Shopify'!A1:C100000");           shpa = get(api, "'Shopify azi'!A1:C50")
    goog = get(api, "'Google Ads azi'!A1:C100000")
    def store_latest(rows, storename):
        w = {norm(storename)}
        b = latest_by_account(rows, w)
        return b.get(norm(storename), ("",0))
    def flag(d):
        if not d: return "❌ LIPSĂ"
        if d < yest: return f"⚠️ STALE (ultima {d})"
        return f"ok ({d})"
    brands = [brand_filter] if brand_filter else sorted(MAP.keys())
    print(f"### AUDIT CPA & financiar — azi={today}\n(❌=feed lipsă, ⚠️=stale/oprit, ok=are date de azi/ieri)")
    for b in brands:
        mp = MAP.get(b)
        if not mp: print(f"\n{b}: ⚠️ NU e în Mapping"); continue
        print(f"\n=== {b} ===  (Mapping: FB='{mp['fb']}' Shopify='{mp['shop']}' Google='{mp['google'] or '-'}')")
        # FB accounts (comma-separated) — fiecare verificat separat (aici s-a prins 'Magdeal 2')
        accs = [a.strip() for a in mp["fb"].split(",") if a.strip()]
        an = {norm(a) for a in accs}
        blive = latest_by_account(fba, an); bhist = latest_by_account(fb, an)
        for a in accs:
            na = norm(a)
            dl = blive.get(na, ("",0))[0]; dh = bhist.get(na, ("",0))[0]
            miss_live = "❌ contul NU e în 'Facebook Ads azi'" if na not in blive else flag(dl)
            print(f"   FB cont '{a}': azi={miss_live} | istoric={flag(dh)}")
        # Shopify
        if mp["shop"]:
            sl = store_latest(shpa, mp["shop"])[0]; sh = store_latest(shp, mp["shop"])[0]
            print(f"   Shopify '{mp['shop']}': azi={flag(sl)} | istoric={flag(sh)}")
        # Google (poate fi intenționat 0 — nu-l tratăm ca eroare, doar raportăm)
        if mp["google"]:
            gl = store_latest(goog, mp["google"])[0]
            print(f"   Google '{mp['google']}': azi={flag(gl)}  (0/absent poate fi normal dacă nu rulezi Google)")
    print("\nNotă: ❌/⚠️ pe FB/Shopify = problemă de CONECTOR (adaugă contul/magazinul în add-on), NU de script.")
    print("Un cont nou de FB (ex 'Magdeal 2') pus în Mapping dar ❌ în sursă = conectorul nu-l trage încă.")

# ---------- shopify-pull (daily în formatul filei Shopify: Day/Store/Orders/TotalSales/Cost/Gross/Discounts/Shipping/Taxes) ----------
def cmd_shopify_pull(brand, store_label, dfrom, dto):
    import requests
    from collections import defaultdict
    from datetime import datetime as dt, timezone, timedelta
    dom = kb(f"SHOPIFY_ARONA_{brand}_DOMAIN")
    if not dom: sys.exit(f"nu am SHOPIFY_ARONA_{brand}_DOMAIN (shopify-pull merge pe magazinele ARONA-app: ESTEBAN/GT/NUBRA/LABNOIR)")
    cid, cs = kb("SHOPIFY_ARONA_CLIENT_ID"), kb("SHOPIFY_ARONA_CLIENT_SECRET")
    ver = kb("SHOPIFY_ARONA_API_VERSION") or "2026-04"
    tok = requests.post(f"https://{dom}/admin/oauth/access_token", json={"client_id":cid,"client_secret":cs,"grant_type":"client_credentials"}, timeout=30).json()["access_token"]
    def gql(q,v=None): return requests.post(f"https://{dom}/admin/api/{ver}/graphql.json", headers={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"}, json={"query":q,"variables":v or {}}, timeout=60).json()
    tz = gql("{shop{ianaTimezone}}")["data"]["shop"]["ianaTimezone"]
    # RO = UTC+3 vara; folosim offset din numele zonei doar informativ, boundaries cu +03:00 (EEST iulie)
    TZ = timezone(timedelta(hours=3))
    end = (dt.fromisoformat(dto)+timedelta(days=1)).date().isoformat()
    Q = """query($c:String){ orders(first:100, after:$c, query:"created_at:>='%sT00:00:00+03:00' created_at:<'%sT00:00:00+03:00'"){ pageInfo{hasNextPage endCursor} edges{node{ createdAt
      totalDiscountsSet{shopMoney{amount}} totalShippingPriceSet{shopMoney{amount}} totalTaxSet{shopMoney{amount}} currentTotalPriceSet{shopMoney{amount}}
      lineItems(first:100){edges{node{ quantity originalUnitPriceSet{shopMoney{amount}} variant{ inventoryItem{ unitCost{amount} } } }}} }}}}""" % (dfrom, end)
    agg = defaultdict(lambda: dict(orders=0,gross=0.0,disc=0.0,ship=0.0,tax=0.0,cogs=0.0))
    cur=None
    while True:
        r=gql(Q,{"c":cur})
        if r.get("errors"): sys.exit(f"Shopify err: {r['errors']}")
        d=r["data"]["orders"]
        for e in d["edges"]:
            n=e["node"]; day=dt.fromisoformat(n["createdAt"].replace("Z","+00:00")).astimezone(TZ).date().isoformat()
            a=agg[day]; a["orders"]+=1
            f=lambda p: float((n.get(p) or {}).get("shopMoney",{}).get("amount") or 0)
            a["disc"]+=f("totalDiscountsSet"); a["ship"]+=f("totalShippingPriceSet"); a["tax"]+=f("totalTaxSet")
            for le in n["lineItems"]["edges"]:
                li=le["node"]; q=li["quantity"]
                a["gross"]+=float(li["originalUnitPriceSet"]["shopMoney"]["amount"])*q
                uc=(((li.get("variant") or {}).get("inventoryItem") or {}).get("unitCost") or {}).get("amount")
                a["cogs"]+=float(uc or 0)*q
    # break handled below
        if not d["pageInfo"]["hasNextPage"]: break
        cur=d["pageInfo"]["endCursor"]
    print(f"# Store tz {tz}. Rânduri pt fila 'Shopify'/'Shopify azi' (Day | Store | Orders | TotalSales | Cost | Gross | Discounts | Shipping | Taxes):")
    print(f"# (TotalSales = Gross − Discounts + Shipping + Taxes = SUM(F:I) folosit de raport ca 'Vanzari')")
    for day in sorted(agg):
        a=agg[day]; total=a["gross"]-a["disc"]+a["ship"]+a["tax"]
        print(f'{day}\t{store_label}\t{a["orders"]}\t{round(total,2)}\t{round(a["cogs"],2)}\t{round(a["gross"],2)}\t{round(-a["disc"],2)}\t{round(a["ship"],2)}\t{round(a["tax"],2)}')
    print("# ⚠️ Pune rândul ÎN BLOCUL zilei, alfabetic pe magazin (fila e pe dată apoi magazin). NU la coadă (arată orfan).")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit"); a.add_argument("--brand")
    s = sub.add_parser("shopify-pull"); s.add_argument("--brand", required=True); s.add_argument("--store", required=True)
    s.add_argument("--from", dest="dfrom", required=True); s.add_argument("--to", dest="dto", required=True)
    args = ap.parse_args()
    if args.cmd == "audit": cmd_audit(args.brand)
    elif args.cmd == "shopify-pull": cmd_shopify_pull(args.brand, args.store, args.dfrom, args.dto)
