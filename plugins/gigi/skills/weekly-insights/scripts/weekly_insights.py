# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Weekly Performance Insights — week-over-week, combining Google Ads (live) with REAL Shopify
orders (metrics `orders`). Shows paid spend/ROAS/CPA, real revenue/orders/AOV, the blended ad-cost
ratio (Ads spend ÷ total revenue ≈ MER), the Ads-vs-real reconciliation gap, plus a narrative of
what moved and what to do. Read-only.

    uv run weekly_insights.py --customer 5229815058 --brand esteban
    uv run weekly_insights.py --customer 7566352958              # Ads-only (no orders synced)
"""
import os, sys, argparse, datetime, collections
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras
# shared Google Ads MCC client (Ads creds+OAuth+search) — google-ads-mcc/gads.py.
# cx below stays for the Shopify orders query (metrics DB), which gads doesn't cover.
_here = Path(__file__).resolve()
for _up in range(1, 6):
    _cand = _here.parents[_up] / "google-ads-mcc"
    if (_cand / "gads.py").exists():
        sys.path.insert(0, str(_cand)); break
import gads
_PG_OK={"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","options","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _PG_OK]),p.fragment))
def pct(n,o): return f"{'+' if n>=o else ''}{(100*(n-o)/o):.0f}%" if o else ("+∞" if n else "0%")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--customer",required=True); ap.add_argument("--brand")
    a=ap.parse_args()
    cx=psycopg2.connect(clean(os.environ["DATABASE_URL_METRICS"])); cx.set_session(readonly=True)
    c=cx.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    conn=gads.get_connection()
    end=datetime.date.today(); start=end-datetime.timedelta(days=16)
    q=(f"SELECT campaign.name, segments.date, metrics.cost_micros, metrics.conversions, metrics.conversions_value "
       f"FROM campaign WHERE campaign.status='ENABLED' AND segments.date BETWEEN '{start}' AND '{end}'")
    results=gads.search(conn, a.customer, q)
    byd=collections.defaultdict(lambda:{"spend":0.0,"conv":0.0,"val":0.0})
    bycamp=collections.defaultdict(lambda:{"tw":{"spend":0.0,"conv":0.0,"val":0.0},"lw":{"spend":0.0,"conv":0.0,"val":0.0}})
    dates=set()
    for row in results:
        dt=row["segments"]["date"]; m=row["metrics"]; dates.add(dt)
        sp=float(m.get("costMicros",0))/1e6; cv=float(m.get("conversions",0)); vl=float(m.get("conversionsValue",0))
        x=byd[dt]; x["spend"]+=sp; x["conv"]+=cv; x["val"]+=vl
    ds=sorted(dates); today=str(datetime.date.today())
    if ds and ds[-1]==today: ds=ds[:-1]
    if len(ds)<2: sys.exit("prea puține zile de date pentru un raport WoW")
    half=min(7,len(ds)//2)                       # adaptive: full 7v7 when available, else half/half
    tw=ds[-half:]; lw=ds[-2*half:-half]
    def win(days):
        s={"spend":0.0,"conv":0.0,"val":0.0}
        for d in days:
            for k in s: s[k]+=byd[d][k]
        return s
    # per-campaign tw/lw split
    twset,lwset=set(tw),set(lw)
    cc=collections.defaultdict(lambda:{"tw":{"spend":0.0,"conv":0.0,"val":0.0},"lw":{"spend":0.0,"conv":0.0,"val":0.0}})
    for row in results:
        dt=row["segments"]["date"]; nm=row["campaign"]["name"]; m=row["metrics"]
        bucket="tw" if dt in twset else ("lw" if dt in lwset else None)
        if not bucket: continue
        sp=float(m.get("costMicros",0))/1e6; cv=float(m.get("conversions",0)); vl=float(m.get("conversionsValue",0))
        cc[nm][bucket]["spend"]+=sp; cc[nm][bucket]["conv"]+=cv; cc[nm][bucket]["val"]+=vl
    T=win(tw); L=win(lw)
    def roas(s): return s["val"]/s["spend"] if s["spend"] else 0
    def cpa(s): return s["spend"]/s["conv"] if s["conv"] else 0
    print(f"\n╔══ Weekly Insights · {a.customer} ══╗  săpt {tw[0]}..{tw[-1]} vs {lw[0]}..{lw[-1]}")
    print(f"\n— GOOGLE ADS (raportat) —")
    print(f"  spend   {T['spend']:8.0f} lei  ({pct(T['spend'],L['spend'])})")
    print(f"  conv    {T['conv']:8.0f}      ({pct(T['conv'],L['conv'])})")
    print(f"  ROAS    {roas(T):8.1f}      ({pct(roas(T),roas(L))})   CPA {cpa(T):.0f} lei ({pct(cpa(T),cpa(L))})")
    # Shopify real orders
    rev=None
    if a.brand:
        c.execute("""SELECT
            sum("totalPrice") FILTER (WHERE o."createdAt"::date BETWEEN %s::date AND %s::date) tw_rev,
            count(*)         FILTER (WHERE o."createdAt"::date BETWEEN %s::date AND %s::date) tw_ord,
            sum("totalPrice") FILTER (WHERE o."createdAt"::date BETWEEN %s::date AND %s::date) lw_rev,
            count(*)         FILTER (WHERE o."createdAt"::date BETWEEN %s::date AND %s::date) lw_ord
            FROM orders o WHERE o."brandId"=(SELECT id FROM brands WHERE slug=%s)""",
            (tw[0],tw[-1],tw[0],tw[-1],lw[0],lw[-1],lw[0],lw[-1],a.brand))
        rev=c.fetchone()
    if rev and rev["tw_ord"]:
        twr=float(rev["tw_rev"] or 0); lwr=float(rev["lw_rev"] or 0); two=rev["tw_ord"]; lwo=rev["lw_ord"]
        aov_t=twr/two if two else 0; aov_l=lwr/lwo if lwo else 0
        print(f"\n— SHOPIFY (real, toate canalele) —")
        print(f"  comenzi {two:8d}      ({pct(two,lwo)})")
        print(f"  revenue {twr:8.0f} lei  ({pct(twr,lwr)})   AOV {aov_t:.0f} lei ({pct(aov_t,aov_l)})")
        mer=T['spend']/twr if twr else 0
        print(f"\n— BLENDED —")
        print(f"  ad-cost ratio (Ads spend ÷ revenue real) {100*mer:.1f}%   → MER {1/mer if mer else 0:.1f}x")
        print(f"  Ads-attribuit {T['val']:.0f} lei vs revenue real {twr:.0f} lei = Ads ia {100*T['val']/twr if twr else 0:.0f}% din vânzări")
    else:
        print(f"\n— SHOPIFY — (fără date de comenzi în metrics pt acest brand; doar Ads)")
    # movers
    movers=[]
    for nm,d in cc.items():
        if d["lw"]["spend"]<20 and d["tw"]["spend"]<20: continue
        movers.append((roas(d["tw"])-roas(d["lw"]), nm, d))
    print(f"\n— CAMPANII (ROAS Δ) —")
    for delta,nm,d in sorted(movers,reverse=True):
        arrow="▲" if delta>=0 else "▼"
        print(f"  {arrow} {nm[:30]:30s} spend {d['lw']['spend']:.0f}→{d['tw']['spend']:.0f}  ROAS {roas(d['lw']):.1f}→{roas(d['tw']):.1f}")
    print(f"\n— DE FĂCUT —")
    if roas(T)<roas(L)*0.85: print("  • ROAS în scădere WoW — vezi anomaly_detector + change_history (ce s-a schimbat).")
    worst=min(movers,key=lambda x:x[0],default=None)
    if worst and worst[0]<-1: print(f"  • „{worst[1]}\" a scăzut cel mai mult — investighează feed/buget/learning.")
    best=max(movers,key=lambda x:x[0],default=None)
    if best and best[0]>1 and roas(best[2]['tw'])>3: print(f"  • „{best[1]}\" urcă — candidat de scalare (vezi product-matrix).")
    if rev and rev["tw_ord"] and T['spend']/(float(rev['tw_rev'] or 1))>0.25: print("  • ad-cost ratio >25% — eficiență blended scăzută, verifică unde se duce spend-ul (search-terms).")

if __name__=="__main__":
    main()
