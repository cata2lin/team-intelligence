# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""negative_cleaner.py — găsește NEGATIVE prea largi care blochează trafic LEGITIM (replică google-marketing-solutions/negative_keyword_cleaner).

Trage toate negative keywords ale unui cont (campanie + liste partajate + ad-group), le trimite la un LLM
cu contextul de brand, care le clasează RISC (blochează trafic bun) vs OK (blochează corect gunoi). Read-only —
doar raportează RISC-urile pentru review manual; ștergerea o faci după ce te uiți.

Complementar cu `ngram_miner.py` (ăla ADAUGĂ negative; ăsta scoate negativele greșite care sufocă traficul).

Usage:
  uv run negative_cleaner.py --customer 7566352958 --brand "Belasil — detergenți & produse de curățenie, RO"
  uv run negative_cleaner.py --customer 5031005158 --brand "GT Parfumuri — parfumuri inspired-by, RO" --limit 200
"""
import os, sys, json, argparse, importlib.util, subprocess, requests
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
def kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
if not os.environ.get("DATABASE_URL_METRICS"): os.environ["DATABASE_URL_METRICS"]=kb("DATABASE_URL_METRICS")
sp=importlib.util.spec_from_file_location("gads",os.path.join(HERE,"gads.py")); g=importlib.util.module_from_spec(sp); _a=sys.argv; sys.argv=["gads"]; sp.loader.exec_module(g); sys.argv=_a
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
def gv(d,p):
    cur=d
    for k in p.split("."):
        if not isinstance(cur,dict): return None
        cur=cur.get(k)
    return cur
ap=argparse.ArgumentParser()
ap.add_argument("--customer",required=True)
ap.add_argument("--brand",required=True,help="descriere brand + ce vinde (context pt LLM)")
ap.add_argument("--limit",type=int,default=300,help="max negative trimise la LLM")
ap.add_argument("--model",default="gpt-4o-mini")
a=ap.parse_args()
c=g.get_connection(None)
negs={}   # text -> sursă
# 1) campanie
for r in g.search(c,a.customer,"SELECT campaign.name,campaign_criterion.keyword.text,campaign_criterion.keyword.match_type FROM campaign_criterion WHERE campaign_criterion.negative=true AND campaign_criterion.type='KEYWORD'"):
    t=gv(r,"campaignCriterion.keyword.text")
    if t: negs.setdefault(t.lower(),f"camp:{gv(r,'campaign.name')}|{gv(r,'campaignCriterion.keyword.matchType')}")
# 2) liste partajate
for r in g.search(c,a.customer,"SELECT shared_set.name,shared_criterion.keyword.text,shared_criterion.keyword.match_type FROM shared_criterion"):
    t=gv(r,"sharedCriterion.keyword.text")
    if t: negs.setdefault(t.lower(),f"list:{gv(r,'sharedSet.name')}|{gv(r,'sharedCriterion.keyword.matchType')}")
# 3) ad-group
for r in g.search(c,a.customer,"SELECT ad_group.name,ad_group_criterion.keyword.text,ad_group_criterion.keyword.match_type FROM ad_group_criterion WHERE ad_group_criterion.negative=true AND ad_group_criterion.type='KEYWORD'"):
    t=gv(r,"adGroupCriterion.keyword.text")
    if t: negs.setdefault(t.lower(),f"ag:{gv(r,'adGroup.name')}|{gv(r,'adGroupCriterion.keyword.matchType')}")
if not negs: print("(niciun negative keyword pe cont)"); sys.exit(0)
items=list(negs.keys())[:a.limit]
print(f"═══ NEGATIVE CLEANER — cont {a.customer} · {len(negs)} negative (analizez {len(items)}) ═══")
OPENAI=kb("OPENAI_API_KEY")
import time
def ask_llm(payload_items):
    body={"model":a.model,"messages":[{"role":"user","content":(
        "Ești specialist Google Ads. Brand: "+a.brand+".\n"
        "Îți dau o listă de NEGATIVE keywords active. Pt fiecare decide RISC (prea larg / ar putea bloca și trafic "
        "LEGITIM de clienți care ar cumpăra) sau OK (blochează corect: competitori, gratis, joburi, DIY, off-topic). "
        "Fii conservator: RISC doar când chiar poate opri cumpărători reali. Răspunde DOAR JSON: "
        '{"risc":[{"kw":"...","motiv":"...scurt RO"}]}.\n\nNEGATIVE:\n'+json.dumps(payload_items,ensure_ascii=False))}],
        "response_format":{"type":"json_object"},"temperature":0.1}
    for attempt in range(4):
        try:
            r=requests.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization":f"Bearer {OPENAI}","Content-Type":"application/json"},json=body,timeout=120)
            if r.status_code==200: return json.loads(r.json()["choices"][0]["message"]["content"]).get("risc",[])
            if r.status_code in (429,500,502,503): time.sleep(2*(attempt+1)); continue
            print(f"  ⚠️ OpenAI {r.status_code}: {r.text[:120]}"); return []
        except Exception as e:
            time.sleep(2*(attempt+1))
    return []
# procesează în chunk-uri de 100 (robust + evită prompturi uriașe)
risc=[]
for i in range(0,len(items),100):
    risc+=ask_llm(items[i:i+100])
print(f"\n🔴 NEGATIVE cu RISC (ar putea bloca trafic bun) — {len(risc)}:")
for x in risc:
    kw=x.get("kw",""); src=negs.get(kw.lower(),"?")
    print(f"  „{kw}\"  [{src}]\n      → {x.get('motiv','')}")
if not risc: print("  ✅ niciun negative riscant — lista e curată")
print(f"\n→ Pt a scoate unul: identifică sursa (camp/list/ag) și șterge criteriul respectiv (dry-run întâi).")
