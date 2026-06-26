# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31", "google-auth>=2.0"]
# ///
"""real_roas_unified.py — ROAS REAL per CANAL (Google / Meta / TikTok) per brand, vs breakeven.
Venit = GA4 atribuit pe canal (neutru) ÷ Spend = cache.daily_ad_spend_ron (RON, per platform).
Răspunde „suntem profitabili pe canalul X, brandul Y?" — și alertează ce canal e SUB breakeven.

  Google  = GA4 sessionDefaultChannelGroup ∈ {Paid Search, Paid Shopping, Cross-network}
  Meta    = GA4 sessionSource ∋ facebook/instagram/fb/ig/meta
  TikTok  = GA4 sessionSource ∋ tiktok/bytedance

  uv run real_roas_unified.py --days 30
  uv run real_roas_unified.py --brand Esteban
"""
import os, sys, json, argparse
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
from google.oauth2 import service_account
import google.auth.transport.requests as gar

# brand -> store_name (în daily_ad_spend_ron), indiciu GA4, breakeven ROAS (aprox, ex-TVA livrat)
BRANDS = {
  "Esteban":  {"store":"esteban.ro",     "ga4":["esteban","maison"],          "be":3.3},
  "GT":       {"store":"georgetalent.ro", "ga4":["george talent","george-talent","gt parfum"], "be":3.3},
  "Nubra":    {"store":"nubra",           "ga4":["nubra"],                     "be":3.3},
  "Belasil":  {"store":"belasil.ro",      "ga4":["belasil"],                   "be":3.4},
  "Carpetto": {"store":"carpetto.ro",     "ga4":["carpetto"],                  "be":2.8},
  "Gento":    {"store":"gento.ro",        "ga4":["gento"],                     "be":2.8},
  "Grandia":  {"store":"grandia.ro",      "ga4":["grandia"],                   "be":3.3},
  "Magdeal":  {"store":"magdeal.ro",      "ga4":["magdeal"],                   "be":2.6},
  "Ofertele": {"store":"ofertelezilei.ro","ga4":["ofertele"],                 "be":2.6},
}
GOOGLE_CH = {"Paid Search","Paid Shopping","Cross-network"}
META_SRC = ("facebook","instagram","fb","ig","meta","m.facebook","fbclid","an")
TT_SRC   = ("tiktok","bytedance","tt")

def _kb(k):
    import subprocess
    return subprocess.run(["uv","run",str(Path(__file__).resolve().parents[2]/"core"/"scripts"/"kb.py"),"secret-get",k],capture_output=True,text=True).stdout.strip()
_OK={"host","port","dbname","user","password","sslmode","connect_timeout","application_name","channel_binding"}
clean=lambda d:(lambda p: d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,True) if x.lower() in _OK]),p.fragment)))(urlsplit(d))
def sa_creds():
    for up in range(0,8):
        c=Path(__file__).resolve().parents[up]/"google_credentials.json"
        if c.exists(): return service_account.Credentials.from_service_account_file(str(c),scopes=["https://www.googleapis.com/auth/analytics.readonly"])
    return service_account.Credentials.from_service_account_info(json.loads(_kb("GA4_SA_JSON")),scopes=["https://www.googleapis.com/auth/analytics.readonly"])

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--days",type=int,default=30); ap.add_argument("--brand"); a=ap.parse_args()
    start=f"{a.days}daysAgo"
    url=os.getenv("DATABASE_URL_METRICS") or _kb("DATABASE_URL_METRICS")
    cx=psycopg2.connect(clean(url)); cx.set_session(readonly=True)
    def spend(store):
        with cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute("SELECT platform, COALESCE(SUM(spend_ron),0) s FROM cache.daily_ad_spend_ron WHERE store_name=%s AND date >= current_date - %s GROUP BY platform",(store,a.days))
            return {r["platform"]:float(r["s"]) for r in c.fetchall()}
    cred=sa_creds(); cred.refresh(gar.Request()); AH={"Authorization":f"Bearer {cred.token}","Content-Type":"application/json"}
    asum=requests.get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=200",headers=AH,timeout=30).json()
    props={p.get("displayName","").lower():p.get("property") for ac in asum.get("accountSummaries",[]) for p in ac.get("propertySummaries",[])}
    def find_prop(hints):
        for h in hints:
            for nm,pr in props.items():
                if h in nm: return pr
        return None
    def ga4_rev(prop):
        body={"dateRanges":[{"startDate":start,"endDate":"yesterday"}],"dimensions":[{"name":"sessionDefaultChannelGroup"},{"name":"sessionSource"}],"metrics":[{"name":"purchaseRevenue"}]}
        gr=requests.post(f"https://analyticsdata.googleapis.com/v1beta/{prop}:runReport",headers=AH,json=body,timeout=40).json()
        g=m=t=0.0
        for row in gr.get("rows",[]):
            ch=row["dimensionValues"][0]["value"]; src=row["dimensionValues"][1]["value"].lower(); rev=float(row["metricValues"][0]["value"])
            if ch in GOOGLE_CH: g+=rev
            elif any(k in src for k in META_SRC): m+=rev
            elif any(k in src for k in TT_SRC): t+=rev
        return {"google":g,"meta":m,"tiktok":t}
    items={a.brand:BRANDS[a.brand]} if a.brand and a.brand in BRANDS else BRANDS
    print(f"\nROAS REAL per CANAL — {a.days} zile (venit GA4 ÷ spend RON) · 🔴 = sub breakeven\n")
    print(f"{'Brand':10} {'Canal':7} {'Spend':>8} {'VenitGA4':>9} {'ROAS':>6} {'BE':>4}")
    print("-"*52)
    for name,cfg in items.items():
        sp=spend(cfg["store"]); prop=find_prop(cfg["ga4"])
        rev=ga4_rev(prop) if prop else {"google":0,"meta":0,"tiktok":0}
        for chan in ("google","meta","tiktok"):
            s=sp.get(chan,0); r=rev.get(chan,0)
            if s<50: continue
            roas=r/s if s else 0; flag="🔴" if roas<cfg["be"] else "🟢"
            print(f"{name:10} {chan:7} {s:8,.0f} {r:9,.0f} {roas:5.1f}x {cfg['be']:4.1f} {flag}{' ⚠ fără GA4' if not prop else ''}")
    print("\nNotă: GA4 last-click (sub-estimează social view-through). Breakeven = aprox per brand;")
    print("split Meta/TikTok pe sessionSource. Adevăr suplimentar: comenzi Shopify cu utm_source.")

if __name__=="__main__":
    main()
