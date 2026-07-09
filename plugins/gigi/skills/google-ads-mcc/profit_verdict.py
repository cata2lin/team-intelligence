# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""Profit-based SCALE/HOLD/CUT verdict per ENABLED campaign, across all MCC accounts.

Judges by PROFIT (CPA vs real breakeven from breakeven.py, stored in brandref), NOT by a flat
CPA target — so a PMax at CPA 30 with breakeven 61 is correctly SCALE, not "over target 20".

Zones (per brand, from brandref):
  SCALE  CPA <= scale_cpa (= BE_CPA*0.7, keeps ~30% contribution)  → raise budget / loosen target
  HOLD   scale_cpa < CPA <= breakeven_cpa                          → profitable but thin, optimize
  CUT    CPA > breakeven_cpa                                       → losing money, fix/pause

⚠️ Google-reported ROAS is inflated ~1.5x (last-click + Shopping App) — the CPA gate is the
robust one; the shown ROAS is Google-reported (divide by ~1.5 for real before vs breakeven_roas).
Refresh the breakeven inputs with `gigi:fulfillment-analytics/breakeven.py --store all` then
re-store into brandref. Read-only on Google Ads. Usage: `uv run profit_verdict.py [--days 30]`.
"""
import os, re, sys, argparse, importlib.util, requests
DAYS=int(next((a.split("=")[1] for a in sys.argv if a.startswith("--days=")), "30"))
if "--days" in sys.argv:
    try: DAYS=int(sys.argv[sys.argv.index("--days")+1])
    except: pass
API="v21"
HERE=os.path.dirname(os.path.abspath(__file__))
spec=importlib.util.spec_from_file_location("brandref",os.path.join(HERE,"brandref.py"))
br=importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
# brand-key (brandref) -> (google customer id, currency)
ACCOUNTS=[("grandia","9069610821","RON"),("gt","5031005158","RON"),("nubra","7585902074","RON"),
 ("belasil","7566352958","RON"),("ofertele","4778636466","RON"),("gento","8148962111","RON"),
 ("carpetto","4069952156","RON"),("rossi","8287989891","RON"),("nocturna","2630158527","RON"),
 ("bonhaus_pl","6858257397","PLN"),("bonhaus_cz","3141935298","CZK")]
# auth from metrics DB (same pattern as gads.py)
import psycopg2, psycopg2.extras, subprocess
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
def _kb(k): return subprocess.run(["uv","run",os.path.join(HERE,"..","..","..","core","scripts","kb.py"),"secret-get",k],capture_output=True,text=True).stdout.strip() if not os.environ.get("DATABASE_URL_METRICS") else os.environ["DATABASE_URL_METRICS"]
_OK={"host","port","dbname","user","password","sslmode","connect_timeout"}
def _clean(d):
    p=urlsplit(d);return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _OK]),p.fragment))
DB=os.environ.get("DATABASE_URL_METRICS") or _kb("DATABASE_URL_METRICS")
cn=psycopg2.connect(_clean(DB)); cn.set_session(readonly=True)
with cn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true');c=dict(cur.fetchone())
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":c["cid"],"client_secret":c["csec"],"refresh_token":c["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":c["dev"],"login-customer-id":re.sub(r"\D","",c["mcc"]),"Content-Type":"application/json"}
def q(cid,g):
    r=requests.post(f"https://googleads.googleapis.com/{API}/customers/{cid}/googleAds:search",headers=H,json={"query":g},timeout=60)
    return r.json().get("results",[]) if r.status_code==200 else []
GAQL=("SELECT campaign.name,campaign.advertising_channel_type,metrics.cost_micros,metrics.conversions,"
 f"metrics.conversions_value FROM campaign WHERE campaign.status='ENABLED' AND metrics.cost_micros>0 "
 f"AND segments.date DURING LAST_{DAYS}_DAYS")
buckets={"SCALE":[],"HOLD":[],"CUT":[]}
for brand,cid,cur in ACCOUNTS:
    ref=br.get(brand) or {}
    be=ref.get("breakeven_cpa"); scale=ref.get("scale_cpa"); beroas=ref.get("breakeven_roas")
    if not be: continue
    for r in q(cid,GAQL):
        cc=r["campaign"]; m=r["metrics"]; nm=cc["name"][:26]; ch=cc.get("advertisingChannelType","")[:4]
        cost=int(m.get("costMicros",0))/1e6; conv=float(m.get("conversions",0)); val=float(m.get("conversionsValue",0))
        if conv<8: continue  # anti-noise
        cpa=cost/conv; roas=val/cost if cost else 0
        line=f"{brand}·{nm} [{ch}] CPA={cpa:.0f} ({cur}; BE {be}, scale {scale}) ROASg~{roas:.1f} conv{DAYS}={conv:.0f} cost={cost:.0f}"
        z="SCALE" if cpa<=scale else ("HOLD" if cpa<=be else "CUT")
        buckets[z].append(line)
LBL={"SCALE":"🟢 SCALE (CPA ≤ scale_cpa = profit sănătos → +buget/relaxează target)",
 "HOLD":"🟡 HOLD (scale_cpa < CPA ≤ breakeven = profit subțire → optimizează, nu scala)",
 "CUT":"🔴 CUT/FIX (CPA > breakeven = pierde bani)"}
print(f"=== VERDICT PE PROFIT — ultimele {DAYS} zile (CPA vs breakeven real din brandref) ===")
print("   ⚠️ ROASg = Google-reported (~1.5× umflat); gate-ul robust = CPA vs breakeven.")
for z in ["SCALE","HOLD","CUT"]:
    print(f"\n{LBL[z]} — {len(buckets[z])}")
    for x in buckets[z]: print("  "+x)
