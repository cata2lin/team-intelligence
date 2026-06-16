# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Budget Simulator / Forecaster — "what if I change the budget by ±X%?" per ENABLED campaign.
Uses the last N days (spend, conv, value, ROAS) and the **budget-lost impression share** to estimate
headroom, then projects conversions / revenue / ROAS under a diminishing-returns model, plus a
**profit** view (margin + COD delivery rate). Transparent assumptions, read-only. Not a crystal ball —
a structured estimate to size a budget move before you make it.

    uv run budget_sim.py --customer 7566352958
    uv run budget_sim.py --customer 5229815058 --margin 0.70 --delivery-rate 0.85 --scenarios "-20,10,20,50,100"
    uv run budget_sim.py --customer 7566352958 --campaign "All Products" --elasticity 0.6
"""
import os, sys, argparse
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras, requests
API=os.environ.get("GADS_API_VERSION","v21")
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--customer",required=True); ap.add_argument("--campaign")
    ap.add_argument("--days",type=int,default=30)
    ap.add_argument("--elasticity",type=float,help="0..1; conv ∝ budget^elasticity (1=linear, lower=diminishing). Default: derived from budget-lost IS.")
    ap.add_argument("--margin",type=float,help="profit margin (e.g. 0.70 for Esteban 2+1). Enables profit view.")
    ap.add_argument("--delivery-rate",type=float,default=1.0,help="COD: fraction of orders actually delivered/paid (e.g. 0.85)")
    ap.add_argument("--scenarios",default="-20,10,20,50",help="budget % changes to simulate")
    a=ap.parse_args()
    cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
    c=cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute('SELECT "developerToken" dev,"loginCustomerId" mcc,"oauthClientId" cid,"oauthClientSecret" csec,"refreshToken" rt FROM google_ads_connections WHERE "isActive"=true'); r=c.fetchone()
    tok=requests.post("https://oauth2.googleapis.com/token",data={"grant_type":"refresh_token","client_id":r["cid"],"client_secret":r["csec"],"refresh_token":r["rt"]},timeout=20).json()["access_token"]
    H={"Authorization":f"Bearer {tok}","developer-token":r["dev"],"login-customer-id":"".join(ch for ch in str(r["mcc"]) if ch.isdigit()),"Content-Type":"application/json"}
    where="campaign.status='ENABLED'"+(f" AND campaign.name='{a.campaign}'" if a.campaign else "")
    q=(f"SELECT campaign.name, campaign.advertising_channel_type, metrics.cost_micros, metrics.conversions, "
       f"metrics.conversions_value, metrics.search_budget_lost_impression_share "
       f"FROM campaign WHERE {where} AND segments.date DURING LAST_{a.days if a.days in (7,14,30) else 30}_DAYS")
    rr=requests.post(f"https://googleads.googleapis.com/{API}/customers/{a.customer}/googleAds:searchStream",headers=H,json={"query":q},timeout=120)
    if rr.status_code!=200: sys.exit(f"Ads API {rr.status_code}: {rr.text[:300]}")
    camps={}
    for b in rr.json():
        for row in b.get("results",[]):
            cmp=row["campaign"]; m=row["metrics"]; nm=cmp["name"]
            d=camps.setdefault(nm,{"type":cmp.get("advertisingChannelType",""),"spend":0.0,"conv":0.0,"val":0.0,"blis":[]})
            d["spend"]+=float(m.get("costMicros",0))/1e6; d["conv"]+=float(m.get("conversions",0)); d["val"]+=float(m.get("conversionsValue",0))
            if m.get("searchBudgetLostImpressionShare") is not None: d["blis"].append(float(m["searchBudgetLostImpressionShare"]))
    scen=[float(x) for x in a.scenarios.split(",")]
    days=a.days
    for nm,d in sorted(camps.items(),key=lambda x:-x[1]["spend"]):
        if d["spend"]<10: continue
        S=d["spend"]; C=d["conv"]; V=d["val"]; roas=V/S if S else 0; aov=V/C if C else 0
        blis=sum(d["blis"])/len(d["blis"]) if d["blis"] else None
        # elasticity: if a lot of impressions are lost to budget, extra budget buys near-linear volume
        e=a.elasticity if a.elasticity is not None else (0.85 if (blis and blis>0.3) else (0.7 if (blis and blis>0.1) else 0.55))
        print(f"\n=== {nm} ({d['type']}) · {days}z ===")
        hdr=f"  buget/zi {S/days:.0f} lei · ROAS {roas:.1f} · {C:.0f} conv · AOV {aov:.0f}"
        if blis is not None: hdr+=f" · buget-lost IS {blis*100:.0f}%"
        hdr+=f" · elasticitate {e:.2f}"+("" if a.elasticity is not None else " (auto)")
        print(hdr)
        cols=f"  {'scenariu':<10}{'buget/zi':>9}{'conv':>7}{'revenue':>9}{'ROAS':>6}"
        if a.margin: cols+=f"{'profit':>9}"
        print(cols)
        for ch in [0.0]+scen:
            m_=1+ch/100.0
            convN=C*(m_**e); valN=convN*aov; spendN=S*m_; roasN=valN/spendN if spendN else 0
            line=f"  {('actual' if ch==0 else f'{ch:+.0f}%'):<10}{spendN/days:>9.0f}{convN:>7.0f}{valN:>9.0f}{roasN:>6.1f}"
            if a.margin:
                profit=valN*a.margin*a.delivery_rate - spendN
                line+=f"{profit:>9.0f}"
            print(line)
        if a.margin:
            be_roas=1.0/(a.margin*a.delivery_rate)
            print(f"  → ROAS breakeven (profit=0) = {be_roas:.1f}  (marjă {a.margin*100:.0f}% × livrare {a.delivery_rate*100:.0f}%)")
        if blis is not None:
            if blis>0.3: print(f"  ▲ {blis*100:.0f}% impresii pierdute pe BUGET → headroom mare, scalează (ROAS scade lent).")
            elif blis<0.05: print(f"  ▼ aproape 0% pierdut pe buget → ești la plafon de cerere; +buget = diminishing returns.")

if __name__=="__main__":
    main()
