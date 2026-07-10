# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""Profit-based SCALE/HOLD/CUT verdict per ENABLED campaign, across all MCC accounts,
+ for SCALE campaigns a real-time BUDGET-LIMITED check (spend TODAY vs current budget)
so you know which are 🔥 raise-budget-NOW vs necapat (grow horizontally / wait).

Judges by PROFIT (CPA vs real breakeven from breakeven.py, stored in brandref), NOT a flat
CPA target — a PMax at CPA 30 with breakeven 61 is correctly SCALE, not "over target 20".

Zones (per brand, from brandref):
  SCALE  CPA <= scale_cpa (= BE_CPA*0.7, keeps ~30% contribution)  → candidate to scale
  HOLD   scale_cpa < CPA <= breakeven_cpa                          → profitable but thin, optimize
  CUT    CPA > breakeven_cpa                                       → losing money, fix/pause

🔑 SCALE ≠ raise-budget. A SCALE campaign only deserves MORE budget if it is BUDGET-LIMITED.
   ⚠️ Check budget-limited on TODAY's spend vs the CURRENT budget — NOT a 7-day avg. After a
   same-day budget raise the 7-day avg reflects the OLD budget and FALSELY reads "not capped"
   (lesson, iul-2026). A budget raise takes 1-3 days to be USED → re-check daily, don't stack
   same-day. Budget changes ≤20% don't reset learning; a SCALE campaign still capped on its
   NEW budget deserves another ≤20% bump. If SCALE but NOT capped → grow horizontal (non-brand
   keywords / new products / geos), budget won't be spent.

⚠️ Google-reported ROAS is inflated ~1.5x — the CPA gate is robust; shown ROAS is Google's.
Refresh breakeven: `gigi:fulfillment-analytics/breakeven.py --store all` → re-store in brandref.
Read-only on Google Ads. Usage: `uv run profit_verdict.py [--days 30]`.
"""
import os, re, sys, importlib.util, requests
DAYS=30
if "--days" in sys.argv:
    try: DAYS=int(sys.argv[sys.argv.index("--days")+1])
    except: pass
API="v21"
HERE=os.path.dirname(os.path.abspath(__file__))
spec=importlib.util.spec_from_file_location("brandref",os.path.join(HERE,"brandref.py"))
br=importlib.util.module_from_spec(spec); spec.loader.exec_module(br)
ACCOUNTS=[("grandia","9069610821","RON"),("gt","5031005158","RON"),("nubra","7585902074","RON"),
 ("belasil","7566352958","RON"),("ofertele","4778636466","RON"),("gento","8148962111","RON"),
 ("carpetto","4069952156","RON"),("rossi","8287989891","RON"),("nocturna","2630158527","RON"),
 ("bonhaus_pl","6858257397","PLN"),("bonhaus_cz","3141935298","CZK")]
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
GAQL=("SELECT campaign.id,campaign.name,campaign.advertising_channel_type,campaign_budget.amount_micros,"
 "metrics.cost_micros,metrics.conversions,metrics.conversions_value FROM campaign "
 f"WHERE campaign.status='ENABLED' AND metrics.cost_micros>0 AND segments.date DURING LAST_{DAYS}_DAYS")
TODAY_Q=("SELECT campaign.id,metrics.cost_micros,metrics.search_budget_lost_impression_share "
 "FROM campaign WHERE campaign.status='ENABLED' AND segments.date DURING TODAY")
buckets={"SCALE":[],"HOLD":[],"CUT":[]}
for brand,cid,cur in ACCOUNTS:
    ref=br.get(brand) or {}
    be=ref.get("breakeven_cpa"); scale=ref.get("scale_cpa")
    if not be: continue
    rows=q(cid,GAQL)
    scale_ids=[]
    parsed=[]
    for r in rows:
        cc=r["campaign"]; m=r["metrics"]
        cost=int(m.get("costMicros",0))/1e6; conv=float(m.get("conversions",0)); val=float(m.get("conversionsValue",0))
        if conv<8: continue
        cpa=cost/conv; roas=val/cost if cost else 0
        z="SCALE" if cpa<=scale else ("HOLD" if cpa<=be else "CUT")
        bud=int(r.get("campaignBudget",{}).get("amountMicros",0))/1e6
        parsed.append((z,cc["id"],cc["name"][:26],cc.get("advertisingChannelType","")[:4],cpa,roas,conv,cost,bud))
        if z=="SCALE": scale_ids.append(cc["id"])
    # real-time budget-limited (TODAY) only where we have SCALE campaigns
    today={}
    if scale_ids:
        for r in q(cid,TODAY_Q):
            m=r["metrics"]; today[r["campaign"]["id"]]={"cost":int(m.get("costMicros",0))/1e6,
                "blis":float(m["searchBudgetLostImpressionShare"]) if "searchBudgetLostImpressionShare" in m else None}
    for z,cmpid,nm,ch,cpa,roas,conv,cost,bud in parsed:
        base=f"{brand}·{nm} [{ch}] CPA={cpa:.0f} ({cur}; BE {be}, scale {scale}) ROASg~{roas:.1f} conv{DAYS}={conv:.0f}"
        if z=="SCALE":
            t=today.get(cmpid,{}); tc=t.get("cost",0); blis=t.get("blis")
            util=tc/bud*100 if bud else 0
            capped = util>=65 or (blis is not None and blis>3)
            flag="🔥 RAISE (budget-limited AZI)" if capped else "→ necapat: orizontal / așteaptă"
            b=f" lostBud={blis*100:.0f}%" if blis is not None else ""
            base+=f" | bud={bud:.0f} azi={tc:.0f}({util:.0f}%){b} {flag}"
        buckets[z].append(base)
LBL={"SCALE":"🟢 SCALE (profit sănătos) — 🔥=budget-limited AZI (crește ≤20%) · necapat=orizontal",
 "HOLD":"🟡 HOLD (profit subțire → optimizează, nu scala)",
 "CUT":"🔴 CUT/FIX (CPA > breakeven = pierde bani)"}
print(f"=== VERDICT PE PROFIT + BUDGET-LIMITED (AZI) — CPA {DAYS}z vs breakeven brandref ===")
print("   ⚠️ ROASg = Google-reported (~1.5× umflat); gate = CPA. AZI e zi PARȚIALĂ → util mare/lostBud>0 = capat.")
for z in ["SCALE","HOLD","CUT"]:
    print(f"\n{LBL[z]} — {len(buckets[z])}")
    for x in buckets[z]: print("  "+x)
