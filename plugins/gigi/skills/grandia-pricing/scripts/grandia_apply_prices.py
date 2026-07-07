# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31"]
# ///
"""
Apply the Grandia repricing plan to Shopify (store GRAN) — SAFELY.

Reads the reprice TSV (grandia_reprice*.py --tsv), selects the CLEAR wins, and
for each SKU: resolves the live variant, VERIFIES the live price still matches
the analysed "Preț acum" (skips if it drifted), then sets the new price via
productVariantsBulkUpdate. Reads userErrors. Dry-run by default; --apply writes.

Selection:
  RAISE apply = Acțiune CREȘTE & marjă/CPA nouă ≥ 0 & has market price & NOT "verifică piața"/"CPA-problemă"
  LOWER apply = Acțiune SCADE & has market price & we're above it (clear overprice) & stock > 1
  everything else → HELD (printed, not applied)

Token: pulled from KB secret SHOPIFY_STORES_CSV in-process, never printed.

Usage:
  uv run grandia_apply_prices.py --tsv plan.tsv            # dry-run (preview + verify)
  uv run grandia_apply_prices.py --tsv plan.tsv --apply    # write
"""
import csv, json, os, subprocess, sys, time
import requests

TSV = sys.argv[sys.argv.index("--tsv")+1] if "--tsv" in sys.argv else None
APPLY = "--apply" in sys.argv
PRICE_TOL = 0.02   # live price must be within 2% of analysed "Preț acum" else skip
PREFIX = "GRAN"
API = "2026-01"

from pathlib import Path as _Path
_here = _Path(__file__).resolve()
for _up in range(2, 8):
    _c = _here.parents[_up] / "core" / "scripts"
    if (_c / "arona_pg.py").exists():
        sys.path.insert(0, str(_c)); break
try:
    import arona_pg as _apg
    def kb_secret(key): return _apg.secret(key)
except Exception:
    def kb_secret(key):
        v = os.environ.get(key)
        if v: return v
        for cand in ("kb.py", str(_here.parents[4] / "core" / "scripts" / "kb.py")):
            try:
                out = subprocess.run(["uv","run",cand,"secret-get",key], capture_output=True, text=True)
                if out.returncode == 0 and out.stdout.strip(): return out.stdout.strip()
            except Exception: pass
        return ""

# resolve GRAN shop+token (never print token)
shop = token = None
for row in csv.reader(kb_secret("SHOPIFY_STORES_CSV").splitlines()):
    if row and row[0] == PREFIX:
        shop, token = row[1], row[2]
if not token:
    print("no token for GRAN"); sys.exit(1)
URL = f"https://{shop}/admin/api/{API}/graphql.json"
HDR = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

def gql(q, v=None):
    for attempt in range(5):
        r = requests.post(URL, headers=HDR, json={"query": q, "variables": v or {}}, timeout=30)
        if r.status_code == 200:
            d = r.json()
            if d.get("errors") and any("throttled" in str(e).lower() for e in d["errors"]):
                time.sleep(2*(attempt+1)); continue
            return d
        time.sleep(2*(attempt+1))
    r.raise_for_status()

VAR_BY_SKU = """query($q:String!){ productVariants(first:5, query:$q){ nodes{
  id price product{ id title } inventoryItem{ sku } } } }"""
MUT = """mutation($productId:ID!,$variants:[ProductVariantsBulkInput!]!){
  productVariantsBulkUpdate(productId:$productId, variants:$variants){
    productVariants{ id price } userErrors{ field message } } }"""

# ---- load plan + select ----
rows = list(csv.DictReader(open(TSV, encoding="utf-8"), delimiter="\t"))
def num(x):
    try: return float(str(x).replace(",", ".")) if str(x).strip() not in ("","—") else None
    except: return None

apply_rows, held = [], []
for r in rows:
    act, flag = r["Acțiune"], r.get("Flag","")
    now, new, mkt = num(r["Preț acum"]), num(r["Preț nou"]), num(r["Preț piață"])
    newmarg = num(r["Marjă/CPA nouă %"]); stock = num(r["Stoc"]) or 0
    ok = False; reason = ""
    if act == "CREȘTE":
        if newmarg is not None and newmarg >= 0 and mkt and "verific" not in flag.lower() and "cpa" not in flag.lower():
            ok = True
        else:
            reason = "creștere fără piață (verifică)" if not mkt else ("CPA-problemă" if newmarg is not None and newmarg<0 else flag)
    elif act == "SCADE":
        if mkt and now and now > mkt and stock > 1:
            ok = True
        else:
            reason = "scădere fără ancoră piață / stoc mic (probă cerere)"
    (apply_rows if ok else held).append((r, reason))

print(f"{'APPLY' if APPLY else 'DRY'} — store {shop}  |  {len(apply_rows)} de aplicat, {len(held)} ținute pt review\n"+"="*96)

done = fail = skip = 0
for r, _ in apply_rows:
    skus = [s.strip() for s in r["SKU"].split(",") if s.strip()]
    now, new = num(r["Preț acum"]), num(r["Preț nou"])
    for sku in skus:
        d = gql(VAR_BY_SKU, {"q": f"sku:{sku}"})
        nodes = (((d or {}).get("data") or {}).get("productVariants") or {}).get("nodes") or []
        node = next((n for n in nodes if (n.get("inventoryItem") or {}).get("sku") == sku), nodes[0] if nodes else None)
        if not node:
            print(f"  ✗ {sku:16} negăsit în Shopify"); skip += 1; continue
        live = float(node["price"])
        if now and abs(live/now - 1) > PRICE_TOL:
            print(f"  ⏭ {sku:16} preț live {live:.0f} ≠ analiză {now:.0f} — SAR (s-a schimbat)"); skip += 1; continue
        tag = "CREȘTE" if r["Acțiune"]=="CREȘTE" else "SCADE"
        if not APPLY:
            print(f"  • {tag:6} {sku:16} {live:.0f} → {new:.0f}   {r['Produs'][:34]}"); continue
        m = gql(MUT, {"productId": node["product"]["id"],
                      "variants": [{"id": node["id"], "price": f"{new:.2f}"}]})
        errs = (((m or {}).get("data") or {}).get("productVariantsBulkUpdate") or {}).get("userErrors") or []
        if errs:
            print(f"  ✗ {sku:16} userError: {errs}"); fail += 1
        else:
            print(f"  ✓ {tag:6} {sku:16} {live:.0f} → {new:.0f}   {r['Produs'][:34]}"); done += 1

print("="*96)
if APPLY:
    print(f"APLICAT: {done} prețuri schimbate, {skip} sărite (drift/negăsit), {fail} erori.")
else:
    print(f"[DRY] {sum(len([s for s in r['SKU'].split(',') if s.strip()]) for r,_ in apply_rows)} variante de scris. Rulează cu --apply.")
print("\n── ȚINUTE pt review (nu se aplică automat) ──")
for r, reason in held:
    print(f"  {r['Acțiune']:6} {r['SKU']:18} {r['Preț acum']}→{r['Preț nou']}  [{reason}]  {r['Produs'][:34]}")
