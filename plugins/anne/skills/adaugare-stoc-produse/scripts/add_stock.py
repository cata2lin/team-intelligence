# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0", "requests>=2.0"]
# ///
"""
add_stock.py — adauga stoc marfa noua (container) pe magazinele "deals" Arona.

REGULA DE AUR (invatata pe teren): stocul se pune DOAR pe MAGDEAL (master).
Un app de sync pe BARCODE copiaza stocul de pe Magdeal pe casa/reduceri/oferte.
NU scrie stoc pe toate magazinele deodata -> sync-ul e reactiv/bidirectional si
amplifica scrierile -> cifrele drifteaza (dovedit iul-2026: setand toate 4, un SKU
a sarit de la 500 la 3030). Barcode + tracking + policy + tag = OK pe toate.

Flux:
  plan   --container C42            # citeste tabul, barcode map, audit, DRY-RUN (nu scrie)
  apply  --container C42 --apply    # barcode+tracking+DENY+scot test pe toate; STOC doar pe MAGDEAL
  dupes  --container C42 [--apply]  # dublurile (fara barcode + stoc 0) -> DRAFT
  verify --container C42            # verificare finala completa
  green  --container C42 --apply    # marcheaza verde randurile rezolvate in tab

Auth Google: OAuth Desktop ~/.config/gcp/sheets-token.json (vezi core:export-to-google-sheet).
Shopify: tokenii din SHOPIFY_STORES_CSV (env=path/text) sau KB secret (vezi gigi:shopify-stores).
Windows: fortam UTF-8 la output.
"""
import sys, os, csv, io, json, time, argparse, subprocess, urllib.request, urllib.error
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

# ------------------------- CONFIG -------------------------
API_VERSION = "2026-01"
MASTER = "MAG"                          # Magdeal = magazinul master (aici pui stocul)
SATELLITES = ["BON", "RED", "OFER"]     # casaofertelor / reduceribune / ofertelezilei (copiaza prin sync)
STORE_DOMAIN = {"MAG": "magdeal.ro", "BON": "casaofertelor.ro",
                "RED": "reduceribune.ro", "OFER": "ofertelezilei.ro"}

# Spreadsheet cu containerele (un tab per container: C40, C41, ' C42 08 Iulie', ...)
CONTAINER_SID = "1PjlFq31Es39jW6wZqpE5yuAnW0gO72M_7ElLPz7OitU"
# Spreadsheet-ul TOM cu barcode-uri (col Sku -> col Barcode)
BARCODE_SID = "10eSCKItlCHMl8S5A2YGjBZBZwRe506HH0ETpgR7BV7A"
BARCODE_TABS = ["✅ TOM - WINNER_WORK", "✅ TOM - TO BE VERIFIED_WORK",
                "SHOPIFY_VARIANTS_CASA"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TOKEN_FILE = Path.home() / ".config" / "gcp" / "sheets-token.json"

# ------------------------- SHEETS -------------------------
def sheets():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
    return build("sheets", "v4", credentials=creds).spreadsheets()

def find_tab(svc, sid, needle):
    """Return (title, gid) of the first tab whose title contains needle (case/space-insensitive)."""
    meta = svc.get(spreadsheetId=sid, fields="sheets.properties").execute()
    nl = needle.strip().lower()
    for s in meta["sheets"]:
        p = s["properties"]
        if nl in p["title"].strip().lower():
            return p["title"], p["sheetId"]
    raise SystemExit(f"Tab care contine {needle!r} nu a fost gasit in {sid}")

def _is_white(bg):
    # Google omite canalele = 0, deci defaulteaza la 0 (NU la 1): galben {red:1,green:1}
    # are blue=0, verde {green:1} are red=blue=0. Doar {1,1,1} sau lipsa = alb.
    if not bg:
        return True
    r, g, b = bg.get("red", 0), bg.get("green", 0), bg.get("blue", 0)
    return r > 0.95 and g > 0.95 and b > 0.95

def read_container(svc, tab):
    """Return (gid, header_row_idx, sku_col, qty_col, [ (row_idx, sku, qty, is_white) ]).
    Randurile ALBE = de facut. Verde/galben (puse de user) = ignorate."""
    title, gid = tab
    data = svc.get(spreadsheetId=CONTAINER_SID, ranges=[f"'{title}'"],
        fields="sheets.data.rowData.values(formattedValue,effectiveFormat.backgroundColor)").execute()
    rows = data["sheets"][0]["data"][0].get("rowData", [])
    # find header row (contine "SKU" si "Cantitate")
    hr = sku_c = qty_c = None
    for i, row in enumerate(rows):
        vals = [(c.get("formattedValue") or "").strip().lower() for c in row.get("values", [])]
        if "sku" in vals and any(v in ("cantitate", "quantity") for v in vals):
            hr = i
            sku_c = vals.index("sku")
            qty_c = next(j for j, v in enumerate(vals) if v in ("cantitate", "quantity"))
            break
    if hr is None:
        raise SystemExit("Nu am gasit randul de header (SKU + Cantitate) in tab.")
    out = []
    for i in range(hr + 1, len(rows)):
        cells = rows[i].get("values", [])
        if len(cells) <= max(sku_c, qty_c):
            continue
        sku = (cells[sku_c].get("formattedValue") or "").strip()
        qty = (cells[qty_c].get("formattedValue") or "").strip()
        if not sku or not qty:
            continue
        try:
            q = int(float(qty.replace(",", "")))
        except ValueError:
            continue
        white = _is_white(cells[sku_c].get("effectiveFormat", {}).get("backgroundColor"))
        out.append((i + 1, sku, q, white))  # row_idx is 1-based
    return gid, hr, sku_c, qty_c, out

def build_barcode_map(svc, skus):
    """Return (map sku->barcode, missing[], conflicts{})."""
    want = set(skus)
    found = {}  # sku -> {barcode -> set(tabs)}
    for tab in BARCODE_TABS:
        try:
            res = svc.values().get(spreadsheetId=BARCODE_SID, range=f"'{tab}'").execute()
        except Exception:
            continue
        rows = res.get("values", [])
        if not rows:
            continue
        hdr = [(h or "").strip().lower() for h in rows[0]]
        try:
            ci_sku = hdr.index("sku")
        except ValueError:
            continue
        ci_bc = next((j for j, h in enumerate(hdr) if h in ("barcode", "cod bare", "ean")), None)
        if ci_bc is None:
            continue
        for r in rows[1:]:
            if len(r) <= max(ci_sku, ci_bc):
                continue
            sku = (r[ci_sku] or "").strip()
            bc = (r[ci_bc] or "").strip()
            if sku in want and bc:
                found.setdefault(sku, {}).setdefault(bc, set()).add(tab)
    bmap, missing, conflicts = {}, [], {}
    for sku in skus:
        e = found.get(sku)
        if not e:
            missing.append(sku)
        elif len(e) > 1:
            conflicts[sku] = {b: sorted(t) for b, t in e.items()}
        else:
            bmap[sku] = next(iter(e))
    return bmap, missing, conflicts

def color_rows_green(svc, gid, row_idxs, sku_c):
    if not row_idxs:
        return
    # verde standard (aceeasi nuanta ca randurile deja verzi din sheet)
    green = {"red": 0, "green": 1, "blue": 0}
    reqs = [{"repeatCell": {
        "range": {"sheetId": gid, "startRowIndex": r - 1, "endRowIndex": r,
                  "startColumnIndex": max(0, sku_c - 2), "endColumnIndex": sku_c + 2},
        "cell": {"userEnteredFormat": {"backgroundColor": green}},
        "fields": "userEnteredFormat.backgroundColor"}} for r in row_idxs]
    svc.batchUpdate(spreadsheetId=CONTAINER_SID, body={"requests": reqs}).execute()

# ------------------------- SHOPIFY -------------------------
def _csv_text():
    env = os.getenv("SHOPIFY_STORES_CSV")
    if env:
        return env if "\n" in env else open(env, encoding="utf-8-sig").read()
    kb = os.getenv("KB_PY")
    if not kb:
        d = os.getcwd()
        for _ in range(8):
            c = os.path.join(d, "team-intelligence", "plugins", "core", "scripts", "kb.py")
            if os.path.exists(c):
                kb = c; break
            d = os.path.dirname(d)
    if kb and os.path.exists(kb):
        out = subprocess.run(["uv", "run", kb, "secret-get", "SHOPIFY_STORES_CSV"],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    raise SystemExit("Nu pot rezolva SHOPIFY_STORES_CSV (env sau KB). Vezi gigi:shopify-stores.")

_STORE_CACHE = {}
def store(prefix):
    if prefix in _STORE_CACHE:
        return _STORE_CACHE[prefix]
    for row in csv.DictReader(io.StringIO(_csv_text())):
        if (row.get("prefix") or "").strip().lstrip("﻿").upper() == prefix.upper():
            shop = (row.get("shop") or "").strip().replace("https://", "").strip("/")
            _STORE_CACHE[prefix] = (shop, (row.get("token") or "").strip())
            return _STORE_CACHE[prefix]
    raise SystemExit(f"prefix {prefix!r} negasit in stores.csv")

def gql(prefix, query, variables=None):
    shop, token = store(prefix)
    url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    data = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "X-Shopify-Access-Token": token, "Content-Type": "application/json"})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                res = json.loads(r.read().decode())
            ts = ((res.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
            if ts.get("currentlyAvailable", 999) < 100:
                time.sleep(1.0)
            return res
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 2)) + attempt); continue
            raise SystemExit(f"HTTP {e.code}: {e.read().decode()[:300]}")
    raise SystemExit("Prea multe 429.")

Q_VAR = """query($q:String!){ productVariants(first:10,query:$q){ edges{ node{ id sku barcode inventoryPolicy
 inventoryItem{ id tracked inventoryLevels(first:5){ edges{ node{ location{ id name }
   quantities(names:["on_hand"]){ name quantity } } } } }
 product{ id title status tags } } } } }"""
M_VAR = """mutation($pid:ID!,$variants:[ProductVariantsBulkInput!]!){
 productVariantsBulkUpdate(productId:$pid,variants:$variants){ userErrors{ field message } } }"""
M_TAGSRM = """mutation($id:ID!,$tags:[String!]!){ tagsRemove(id:$id,tags:$tags){ userErrors{ field message } } }"""
M_SETQTY = """mutation($input:InventorySetQuantitiesInput!){
 inventorySetQuantities(input:$input){ userErrors{ field message } } }"""
M_PRODUPD = """mutation($input:ProductUpdateInput!){ productUpdate(product:$input){
 product{ id status } userErrors{ field message } } }"""

def variants_for(prefix, sku):
    r = gql(prefix, Q_VAR, {"q": f"sku:{sku}"})
    hits = [e["node"] for e in (((r.get("data") or {}).get("productVariants") or {}).get("edges") or [])
            if (e["node"].get("sku") or "").strip() == sku]
    return hits

def on_hand(node):
    lv = node["inventoryItem"]["inventoryLevels"]["edges"]
    return lv[0]["node"]["quantities"][0]["quantity"] if lv else None

def first_loc(node):
    lv = node["inventoryItem"]["inventoryLevels"]["edges"]
    return lv[0]["node"]["location"]["id"] if lv else None

def check(res, path):
    if res.get("errors"):
        raise SystemExit(f"GraphQL error: {json.dumps(res['errors'])[:300]}")
    node = res.get("data", {})
    for p in path:
        node = (node or {}).get(p, {})
    ue = (node or {}).get("userErrors") or []
    if ue:
        raise SystemExit(f"userErrors: {ue}")

def real_variant(hits, expected_bc):
    """Varianta reala = cea cu barcode-ul asteptat (sau, daca lipseste, singura cu barcode)."""
    m = [n for n in hits if (n["barcode"] or "").strip() == expected_bc]
    if m:
        return m[0]
    m = [n for n in hits if (n["barcode"] or "").strip()]
    return m[0] if len(m) == 1 else (hits[0] if hits else None)

# ------------------------- STEPS -------------------------
def load(container):
    svc = sheets()
    tab = find_tab(svc, CONTAINER_SID, container)
    gid, hr, sku_c, qty_c, allrows = read_container(svc, tab)
    todo = [(ri, sku, q) for (ri, sku, q, white) in allrows if white]
    skus = [s for _, s, _ in todo]
    bmap, missing, conflicts = build_barcode_map(svc, skus)
    return svc, tab, gid, sku_c, todo, bmap, missing, conflicts

def cmd_plan(a):
    svc, tab, gid, sku_c, todo, bmap, missing, conflicts = load(a.container)
    print(f"Tab: {tab[0]!r}  | randuri ALBE de facut: {len(todo)}")
    if missing:  print(f"!! BARCODE LIPSA ({len(missing)}): {missing}  -> completeaza in sheet-ul TOM inainte")
    if conflicts: print(f"!! BARCODE CONFLICT: {conflicts}")
    print(f"\n{'SKU':<12}{'qty':>6}  {'barcode':<15} MAG_stoc  dubluri?")
    for ri, sku, q in todo:
        bc = bmap.get(sku, "??")
        hits = variants_for(MASTER, sku)
        oh = on_hand(real_variant(hits, bc)) if hits else None
        dup = f"DA({len(hits)})" if len(hits) > 1 else "-"
        print(f"{sku:<12}{q:>6}  {str(bc):<15} {str(oh):>7}   {dup}")
    print("\nPLAN: barcode+tracking+DENY+scot 'test' pe toate; STOC doar pe MAGDEAL; dublurile -> draft (cmd dupes).")

def cmd_apply(a):
    svc, tab, gid, sku_c, todo, bmap, missing, conflicts = load(a.container)
    if missing or conflicts:
        raise SystemExit(f"Rezolva barcode-urile intai (lipsa={missing}, conflict={list(conflicts)}).")
    for ri, sku, q in todo:
        bc = bmap[sku]
        print(f"--- {sku} (bc {bc}, qty {q}) ---")
        # MASTER: barcode + DENY + tracked + on_hand=q + scot 'test'
        hits = variants_for(MASTER, sku)
        rn = real_variant(hits, bc)
        if not rn:
            print(f"  !! {sku} lipseste pe {MASTER} — SKIP"); continue
        if a.apply:
            check(gql(MASTER, M_VAR, {"pid": rn["product"]["id"], "variants": [
                {"id": rn["id"], "barcode": bc, "inventoryPolicy": "DENY",
                 "inventoryItem": {"tracked": True}}]}), ["productVariantsBulkUpdate"])
            loc = first_loc(rn)
            check(gql(MASTER, M_SETQTY, {"input": {"name": "on_hand", "reason": "received",
                "ignoreCompareQuantity": True, "quantities": [
                {"inventoryItemId": rn["inventoryItem"]["id"], "locationId": loc, "quantity": q}]}}),
                ["inventorySetQuantities"])
            tags = [t for t in rn["product"]["tags"] if t.strip().lower() == "test"]
            if tags:
                check(gql(MASTER, M_TAGSRM, {"id": rn["product"]["id"], "tags": tags}), ["tagsRemove"])
        print(f"  {MASTER}: stoc={q} + barcode + DENY + tracked + test scos")
        # SATELITE: DOAR barcode + tracked (stocul vine prin sync de pe MASTER)
        for pfx in SATELLITES:
            hs = variants_for(pfx, sku)
            rs = real_variant(hs, bc)
            if not rs:
                print(f"  !! {sku} lipseste pe {pfx}"); continue
            if a.apply:
                check(gql(pfx, M_VAR, {"pid": rs["product"]["id"], "variants": [
                    {"id": rs["id"], "barcode": bc, "inventoryItem": {"tracked": True}}]}),
                    ["productVariantsBulkUpdate"])
            print(f"  {pfx}: barcode + tracked (fara stoc — sync copiaza de pe {MASTER})")
    print("\n" + ("APLICAT. Ruleaza 'dupes' apoi 'verify'." if a.apply else "DRY-RUN (fara --apply)."))

def cmd_dupes(a):
    svc, tab, gid, sku_c, todo, bmap, missing, conflicts = load(a.container)
    n = 0
    for ri, sku, q in todo:
        bc = bmap.get(sku)
        hits = variants_for(MASTER, sku)
        if len(hits) < 2:
            continue
        dupes = [h for h in hits if not (h["barcode"] or "").strip() and on_hand(h) == 0]
        real = [h for h in hits if (h["barcode"] or "").strip()]
        if not real:
            print(f"{sku}: !! niciun real cu barcode — SKIP (nu risc)"); continue
        for d in dupes:
            print(f"{sku}: dublura prod={d['product']['id'].split('/')[-1]} '{d['product']['title'][:40]}' -> DRAFT")
            n += 1
            if a.apply:
                check(gql(MASTER, M_PRODUPD, {"input": {"id": d["product"]["id"], "status": "DRAFT"}}),
                      ["productUpdate"])
    print(f"\n{n} dubluri {'trecute pe DRAFT' if a.apply else '(DRY-RUN)'}")

def cmd_verify(a):
    svc, tab, gid, sku_c, todo, bmap, missing, conflicts = load(a.container)
    fails, ok_rows = [], []
    for ri, sku, q in todo:
        bc = bmap.get(sku)
        row_ok = True
        for pfx in [MASTER] + SATELLITES:
            hits = variants_for(pfx, sku)
            rn = real_variant(hits, bc)
            if not rn:
                fails.append(f"{sku}/{pfx}: lipseste"); row_ok = False; continue
            if (rn["barcode"] or "") != bc: fails.append(f"{sku}/{pfx}: barcode {rn['barcode']}!={bc}"); row_ok = False
            if rn["inventoryPolicy"] != "DENY": fails.append(f"{sku}/{pfx}: policy {rn['inventoryPolicy']}"); row_ok = False
            if not rn["inventoryItem"]["tracked"]: fails.append(f"{sku}/{pfx}: tracking off"); row_ok = False
            if on_hand(rn) != q: fails.append(f"{sku}/{pfx}: stoc {on_hand(rn)}!={q}"); row_ok = False
            if rn["product"]["status"] != "ACTIVE": fails.append(f"{sku}/{pfx}: real NU e ACTIVE"); row_ok = False
            if pfx == MASTER and any(t.strip().lower() == "test" for t in rn["product"]["tags"]):
                fails.append(f"{sku}/MAG: inca are tag test"); row_ok = False
        if row_ok:
            ok_rows.append(ri)
    print(f"OK: {len(ok_rows)}/{len(todo)} SKU-uri complet corecte pe toate magazinele")
    for f in fails:
        print("  XX", f)
    return ok_rows

def cmd_green(a):
    svc, tab, gid, sku_c, todo, bmap, missing, conflicts = load(a.container)
    ok_rows = cmd_verify(a)
    if a.apply:
        color_rows_green(svc, gid, ok_rows, sku_c)
        print(f"Marcat verde {len(ok_rows)} randuri.")
    else:
        print("DRY-RUN: as marca verde randurile:", ok_rows)

def main():
    ap = argparse.ArgumentParser(description="Adauga stoc marfa noua pe magazinele deals (Magdeal=master).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("plan", "apply", "dupes", "verify", "green"):
        p = sub.add_parser(name)
        p.add_argument("--container", required=True, help="ex: C42")
        p.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    {"plan": cmd_plan, "apply": cmd_apply, "dupes": cmd_dupes,
     "verify": cmd_verify, "green": cmd_green}[a.cmd](a)

if __name__ == "__main__":
    main()
