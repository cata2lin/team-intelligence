#!/usr/bin/env python3
"""Oglindește în metrics (`cache.oh_livrabilitate`) starea comenzilor din ORDER HUB, ca dashboard-ul metrics să nu mai
recalculeze statusul din AWBprint (S1, 24-oct-2026).

De ce așa: aplicația metrics (Vercel) citea livrat / refuzat / transport direct din AWBprint (`aggregated_status`,
`transport_cost`), adică statusurile internaționale înghețate din 27-sep și transportul gol din 28-aug. Metrics are
deja conexiune la propria bază; o tabelă cache alimentată de aici o mută pe OH fără o cheie nouă în Vercel.

  · sursa: `GET /api/cs/livrabilitate?luna=` (starea din COLETE; transport fără TVA), cheia OH_PROFIT_CITIRE_TOKEN;
  · pe lună: se înlocuiește luna DOAR dacă citirea a reușit (fail-safe: o lună căzută rămâne cum era);
  · comutatorul: `cache.metrics_setari` cheia `status_sursa` = oh | awbprint (citit de metrics la fiecare raport).
    Întoarcere: UPDATE cache.metrics_setari SET valoare='awbprint' WHERE cheie='status_sursa'.

  oh_livrabilitate_cache.py [--luni 4] [--dry-run]
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.request

import psycopg2

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OH = os.environ.get("OH_URL", "https://orderhub.arona.ro")
DDL = """
CREATE SCHEMA IF NOT EXISTS cache;
CREATE TABLE IF NOT EXISTS cache.oh_livrabilitate (
    comanda text PRIMARY KEY, prefix text NOT NULL, zi date NOT NULL, stare text NOT NULL,
    total numeric, moneda text, transport_fara_tva numeric, actualizat timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS oh_livrabilitate_prefix_zi ON cache.oh_livrabilitate (prefix, zi);
CREATE TABLE IF NOT EXISTS cache.metrics_setari (cheie text PRIMARY KEY, valoare text NOT NULL,
    actualizat timestamptz NOT NULL DEFAULT now());
"""


def _dsn():
    u = os.environ["DATABASE_URL_METRICS"]
    return re.sub(r"[?&](schema|pgbouncer|connection_limit)=[^&]*", "", u)


def _luna(luna, tok):
    req = urllib.request.Request(OH + "/api/cs/livrabilitate?luna=" + luna,
                                 headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    if not d.get("ok"):
        raise RuntimeError("OH a refuzat luna %s: %s" % (luna, str(d)[:200]))
    col = d["coloane"]
    return [dict(zip(col, x)) for x in d["randuri"]]


def randuri_db(randuri):
    """(comanda, prefix, zi, stare, total, moneda, transport) — prefixul ca în metrics: literele de la începutul
    numelui comenzii (`substring(order_number from '^[A-Za-z]+')`). Funcție pură (testată)."""
    out = []
    for x in randuri:
        m = re.match(r"^[A-Za-z]+", x.get("comanda") or "")
        if not m or not x.get("zi"):
            continue
        out.append((x["comanda"], m.group(0), x["zi"], x["stare"], x.get("total"), x.get("moneda"),
                    x.get("transport_fara_tva")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--luni", type=int, default=4, help="luna curentă + precedentele")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    tok = os.environ.get("OH_PROFIT_CITIRE_TOKEN") or os.environ.get("OH_CS_SB_TOKEN")
    if not tok:
        sys.exit("EROARE: lipsește cheia de citire Order Hub (OH_PROFIT_CITIRE_TOKEN)")
    z, luni = dt.date.today().replace(day=1), []
    for _ in range(a.luni):
        luni.append(z.strftime("%Y-%m"))
        z = (z - dt.timedelta(days=1)).replace(day=1)
    cx = psycopg2.connect(_dsn())
    try:
        with cx, cx.cursor() as c:
            c.execute(DDL)
            c.execute("INSERT INTO cache.metrics_setari (cheie, valoare) VALUES ('status_sursa', 'oh') ON CONFLICT DO NOTHING")
        rc = 0
        for luna in sorted(luni):
            try:
                rows = randuri_db(_luna(luna, tok))
            except Exception as e:  # noqa: BLE001 — luna rămâne cum era
                print("  %s: OH indisponibil (%s) — păstrez ce era" % (luna, str(e)[:120])); rc = 1
                continue
            if not rows:
                print("  %s: 0 rânduri de la OH — păstrez ce era" % luna); rc = 1
                continue
            if a.dry_run:
                print("  %s: %d rânduri (dry-run)" % (luna, len(rows))); continue
            inc = luna + "-01"
            with cx, cx.cursor() as c:
                c.execute("DELETE FROM cache.oh_livrabilitate WHERE zi >= %s::date AND zi < (%s::date + interval '1 month')",
                          (inc, inc))
                sters = c.rowcount
                from psycopg2.extras import execute_values
                execute_values(c, "INSERT INTO cache.oh_livrabilitate (comanda, prefix, zi, stare, total, moneda, "
                                  "transport_fara_tva) VALUES %s ON CONFLICT (comanda) DO UPDATE SET prefix=EXCLUDED.prefix, "
                                  "zi=EXCLUDED.zi, stare=EXCLUDED.stare, total=EXCLUDED.total, moneda=EXCLUDED.moneda, "
                                  "transport_fara_tva=EXCLUDED.transport_fara_tva, actualizat=now()", rows, page_size=2000)
            print("  %s: %d rânduri (înlocuite %d)" % (luna, len(rows), sters))
        return rc
    finally:
        cx.close()


if __name__ == "__main__":
    sys.exit(main())
