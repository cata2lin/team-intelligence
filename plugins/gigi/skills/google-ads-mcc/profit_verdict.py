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
   ⚠️ Budget-limited signal = YESTERDAY's FULL DAY util (spent ≥92% of budget) or lost-budget-IS>5%
   — time-of-day independent. Do NOT use (a) a 7-day avg: after a same-day raise it reflects the OLD
   budget and falsely reads "not capped"; nor (b) today's morning spend: too early, util low for all
   → false "not capped" (both lessons, iul-2026). `azi=` column is pacing only. A budget raise takes
   1-3 days to be USED → re-check daily, don't stack same-day. Budget changes ≤20% don't reset
   learning; a SCALE campaign still capped on its NEW budget deserves another ≤20% bump. If SCALE
   but NOT capped → grow horizontal (non-brand keywords / new products / geos), budget won't be spent.

🚨 CURRENCY. brandref breakeven is in RON (breakeven.py prints "AOV în RON"), but Google Ads reports CPA
   in the ACCOUNT's currency (CZK/PLN/EUR). Comparing them raw is a units bug that INVERTS the verdict:
   Bonhaus CZ read "CPA 125 CZK vs BE 37 = CUT, 3.4x over" when 125 CZK = 25 RON < 37 RON = actually
   PROFITABLE (real bug, iul-2026 — nearly throttled two healthy campaigns). CPA is now converted to RON
   via metrics.fx_rates (BNR) before every comparison, and printed as "125 CZK = 25 RON".

🔑 CUT ≠ pause. A long window MASKS a recovery: a campaign reset/fixed recently still drags its bad
   pre-fix days inside the 30d average. Belasil "All Products" (iul-2026) read CUT on 30d (CPA 44 > BE 40)
   while its LAST 7 DAYS were 34 = profitable and still improving — cutting it would have killed a campaign
   that had just repaired itself. So every CUT line now also prints its 7-day CPA and says whether the
   verdict survives on the trend. NEVER pause on the long-window verdict alone.

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
    # 🔑 breakeven-ul din brandref e în RON (breakeven.py: „AOV în RON"), dar Google Ads dă CPA în MONEDA
    # CONTULUI (CZK/PLN/EUR). Fără conversie, un CPA de 125 CZK părea „3,4× peste BE 37" când de fapt
    # 125 CZK = 25 RON < 37 RON = PROFITABIL. Bug real (iul-2026): ambele campanii Bonhaus CZ ieșeau
    # fals „CUT". Convertim ÎNTOTDEAUNA CPA-ul în RON înainte de comparație.
    cur.execute("""SELECT DISTINCT ON ("fromCurrency") "fromCurrency" f, rate FROM fx_rates
                   WHERE "toCurrency"='RON' AND "fromCurrency" IN ('CZK','PLN','EUR','BGN','HUF')
                   ORDER BY "fromCurrency", "rateDate" DESC""")
    FX={r["f"]: float(r["rate"]) for r in cur.fetchall()}
FX["RON"]=1.0
tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":c["cid"],"client_secret":c["csec"],"refresh_token":c["rt"]},timeout=20).json()["access_token"]
H={"Authorization":f"Bearer {tok}","developer-token":c["dev"],"login-customer-id":re.sub(r"\D","",c["mcc"]),"Content-Type":"application/json"}
def q(cid,g):
    r=requests.post(f"https://googleads.googleapis.com/{API}/customers/{cid}/googleAds:search",headers=H,json={"query":g},timeout=60)
    return r.json().get("results",[]) if r.status_code==200 else []
GAQL=("SELECT campaign.id,campaign.name,campaign.advertising_channel_type,campaign_budget.amount_micros,"
 "metrics.cost_micros,metrics.conversions,metrics.conversions_value FROM campaign "
 f"WHERE campaign.status='ENABLED' AND metrics.cost_micros>0 AND segments.date DURING LAST_{DAYS}_DAYS")
import datetime
# Fereastră RECENTĂ dar AȘEZATĂ: zilele -8..-3. Sar peste ultimele 2 zile fiindcă atribuirea conversiilor
# vine cu ÎNTÂRZIERE → CPA-ul ultimelor 1-2 zile e mereu fals-umflat (dovadă Grandia 13-iul: Google raporta
# 58,9 conv → CPA 85 „pierdere", când magazinul avusese REAL 111 comenzi). Dacă foloseam LAST_7_DAYS,
# lag-ul ar fi făcut orice campanie să pară că se saturează → n-aș mai fi scalat niciodată nimic.
_t=datetime.date.today()
_R0=(_t-datetime.timedelta(days=8)).isoformat(); _R1=(_t-datetime.timedelta(days=3)).isoformat()
RECENT_Q=("SELECT campaign.id,metrics.cost_micros,metrics.conversions FROM campaign "
 f"WHERE campaign.status='ENABLED' AND metrics.cost_micros>0 AND segments.date BETWEEN '{_R0}' AND '{_R1}'")
TODAY_Q=("SELECT campaign.id,metrics.cost_micros FROM campaign WHERE campaign.status='ENABLED' AND segments.date DURING TODAY")
YEST_Q=("SELECT campaign.id,metrics.cost_micros,metrics.search_budget_lost_impression_share "
 "FROM campaign WHERE campaign.status='ENABLED' AND segments.date DURING YESTERDAY")
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
        fx=FX.get(cur,1.0); cpa_ron=cpa*fx           # BE e în RON → compară RON cu RON
        z="SCALE" if cpa_ron<=scale else ("HOLD" if cpa_ron<=be else "CUT")
        bud=int(r.get("campaignBudget",{}).get("amountMicros",0))/1e6
        parsed.append((z,cc["id"],cc["name"][:26],cc.get("advertisingChannelType","")[:4],cpa,roas,conv,cost,bud,cpa_ron,fx))
        if z=="SCALE": scale_ids.append(cc["id"])
    # Fereastra de 30z MINTE în AMBELE sensuri — de aia trendul recent se verifică la TOATE campaniile:
    #  · CUT stale  → campania s-a REPARAT deja (Belasil 30z=44 CUT, dar recent 34 = profitabil).
    #  · SCALE stale → campania se SATUREAZĂ acum (Grandia 13-iul: 30z=51 zicea SCALE, dar spend +37%
    #    adusese doar +12% comenzi → cost/comandă 44→54; am scalat în saturare pe un verdict orb).
    recent={}
    if parsed:
        for r in q(cid,RECENT_Q):
            m=r["metrics"]; c7=int(m.get("costMicros",0))/1e6; v7=float(m.get("conversions",0))
            if v7: recent[r["campaign"]["id"]]=c7/v7
    # real-time budget-limited (TODAY) only where we have SCALE campaigns
    today={}; yest={}
    if scale_ids:
        for r in q(cid,TODAY_Q):
            today[r["campaign"]["id"]]=int(r["metrics"].get("costMicros",0))/1e6
        for r in q(cid,YEST_Q):
            m=r["metrics"]; yest[r["campaign"]["id"]]={"cost":int(m.get("costMicros",0))/1e6,
                "blis":float(m["searchBudgetLostImpressionShare"]) if "searchBudgetLostImpressionShare" in m else None}
    for z,cmpid,nm,ch,cpa,roas,conv,cost,bud,cpa_ron,fx in parsed:
        loc=f"{cpa:.0f} {cur} = " if cur!="RON" else ""
        base=f"{brand}·{nm} [{ch}] CPA={loc}{cpa_ron:.0f} RON (BE {be}, scale {scale}) ROASg~{roas:.1f} conv{DAYS}={conv:.0f}"
        if z=="SCALE":
            y=yest.get(cmpid,{}); yc=y.get("cost",0); blis=y.get("blis"); yutil=yc/bud*100 if bud else 0
            tc=today.get(cmpid,0); tutil=tc/bud*100 if bud else 0
            # SIGNAL = YESTERDAY full day (time-of-day independent). azi = pacing only.
            # fall back to today only if the campaign had no spend yesterday (brand-new).
            capped = (yutil>=92 or (blis is not None and blis>0.05)) or (yc==0 and tutil>=65)
            # 🛑 SATURARE: 30z zice SCALE, dar fereastra recentă (așezată) e deja peste prag → NU mai scala,
            # oricât de „budget-limited" ar fi. Bugetul în plus cumpără doar comenzi tot mai scumpe.
            c7=recent.get(cmpid); c7=c7*fx if c7 is not None else None
            sat=""
            if c7 is not None and c7>be:
                capped=False; sat=f" 🛑 recent CPA={c7:.0f} RON > BE {be} = SE SATUREAZĂ → NU SCALA (taie bugetul)"
            elif c7 is not None and c7>scale:
                sat=f" ⚠️ recent CPA={c7:.0f} RON (>scale {scale}) = randamente descrescătoare → scalează MIC sau deloc"
            flag=("🔥 RAISE (budget-limited IERI)" if capped else "→ necapat: orizontal / așteaptă") if not sat else ""
            b=f" lostBud={blis*100:.0f}%" if blis is not None else ""
            base+=f" | bud={bud:.0f} IERI={yc:.0f}({yutil:.0f}%){b} azi={tc:.0f}({tutil:.0f}%) {flag}{sat}"
        elif z=="CUT":
            c7=recent.get(cmpid)
            c7=c7*fx if c7 is not None else None   # 7z e tot în moneda contului → în RON
            if c7 is None: base+=" | 7z=fără date → verifică manual înainte să tai"
            elif c7<=scale: base+=f" | ⚠️ 7z CPA={c7:.0f} RON = SCALE! NU TĂIA — verdictul CUT e stale (fereastra {DAYS}z târăște perioada veche)"
            elif c7<=be: base+=f" | ⚠️ 7z CPA={c7:.0f} RON < BE {be} → SE REPARĂ, NU TĂIA (lasă-l să se așeze)"
            else: base+=f" | 7z CPA={c7:.0f} RON (tot > BE) → CUT confirmat pe trend"
        buckets[z].append(base)
LBL={"SCALE":"🟢 SCALE (profit sănătos) — 🔥=budget-limited IERI (crește ≤20%) · necapat=orizontal",
 "HOLD":"🟡 HOLD (profit subțire → optimizează, nu scala)",
 "CUT":"🔴 CUT/FIX (CPA > breakeven = pierde bani)"}
print(f"=== VERDICT PE PROFIT + BUDGET-LIMITED (IERI zi completă) — CPA {DAYS}z vs breakeven brandref ===")
print("   ⚠️ ROASg=Google (~1.5× umflat); gate=CPA. Budget-limited=IERI (fiabil la orice oră); azi=doar pacing.")
for z in ["SCALE","HOLD","CUT"]:
    print(f"\n{LBL[z]} — {len(buckets[z])}")
    for x in buckets[z]: print("  "+x)
