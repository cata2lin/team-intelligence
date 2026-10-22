#!/usr/bin/env python3
"""Pune la loc, dintr-un backup `_bak_po_<lună>`, comenzile pe care sync-ul le-a PIERDUT dintr-un magazin.

De ce (22-oct-2026): `profit_orders_sync.py 2026-10` a rescris luna cu 52.085 de comenzi în loc de 59.705 — Esteban a
rămas cu 9.762 (până pe 12-oct) față de 19.384 (până pe 21-oct) în `_bak_po_202610`, ~1,14 M lei venit ex-TVA dispăruți
din P&L, iar scriptul a scris „✅ OK" (pragul lui e 15% pe TOT tabelul). Backup-ul îl face chiar scriptul, înainte de
rulare, deci ține starea bună de dinainte.

Implicit DRY-RUN: numără ce ar adăuga (pe prefix: comenzi, venit, pe statusuri) și nu scrie nimic. Cu `--apply`:
  1. salvează luna vie într-o tabelă nouă `_bak_po_<lună>_inainte_restaurare_<timestamp>` (întoarcerea);
  2. ADAUGĂ (nu rescrie) rândurile din backup care lipsesc din tabela vie, după (month, prefix, order_name) — rândurile
     vii rămân cu statusul lor mai nou; cele adăugate vin cu statusul din backup, pe care tracking-ul următor îl
     reîmprospătează;
  3. verifică numărătoarea după.
După `--apply`, cache-ul `cache.brand_pnl_monthly` se reface cu `build_cache.py --table brand_pnl_real --apply`.

  uv run profit_orders_restaureaza.py 2026-10                 # dry-run, toate prefixele
  uv run profit_orders_restaureaza.py 2026-10 --prefix EST    # dry-run, un magazin
  uv run profit_orders_restaureaza.py 2026-10 --prefix EST --apply
"""
import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

DB = os.environ.get("PROFITABILITY_DB", "/root/Scripturi/data/profitability.db")


def _coloane(con, tabel):
    return [r[1] for r in con.execute(f"PRAGMA table_info({tabel})") if r[1] != "id"]


def lipsa(con, bak, luna, prefix=None):
    """Rândurile din backup care NU sunt în tabela vie, pe (month, prefix, order_name)."""
    cond = " AND b.prefix = ?" if prefix else ""
    args = [luna] + ([prefix] if prefix else [])
    return con.execute(f"""
        SELECT b.prefix, COUNT(*), ROUND(SUM(b.revenue), 2),
               SUM(b.status_category = 'Livrata'), SUM(b.status_category = 'Refuzata'),
               SUM(b.status_category = 'In curs de livrare'), SUM(b.status_category = 'Anulata'),
               MIN(substr(b.created_at, 1, 10)), MAX(substr(b.created_at, 1, 10))
          FROM {bak} b
         WHERE b.month = ?{cond}
           AND NOT EXISTS (SELECT 1 FROM profit_orders p
                            WHERE p.month = b.month AND p.prefix = b.prefix AND p.order_name = b.order_name)
         GROUP BY b.prefix ORDER BY 2 DESC""", args).fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("luna", help="AAAA-LL")
    ap.add_argument("--prefix", help="doar magazinul ăsta (ex. EST)")
    ap.add_argument("--backup", help="tabela de backup (implicit _bak_po_<lună>)")
    ap.add_argument("--apply", action="store_true", help="scrie; fără el doar numără")
    a = ap.parse_args()
    if not re.fullmatch(r"\d{4}-\d{2}", a.luna):
        sys.exit("luna trebuie să fie AAAA-LL")
    bak = a.backup or "_bak_po_" + a.luna.replace("-", "")
    if not re.fullmatch(r"[A-Za-z0-9_]+", bak):
        sys.exit("nume de tabelă invalid")

    uri = f"file:{DB}?mode={'rw' if a.apply else 'ro'}"
    con = sqlite3.connect(uri, uri=True, timeout=60)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (bak,)).fetchone():
        sys.exit(f"nu există tabela {bak}")
    n_bak = con.execute(f"SELECT COUNT(*) FROM {bak} WHERE month = ?", (a.luna,)).fetchone()[0]
    n_viu = con.execute("SELECT COUNT(*) FROM profit_orders WHERE month = ?", (a.luna,)).fetchone()[0]
    print(f"[{a.luna}] backup {bak}: {n_bak} comenzi · tabela vie: {n_viu} comenzi")

    randuri = lipsa(con, bak, a.luna, a.prefix)
    if not randuri:
        print("nimic de restaurat: tabela vie are tot ce are backup-ul"); return 0
    tot = 0
    print(f"{'prefix':8} {'lipsă':>7} {'venit':>12} {'livrate':>8} {'refuz':>6} {'în curs':>8} {'anulate':>8}  perioada")
    for p, n, venit, liv, ref, curs, anul, de_la, pana_la in randuri:
        tot += n
        print(f"{p:8} {n:7d} {venit or 0:12,.2f} {liv or 0:8d} {ref or 0:6d} {curs or 0:8d} {anul or 0:8d}  {de_la}..{pana_la}")
    print(f"total de adăugat: {tot} comenzi")

    if not a.apply:
        print("DRY-RUN: nimic scris. Rulează cu --apply ca să adaugi rândurile.")
        return 0

    cols = _coloane(con, "profit_orders")
    cols_bak = set(_coloane(con, bak))
    if not set(cols) <= cols_bak:
        sys.exit(f"backup-ul n-are coloanele {sorted(set(cols) - cols_bak)}")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    siguranta = f"{bak}_inainte_restaurare_{ts}"
    cond = " AND b.prefix = ?" if a.prefix else ""
    args = [a.luna] + ([a.prefix] if a.prefix else [])
    lista = ", ".join(cols)
    with con:
        con.execute(f"CREATE TABLE {siguranta} AS SELECT * FROM profit_orders WHERE month = ?", (a.luna,))
        cur = con.execute(f"""
            INSERT INTO profit_orders ({lista})
            SELECT {', '.join('b.' + c for c in cols)} FROM {bak} b
             WHERE b.month = ?{cond}
               AND NOT EXISTS (SELECT 1 FROM profit_orders p
                                WHERE p.month = b.month AND p.prefix = b.prefix AND p.order_name = b.order_name)""",
            args)
        adaugate = cur.rowcount
    n_dupa = con.execute("SELECT COUNT(*) FROM profit_orders WHERE month = ?", (a.luna,)).fetchone()[0]
    print(f"adăugate: {adaugate} · tabela vie: {n_viu} → {n_dupa} · întoarcerea: {siguranta}")
    if n_dupa != n_viu + adaugate:
        print("⚠️ numărătoarea nu se închide — verifică înainte de orice altceva"); return 2
    print("urmează: build_cache.py --table brand_pnl_real --apply (cache.brand_pnl_monthly)")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
