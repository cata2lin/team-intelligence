#!/usr/bin/env python3
"""Reconstruieste lista de produse din /colete: TOATE produsele magazinelor din pipeline (nu doar cele fara
densitate), ca depozitul sa poata si CORECTA, nu doar completa. Ordonate dupa cate comenzi a generat produsul
in ultimele 90 de zile -> ce conteaza cel mai mult apare primul. Pastreaza intrarile manuale care nu mai sunt
pe niciun magazin. Scrie si indexul SKU -> valoare Shopify + product id (parcel_shop_index.write_index),
din care pagina afiseaza ce e DEJA pus in Shopify.

Lista de magazine e in parcel_shop_index.STORES (un singur loc — inainte era scrisa separat aici si in push,
iar Grandia lipsea din varianta asta, deci produsele ei nu ajungeau niciodata in pagina).
"""
import os, sys, json, shutil, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parcel_shop_index as SI

OUT = "/root/Scripturi/data/parcel_products.json"
APPLY = "--apply" in sys.argv
SKIP_ARCHIVED = "--keep-archived" not in sys.argv


def volume():
    """comenzi per SKU, ultimele 90 zile (AWBprint) -> prioritate pt depozit"""
    import psycopg2
    url = None
    for f in ("/root/Scripturi/.env", "/root/Scripturi/.env.xconnector"):
        try:
            for line in open(f):
                if line.startswith("DATABASE_URL_AWBPRINT="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'"); break
        except Exception:
            pass
        if url:
            break
    if not url:
        return {}
    c = psycopg2.connect(url); cur = c.cursor()
    cur.execute("""
      SELECT li->'inventory_item'->>'sku' AS sku, COUNT(DISTINCT o.id)
      FROM orders o CROSS JOIN LATERAL json_array_elements(o.line_items) li
      WHERE o.frisbo_created_at >= now() - interval '90 days'
        AND li->'inventory_item'->>'sku' IS NOT NULL
      GROUP BY 1""")
    v = {s: n for s, n in cur.fetchall()}
    c.close()
    return v


vol = volume()
print("volum comenzi (90 zile): %d SKU-uri\n" % len(vol))

rows = SI.scan()
idx = SI.index_from_rows(rows)

by_sku, skipped = {}, {"test": 0, "arhivat": 0}
for r in rows:
    if "test" in r["tags"]:
        skipped["test"] += 1
        continue
    if SKIP_ARCHIVED and r["status"] == "ARCHIVED":
        skipped["arhivat"] += 1
        continue
    rec = by_sku.setdefault(r["sku"], {"sku": r["sku"], "title": r["title"], "img": r["img"], "stores": []})
    if r["label"] not in rec["stores"]:
        rec["stores"].append(r["label"])
    if not rec.get("img") and r["img"]:
        rec["img"] = r["img"]
print("\nsarite: %d cu tag `test`, %d arhivate" % (skipped["test"], skipped["arhivat"]))

# pastreaza intrarile manuale care nu-s pe niciun magazin din pipeline
kept = 0
try:
    for old in json.load(open(OUT)):
        if old.get("sku") and old["sku"] not in by_sku:
            by_sku[old["sku"]] = old; kept += 1
except Exception:
    pass
if kept:
    print("pastrate din lista veche (nu mai sunt pe magazine): %d" % kept)

items = sorted(by_sku.values(), key=lambda r: (-vol.get(r["sku"], 0), r["sku"]))
cu_val = sum(1 for r in items if (idx.get(r["sku"]) or {}).get("box"))
print("\nSKU-uri unice in lista: %d (inainte: %s) | cu valoare deja in Shopify: %d"
      % (len(items), len(json.load(open(OUT))) if os.path.exists(OUT) else "?", cu_val))
print("primele 10 dupa volum:")
for r in items[:10]:
    print("   %-28s %5d comenzi/90z  %s" % (r["sku"], vol.get(r["sku"], 0), (r["title"] or "")[:45]))

if not APPLY:
    print("\nDRY-RUN — nu s-a scris nimic.")
    sys.exit(0)
if os.path.exists(OUT):
    shutil.copy2(OUT, OUT + ".bak_" + datetime.datetime.now().strftime("%Y%m%dT%H%M%S"))
json.dump(items, open(OUT, "w"), ensure_ascii=False)
print("\n✅ scris %s (%d produse)" % (OUT, len(items)))
print("✅ scris %s (%d SKU-uri cu product id per magazin)" % (SI.INDEX, SI.write_index(idx)))
