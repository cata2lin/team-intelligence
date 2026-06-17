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
from pathlib import Path
# shared Google Ads MCC client (creds + OAuth + GAQL search) — google-ads-mcc/gads.py
_here = Path(__file__).resolve()
for _up in range(1, 6):
    _cand = _here.parents[_up] / "google-ads-mcc"
    if (_cand / "gads.py").exists():
        sys.path.insert(0, str(_cand)); break
import gads

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--customer",required=True); ap.add_argument("--campaign")
    ap.add_argument("--days",type=int,default=30)
    ap.add_argument("--elasticity",type=float,help="0..1; conv ∝ budget^elasticity (1=linear, lower=diminishing). Default: derived from budget-lost IS.")
    ap.add_argument("--margin",type=float,help="profit margin (e.g. 0.70 for Esteban 2+1). Enables profit view.")
    ap.add_argument("--delivery-rate",type=float,default=1.0,help="COD: fraction of orders actually delivered/paid (e.g. 0.85)")
    ap.add_argument("--scenarios",default="-20,10,20,50",help="budget % changes to simulate")
    a=ap.parse_args()
    conn=gads.get_connection()
    where="campaign.status='ENABLED'"+(f" AND campaign.name='{a.campaign}'" if a.campaign else "")
    q=(f"SELECT campaign.name, campaign.advertising_channel_type, metrics.cost_micros, metrics.conversions, "
       f"metrics.conversions_value, metrics.search_budget_lost_impression_share "
       f"FROM campaign WHERE {where} AND segments.date DURING LAST_{a.days if a.days in (7,14,30) else 30}_DAYS")
    camps={}
    for row in gads.search(conn, a.customer, q):
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
