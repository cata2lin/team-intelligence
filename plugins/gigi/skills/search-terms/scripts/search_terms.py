# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Search Term Analyzer — mine search_term_view: find WASTE (spend, 0 conversions) → ready-to-paste
negative keywords, flag COMPETITOR spend, surface converting NON-BRAND terms as keyword opportunities,
and split spend brand vs non-brand. Read-only; prints `gads.py add-negatives` commands you then run.

    uv run search_terms.py --customer 7566352958 --brand-terms belasil
    uv run search_terms.py --customer 5229815058 --brand-terms "esteban,maison d'esteban" --days 14
    uv run search_terms.py --customer 7566352958 --brand-terms belasil --competitor-terms "dero,ariel,persil,chanteclair" --min-waste 5
"""
import os, sys, argparse, re, collections
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
    ap.add_argument("--days",type=int,default=30)
    ap.add_argument("--brand-terms",default="",help="comma list that marks a term as BRAND (don't negative)")
    ap.add_argument("--competitor-terms",default="",help="comma list of competitor names to flag")
    ap.add_argument("--junk-terms",default="gratis,free,pdf,reteta,rețetă,diy,olx,second hand,angajare,job",help="irrelevant markers")
    ap.add_argument("--min-waste",type=float,default=3.0,help="RON spend with 0 conv to flag as negative candidate")
    ap.add_argument("--campaign",help="filter to one campaign name")
    a=ap.parse_args()
    brand=[t.strip().lower() for t in a.brand_terms.split(",") if t.strip()]
    comp=[t.strip().lower() for t in a.competitor_terms.split(",") if t.strip()]
    junk=[t.strip().lower() for t in a.junk_terms.split(",") if t.strip()]
    conn=gads.get_connection()
    where="metrics.cost_micros > 0"+(f" AND campaign.name = '{a.campaign}'" if a.campaign else "")
    q=(f"SELECT search_term_view.search_term, campaign.name, metrics.cost_micros, metrics.conversions, "
       f"metrics.conversions_value, metrics.clicks FROM search_term_view "
       f"WHERE segments.date DURING LAST_{a.days}_DAYS AND {where}")
    T=[]
    for row in gads.search(conn, a.customer, q):
        m=row["metrics"]; t=row["searchTermView"]["searchTerm"].lower()
        T.append({"t":t,"cost":float(m.get("costMicros",0))/1e6,"conv":float(m.get("conversions",0)),
                  "val":float(m.get("conversionsValue",0)),"clicks":int(m.get("clicks",0))})
    def has(t,lst): return any(w in t for w in lst)
    tot=sum(x["cost"] for x in T); brand_spend=sum(x["cost"] for x in T if has(x["t"],brand))
    waste=[x for x in T if x["conv"]==0 and x["cost"]>=a.min_waste and not has(x["t"],brand)]
    waste.sort(key=lambda x:-x["cost"]); waste_sum=sum(x["cost"] for x in waste)
    comp_terms=[x for x in T if has(x["t"],comp)]; comp_sum=sum(x["cost"] for x in comp_terms)
    junk_terms=[x for x in waste if has(x["t"],junk)]
    winners=[x for x in T if x["conv"]>=1 and not has(x["t"],brand)]; winners.sort(key=lambda x:-x["val"])

    print(f"\n=== Search Terms · {a.customer} · {a.days} zile · {len(T)} termeni · {tot:.0f} RON ===")
    print(f"  brand: {brand_spend:.0f} RON ({100*brand_spend/tot if tot else 0:.0f}%) | non-brand: {tot-brand_spend:.0f} RON")
    print(f"  RISIPĂ (0 conv, ≥{a.min_waste:.0f} RON): {waste_sum:.0f} RON pe {len(waste)} termeni ({100*waste_sum/tot if tot else 0:.0f}% din spend)")
    print(f"\n── TOP NEGATIVE CANDIDATES (risipă 0-conv) ──")
    for x in waste[:20]:
        tag=" [COMP]" if has(x["t"],comp) else (" [JUNK]" if has(x["t"],junk) else (" [GENERIC]" if len(x["t"].split())<=1 else ""))
        print(f"  {x['cost']:6.0f} RON  {x['clicks']:3d} cl  0 conv  „{x['t']}\"{tag}")
    if comp_terms:
        print(f"\n── CONCURENȚI ({comp_sum:.0f} RON pe {len(comp_terms)} termeni) ──")
        for x in sorted(comp_terms,key=lambda x:-x['cost'])[:8]: print(f"  {x['cost']:6.0f} RON  {x['conv']:.0f} conv  „{x['t']}\"")
    print(f"\n── WINNERS non-brand (convertesc → candidați keyword) ──")
    for x in winners[:10]:
        roas=x['val']/x['cost'] if x['cost'] else 0
        print(f"  {x['cost']:6.0f} RON  {x['conv']:.0f} conv  ROAS {roas:4.1f}  „{x['t']}\"")
    if waste:
        neg=",".join(sorted({x['t'] for x in waste[:30]}))
        print(f"\n── COMANDĂ (rulează după review) ──")
        print(f"  uv run gads.py add-negatives --customer {a.customer} --campaign <ID> --match PHRASE --terms \"{neg[:300]}{'...' if len(neg)>300 else ''}\"")

if __name__=="__main__":
    main()
