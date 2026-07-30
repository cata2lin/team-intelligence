# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""feed_attr_filler.py — completează ATRIBUTE lipsă în feed-ul Google Shopping cu LLM (replică open-source FeedGen).

PMax favorizează bestsellerii și lasă long-tail-ul „zombie" (0 impresii) fiindcă titlurile/atributele-s sărace.
Acest tool ia produse (JSON/CSV cu title + description [+ type]) și generează, per produs: titlu OPTIMIZAT
(atribute-cheie în față, ≤150 char), google_product_category, product_type, culoare, material, gen (dacă e cazul),
și o descriere mai bogată — exact ce hrănește feed-ul ca produsele să intre în licitații long-tail.

Read-only pe Google (doar GENEREAZĂ sugestii); scrierea în feed = editezi produsul în Shopify (metafields/atribute)
sau urci în Merchant Center. Feed-agnostic: merge pe orice export de produse.

Usage:
  uv run feed_attr_filler.py --sample                         # demo pe produse exemplu
  uv run feed_attr_filler.py --input produse.json             # JSON: [{"id","title","description","type"}]
  uv run feed_attr_filler.py --input export.csv --brand "Grandia — home & garden RO"
"""
import os, sys, json, csv, argparse, subprocess, requests, time
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
def kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
ap=argparse.ArgumentParser()
ap.add_argument("--input",help="JSON [{id,title,description,type}] sau CSV cu coloane title,description[,type]")
ap.add_argument("--sample",action="store_true")
ap.add_argument("--brand",default="magazin e-commerce România")
ap.add_argument("--lang",default="ro")
ap.add_argument("--model",default="gpt-4o-mini")
ap.add_argument("--out",help="scrie sugestiile în acest JSON")
a=ap.parse_args()
SAMPLE=[
 {"id":"1","title":"Pompă transfer motorină","description":"Pompă electrică pentru transfer combustibil."},
 {"id":"2","title":"Set 5 lavete","description":"Lavete microfibră pentru curățenie."},
 {"id":"3","title":"Scaun ergonomic","description":""},
]
def load(path):
    if path.endswith(".json"): return json.load(open(path,encoding="utf-8"))
    rows=[]
    with open(path,encoding="utf-8-sig") as f:
        for r in csv.DictReader(f): rows.append({"id":r.get("id",""),"title":r.get("title") or r.get("Title",""),"description":r.get("description") or r.get("Description",""),"type":r.get("type","")})
    return rows
prods = SAMPLE if a.sample else load(a.input)
if not prods: print("(niciun produs)"); sys.exit(0)
OPENAI=kb("OPENAI_API_KEY")
SYS=(f"Ești specialist Google Shopping feed pt „{a.brand}\". Pt fiecare produs generează atribute care cresc "
 f"acoperirea pe căutări long-tail. Titlul optimizat: atribut-cheie + brand/model + specificații în FAȚĂ, natural, "
 f"în limba '{a.lang}', ≤150 caractere, fără MAJUSCULE excesive/„!!!\". Ghicește culoare/material DOAR dacă rezultă "
 f"clar din text (altfel null). google_product_category = calea Google taxonomy plauzibilă. "
 "Răspunde DOAR JSON: {\"produse\":[{\"id\",\"title_optimized\",\"google_product_category\",\"product_type\","
 "\"color\",\"material\",\"gender\",\"description_enriched\",\"missing_before\":[\"...atributele care lipseau\"]}]}")
def ask(batch):
    body={"model":a.model,"messages":[{"role":"system","content":SYS},{"role":"user","content":json.dumps(batch,ensure_ascii=False)}],
          "response_format":{"type":"json_object"},"temperature":0.3}
    for attempt in range(6):
        try:
            r=requests.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization":f"Bearer {OPENAI}","Content-Type":"application/json"},json=body,timeout=120)
            if r.status_code==200: return json.loads(r.json()["choices"][0]["message"]["content"]).get("produse",[])
            if r.status_code in (429,500,502,503): time.sleep(3*(attempt+1)); continue
            print(f"  ⚠️ OpenAI {r.status_code}: {r.text[:120]}"); return []
        except Exception as e: time.sleep(3*(attempt+1))
    print("  ⚠️ OpenAI indisponibil după 6 încercări (server 5xx) — reîncearcă mai târziu"); return []
res=[]
for i in range(0,len(prods),20): res+=ask(prods[i:i+20])
print(f"═══ FEED ATTR FILLER — {len(prods)} produse → {len(res)} îmbogățite ═══\n")
for p in res:
    print(f"● [{p.get('id')}] {p.get('title_optimized','')}")
    meta=[f"cat={p.get('google_product_category')}",f"type={p.get('product_type')}"]
    for k in ("color","material","gender"):
        if p.get(k): meta.append(f"{k}={p[k]}")
    print("   "+" · ".join(str(m) for m in meta))
    if p.get("missing_before"): print(f"   completat: {', '.join(p['missing_before'])}")
    if p.get("description_enriched"): print(f"   desc: {p['description_enriched'][:120]}")
    print()
if a.out:
    json.dump(res,open(a.out,"w",encoding="utf-8"),ensure_ascii=False,indent=1); print(f"→ scris în {a.out}")
print("→ Scrie înapoi: editează produsul în Shopify (title/product_type/metafields) via `gigi:shopify-stores`, sau urcă în Merchant Center.")
