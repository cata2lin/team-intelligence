# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""ngram_miner.py — analiză n-gram pe search terms (replică feature-ul Optmyzr/Opteo, native Python).

Agregă termenii de căutare în 1/2/3-grame, cu cost/conversii/CPA per n-gram, ca să vezi PATTERN-uri
de risipă (cuvinte care apar în multe query-uri, ard buget, 0 conversii) → candidați de NEGATIVE,
și n-grame cu CPA bun → candidați de KEYWORD. Un cont cu 1M search terms are ~30-50k n-grame = review tractabil.

Sursă: search_term_view via wrapper v21. Read-only (doar RAPORTEAZĂ candidați; nu adaugă negative — folosește
`gads.py add-negatives` după review). Breakeven per brand din brandref → marchează risipa peste prag.

Usage:
  uv run ngram_miner.py --customer 5031005158 --days 30
  uv run ngram_miner.py --customer 5031005158 --days 30 --brand "george talent,gt parfumuri" --min-cost 30
  uv run ngram_miner.py --customer 5031005158 --n 2 --min-terms 3 --top 40
"""
import os, sys, re, argparse, importlib.util, subprocess
from collections import defaultdict
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
if not os.environ.get("DATABASE_URL_METRICS"):
    os.environ["DATABASE_URL_METRICS"]=subprocess.run(["uv","run",KB,"secret-get","DATABASE_URL_METRICS"],capture_output=True,text=True).stdout.strip()
sp=importlib.util.spec_from_file_location("gads",os.path.join(HERE,"gads.py")); g=importlib.util.module_from_spec(sp); _a=sys.argv; sys.argv=["gads"]; sp.loader.exec_module(g); sys.argv=_a
try:
    sb=importlib.util.spec_from_file_location("brandref",os.path.join(HERE,"brandref.py")); br=importlib.util.module_from_spec(sb); sb.loader.exec_module(br)
except Exception: br=None
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
def gv(d,p):
    cur=d
    for k in p.split("."):
        if not isinstance(cur,dict): return None
        cur=cur.get(k)
    return cur
def num(x): return float(x) if x not in (None,"") else 0.0
# stopwords RO+EN — ca 1-gramele să nu fie dominate de cuvinte de legătură
STOP=set("de la cu si și în in pe un o al ale a pentru din care ce mai fara fără sau ca to the a an of for and or with in on at by from is are be best top new online buy".split())
ap=argparse.ArgumentParser()
ap.add_argument("--customer",required=True)
ap.add_argument("--days",type=int,default=30)
ap.add_argument("--n",type=int,default=3,help="mărimea maximă a n-gramelor (1..3)")
ap.add_argument("--brand",default="",help="termeni de brand (csv) — n-gramele cu ei sunt marcate BRAND, nu risipă")
ap.add_argument("--min-cost",type=float,default=20.0,help="prag cost pt candidați de negative (moneda contului)")
ap.add_argument("--min-terms",type=int,default=2,help="n-grama trebuie să apară în ≥N termeni distincți")
ap.add_argument("--top",type=int,default=30)
a=ap.parse_args()
brand_toks=[t.strip().lower() for t in a.brand.split(",") if t.strip()]
c=g.get_connection(None)
q=(f"SELECT search_term_view.search_term,campaign.name,metrics.cost_micros,metrics.conversions,"
   f"metrics.clicks,metrics.conversions_value FROM search_term_view "
   f"WHERE segments.date DURING LAST_{a.days}_DAYS AND campaign.advertising_channel_type='SEARCH'")
rows=g.search(c,a.customer,q)
if not rows: print("(niciun search term — contul are campanii Search active?)"); sys.exit(0)
# \w (unicode) prinde toate diacriticele RO/CZ/PL/SK/HU/HR/BG (ř,ě,ą,ł,ő,č,š,ž…), nu doar românești
def toks(s): return [w for w in re.findall(r"\w+", (s or "").lower(), re.UNICODE) if len(w)>1 and not w.isdigit()]
grams=defaultdict(lambda:{"cost":0.0,"conv":0.0,"clk":0.0,"val":0.0,"terms":set()})
tot_cost=tot_conv=0.0
for r in rows:
    st=gv(r,"searchTermView.searchTerm"); m=r.get("metrics",{})
    cost=num(m.get("costMicros"))/1e6; conv=num(m.get("conversions")); clk=num(m.get("clicks")); val=num(m.get("conversionsValue"))
    tot_cost+=cost; tot_conv+=conv
    w=toks(st)
    seen=set()
    for size in range(1,a.n+1):
        for i in range(len(w)-size+1):
            gramw=w[i:i+size]
            if size==1 and gramw[0] in STOP: continue
            key=" ".join(gramw)
            if key in seen: continue   # nu dubla costul termenului pt aceeași n-gramă
            seen.add(key)
            d=grams[key]; d["cost"]+=cost; d["conv"]+=conv; d["clk"]+=clk; d["val"]+=val; d["terms"].add(st)
def is_brand(k): return any(bt in k for bt in brand_toks) if brand_toks else False
rec=[]
for k,d in grams.items():
    if len(d["terms"])<a.min_terms: continue
    cpa=d["cost"]/d["conv"] if d["conv"] else 0
    rec.append((k,d,cpa))
# RISIPĂ: cost mare, 0 conversii, non-brand
waste=sorted([x for x in rec if x[1]["conv"]==0 and x[1]["cost"]>=a.min_cost and not is_brand(x[0])],key=lambda x:-x[1]["cost"])
# CÂȘTIGĂTORI: conversii cu CPA bun, non-brand
be=None
if br:
    for key in ["grandia","gt","nubra","belasil","ofertele","gento","carpetto","rossi","nocturna","bonhaus_pl","bonhaus_cz"]:
        pass
winners=sorted([x for x in rec if x[1]["conv"]>=1 and not is_brand(x[0])],key=lambda x:(x[2] if x[2] else 9e9))
cur=""
print(f"═══ N-GRAM MINER — cont {a.customer}, {a.days}z · {len(rows)} termeni → {len(grams)} n-grame ═══")
print(f"Total: cost={tot_cost:,.0f} · conv={tot_conv:.0f} · CPA={tot_cost/tot_conv if tot_conv else 0:.0f}\n")
print(f"🔴 RISIPĂ (candidați NEGATIVE — cost≥{a.min_cost:.0f}, 0 conv, ≥{a.min_terms} termeni, non-brand) — top {a.top}:")
print(f"  {'n-gramă':32}{'cost':>8}{'clk':>6}{'#termeni':>9}")
for k,d,_ in waste[:a.top]:
    print(f"  {k[:32]:32}{d['cost']:>8.0f}{d['clk']:>6.0f}{len(d['terms']):>9}")
if not waste: print("  (fără risipă clară peste prag — piața caută relevant)")
print(f"\n🟢 CÂȘTIGĂTORI (candidați KEYWORD — au conversii, CPA mic, non-brand) — top {a.top}:")
print(f"  {'n-gramă':32}{'cost':>8}{'conv':>6}{'CPA':>6}{'#termeni':>9}")
for k,d,cpa in winners[:a.top]:
    print(f"  {k[:32]:32}{d['cost']:>8.0f}{d['conv']:>6.1f}{cpa:>6.0f}{len(d['terms']):>9}")
print(f"\n→ După review: `gads.py add-negatives --customer {a.customer} --campaign <ID> --terms \"...\" --match PHRASE`")
print(f"→ Sau winners ca EXACT: `gads.py add-keywords --customer {a.customer} --adgroup <ID> --terms \"...\" --match EXACT`")
