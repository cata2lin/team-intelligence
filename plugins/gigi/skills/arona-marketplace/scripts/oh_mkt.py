"""oh_mkt.py — CITIRI marketplace din Order Hub (eMAG / Trendyol / Okazii / Pepita / OLX / CEL), DOAR SELECT.

Rulează ÎN containerul orderhub-web, trimis pe stdin (nimic copiat pe server, niciun cod OH schimbat):
    ssh root@161.97.69.226 'docker exec -i -w /app -e PYTHONPATH=/app orderhub-web /opt/venv/bin/python - <mod> [args]'

Moduri:
    conexiuni                          conexiunile + listările pe stare + ultimul import / eroare
    comenzi <marketplace|-> <comanda|-> <zile> <doar_neexpediate 0|1> <limita>
    listare <sku>                      rândurile marketplace_listari pentru un SKU (sau barcode), pe toate conexiunile
    feed <conexiune> <sku|-> <limita>  ce vede Okazii/Pepita în feed: articol, preț, cantitatea de acum (stock-sync)

Sesiune READ ONLY (Postgres refuză orice scriere). Fără date personale în ieșire (fără nume/telefon/adresă client).
"""
import asyncio
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, "/app")


def _dsn():
    return os.environ["DATABASE_URL"].replace("+asyncpg", "")


def _j(x):
    print(json.dumps(x, ensure_ascii=False, default=str, indent=1))


async def _con():
    import asyncpg
    c = await asyncpg.connect(_dsn())
    await c.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return c


async def conexiuni():
    c = await _con()
    rows = await c.fetch("""
      select k.id, k.marketplace, k.storefront, k.nume, k.activ, k.import_comenzi, k.scrie_status, k.mod_stoc,
             k.stoc_tampon, k.magazin_comenzi_id, s.name magazin_comenzi, k.ultima_import_la,
             left(k.ultima_eroare, 200) ultima_eroare,
             (select json_object_agg(stare, n) from (select stare, count(*) n from marketplace_listari l
                where l.conexiune_id = k.id group by 1) z) listari
      from marketplace_conexiuni k left join stores s on s.id = k.magazin_comenzi_id order by k.id""")
    out = []
    for r in rows:
        d = dict(r)
        d["listari"] = json.loads(d["listari"]) if isinstance(d["listari"], str) else (d["listari"] or {})
        out.append(d)
    _j(out)


async def comenzi(mp, comanda, zile, neexp, limita):
    c = await _con()
    where, args = ["o.store_id in (select magazin_comenzi_id from marketplace_conexiuni where magazin_comenzi_id is not null"
                   + (" and marketplace = $1)" if mp else ")")], []
    if mp:
        args.append(mp)
    if comanda:
        args.append("%" + comanda + "%")
        where.append("o.name ilike $%d" % len(args))
    else:
        args.append(int(zile))
        where.append("o.created_at >= now() - make_interval(days => $%d)" % len(args))
    if neexp:
        where.append("o.cancelled_at is null and not exists (select 1 from shipments x where x.order_id = o.id "
                     "and x.derived_status in ('in_transit','pickup_office','delivery_issues','shipped','delivered','refused'))")
    args.append(int(limita))
    rows = await c.fetch("""
      select s.name magazin, o.name comanda, o.created_at, o.cancelled_at, o.derived_status status_comanda,
             o.processing_status, o.total_price, o.currency, o.invoice_series || coalesce(o.invoice_number,'') factura,
             o.parcel_count, o.shipping_country tara,
             (select json_agg(json_build_object('awb', x.awb, 'curier', x.courier, 'status', x.derived_status,
                 'ultim_status', x.last_status, 'printat_la', x.printed_at, 'creat_la', x.created_at) order by x.id)
                from shipments x where x.order_id = o.id) colete,
             (select json_agg(json_build_object('sku', li.sku, 'qty', li.quantity)) from line_items li
                where li.order_id = o.id) produse
      from orders o join stores s on s.id = o.store_id
      where %s order by o.created_at desc limit $%d""" % (" and ".join(where), len(args)), *args)
    out = []
    for r in rows:
        d = dict(r)
        col = json.loads(d["colete"]) if d["colete"] else []
        d["colete"] = col
        d["produse"] = json.loads(d["produse"]) if d["produse"] else []
        d["coada_print"] = (not d["cancelled_at"]) and any(x.get("awb") and not x.get("printat_la") for x in col)
        out.append(d)
    _j({"n": len(out), "comenzi": out})


async def listare(sku):
    c = await _con()
    rows = await c.fetch("""
      select k.id conexiune, k.nume, l.sku, l.barcode, l.id_extern, l.stare, l.ultim_stoc, l.ultim_pret,
             l.ultim_pret_lista, l.ultima_trimitere_stoc_la, l.stoc_murdar, l.magazin_sursa_id, l.lot_id,
             l.erori, l.updated_at,
             l.date_marketplace - 'feed' date_marketplace
      from marketplace_listari l join marketplace_conexiuni k on k.id = l.conexiune_id
      where lower(l.sku) = lower($1) or l.barcode = $1 order by k.id""", sku)
    out = []
    for r in rows:
        d = dict(r)
        for k in ("erori", "date_marketplace"):
            if isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        out.append(d)
    ty = await c.fetchval("select count(*) from marketplace_listari l join marketplace_conexiuni k on k.id=l.conexiune_id "
                          "where k.marketplace='trendyol'")
    _j({"sku": sku, "listari_oh": out,
        "nota": None if ty else "Trendyol NU are listări în OH (catalogul e pe VPS) → trendyol_produse pt starea live."})


async def feed(cid, sku, limita):
    from sqlalchemy import text
    import database
    import models
    from services import stoc_master
    from services.marketplace import feed as FD
    async with database.AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        c = await db.get(models.MarketplaceConexiune, cid)
        if c is None or c.marketplace not in ("okazii", "pepita"):
            _j({"eroare": "conexiunea %s nu e un feed Okazii/Pepita" % cid})
            return
        idx = await stoc_master.stoc()
        randuri = await FD.randuri_feed(db, c, idx)
    tot = len(randuri)
    in_feed = sum(1 for _, _, q in randuri if q >= 1)
    sel = [(r, a, q) for r, a, q in randuri if not sku or sku.lower() in str(r.sku or "").lower()
           or sku == str(r.barcode)]
    _j({"conexiune": cid, "nume": c.nume, "articole_listate": tot, "in_feed_cu_stoc": in_feed,
        "lipsa_din_feed_stoc_0": tot - in_feed, "plafon": int((c.setari or {}).get("feed_maxim") or 20),
        "articole": [{"sku": r.sku, "barcode": r.barcode, "titlu": a.get("titlu"), "pret": a.get("pret"),
                      "pret_lista": a.get("pret_lista"), "categorie_id": r.categorie_id, "cantitate_feed": q,
                      "stare": r.stare, "imagini": len(a.get("imagini") or [])} for r, a, q in sel[:int(limita)]]})


def main():
    a = sys.argv[1:]
    mod = a[0] if a else ""
    n = lambda i, d="": (a[i] if len(a) > i and a[i] != "-" else d)  # noqa: E731
    if mod == "conexiuni":
        asyncio.run(conexiuni())
    elif mod == "comenzi":
        asyncio.run(comenzi(n(1), n(2), int(n(3, "7")), n(4, "0") == "1", int(n(5, "50"))))
    elif mod == "listare":
        asyncio.run(listare(n(1)))
    elif mod == "feed":
        asyncio.run(feed(int(n(1, "8")), n(2), int(n(3, "50"))))
    else:
        sys.exit("mod necunoscut: %r" % mod)


main()
