#!/usr/bin/env python3
"""Gardă: comenzi care există în Order Hub, dar lipsesc din `profit_orders` (motorul de profit).

De ce (24-oct-2026): pe 13-oct, tokenul Bonhaus HU și SK a dat 401 (aplicația dezinstalată), descărcarea a ieșit goală,
iar re-sincronizarea lunii a șters rândurile lor fără nicio alarmă (sep: 79.903 → 79.376, „✅ OK”). PL a urmat pe
15-oct. Septembrie–octombrie au rămas fără HU / PL / SK în P&L zece zile, până le-a observat un om.

Regula: pe fiecare lună din fereastră, numele de comenzi din OH (`/api/cs/livrabilitate`) se compară cu
`profit_orders.order_name`; pe fiecare prefix de comandă (literele de la început), dacă OH are ≥ PRAG_MIN comenzi și
peste PRAG_LIPSA% lipsesc din profit_orders → CRIT. Prefixele care NU intră în motor prin design (marketplace-uri) sunt
excluse explicit, cu motivul lângă.

  garda_oh_profit_orders.py [--luni 3]            # raport; iese cu 1 dacă e ceva roșu
Folosit și ca funcție: `verifica(luni)` → [(stare, cheie, detaliu)], forma din data_health.
"""
import argparse
import collections
import re
import sqlite3
import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "/root/Scripturi")

DB = "/root/Scripturi/data/profitability.db"
PRAG_MIN = 20
PRAG_LIPSA = 50.0
# prefixe de comandă care nu intră în motorul de profit. Marketplace-urile (EMAG, TY, OLXT, …) intră din 24-oct prin
# `marketplace_profit_sync.py`, deci garda le cere acum ca pe orice magazin.
EXCLUSE = {
    "BUCSA": "Portal parteneri (total 0 în Orders, marfa se facturează în afara lui)",
    "PROBA": "comandă de probă din Portal parteneri",
}
_PREFIX = re.compile(r"^#?([A-Za-z]+)")
# Comenzile Duppo HU și Duppo CZ au fost recreate în Duppo EU (fostul Duppo BG), cu eticheta `orig-<comanda veche>`:
# sunt în profit_orders sub DUPBG, nu lipsesc (ownerul, 24-oct).
_ORIG = re.compile(r"orig-([A-Za-z]+\d+)", re.I)


def _luni(n):
    y, m = date.today().year, date.today().month
    out = []
    for _ in range(n):
        out.append("%04d-%02d" % (y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def compara(nume_oh, nume_po):
    """{prefix: (în OH, lipsă din profit_orders)} — funcție pură (testată). Nume comparate fără majuscule."""
    po = {str(n).upper() for n in nume_po}
    out = collections.defaultdict(lambda: [0, 0])
    for n in nume_oh:
        m = _PREFIX.match(n or "")
        p = m.group(1).upper() if m else "?"
        out[p][0] += 1
        if str(n).upper() not in po:
            out[p][1] += 1
    return {p: tuple(v) for p, v in out.items()}


def verifica(luni=3):
    from core import status_oh
    rows = []
    cx = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    try:
        for luna in _luni(luni):
            oh = status_oh.pe_comenzi(luna)
            if not oh:
                rows.append(("WARN", "oh_vs_profit.%s" % luna, "Order Hub n-a dat comenzile lunii (cheie? rețea?)"))
                continue
            po = []
            for nume, tags in cx.execute("SELECT order_name, tags FROM profit_orders WHERE month = ?", (luna,)):
                po.append(nume)
                po += _ORIG.findall(tags or "")    # comenzi migrate (Duppo HU / CZ → Duppo EU): `orig-DPHU1001`
            rosii = []
            for p, (tot, lipsa) in sorted(compara(oh.keys(), po).items()):
                if p in EXCLUSE or tot < PRAG_MIN:
                    continue
                if 100.0 * lipsa / tot > PRAG_LIPSA:
                    rosii.append("%s %d/%d lipsă" % (p, lipsa, tot))
            rows.append(("CRIT" if rosii else "OK", "oh_vs_profit.%s" % luna,
                         ("prefixe cu comenzi în OH și lipsă din profit_orders: " + ", ".join(rosii)) if rosii
                         else "toate prefixele din OH au comenzile în profit_orders"))
    finally:
        cx.close()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--luni", type=int, default=3)
    a = ap.parse_args()
    rows = verifica(a.luni)
    for st, k, d in rows:
        print("%-4s %-22s %s" % (st, k, d))
    rosu = [r for r in rows if r[0] == "CRIT"]
    if rosu:
        try:
            import briefing_alert
            briefing_alert.scrie("Gardă OH vs profit_orders: comenzi lipsă din P&L",
                                 "\n".join("%s — %s" % (k, d) for _, k, d in rosu))
        except Exception:
            pass
    return 1 if rosu else 0


if __name__ == "__main__":
    sys.exit(main())
