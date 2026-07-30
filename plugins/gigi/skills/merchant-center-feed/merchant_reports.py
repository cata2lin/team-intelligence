# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""merchant_reports.py — PERFORMANCE reports per produs din Merchant Center (MCQL product_performance_view).

Ce nu avea `merchant_feed.py` (ăla = feed-HEALTH / dezaprobări): aici = clicuri, impresii, CTR, conversii per PRODUS
din Google Shopping/free listings — ideea furată din nicolasacchi/merchant-cli, dar în Python cu OAuth-ul nostru din KB.
Vezi ce produse din feed chiar aduc clicuri/vânzări vs care-s „zombie" (0 clicuri). Read-only.

Usage:
  uv run merchant_reports.py --store grandia --days 30 --top 30
  uv run merchant_reports.py --store 5677157050 --mcql "SELECT offer_id,title,clicks FROM product_performance_view WHERE date DURING LAST_30_DAYS"
"""
import os, sys, json, argparse, subprocess, datetime, importlib.util
import requests
HERE=os.path.dirname(os.path.abspath(__file__))
# reutilizează _kb/_token/ACCOUNTS din merchant_feed.py (import-safe: main sub if __name__)
_spec=importlib.util.spec_from_file_location("merchant_feed",os.path.join(HERE,"merchant_feed.py"))
mf=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(mf)
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
ap=argparse.ArgumentParser()
ap.add_argument("--store",required=True,help="nume (grandia/esteban/...) sau merchant_id")
ap.add_argument("--days",type=int,default=30)
ap.add_argument("--top",type=int,default=30)
ap.add_argument("--mcql",help="query MCQL raw (override)")
a=ap.parse_args()
acct=mf.ACCOUNTS.get(a.store.lower(),a.store)
tok=mf._token()
to=datetime.date.today(); frm=to-datetime.timedelta(days=a.days)
q=a.mcql or (f"SELECT offer_id, title, clicks, impressions, click_through_rate, conversions, conversion_value "
             f"FROM product_performance_view WHERE date BETWEEN '{frm.isoformat()}' AND '{to.isoformat()}'")
H={"Authorization":f"Bearer {tok}","Content-Type":"application/json"}
rows=[]; page=None
for _ in range(20):
    body={"query":q,"pageSize":1000}
    if page: body["pageToken"]=page
    r=requests.post(f"https://merchantapi.googleapis.com/reports/v1/accounts/{acct}/reports:search",headers=H,json=body,timeout=120)
    if r.status_code!=200:
        print(f"❌ Merchant API {r.status_code}: {r.text[:400]}"); sys.exit(1)
    d=r.json()
    rows+=d.get("results",[])
    page=d.get("nextPageToken")
    if not page: break
def g(row,*keys):
    pv=row.get("productPerformanceView",row);
    for k in keys:
        if k in pv: return pv[k]
    return None
def num(x): return float(x) if x not in (None,"") else 0.0
recs=[]
for row in rows:
    pv=row.get("productPerformanceView",{})
    recs.append({"offer":pv.get("offerId") or pv.get("offer_id"),"title":(pv.get("title") or "")[:44],
                 "clicks":num(pv.get("clicks")),"impr":num(pv.get("impressions")),
                 "ctr":num(pv.get("clickThroughRate")),"conv":num(pv.get("conversions")),
                 "val":num(pv.get("conversionValueMicros"))/1e6})
tc=sum(x["clicks"] for x in recs); ti=sum(x["impr"] for x in recs); tconv=sum(x["conv"] for x in recs)
zombies=[x for x in recs if x["clicks"]==0]
recs.sort(key=lambda x:-x["clicks"])
print(f"═══ MERCHANT PERFORMANCE — {a.store} ({acct}), {a.days}z · {len(recs)} produse cu date ═══")
print(f"Total: clicuri={tc:,.0f} · impresii={ti:,.0f} · CTR={100*tc/ti if ti else 0:.2f}% · conv={tconv:.0f}")
print(f"🧟 ZOMBIE (0 clicuri în {a.days}z): {len(zombies)} produse\n")
print(f"  {'offer':22}{'clicuri':>8}{'impr':>8}{'CTR%':>7}{'conv':>6}  titlu")
for x in recs[:a.top]:
    print(f"  {(x['offer'] or '')[:22]:22}{x['clicks']:>8.0f}{x['impr']:>8.0f}{x['ctr']*100:>6.1f}%{x['conv']:>6.1f}  {x['title']}")
print(f"\n→ Zombie (0 clicuri) = candidați pt feed_attr_filler (titluri/atribute mai bune) sau excludere din PMax.")
