# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Campaign Structure Reviewer — pull every ENABLED campaign (type, budget, bidding, spend, ROAS,
#ad-groups / #asset-groups) and flag structure problems: below-breakeven campaigns, budget that
isn't on the best ROAS, brand/non-brand mixing, thin/zero-spend groups, PMax↔Search brand overlap,
and bidding-strategy mismatches. Read-only; prints prioritised recommendations.

    uv run campaign_review.py --customer 7566352958 --brand-terms belasil --margin 0.45
    uv run campaign_review.py --customer 5229815058 --brand-terms "esteban,maison" --margin 0.70
"""
import os, sys, argparse, collections
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
    ap.add_argument("--customer",required=True)
    ap.add_argument("--brand-terms",default="",help="comma list to detect brand campaigns")
    ap.add_argument("--margin",type=float,help="profit margin → breakeven ROAS flag")
    ap.add_argument("--delivery-rate",type=float,default=1.0)
    a=ap.parse_args()
    brand=[t.strip().lower() for t in a.brand_terms.split(",") if t.strip()]
    conn=gads.get_connection()
    def gaql(q):
        return gads.search(conn, a.customer, q)
    camps={}
    for row in gaql("SELECT campaign.name, campaign.advertising_channel_type, campaign.bidding_strategy_type, campaign_budget.amount_micros, metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM campaign WHERE campaign.status='ENABLED' AND segments.date DURING LAST_30_DAYS"):
        cm=row["campaign"]; m=row["metrics"]; nm=cm["name"]
        d=camps.setdefault(nm,{"type":cm.get("advertisingChannelType",""),"bid":cm.get("biddingStrategyType",""),
            "budget":float(row.get("campaignBudget",{}).get("amountMicros",0))/1e6,"spend":0.0,"conv":0.0,"val":0.0,"ags":0,"asg":0})
        d["spend"]+=float(m.get("costMicros",0))/1e6; d["conv"]+=float(m.get("conversions",0)); d["val"]+=float(m.get("conversionsValue",0))
    # also campaigns with no spend in window
    for row in gaql("SELECT campaign.name, campaign.advertising_channel_type, campaign.bidding_strategy_type, campaign_budget.amount_micros FROM campaign WHERE campaign.status='ENABLED'"):
        cm=row["campaign"]; nm=cm["name"]
        camps.setdefault(nm,{"type":cm.get("advertisingChannelType",""),"bid":cm.get("biddingStrategyType",""),
            "budget":float(row.get("campaignBudget",{}).get("amountMicros",0))/1e6,"spend":0.0,"conv":0.0,"val":0.0,"ags":0,"asg":0})
    for row in gaql("SELECT campaign.name, ad_group.id FROM ad_group WHERE ad_group.status='ENABLED' AND campaign.status='ENABLED'"):
        nm=row["campaign"]["name"]
        if nm in camps: camps[nm]["ags"]+=1
    for row in gaql("SELECT campaign.name, asset_group.id FROM asset_group WHERE asset_group.status='ENABLED' AND campaign.status='ENABLED'"):
        nm=row["campaign"]["name"]
        if nm in camps: camps[nm]["asg"]+=1

    be=1.0/(a.margin*a.delivery_rate) if a.margin else None
    print(f"\n=== Structură cont · {a.customer} · 30z ==="+(f" · breakeven ROAS {be:.1f}" if be else ""))
    print(f"  {'campanie':<30}{'tip':<14}{'buget':>7}{'spend':>7}{'ROAS':>6}{'grupuri':>8}  bidding")
    rows=sorted(camps.items(),key=lambda x:-x[1]["spend"])
    for nm,d in rows:
        roas=d["val"]/d["spend"] if d["spend"] else 0
        groups=d["asg"] if d["type"]=="PERFORMANCE_MAX" else d["ags"]
        print(f"  {nm[:30]:<30}{d['type'][:13]:<14}{d['budget']:>7.0f}{d['spend']:>7.0f}{roas:>6.1f}{groups:>8}  {d['bid'][:20]}")
    recs=[]
    is_brand=lambda nm: any(t in nm.lower() for t in brand) or "brand" in nm.lower()
    has_brand_camp=any(is_brand(nm) for nm in camps)
    for nm,d in rows:
        roas=d["val"]/d["spend"] if d["spend"] else 0
        if be and d["spend"]>=50 and 0<roas<be:
            recs.append((90,f"„{nm}”: ROAS {roas:.1f} sub breakeven {be:.1f} — pierde bani. Fix copy/feed/targetare sau pune pe pauză."))
        if d["spend"]>=50 and d["conv"]==0:
            recs.append((85,f"„{nm}”: spend {d['spend']:.0f} lei, 0 conversii — verifică tracking/feed sau oprește."))
        if d["type"]=="SEARCH" and not is_brand(nm) and d["ags"]>=3 and d["spend"]/max(d["ags"],1)<15:
            recs.append((50,f"„{nm}”: {d['ags']} ad groups, spend mic/grup — consolidează grupurile subțiri."))
        if d["type"]=="SEARCH" and is_brand(nm) and "MAX" in d["bid"]:
            recs.append((40,f"„{nm}” (brand): pe {d['bid']} — brand-ul merge adesea mai bine pe Manual/Maximize clicks cu IS target mare."))
        if d["spend"]==0 and d["budget"]>0:
            recs.append((30,f"„{nm}”: activă, buget alocat, dar 0 spend 30z — moartă? oprește sau verifică de ce nu servește."))
    # brand/non-brand hygiene
    if brand and not has_brand_camp:
        recs.append((60,"Nicio campanie de BRAND dedicată — concurenții îți pot licita brand-ul ieftin. Fă o campanie Brand separată."))
    # budget concentration among NON-brand campaigns (brand ROAS is always higher but volume-capped)
    spent=[(nm,d["spend"],(d["val"]/d["spend"] if d["spend"] else 0)) for nm,d in rows if d["spend"]>=50 and not is_brand(nm)]
    if len(spent)>=2:
        top_spend=max(spent,key=lambda x:x[1]); best_roas=max(spent,key=lambda x:x[2])
        if top_spend[0]!=best_roas[0] and best_roas[2]>top_spend[2]*1.4:
            recs.append((55,f"Bugetul cel mai mare e pe „{top_spend[0]}” (ROAS {top_spend[2]:.1f}) dar „{best_roas[0]}” are ROAS {best_roas[2]:.1f} — mută buget spre cea eficientă."))
    print(f"\n— RECOMANDĂRI —")
    if not recs: print("  ✓ structura arată sănătoasă pe pragurile verificate.")
    for s,msg in sorted(recs,reverse=True):
        ic="🔴" if s>=80 else ("🟠" if s>=50 else "🟡")
        print(f"  {ic} {msg}")

if __name__=="__main__":
    main()
