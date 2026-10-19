"""ty_live.py — catalogul Trendyol LIVE (Product v2) + schimbarea de preț, prin funcțiile TESTATE din
/root/Scripturi/trendyol_stock_push.py (ty_produse = `/products/approved` cu nextPageToken, ty_push =
`price-and-inventory`, ty_batch = rezultatul pe articol). Endpoint-ul vechi `/products` (v1) dă 426 din 13-oct.

Rulează pe serverul de scripturi, trimis pe stdin (`.venv/bin/python - <mod> …` din /root/Scripturi cu .env încărcat):
    produse <filtru|-> <limita> <storefront>        citire: variante aprobate (sku/barcode/titlu conține filtrul)
    pret <sku|barcode> <salePrice> <listPrice> <0|1> <storefront>
                                                    0 = PREVIEW (nu trimite nimic), 1 = trimite + citește batch-ul

Prețul pleacă împreună cu CANTITATEA CITITĂ ACUM (Trendyol ignoră o cerere fără preț+cantitate; vezi ty_push), deci
stocul nu se schimbă — stocul îl ține stock-sync. Secretele le ia trendyol_stock_push din KB; nu se printează.
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "/root/Scripturi")
os.chdir("/root/Scripturi")
import trendyol_stock_push as T  # noqa: E402

_orig_headers = T.ty_headers


def _storefront(sf):
    sf = (sf or "RO").upper()
    if sf not in ("RO", "BG", "GR"):
        sys.exit("storefront necunoscut: %s (RO/BG/GR)" % sf)

    def h():
        d = _orig_headers()
        d["storeFrontCode"] = sf
        return d
    T.ty_headers = h
    return sf


def _rezumat(p):
    stare = "blacklist" if p["blacklisted"] else "arhivat" if p["archived"] else "blocat" if p["locked"] else \
        "onSale" if p["onSale"] else ("onSale=false (stoc 0 — capcana: NU e inactiv)" if not p["quantity"]
                                      else "onSale=false")
    return dict(p, stare=stare)


def produse(filtru, limita, sf):
    sf = _storefront(sf)
    allp = T.ty_produse()
    f = (filtru or "").lower()
    sel = [p for p in allp if not f or f in str(p.get("stockCode") or "").lower()
           or f == str(p.get("barcode") or "").lower() or f in str(p.get("title") or "").lower()]
    cnt = {"total_variante": len(allp),
           "onSale": sum(1 for p in allp if p["onSale"]),
           "stoc_0": sum(1 for p in allp if not p["quantity"]),
           "blacklisted": sum(1 for p in allp if p["blacklisted"]),
           "archived": sum(1 for p in allp if p["archived"]),
           "locked": sum(1 for p in allp if p["locked"])}
    print(json.dumps({"storefront": sf, "sumar": cnt, "potrivite": len(sel),
                      "variante": [_rezumat(p) for p in sel[:limita]]}, ensure_ascii=False, indent=1))


def pret(cheie, sale, lista, aplica, sf):
    sf = _storefront(sf)
    allp = T.ty_produse()
    k = cheie.lower()
    m = [p for p in allp if str(p.get("stockCode") or "").lower() == k or str(p.get("barcode") or "").lower() == k]
    if len(m) != 1:
        print(json.dumps({"eroare": "%d variante pentru %r (trebuie exact 1)" % (len(m), cheie),
                          "variante": [_rezumat(p) for p in m[:10]]}, ensure_ascii=False, indent=1))
        sys.exit(2)
    p = m[0]
    if sale <= 0 or lista < sale:
        sys.exit("preț invalid: salePrice %s, listPrice %s (listPrice ≥ salePrice > 0)" % (sale, lista))
    if p["blacklisted"] or p["archived"] or p["locked"]:
        sys.exit("varianta e %s pe Trendyol — prețul nu se poate schimba de aici" % _rezumat(p)["stare"])
    item = {"barcode": p["barcode"], "quantity": int(p["quantity"] or 0), "salePrice": float(sale), "listPrice": float(lista)}
    plan = {"storefront": sf, "sku": p["stockCode"], "barcode": p["barcode"], "titlu": p["title"],
            "acum": {"salePrice": p["salePrice"], "listPrice": p["listPrice"], "quantity": p["quantity"], "onSale": p["onSale"]},
            "nou": item,
            "nota": "cantitatea se retrimite NESCHIMBATĂ (Trendyol cere preț+cantitate împreună); stocul rămâne la stock-sync"
                    + ("; stoc 0 → onSale rămâne false până dă stock-sync stoc" if not p["quantity"] else "")}
    if not aplica:
        print(json.dumps(dict(plan, mod="PREVIEW — nimic trimis"), ensure_ascii=False, indent=1))
        return
    r = T.ty_push([item])
    bid = r.get("batchRequestId")
    rez = T.ty_batch(bid) if bid else r
    print(json.dumps(dict(plan, mod="TRIMIS", batchRequestId=bid,
                          rezultat=[{"status": i.get("status"), "failureReasons": i.get("failureReasons")}
                                    for i in (rez.get("items") or [])]), ensure_ascii=False, indent=1))


a = sys.argv[1:]
mod = a[0] if a else ""
if mod == "produse":
    produse(a[1] if len(a) > 1 and a[1] != "-" else "", int(a[2]) if len(a) > 2 else 50, a[3] if len(a) > 3 else "RO")
elif mod == "pret":
    pret(a[1], float(a[2]), float(a[3]), a[4] == "1", a[5] if len(a) > 5 else "RO")
else:
    sys.exit("mod necunoscut: %r" % mod)
