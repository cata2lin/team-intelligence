# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31","markitdown[pdf]>=0.0.1a2"]
# ///
"""AUDIT facturi SmartBill: TOTALUL FACTURAT != cât a PLĂTIT clientul (tipic: transport nefacturat).

METODA (capcanele din SKILL.md sunt reale — m-au păcălit de 2 ori):
  1. PDF → Markdown cu markitdown  → PĂSTREAZĂ TABELUL de linii (pypdf îl turtește și pierde linia!)
  2. legătura factură↔comandă = câmpul `Order: <nume>` DE PE FACTURĂ (nu potriviri pe nume/sume!)
  3. verdict pe UN SINGUR NUMĂR: `TOTAL PLATA` vs `totalPrice` (Shopify)
     NU căuta cuvântul „transport" — la GT linia se cheamă „Livrare prin DPD"
  4. doar comenzile cu transport TAXAT (>0) — la livrare gratuită lipsa liniei e CORECTĂ

⚠️ RATE-LIMIT: SmartBill blochează după câteva zeci de PDF-uri, iar
   `xconnector.app/download/invoice` PROXY-EAZĂ SmartBill (NU e o portiță!).
   → EȘANTIONEAZĂ (--limit). Tiparul e determinist PER MAGAZIN → expunerea totală
   se calculează din comenzile Shopify (gratis), NU citind mii de facturi.

Usage:  uv run audit_invoices.py --days 7 [--limit 400] [--store GT]
"""
import sys, io, re, csv, json, time, datetime, subprocess, importlib.util, collections
import requests
from markitdown import MarkItDown

DAYS  = int(sys.argv[sys.argv.index("--days")+1])  if "--days"  in sys.argv else 7
LIMIT = int(sys.argv[sys.argv.index("--limit")+1]) if "--limit" in sys.argv else 400
ONLY  = sys.argv[sys.argv.index("--store")+1].upper() if "--store" in sys.argv else None

XP = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/gigi/skills/xconnector/xconnector.py"
_spec = importlib.util.spec_from_file_location("xcmod", XP)
xcm = importlib.util.module_from_spec(_spec)
_argv = sys.argv; sys.argv = ["x"]; _spec.loader.exec_module(xcm); sys.argv = _argv

KB = "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
def sec(k):
    return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()

rows = list(csv.reader(sec("SHOPIFY_STORES_CSV").splitlines()))
STORES = {r[1]: (r[0].strip().upper(), r[2]) for r in rows[1:] if r and len(r) > 2}
to = datetime.date.today(); fr = to - datetime.timedelta(days=DAYS)
md = MarkItDown()

# ── 1. cât a PLĂTIT clientul (Shopify — gratis, fără rate-limit) ──────────────
Q = '''query($c:String,$q:String!){ orders(first:250, after:$c, query:$q){ pageInfo{hasNextPage endCursor}
 edges{ node{ name totalPriceSet{shopMoney{amount}} subtotalPriceSet{shopMoney{amount}}
   totalShippingPriceSet{shopMoney{amount}} } } } }'''
paid = {}
for dom, (pfx, tok) in STORES.items():
    if ONLY and pfx != ONLY:
        continue
    cur = None
    while True:
        try:
            d = requests.post(f"https://{dom}/admin/api/2026-01/graphql.json",
                              headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"},
                              json={"query": Q, "variables": {"c": cur, "q": f"created_at:>={fr}"}},
                              timeout=60).json().get("data", {}).get("orders", {})
        except Exception:
            break
        for e in d.get("edges", []):
            n = e["node"]
            paid[n["name"]] = (pfx,
                               float(n["totalPriceSet"]["shopMoney"]["amount"]),
                               float(n["subtotalPriceSet"]["shopMoney"]["amount"]),
                               float(n["totalShippingPriceSet"]["shopMoney"]["amount"]))
        if d.get("pageInfo", {}).get("hasNextPage"):
            cur = d["pageInfo"]["endCursor"]
        else:
            break
print(f"  comenzi Shopify ({fr}→{to}): {len(paid)}")

# ── 2. comanda → factura (xConnector documents) ───────────────────────────────
inv = {}
for s in xcm.load_shops():
    try:
        orders = xcm.XC(s["apiKey"]).orders(fr.isoformat(), to.isoformat())
    except Exception:
        continue
    for o in orders:
        doc = xcm.inv_doc(o); nm = o.get("orderName")
        if doc and nm and nm in paid and paid[nm][3] > 0:          # DOAR cu transport taxat
            if not ONLY or paid[nm][0] == ONLY:
                inv[nm] = doc["url"]
targets = list(inv.items())[:LIMIT]
print(f"  facturi cu transport taxat: {len(inv)} → eșantion verificat: {len(targets)}")
if len(inv) > LIMIT:
    print("  ⚠️ EȘANTION (rate-limit!). Tiparul e determinist per magazin → expunerea TOTALĂ")
    print("     se calculează din Shopify, NU citind toate facturile.")

# ── 3. citește facturile (markitdown păstrează tabelul) ──────────────────────
def read_invoice(url):
    for i in range(3):
        try:
            r = requests.get(url, timeout=50)
            if r.status_code == 200 and r.content[:4] == b"%PDF":
                txt = md.convert_stream(io.BytesIO(r.content), file_extension=".pdf").text_content
                flat = re.sub(r"\s+", " ", txt)
                if "A N U L A T A" in flat:
                    return None
                m = re.search(r"TOTAL PLATA\s*\|?\s*(-?[\d.,]+)", flat)
                if not m:
                    return None
                tot = float(m.group(1).replace(",", ""))
                if tot < 0:
                    return None                                       # storno
                o = re.search(r"Order:\s*([A-Z]+\d+)", flat)          # comanda E PE FACTURĂ
                return tot, (o.group(1) if o else None)
        except Exception:
            pass
        time.sleep(3 * (i + 1))
    return None

res = []
for i, (nm, url) in enumerate(targets):
    got = read_invoice(url)
    if not got:
        continue
    itot, ordref = got
    if ordref and ordref != nm:        # factura declară ALTĂ comandă → n-o folosi
        continue
    pfx, tot, sub, shp = paid[nm]
    res.append((nm, pfx, itot, tot, sub, shp))
    time.sleep(0.8)                    # blând (xConnector proxy-ează SmartBill)
    if i and i % 50 == 0:
        print(f"    …{i}/{len(targets)}", flush=True)

# ── 4. verdict ───────────────────────────────────────────────────────────────
ok  = [r for r in res if abs(r[3] - r[2]) <= 0.05]                            # factura = plătit
bad = [r for r in res if abs(r[3] - r[2]) > 0.05 and abs(r[4] - r[2]) < 1.0]  # factura = doar produsele
oth = [r for r in res if r not in ok and r not in bad]
print(f"\n  ══ {len(res)} facturi verificate (toate cu transport taxat) ══")
print(f"  ✅ corecte (factura = plătit):          {len(ok)}")
print(f"  🔴 FĂRĂ TRANSPORT (factura = produse):  {len(bad)}")
print(f"  ❔ alte diferențe:                      {len(oth)}")
if bad:
    per = collections.defaultdict(lambda: [0, 0.0])
    for r in bad:
        per[r[1]][0] += 1; per[r[1]][1] += r[5]
    print(f"\n  💰 în eșantion: {sum(r[5] for r in bad):,.2f} lei transport nefacturat")
    for p, (c, s) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        tp = len([r for r in res if r[1] == p])
        print(f"    {p:6} {c:4}/{tp:<4} rupte ({c/tp*100:5.1f}%) · {s:>10,.2f} lei")
    print("\n  ⇒ magazin cu ~100% rupte = CONFIG connector SmartBill în xConnector, NU cronul nostru.")
    print("  ⇒ expunere TOTALĂ = suma `totalShipping` pe comenzile PAID ale acelor magazine (Shopify).")
    for r in bad[:10]:
        print(f"    {r[1]:5} {r[0]:11} plătit={r[3]:8.2f} produse={r[4]:8.2f} transport={r[5]:6.2f} → FACTURA={r[2]:8.2f}")
json.dump([{"order": r[0], "store": r[1], "invoice_total": r[2], "paid": r[3],
            "subtotal": r[4], "shipping": r[5]} for r in bad], open("audit_missing.json", "w"), indent=1)
print("\n  lista completă → audit_missing.json")
