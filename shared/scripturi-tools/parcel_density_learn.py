#!/usr/bin/env python3
"""ÎNVAȚĂ densitatea de colete din ISTORICUL de expedieri (AWBprint) -> parcel_density.db (pagina /colete).

Dovada = comanda cu UN SINGUR SKU, expediata in UN SINGUR colet (order_awbs.package_count=1), care a PLECAT
fizic. Daca N bucati au incaput intr-o cutie si coletul a plecat => intra cel putin N/colet.
NU foloseste nr. de colete calculat de noi ca sursa (ar fi circular) - doar cazurile package_count=1.

REGULI DE SIGURANTA:
  1. NU reduce NICIODATA capacitatea. Actualizeaza doar daca istoricul dovedeste ca incap MAI MULTE
     decat spune valoarea curenta (dovada e o limita INFERIOARA, nu un maxim).
  2. NU suprascrie randurile introduse de OM (depozit prin /colete, sau owner). Doar cele invatate ('istoric').
Dry-run implicit; scrie doar cu --apply.
"""
import os, sys, json, sqlite3, datetime, collections

APPLY = "--apply" in sys.argv
DAYS = 365
MIN_EV = int(os.environ.get("MIN_EV", "1"))   # dovezi minime la capacitatea maxima
DB = "/root/Scripturi/data/parcel_density.db"
MAP = "/root/Scripturi/data/sku_box_map.json"
STORES = ("ofertelezilei.ro", "reduceribune.ro", "casaofertelor.ro", "magdeal.ro")

SQL = """
WITH so AS (
  SELECT o.id, li->'inventory_item'->>'sku' AS sku, o.item_count AS buc,
         (SELECT MAX(a.package_count) FROM order_awbs a
           WHERE a.order_id = o.id AND COALESCE(a.is_return_label,false)=false) AS colete
  FROM orders o
  CROSS JOIN LATERAL json_array_elements(o.line_items) li
  LEFT JOIN stores s ON s.uid = o.store_uid
  WHERE o.unique_sku_count = 1 AND o.item_count >= 2
    AND o.frisbo_created_at >= now() - interval '%d days'
    AND o.aggregated_status IN ('delivered','back_to_sender','in_transit','unsuccessful_delivery')
    AND s.name IN %s
), ev AS (
  SELECT sku, MAX(buc) FILTER (WHERE colete = 1) AS cap
  FROM so WHERE colete IS NOT NULL AND sku IS NOT NULL GROUP BY sku
)
SELECT e.sku, e.cap, d.n
FROM ev e
JOIN LATERAL (SELECT COUNT(*) AS n FROM so WHERE so.sku = e.sku AND so.colete = 1 AND so.buc = e.cap) d ON true
WHERE e.cap >= 2
""" % (DAYS, str(STORES))


def awb_rows():
    import psycopg2
    url = None
    for f in ("/root/Scripturi/.env", "/root/Scripturi/.env.xconnector"):
        try:
            for line in open(f):
                if line.startswith("DATABASE_URL_AWBPRINT="):
                    url = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            pass
        if url:
            break
    if not url:
        sys.exit("DATABASE_URL_AWBPRINT lipseste")
    c = psycopg2.connect(url)
    cur = c.cursor()
    cur.execute(SQL)
    rows = cur.fetchall()
    c.close()
    return rows


rows = awb_rows()
print("dovezi din istoric: %d SKU-uri (fereastra %d zile, magazine deals RO)" % (len(rows), DAYS))

cur_map = {}
try:
    cur_map = json.load(open(MAP))
except Exception as e:
    print("!! nu pot citi harta curenta:", e)

con = sqlite3.connect(DB)
con.execute("""create table if not exists parcel_density(
    sku text primary key, per_parcel integer, nr_cutii real, note text,
    updated_at text, updated_by text)""")
human = {}
for sku, by in con.execute("select sku, coalesce(updated_by,'') from parcel_density"):
    human[sku] = not by.startswith("istoric")

ts = datetime.datetime.now().isoformat(timespec="seconds")
plan, skip_human, skip_ok = [], [], []
for sku, cap, n in rows:
    if n < MIN_EV:
        continue
    if human.get(sku):
        skip_human.append((sku, cap))
        continue
    d_now = cur_map.get(sku)                       # cutii per bucata
    cap_now = (1.0 / d_now) if d_now else None     # bucati per cutie
    if cap_now is not None and cap_now >= cap - 1e-9:
        skip_ok.append((sku, cap_now, cap))        # deja la fel de generos sau mai mult
        continue
    plan.append((sku, cap, n, cap_now))

print("\n%-34s %10s %10s %8s" % ("SKU", "acum", "din istoric", "dovezi"))
for sku, cap, n, cap_now in sorted(plan, key=lambda r: -r[2])[:40]:
    print("%-34s %10s %10s %8d" % (sku, ("%g/cutie" % cap_now) if cap_now else "nesetat",
                                   "%d/cutie" % cap, n))
if len(plan) > 40:
    print("... si inca %d" % (len(plan) - 40))

print("\nDE ACTUALIZAT: %d | sarite (om a setat): %d | sarite (deja ok): %d"
      % (len(plan), len(skip_human), len(skip_ok)))
if skip_human:
    print("  protejate (setate de om):", ", ".join(s for s, _ in skip_human[:12]),
          "..." if len(skip_human) > 12 else "")

if not APPLY:
    print("\nDRY-RUN — nu s-a scris nimic. Ruleaza cu --apply.")
    sys.exit(0)

for sku, cap, n, cap_now in plan:
    con.execute("""insert into parcel_density(sku, per_parcel, nr_cutii, note, updated_at, updated_by)
                   values(?,?,?,?,?,?)
                   on conflict(sku) do update set per_parcel=excluded.per_parcel,
                     nr_cutii=excluded.nr_cutii, note=excluded.note,
                     updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                (sku, int(cap), round(1.0 / cap, 6),
                 "invatat din istoric: %d buc intr-un colet, %d comenzi livrate" % (cap, n),
                 ts, "istoric"))
con.commit()
print("\n✅ scrise %d randuri in parcel_density.db (updated_by='istoric')" % len(plan))
print("   randuri totale acum: %d" % con.execute("select count(*) from parcel_density").fetchone()[0])
