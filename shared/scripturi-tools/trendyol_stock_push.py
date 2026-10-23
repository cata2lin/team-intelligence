#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""trendyol_stock_push.py — PUNTE: impinge cantitatea de stoc pe Trendyol.

DE CE EXISTA: din 2026-07-22 nimic nu mai scrie cantitati pe Trendyol. Calea veche (baza
`inventorysync` / `trendyol_pushes`) s-a oprit atunci, iar `stock-sync` (sync.arona.ro) are
magazinul Trendyol configurat (regula PLATFORM_DEFAULT TRENDYOL PERCENTAGE 16%, writesEnabled)
dar toate cele 713 listinguri au `listingStatus: INACTIVE`, deci matcher-ul nu leaga niciun
master si alocatorul da 0. Rezultat: ~700 de produse aprobate pe Trendyol cu stoc 0.

⚠️ PUNTE, NU SOLUTIE. In clipa in care conectorul Trendyol al lui stock-sync incepe sa lege
listingurile, sunt DOI scriitori pe acelasi cimp. Atunci scriptul asta se OPRESTE.

Stocul vine din sursa AUTORITATIVA (`/v1/stock/{barcode}` din stock-sync), nu din AWBprint
(vezi regula organizatiei: stocul se citeste din stock-sync / rafturi).

DRY-RUN implicit. Scrie doar cu `--apply`.
  uv run trendyol_stock_push.py                 # ce s-ar schimba
  uv run trendyol_stock_push.py --apply         # scrie pe Trendyol
  uv run trendyol_stock_push.py --procent 10    # alta felie
  uv run trendyol_stock_push.py --sku gt-33 --sku gt-30
"""
# /// script
# dependencies = ["requests"]
# ///
import os, sys, json, math, time, argparse, subprocess, functools
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
def _prima_cale(*cai):
    for c in cai:
        if os.path.exists(c):
            return c
    return cai[-1]


KB = os.environ.get("KB_PY") or _prima_cale(
    "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py",
    "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py")
UV = os.environ.get("UV_BIN") or _prima_cale("/root/.local/bin/uv",
                                             "/Users/gheorghebeschea/.local/bin/uv", "uv")
LOT = 100          # Trendyol accepta maxim 100 de articole / cerere
TRENDYOL_STORE_ID = "d0b88584-cb48-4d33-a2da-402da4283a61"   # magazinul Trendyol in stock-sync
PAUZA = 1.0        # intre loturi

@functools.lru_cache(maxsize=16)
def secret(k):
    o = subprocess.run([UV, "run", KB, "secret-get", k], capture_output=True, text=True,
                       cwd=os.path.dirname(KB))
    if o.returncode:
        sys.exit("secret %s: %s" % (k, o.stderr[-300:]))
    return o.stdout.strip()

# ---------------------------------------------------------------- stock-sync
def ss_get(path):
    r = requests.get(secret("STOCK_SYNC_API_URL").rstrip("/") + path,
                     headers={"Authorization": "Bearer " + secret("STOCK_SYNC_API_TOKEN")},
                     timeout=90)
    r.raise_for_status()
    return r.json()

def listinguri_active_trendyol():
    """Barcode-urile pe care stock-sync le-a LEGAT deja pe magazinul Trendyol.
    Contract de neciocnire: scriptul asta DOAR deschide ușa (scoate un produs din starea
    stoc-0 → `onSale:false` → listing INACTIVE → alocare 0 → stoc 0). De cum listingul e ACTIVE,
    stocul îl ține stock-sync; aici îl sărim, ca să nu fim doi scriitori pe același câmp."""
    out, cur = set(), None
    while True:
        j = ss_get("/v1/listings?storeId=%s&limit=200%s"
                   % (TRENDYOL_STORE_ID, "&cursor=%s" % cur if cur else ""))
        for l in j.get("listings") or []:
            if l.get("listingStatus") == "ACTIVE":
                out.add((l.get("barcodeNormalized") or "").strip())
        cur = j.get("nextCursor") or (j.get("pagination") or {}).get("nextCursor")
        if not cur:
            return out


def depozit():
    """barcodeNormalized -> bucati disponibile global, din master-produsele stock-sync."""
    out, cur = {}, None
    while True:
        j = ss_get("/v1/master-products?limit=200" + ("&cursor=%s" % cur if cur else ""))
        for m in j.get("masterProducts") or []:
            if m.get("status") == "ACTIVE":
                out[m["barcodeNormalized"]] = int(m.get("globalAvailable") or 0)
        cur = j.get("nextCursor") or (j.get("pagination") or {}).get("nextCursor")
        if not cur:
            return out

# ---------------------------------------------------------------- Trendyol
def ty_headers():
    return {"Authorization": "Basic " + secret("TRENDYOL_TOKEN"),
            "User-Agent": secret("TRENDYOL_SELLER_ID") + " - SelfIntegration",
            "storeFrontCode": secret("TRENDYOL_STORE_FRONT_CODE") or "RO",
            "Content-Type": "application/json"}

def ty_produse():
    """Produsele APROBATE de pe Trendyol, un rand pe VARIANTA (barcode), in forma veche (v1).

    16-oct-2026: `GET .../products` (Product v1) da 426 UPGRADE_REQUIRED ("brownout... migrate to
    Product v2") din 13-oct 14:15 - cronul a picat tacut 3 zile. v2 = `.../products/approved`:
    max 100/pagina, paginare cu `nextPageToken` (NU `page`), varianta e in `variants[]`, pretul in
    `variants[].price`, cantitatea in `variants[].stock.quantity`, lista neagra in
    `variants[].blacklisted`. Neaprobatele (`/products/unapproved`) nu primesc stoc oricum."""
    sid = secret("TRENDYOL_SELLER_ID")
    base = "https://apigw.trendyol.com/integration/product/sellers/%s/products/approved" % sid
    out, tok, pagini = [], None, 0
    while pagini < 500:
        par = {"size": 100}
        if tok:
            par["nextPageToken"] = tok
        r = requests.get(base, params=par, headers=ty_headers(), timeout=60)
        r.raise_for_status()
        d = r.json()
        for c in d.get("content") or []:
            for v in c.get("variants") or []:
                pr = v.get("price") or {}
                out.append({"barcode": v.get("barcode"), "stockCode": v.get("stockCode"),
                            "title": c.get("title"), "approved": True,
                            "archived": bool(v.get("archived")), "locked": bool(v.get("locked")),
                            "blacklisted": bool(v.get("blacklisted")), "onSale": v.get("onSale"),
                            "quantity": (v.get("stock") or {}).get("quantity"),
                            "salePrice": pr.get("salePrice"), "listPrice": pr.get("listPrice")})
        pagini += 1
        tok = d.get("nextPageToken")
        if not tok or not d.get("content"):
            break
    return out


def ty_blacklist(prods=None):
    """Codurile de bare pe lista neagra Trendyol (in v2 e un cimp pe varianta). Un produs pe lista
    neagra nu se vinde oricit stoc i-ai trimite, deci nu-l trimitem."""
    return {(p.get("barcode") or "").strip() for p in (prods if prods is not None else ty_produse())
            if p.get("blacklisted")}

def ty_push(items):
    """⚠️ CANTITATEA SINGURA NU SE APLICA. Trendyol accepta `{barcode, quantity}`, raspunde
    batchRequestId si `status: COMPLETED` cu `failedItemCount: 0` — si NU schimba nimic.
    Cantitatea se aplica doar daca trimiti SI `salePrice` + `listPrice` (asa face si adaptorul
    nostru din stock-sync: „ALWAYS carry current prices (never zero/stale)").
    Masurat pe n-47 pe 13-oct-2026: push doar cu quantity -> batch COMPLETED, qty a rams 0."""
    for it in items:
        for k in ("barcode", "quantity", "salePrice", "listPrice"):
            if it.get(k) in (None, ""):
                raise SystemExit("articol fara %s: %r" % (k, it))
    sid = secret("TRENDYOL_SELLER_ID")
    url = ("https://apigw.trendyol.com/integration/inventory/sellers/%s/products/price-and-inventory"
           % sid)
    r = requests.post(url, headers=ty_headers(), json={"items": items}, timeout=90)
    if r.status_code >= 400:
        raise SystemExit("push -> %s %s" % (r.status_code, r.text[:400]))
    return r.json()


def ty_batch(bid, incercari=12, pauza=5):
    """Asteapta REZULTATELE PE ARTICOL. `items: []` NU inseamna succes — inseamna ca batch-ul
    inca nu s-a procesat (asa citeste si `pollTrendyolBatch` din stock-sync). Statusul de sus
    `COMPLETED` cu `items: []` m-a pacalit o data; nu te lua dupa el."""
    sid = secret("TRENDYOL_SELLER_ID")
    u = ("https://apigw.trendyol.com/integration/product/sellers/%s/products/batch-requests/%s"
         % (sid, bid))
    for _ in range(incercari):
        r = requests.get(u, headers=ty_headers(), timeout=45)
        r.raise_for_status()
        j = r.json()
        art = j.get("items") or []
        if art and not any((a.get("status") or "").upper() in ("PENDING", "PROCESSING")
                           for a in art):
            return j
        time.sleep(pauza)
    return j

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="scrie efectiv pe Trendyol")
    ap.add_argument("--procent", type=float, default=16.0, help="felia din depozit (implicit 16)")
    ap.add_argument("--plafon", type=int, default=0, help="maxim bucati per SKU (0 = fara)")
    ap.add_argument("--sku", action="append", default=[], help="doar aceste stockCode-uri")
    ap.add_argument("--min-delta", type=int, default=1,
                    help="nu trimite daca diferenta e mai mica de atat")
    ap.add_argument("--bootstrap", action="store_true",
                    help="DOAR produsele pe care stock-sync nu le-a legat inca (pentru cron)")
    a = ap.parse_args()
    doar = {s.strip().lower() for s in a.sku}

    print("citesc depozitul din stock-sync…", flush=True)
    stoc = depozit()
    print("  %d master-produse ACTIVE" % len(stoc))
    legate = listinguri_active_trendyol() if a.bootstrap else set()
    if a.bootstrap:
        print("  %d listinguri deja ACTIVE in stock-sync (le sarim)" % len(legate))
    print("citesc produsele de pe Trendyol…", flush=True)
    prods = ty_produse()
    print("  %d randuri de produs" % len(prods))
    negre = ty_blacklist(prods)
    print("  %d pe lista neagra Trendyol (sarite)" % len(negre))

    plan, fara_stoc, arhivate, neaprobate, pe_negru, fara_pret, deja = ([], [], [], [], [],
                                                                               [], [])
    for pr in prods:
        bc = (pr.get("barcode") or "").strip()
        sku = (pr.get("stockCode") or "").strip()
        if not bc or not sku:
            continue
        if doar and sku.lower() not in doar:
            continue
        if a.bootstrap and bc in legate:
            deja.append(sku); continue
        if bc in negre:
            pe_negru.append(sku); continue
        if pr.get("archived"):
            arhivate.append(sku); continue
        if not pr.get("approved"):
            neaprobate.append(sku); continue
        # codul de bare de pe Trendyol e cel al listingului; masterul se cauta pe acelasi cod
        buc = stoc.get(bc)
        if buc is None or buc <= 0:
            fara_stoc.append(sku); continue
        tinta = int(math.floor(buc * a.procent / 100.0))
        if a.plafon:
            tinta = min(tinta, a.plafon)
        acum = int(pr.get("quantity") or 0)
        if abs(tinta - acum) < a.min_delta:
            continue
        # preturile CURENTE, obligatorii ca sa se aplice cantitatea (vezi ty_push)
        sale = pr.get("salePrice")
        lista = pr.get("listPrice")
        if not sale or not lista or float(lista) < float(sale):
            fara_pret.append(sku); continue
        plan.append({"barcode": bc, "quantity": tinta,
                     "salePrice": float(sale), "listPrice": float(lista),
                     "_sku": sku, "_acum": acum,
                     "_depozit": buc, "_titlu": (pr.get("title") or "")[:44]})

    plan.sort(key=lambda x: -(x["quantity"] - x["_acum"]))
    print("\n%-22s %9s %9s %9s  %s" % ("sku", "depozit", "acum", "tinta", "produs"))
    for x in plan[:40]:
        print("%-22s %9d %9d %9d  %s" % (x["_sku"], x["_depozit"], x["_acum"], x["quantity"], x["_titlu"]))
    if len(plan) > 40:
        print("… si alte %d" % (len(plan) - 40))

    print("\nde trimis: %d SKU-uri, %d bucati (felie %g%%)"
          % (len(plan), sum(x["quantity"] for x in plan), a.procent))
    print("sarite: %d pe lista neagra, %d arhivate pe Trendyol, %d neaprobate, "
          "%d fara stoc in depozit, %d fara preturi valide"
          % (len(pe_negru), len(arhivate), len(neaprobate), len(fara_stoc), len(fara_pret)))
    if a.bootstrap:
        print("        + %d deja legate de stock-sync (le ține el)" % len(deja))

    if not a.apply:
        print("\nDRY-RUN. Nimic nu s-a scris. Adauga --apply ca sa trimita.")
        return
    if not plan:
        print("nimic de trimis.")
        return
    items = [{k: x[k] for k in ("barcode", "quantity", "salePrice", "listPrice")} for x in plan]
    trimise, reusite, picate = 0, 0, 0
    for i in range(0, len(items), LOT):
        lot = items[i:i + LOT]
        r = ty_push(lot)
        bid = r.get("batchRequestId")
        trimise += len(lot)
        j = ty_batch(bid)
        art = j.get("items") or []
        ok = [a for a in art if (a.get("status") or "").upper() == "SUCCESS"]
        nu = [a for a in art if (a.get("status") or "").upper() not in ("SUCCESS",)]
        reusite += len(ok); picate += len(nu)
        print("  lot %d-%d (%s): %d reusite, %d picate%s"
              % (i + 1, i + len(lot), bid, len(ok), len(nu),
                 "" if art else "  ⚠️ fara rezultate pe articol — NU e dovada de succes"))
        for a in nu[:5]:
            print("     ✗ %s: %s" % ((a.get("requestItem") or {}).get("barcode"),
                                     "; ".join(a.get("failureReasons") or ["?"])))
        time.sleep(PAUZA)
    print("trimise %d, confirmate %d, picate %d." % (trimise, reusite, picate))
    print("⚠️ Confirmarea de mai sus e de la Trendyol pe articol. Verifica si cantitatea reala "
          "cu o nouă rulare dry-run — un batch 'COMPLETED' fara rezultate pe articol nu dovedeste "
          "nimic.")

if __name__ == "__main__":
    main()
