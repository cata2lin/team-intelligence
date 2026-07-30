# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client","google-auth"]
# ///
"""
cogs_audit.py — auditează și repară COGS-ul (costPerItem) din Shopify față de
formula CANONICĂ ARONA din sheet-ul „Stoc ARONA" → tab „COGS 2026".

    COGS_RON = (marfă$ + shipping$) x 4.46 x 1.10 (vamă) x 1.21 (TVA)
             = USD_landed x 5.93626

Clase de bug detectate automat:
  MISSING_VAT_DUTY  cost == USD_landed x 4.46  -> cineva s-a oprit la coloana „COGS lei"
                    (a uitat vama 10% + TVA 21%). Cel mai frecvent și cel mai păgubos.
  DIVERGENT         același SKU are costuri diferite între magazine (costul e RON peste tot,
                    deci ORICE diferență e bug).
  MISMATCH          diferă de formulă fără tipar cunoscut (posibil placeholder rotund).
  NO_COST           produs fără cost setat.

⚠️ Costul Shopify e în RON pe TOATE magazinele, inclusiv CZ/BG/PL. Deci:
   - valori identice între magazine = NORMAL, nu placeholder;
   - NU compara costul cu prețul în valuta magazinului (20 lei vs 11.99 EUR nu e anomalie).
   Singurul test valid = „derivă din formulă?".

Exemple:
    uv run cogs_audit.py audit
    uv run cogs_audit.py audit --sku asternut --store RED,OFER
    uv run cogs_audit.py audit --only-bugs
    uv run cogs_audit.py fix --sku oglinda            # dry-run
    uv run cogs_audit.py fix --sku oglinda --apply
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows/depozit

FX, DUTY, VAT = 4.46, 1.10, 1.21
MULT = FX * DUTY * VAT  # 5.93626

SHEET_ID = "1Pke-2fMv8MnHyt9hFAwPNRtZHmZIWLMPSsqr3JzYaE0"   # „Stoc ARONA"
SHEET_TAB = "COGS 2026"
API_VERSION = "2024-01"


def repo_root() -> str:
    for p in (os.environ.get("SCRIPTURI_DIR"),
              os.path.expanduser("~/Downloads/Scripturi"),
              "/root/Scripturi"):
        if p and os.path.isdir(p):
            return p
    sys.exit("Nu găsesc folderul Scripturi (setează SCRIPTURI_DIR).")


def creds_path() -> str:
    p = os.path.join(repo_root(), "google_credentials.json")
    if not os.path.exists(p):
        sys.exit(f"Lipsă {p} (service account looker-sheets).")
    return p


# ─── sursa canonică ──────────────────────────────────────────
def load_canonical() -> dict:
    """SKU (lower) -> {marfa, ship, usd, cogs_ron}"""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    cr = Credentials.from_service_account_file(
        creds_path(), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    svc = build("sheets", "v4", credentials=cr, cache_discovery=False).spreadsheets()
    vals = svc.values().get(spreadsheetId=SHEET_ID, range=f"'{SHEET_TAB}'!A1:R2000",
                            valueRenderOption="UNFORMATTED_VALUE").execute().get("values", [])
    if not vals:
        sys.exit(f"Tabul '{SHEET_TAB}' e gol.")
    hdr = [str(c).strip().lower() for c in vals[0]]

    def idx(*names):
        for n in names:
            if n.lower() in hdr:
                return hdr.index(n.lower())
        return None

    c_sku, c_marfa, c_ship = idx("sku"), idx("cogs"), idx("shipping")
    c_total = idx("total cu tva", "total cu TVA")
    out = {}
    for r in vals[1:]:
        if c_sku is None or len(r) <= c_sku:
            continue
        sku = str(r[c_sku]).strip()
        if not sku:
            continue
        num = lambda i: (r[i] if (i is not None and len(r) > i and isinstance(r[i], (int, float))) else None)
        marfa, ship, total = num(c_marfa), num(c_ship), num(c_total)
        if marfa is None:
            continue
        usd = marfa + (ship or 0)
        out[sku.lower()] = {"marfa": marfa, "ship": ship or 0, "usd": round(usd, 4),
                            "cogs_ron": round(total if total is not None else usd * MULT, 2)}
    return out


# ─── Shopify ─────────────────────────────────────────────────
def stores(filter_prefixes=None):
    path = os.path.join(repo_root(), "stores.csv")
    out = []
    for r in csv.DictReader(open(path)):
        if filter_prefixes and r["prefix"] not in filter_prefixes:
            continue
        out.append((r["prefix"], r["shop"], r["token"]))
    return out


def _get(shop, token, endpoint, params=None, timeout=30):
    url = f"https://{shop}/admin/api/{API_VERSION}/{endpoint}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("X-Shopify-Access-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode()), resp.headers.get("Link", "")


def fetch_variants(shop, token):
    """[(sku, inventory_item_id, product_title, price)] — REST (GraphQL e rupt pe 2024-01)."""
    out, url = [], f"https://{shop}/admin/api/{API_VERSION}/products.json?limit=250&fields=id,title,variants"
    page = 0
    while url and page < 60:
        req = urllib.request.Request(url)
        req.add_header("X-Shopify-Access-Token", token)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            link = resp.headers.get("Link", "")
        prods = data.get("products", [])
        if not prods:
            break
        for p in prods:
            for v in p.get("variants", []):
                sku = (v.get("sku") or "").strip()
                if sku:
                    out.append((sku, v.get("inventory_item_id"), p.get("title", ""), v.get("price")))
        page += 1
        import re
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = m.group(1) if m else None
    return out


def fetch_costs(shop, token, ids):
    costs = {}
    ids = [str(i) for i in ids if i]
    for i in range(0, len(ids), 100):
        try:
            d, _ = _get(shop, token, "inventory_items.json",
                        {"ids": ",".join(ids[i:i + 100]), "limit": 100})
            for it in d.get("inventory_items", []):
                costs[it["id"]] = it.get("cost")
        except Exception as e:
            print(f"    [cost] {str(e)[:60]}", file=sys.stderr)
        time.sleep(0.3)
    return costs


def put_cost(shop, token, inv_item_id, cost):
    url = f"https://{shop}/admin/api/{API_VERSION}/inventory_items/{inv_item_id}.json"
    body = json.dumps({"inventory_item": {"id": inv_item_id, "cost": f"{cost:.2f}"}}).encode()
    req = urllib.request.Request(url, data=body, method="PUT")
    req.add_header("X-Shopify-Access-Token", token)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


# ─── audit ───────────────────────────────────────────────────
def classify(cur, canon):
    """-> (clasa, cost_corect|None)"""
    if cur is None:
        return "NO_COST", canon["cogs_ron"] if canon else None
    if not canon:
        return "NO_REF", None
    good = canon["cogs_ron"]
    if abs(cur - good) < 0.02:
        return "OK", good
    # bug-ul clasic: s-au oprit la coloana „COGS lei" (fără vamă + TVA)
    if abs(cur - canon["usd"] * FX) < 0.02:
        return "MISSING_VAT_DUTY", good
    # fără TVA (dar cu vamă)
    if abs(cur - canon["usd"] * FX * DUTY) < 0.02:
        return "MISSING_VAT", good
    return "MISMATCH", good


def cmd_audit(a):
    canon = load_canonical()
    print(f"Referință canonică: {len(canon)} SKU din „{SHEET_TAB}\" (mult = {MULT:.5f})\n")
    rows, by_sku = [], defaultdict(set)
    for pfx, shop, tok in stores(a.store):
        try:
            variants = fetch_variants(shop, tok)
        except Exception as e:
            print(f"  {pfx}: skip ({str(e)[:60]})")
            continue
        if a.sku:
            variants = [v for v in variants if any(s.lower() in v[0].lower() for s in a.sku)]
        if not variants:
            continue
        costs = fetch_costs(shop, tok, [v[1] for v in variants])
        for sku, iid, title, price in variants:
            cur = costs.get(iid)
            try:
                curf = float(cur) if cur not in (None, "") else None
            except ValueError:
                curf = None
            c = canon.get(sku.lower())
            cls, good = classify(curf, c)
            rows.append({"store": pfx, "sku": sku, "cur": curf, "good": good,
                         "cls": cls, "iid": iid, "title": title, "price": price,
                         "usd": c["usd"] if c else None})
            if curf is not None:
                by_sku[sku.lower()].add(round(curf, 2))
        print(f"  {pfx}: {len(variants)} variante")
        time.sleep(0.2)

    # divergență între magazine (costul e RON peste tot -> orice diferență e bug)
    for r in rows:
        if r["cls"] in ("OK", "NO_REF") and len(by_sku.get(r["sku"].lower(), ())) > 1:
            r["cls"] = "DIVERGENT" if r["cls"] == "NO_REF" else r["cls"]

    order = {"MISSING_VAT_DUTY": 0, "MISSING_VAT": 1, "MISMATCH": 2,
             "DIVERGENT": 3, "NO_COST": 4, "NO_REF": 5, "OK": 6}
    rows.sort(key=lambda r: (order.get(r["cls"], 9), r["sku"], r["store"]))

    shown = [r for r in rows if not a.only_bugs or r["cls"] not in ("OK", "NO_REF")]
    print(f"\n{'CLASĂ':17} {'SKU':28} {'mag':6} {'actual':>8} {'corect':>8} {'Δ':>8}")
    print("-" * 88)
    for r in shown[:a.limit]:
        cur_s = "" if r["cur"] is None else f"{r['cur']:.2f}"
        good_s = "" if r["good"] is None else f"{r['good']:.2f}"
        d = "" if (r["cur"] is None or r["good"] is None) else f"{r['good'] - r['cur']:+.2f}"
        print(f"{r['cls']:17} {r['sku'][:28]:28} {r['store']:6} "
              f"{cur_s:>8} {good_s:>8} {d:>8}")
    if len(shown) > a.limit:
        print(f"... (+{len(shown)-a.limit}; --limit ca să vezi tot)")

    counts = defaultdict(int)
    for r in rows:
        counts[r["cls"]] += 1
    print("\n=== SUMAR ===")
    for k in sorted(counts, key=lambda k: order.get(k, 9)):
        print(f"  {k:17} {counts[k]}")
    bugs = sum(v for k, v in counts.items() if k in ("MISSING_VAT_DUTY", "MISSING_VAT", "MISMATCH", "DIVERGENT"))
    if bugs:
        print(f"\n⚠️  {bugs} de reparat -> `cogs_audit.py fix [--sku …] --apply`")
    if a.json:
        json.dump(rows, open(a.json, "w"), ensure_ascii=False, default=str)
        print(f"salvat: {a.json}")


def cmd_fix(a):
    canon = load_canonical()
    todo = []
    for pfx, shop, tok in stores(a.store):
        try:
            variants = fetch_variants(shop, tok)
        except Exception as e:
            print(f"  {pfx}: skip ({str(e)[:60]})")
            continue
        if a.sku:
            variants = [v for v in variants if any(s.lower() in v[0].lower() for s in a.sku)]
        if not variants:
            continue
        costs = fetch_costs(shop, tok, [v[1] for v in variants])
        for sku, iid, title, price in variants:
            c = canon.get(sku.lower())
            if not c:
                continue
            cur = costs.get(iid)
            try:
                curf = float(cur) if cur not in (None, "") else None
            except ValueError:
                curf = None
            cls, good = classify(curf, c)
            if cls in ("OK", "NO_REF"):
                continue
            if cls == "MISMATCH" and not a.force:
                print(f"  ~ SAR {sku} @ {pfx}: {curf} vs {good} (MISMATCH — pune --force dacă vrei)")
                continue
            todo.append((pfx, shop, tok, sku, iid, curf, good, cls))
        time.sleep(0.2)

    print(f"\nDe corectat: {len(todo)}")
    for pfx, _, _, sku, _, cur, good, cls in todo:
        print(f"   [{cls}] {sku} @ {pfx}: {cur} -> {good:.2f}")
    if not todo:
        return
    if not a.apply:
        print("\n(DRY-RUN — adaugă --apply)")
        return
    ok = 0
    for pfx, shop, tok, sku, iid, cur, good, cls in todo:
        try:
            res = put_cost(shop, tok, iid, good)
            print(f"  ✅ {sku} @ {pfx}: {cur} -> {(res.get('inventory_item') or {}).get('cost')}")
            ok += 1
        except Exception as e:
            print(f"  ❌ {sku} @ {pfx}: {str(e)[:90]}")
        time.sleep(0.5)
    print(f"\nActualizate: {ok}/{len(todo)}")


def main():
    ap = argparse.ArgumentParser(description="Audit/reparare COGS Shopify vs formula canonică ARONA.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("audit", "fix"):
        p = sub.add_parser(name)
        p.add_argument("--sku", action="append", help="filtru substring pe SKU (repetabil)")
        p.add_argument("--store", type=lambda s: s.split(","), help="prefixe magazine, ex RED,OFER")
        if name == "audit":
            p.add_argument("--only-bugs", action="store_true")
            p.add_argument("--limit", type=int, default=60)
            p.add_argument("--json", help="salvează rezultatul brut")
        else:
            p.add_argument("--apply", action="store_true", help="chiar scrie în Shopify")
            p.add_argument("--force", action="store_true", help="repară și MISMATCH (nederivat)")
    a = ap.parse_args()
    (cmd_audit if a.cmd == "audit" else cmd_fix)(a)


if __name__ == "__main__":
    main()
