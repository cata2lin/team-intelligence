# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31","pypdf>=4.0"]
# ///
"""AUDIT: facturi emise FĂRĂ transport, deși clientul l-a plătit.
Metodă (fără SmartBill, fără potriviri pe nume):
  xConnector documents → mapping EXACT comandă→factură + URL PDF
  Shopify → cât a plătit clientul (total / produse / transport)
  compară TOTALUL facturii cu TOTALUL plătit → diferență = transport nefacturat."""
import sys, json, importlib.util, datetime, csv, subprocess, re, io, time, collections
import requests
from pypdf import PdfReader
from concurrent.futures import ThreadPoolExecutor
DAYS=int(sys.argv[sys.argv.index("--days")+1]) if "--days" in sys.argv else 7
XP="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/gigi/skills/xconnector/xconnector.py"
spec=importlib.util.spec_from_file_location("xcmod",XP); x=importlib.util.module_from_spec(spec)
_argv=sys.argv; sys.argv=["x"]; spec.loader.exec_module(x); sys.argv=_argv
KB="/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def sec(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
rows=list(csv.reader(sec("SHOPIFY_STORES_CSV").splitlines()))
SHOP_TOK={r[1]:(r[0].strip().upper(),r[2]) for r in rows[1:] if r and len(r)>2}
to=datetime.date.today(); fr=to-datetime.timedelta(days=DAYS)
# 1) xConnector: comanda -> factura
inv={}   # orderName -> (serie,nr,url,shopDomain)
for s in x.load_shops():
    dom=s.get("shopDomain")
    try: orders=x.XC(s["apiKey"]).orders(fr.isoformat(), to.isoformat())
    except Exception: continue
    for o in orders:
        d=x.inv_doc(o)
        if d and o.get("orderName"):
            m=re.search(r'[?&]s=([^&]+)&n=(\d+)', d.get("url") or "")
            if m: inv[o["orderName"]]=(m.group(1), int(m.group(2)), d["url"], dom)
print(f"  facturi găsite în xConnector ({fr}→{to}): {len(inv)}")
# 2) Shopify: cât a plătit clientul
Q='''query($c:String,$q:String!){ orders(first:250, after:$c, query:$q){ pageInfo{hasNextPage endCursor}
 edges{ node{ name totalPriceSet{shopMoney{amount}} subtotalPriceSet{shopMoney{amount}}
   totalShippingPriceSet{shopMoney{amount}} } } } }'''
paid={}
for dom,(pfx,tok) in SHOP_TOK.items():
    cur=None
    while True:
        try:
            r=requests.post(f"https://{dom}/admin/api/2026-01/graphql.json",
              headers={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"},
              json={"query":Q,"variables":{"c":cur,"q":f"created_at:>={fr}"}},timeout=60).json()
            d=r.get("data",{}).get("orders",{})
        except Exception: break
        for e in d.get("edges",[]):
            n=e["node"]
            paid[n["name"]]=(pfx,
                float(n["totalPriceSet"]["shopMoney"]["amount"]),
                float(n["subtotalPriceSet"]["shopMoney"]["amount"]),
                float(n["totalShippingPriceSet"]["shopMoney"]["amount"]))
        if d.get("pageInfo",{}).get("hasNextPage"): cur=d["pageInfo"]["endCursor"]
        else: break
print(f"  comenzi Shopify în fereastră: {len(paid)}")
# 3) doar comenzile cu FACTURA + TRANSPORT PLĂTIT > 0
tgt=[(nm,)+inv[nm]+paid[nm] for nm in inv if nm in paid and paid[nm][3]>0]
print(f"  → de verificat (au factură ȘI transport taxat): {len(tgt)}")
def check(t):
    nm,ser,num,url,dom,pfx,tot,sub,shp=t
    for i in range(3):
        try:
            r=requests.get(url,timeout=45)
            if r.status_code==200 and r.content[:4]==b"%PDF":
                txt=" ".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages)
                flat=re.sub(r'\s+',' ',txt)
                if "A N U L A T A" in flat: return None
                m=re.search(r'TOTAL PLATA\s*(-?[\d.,]+)', flat)
                if not m: return None
                itot=float(m.group(1).replace(",",""))
                if itot<0: return None
                return (nm,pfx,f"{ser}{num}",tot,sub,shp,itot)
        except Exception: pass
        time.sleep(2*(i+1))
    return None
res=[]
with ThreadPoolExecutor(max_workers=5) as ex:
    for i,r in enumerate(ex.map(check, tgt)):
        if r: res.append(r)
        if i and i%50==0:
            print(f"    …{i}/{len(tgt)} verificate", flush=True); json.dump(res, open("audit_res.json","w"))
json.dump(res, open("audit_res.json","w"))
missing=[r for r in res if abs(r[3]-r[6])>0.05 and abs(r[4]-r[6])<1.0]   # factura ≈ produse, dar clientul a plătit mai mult
okk   =[r for r in res if abs(r[3]-r[6])<=0.05]
other =[r for r in res if r not in missing and r not in okk]
print(f"\n  ══ REZULTAT ({len(res)} facturi verificate) ══")
print(f"  ✅ corecte (factura = plătit):        {len(okk)}")
print(f"  🔴 FĂRĂ TRANSPORT (factura = produse): {len(missing)}")
print(f"  ❔ alte diferențe:                     {len(other)}")
if missing:
    s=sum(r[5] for r in missing)
    per=collections.Counter(r[1] for r in missing)
    print(f"\n  💰 TRANSPORT NEFACTURAT: {s:.2f} lei pe {len(missing)} facturi")
    print(f"  pe magazin: {dict(per)}")
    for r in missing[:12]: print(f"    {r[1]:5} {r[0]:11} {r[2]:12} plătit={r[3]:7.2f} produse={r[4]:7.2f} transport={r[5]:6.2f} FACTURA={r[6]:7.2f}")
if other:
    for r in other[:6]: print(f"    ❔ {r[1]:5} {r[0]:11} {r[2]:12} plătit={r[3]:7.2f} produse={r[4]:7.2f} transport={r[5]:6.2f} FACTURA={r[6]:7.2f}")
