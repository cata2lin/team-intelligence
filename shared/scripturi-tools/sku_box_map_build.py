#!/usr/bin/env python3
"""Construieste map-ul central SKU->nr_cutii (nr colete/unitate) din metafield-urile setate pe magazinele
pipeline-ului de colete (consensus per SKU). order_parcel_count il foloseste ca fallback => setezi metafield-ul
O DATA pe un magazin, merge peste tot. Order Hub (services/cron_parity/parcels.py) NU citeste metafield-uri
deloc — doar map-ul asta, deci un magazin lipsa de aici inseamna colete pe default (32 de comenzi Belasil in
5 zile au plecat cu 1 colet in loc de 2-4, 20-24 aug 2026).

`--fill` completeaza si metafield-ul `nr_produse` (integer) pe magazinele unde lipseste dar SKU-ul are consensus
(vizibilitate). Cron zilnic ruleaza FARA --fill (doar map). Ca `sku_station`/DEPOZIT_SKU_RULES dar pt nr colete.

Lista de magazine: parcel_shop_index.STORES — acelasi loc ca pagina /colete si ca propagarea in Shopify.
(Inainte lista era scrisa separat in trei scripturi si s-a desincronizat: Grandia lipsea din lista pe baza
careia se construieste pagina, deci cele 484 de produse ale ei nu se vedeau niciodata in /colete.)"""
import os, sys, json, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/root/Scripturi")
import parcel_shop_index as SI

FILL = "--fill" in sys.argv
MAP_PATH = "/root/Scripturi/data/sku_box_map.json"

rows = SI.scan()
bysku = collections.defaultdict(list)
for r in rows:
    bysku[r["sku"]].append(r)

mp = {}
for sku, rs in bysku.items():
    eff = [r["box"] for r in rs if r["box"] is not None]   # nr_cutii preferat, altfel nr_produse (in SI.scan)
    if not eff:
        continue
    cons = collections.Counter(eff).most_common(1)[0][0]
    # 0 = INTENTIONAT: produsul nu-si cere colet propriu, calatoreste in coletul altuia (lavetele Belasil in
    # cutia detergentului - regula ownerului, 24-aug-2026). Negativul ramane zgomot si e deja filtrat in SI.fnum.
    if cons >= 0:
        mp[sku] = cons

# overlay AUTORITATIV: input depozit din parcel_density.db (pagina /colete) peste consensul Shopify
try:
    import sqlite3
    _dbc = sqlite3.connect("/root/Scripturi/data/parcel_density.db")
    _n = 0
    for _sku, _nr in _dbc.execute("select sku, nr_cutii from parcel_density where nr_cutii is not null and nr_cutii > 0"):
        mp[_sku] = _nr; _n += 1
    _dbc.close()
    print("overlay parcel_density (depozit): %d SKU-uri" % _n)
except Exception as _e:
    print("overlay parcel_density skip:", _e)

os.makedirs("/root/Scripturi/data", exist_ok=True)
json.dump(mp, open(MAP_PATH, "w"))
print("map SKU->box: %d SKU-uri (din %d produse, %d magazine)" % (len(mp), len(rows), len(SI.STORES)))
print("index SKU->Shopify: %d SKU-uri (%s)" % (SI.write_index(SI.index_from_rows(rows)), SI.INDEX))

# --fill: completeaza nr_produse (integer) unde lipseste dar exista consensus INTREG
if FILL:
    X = SI.xconn()
    tok = SI.tokens()
    MSET = 'mutation($mf:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$mf){ userErrors{ message } } }'
    perstore = collections.defaultdict(list)
    for r in rows:
        if r["src"] is not None:          # are deja nr_cutii sau nr_produse
            continue
        box = mp.get(r["sku"])
        if box is None or abs(box - round(box)) > 1e-9:   # doar valori intregi -> nr_produse
            continue
        perstore[r["dom"]].append((r["gid"], str(int(round(box)))))
    for dom, items in perstore.items():
        t = tok.get(dom)
        if not t:
            continue
        for i in range(0, len(items), 25):
            chunk = items[i:i + 25]
            mf = [{"ownerId": g, "namespace": "custom", "key": "nr_produse",
                   "type": "number_integer", "value": v} for g, v in chunk]
            rr = X.shopify_gql(dom, t, MSET, {"mf": mf})
            ue = ((rr.get("data") or {}).get("metafieldsSet") or {}).get("userErrors") or rr.get("errors")
            if ue:
                print("  FILL ERR %s: %s" % (SI.STORES.get(dom, dom), json.dumps(ue, ensure_ascii=False)[:120]))
        print("  fill %s: %d produse" % (SI.STORES.get(dom, dom), len(items)))
