#!/usr/bin/env python3
"""IMPINGE densitatile din parcel_density.db (pagina /colete = sursa de adevar) in Shopify -> custom.nr_cutii.

/colete e locul unde depozitul corecteaza; de la salvare valoarea pleaca IMEDIAT in Shopify (pagina o face
singura). Scriptul asta e plasa de siguranta zilnica: prinde SKU-urile aparute pe un magazin nou, produsele
create dupa salvare, si push-urile esuate. Idempotent (scrie doar ce difera). Dry-run implicit; scrie cu --apply.
Reimprospateaza si indexul SKU -> valoare Shopify + product id, din care pagina arata ce e DEJA pus in Shopify.

Lista de magazine: parcel_shop_index.STORES (un singur loc pt tot pipeline-ul de colete).
"""
import os, sys, sqlite3, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parcel_shop_index as SI

APPLY = "--apply" in sys.argv
DB = "/root/Scripturi/data/parcel_density.db"

con = sqlite3.connect(DB)
dens = {sku: nr for sku, nr in con.execute(
    "select sku, nr_cutii from parcel_density where nr_cutii is not null and nr_cutii > 0")}
con.close()
print("densitati de propagat (din /colete): %d SKU-uri\n" % len(dens))

rows = SI.scan()
idx = SI.index_from_rows(rows)

tok = SI.tokens()
todo = collections.defaultdict(list)   # dom -> [(gid, box)]
seen = collections.Counter()
for r in rows:
    want = dens.get(r["sku"])
    if want is None:
        continue
    seen[r["dom"]] += 1
    have = r["box"] if r["src"] == "nr_cutii" else None
    if have is None or abs(have - want) > 1e-9:
        todo[r["dom"]].append((r["gid"], want))

tot = collections.Counter()
print()
for dom, label in SI.STORES.items():
    items = todo.get(dom) or []
    done = 0
    if APPLY and items:
        t = tok.get(dom)
        if t:
            SI.ensure_definition(dom, t)
            by_box = collections.defaultdict(list)
            for gid, box in items:
                by_box[box].append(gid)
            for box, gids in by_box.items():
                n, err = SI.set_metafield(dom, t, gids, box)
                done += n
                if err:
                    print("   !! %s: %s" % (label, err))
    print("%-16s produse cu SKU cunoscut: %-5d de scris: %-5d scrise: %s"
          % (label, seen[dom], len(items), done if APPLY else "DRY"))
    tot["seen"] += seen[dom]; tot["todo"] += len(items); tot["done"] += done

print("\nTOTAL: %d produse potrivite | %d de actualizat | %s scrise"
      % (tot["seen"], tot["todo"], tot["done"] if APPLY else "0 (dry-run)"))
if APPLY:
    print("index SKU->Shopify reimprospatat: %d SKU-uri (%s)" % (SI.write_index(idx), SI.INDEX))
