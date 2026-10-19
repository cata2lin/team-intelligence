"""Citiri din bazele aplicației de scanare (AWB Arona), pe serverul de scripturi. DOAR CITIRE.

Rulează pe server (trimis pe stdin de mcp_server / depozit.py: `cd /root/Scripturi && .venv/bin/python - <sub> ...`).
Fiecare bază se deschide cu `mode=ro` — nu poate scrie nimic, nici din greșeală.

  neculese  — etichete PRINTATE (intrate într-un lot pe scanner) și NECULESE după N ore, încă neplecate la curier
  produs    — istoricul unui SKU pe rafturi (pus/scos/cules/mutat), cu sinonimele lui
  anomalii  — coduri scanate negăsite, AWB-uri culese de doi oameni, „unpicked", culegeri fără comandă
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Bucharest")
except Exception:  # pragma: no cover
    TZ = None

DATA = os.environ.get("DEPOZIT_DATA", "data")
STATII_FIZICE = ("Bartolomeu", "Uzina 2", "Parfumuri")
# oh_awbs.status: ce înseamnă „încă la noi" (eticheta făcută, curierul n-a preluat-o)
LA_NOI = (None, "", "processed")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _ro(nume):
    return sqlite3.connect("file:%s/%s?mode=ro" % (DATA, nume), uri=True, timeout=20)


def _acum():
    return datetime.now(TZ) if TZ else datetime.now()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def statie_fizica(statie_lot, statii_oh):
    """Stația FIZICĂ a unei etichete: cea din lot dacă e fizică, altfel cea din oh_colete (lotul poate fi un
    pseudo-grup ca „Neplecate"/„Artevita"). Mai multe stații în OH = colet împărțit."""
    if statie_lot in STATII_FIZICE:
        return statie_lot
    st = sorted(s for s in (statii_oh or ()) if s)
    return " + ".join(st) if st else (statie_lot or "?")


def cmd_neculese(a):
    acum = _acum()
    prag = _iso(acum - timedelta(hours=a.ore))
    de_la = _iso(acum - timedelta(days=a.zile))
    lot = _ro("loturi.db")
    # ultima apariție a fiecărui AWB într-un lot (o reprintare îl pune într-un lot nou)
    rows = lot.execute(
        "SELECT la.awb, la.comanda, l.lot, l.statie, l.creat_la, l.alocat_lui FROM lot_awb la "
        "JOIN loturi l ON l.lot = la.lot WHERE l.creat_la >= ? AND la.awb IS NOT NULL AND la.awb <> '' "
        "ORDER BY l.creat_la", (de_la,)).fetchall()
    lot.close()
    ultim = {}
    for awb, cmd, cod, st, creat, aloc in rows:
        ultim[awb] = (cmd, cod, st, creat, aloc)
    ev = _ro("scan_events.db")
    culese_awb, culese_cmd = set(), set()
    for awb, cmd in ev.execute("SELECT awb, order_number FROM scan_events WHERE action = 'picked' AND created_at >= ?",
                               (de_la,)):
        if awb:
            culese_awb.add(awb)
        elif cmd:
            culese_cmd.add(cmd)
    ev.close()
    oh = _ro("oh_mirror.db")
    stare, statii_oh, anulate = {}, defaultdict(set), set()
    awbs = list(ultim)
    for i in range(0, len(awbs), 900):
        felie = awbs[i:i + 900]
        semne = ",".join("?" * len(felie))
        for awb, st in oh.execute("SELECT awb, status FROM oh_awbs WHERE awb IN (%s)" % semne, felie):
            stare[awb] = st
        for awb, st in oh.execute("SELECT awb, statie FROM oh_colete WHERE awb IN (%s)" % semne, felie):
            statii_oh[awb].add(st)
    comenzi = sorted({v[0] for v in ultim.values() if v[0]})
    for i in range(0, len(comenzi), 900):
        felie = comenzi[i:i + 900]
        for (cmd,) in oh.execute("SELECT order_name FROM oh_orders WHERE cancelled = 1 AND order_name IN (%s)"
                                 % ",".join("?" * len(felie)), felie):
            anulate.add(cmd)
    oh.close()

    rez = []
    plecate = 0
    for awb, (cmd, cod, st, creat, aloc) in ultim.items():
        if creat > prag or awb in culese_awb or (cmd and cmd in culese_cmd):
            continue
        # eticheta trebuie să fie ÎNCĂ vie în OH: lipsă din oh_awbs = anulată/înlocuită (măsurat 13-oct: void în OH,
        # lotul de pe scaner a rămas); comanda anulată; sau curierul a preluat-o deja
        if awb not in stare or stare[awb] not in LA_NOI or (cmd and cmd in anulate):
            plecate += 1
            continue
        sf = statie_fizica(st, statii_oh.get(awb))
        if a.statie and a.statie.lower() not in sf.lower():
            continue
        rez.append((creat, awb, cmd, cod, sf, aloc, stare.get(awb)))
    rez.sort()
    print("Etichete PRINTATE (intrate într-un lot pe scanner) și NECULESE de peste %d h, încă la noi "
          "(Order Hub: fără preluare la curier) — fereastră %d zile%s." % (a.ore, a.zile,
                                                                         ", stația " + a.statie if a.statie else ""))
    print("Total: %d (alte %d neculese în aplicație dar cu eticheta anulată/înlocuită, comanda anulată sau deja preluate de curier — sărite)." % (len(rez), plecate))
    if not rez:
        return
    pe_statie = Counter(r[4] for r in rez)
    print("Pe stație: " + " · ".join("%s %d" % kv for kv in pe_statie.most_common()))
    pe_lot = Counter((r[3], r[4]) for r in rez)
    print("\nLoturile cu cele mai multe neculese:")
    for (cod, sf), n in pe_lot.most_common(a.top):
        print("  %-18s %-12s %4d" % (cod, sf, n))
    print("\nCele mai vechi (data lotului · AWB · comanda · stația · lot · alocat):")
    for creat, awb, cmd, cod, sf, aloc, s in rez[:a.top]:
        print("  %s  %s  %-12s %-12s %s  %s" % (creat[:16].replace("T", " "), awb, cmd or "-", sf, cod, aloc or "-"))
    print("\n⚠ Stare din oglinda Order Hub de pe scaner (oh_mirror, sincronizată la ~10 min). Înainte de orice acțiune pe "
          "o comandă, verifică fișa: arona-orderhub oh_comanda.")


def cmd_anomalii(a):
    acum = _acum()
    de_la = _iso(acum - timedelta(days=a.zile))
    ev = _ro("scan_events.db")
    neg = ev.execute("SELECT code, COUNT(*), MAX(created_at) FROM scan_lookups WHERE found = 0 AND created_at >= ? "
                     "GROUP BY code ORDER BY 2 DESC LIMIT ?", (de_la, a.top)).fetchall()
    tot = ev.execute("SELECT COUNT(*), SUM(found = 0) FROM scan_lookups WHERE created_at >= ?", (de_la,)).fetchone()
    culegeri = ev.execute("SELECT awb, order_number, username, created_at FROM scan_events WHERE action = 'picked' "
                          "AND created_at >= ?", (de_la,)).fetchall()
    unp = ev.execute("SELECT username, COUNT(*) FROM scan_events WHERE action = 'unpicked' AND created_at >= ? "
                     "GROUP BY 1 ORDER BY 2 DESC", (de_la,)).fetchall()
    ev.close()
    print("Anomalii de scanare — ultimele %d zile (de la %s)." % (a.zile, de_la[:16].replace("T", " ")))
    print("\n1. Coduri scanate NEGĂSITE: %d din %d căutări." % (tot[1] or 0, tot[0] or 0))
    for code, n, ult in neg:
        print("  %-28s %3d×  ultima %s" % (code, n, (ult or "")[:16].replace("T", " ")))
    pe_awb = defaultdict(set)
    fara_cmd = 0
    for awb, cmd, user, _ in culegeri:
        if awb:
            pe_awb[awb].add(user or "?")
        if not cmd:
            fara_cmd += 1
    dubluri = sorted((awb, us) for awb, us in pe_awb.items() if len(us) > 1)
    print("\n2. AWB-uri culese de DOI sau mai mulți oameni: %d" % len(dubluri))
    for awb, us in dubluri[:a.top]:
        print("  %s  %s" % (awb, ", ".join(sorted(us))))
    print("\n3. „Unpicked” (cules anulat) pe om: " + (" · ".join("%s %d" % (u or "?", n) for u, n in unp) or "niciunul"))
    print("\n4. Culegeri fără număr de comandă: %d din %d" % (fara_cmd, len(culegeri)))


def cmd_produs(a):
    """Istoricul unui SKU pe rafturi (shelf_events — jurnalul viu; pick_items s-a oprit în sep-2026)."""
    k = a.sku.strip().lower()
    loc = _ro("locations.db")
    alias = [r[0] for r in loc.execute("SELECT alias FROM sku_aliases WHERE lower(canonical) = ?", (k,))]
    chei = [k] + [x.lower() for x in alias]
    semne = ",".join("?" * len(chei))
    de_la = _iso(_acum() - timedelta(days=a.zile))
    sumar = loc.execute("SELECT action, COUNT(*), SUM(ABS(COALESCE(qty_before, 0) - COALESCE(qty, 0))) FROM shelf_events "
                        "WHERE lower(sku) IN (%s) AND ts >= ? GROUP BY 1 ORDER BY 2 DESC" % semne, chei + [de_la]).fetchall()
    ev = loc.execute("SELECT ts, action, code, name, qty_before, qty, username, order_ref FROM shelf_events "
                     "WHERE lower(sku) IN (%s) ORDER BY id DESC LIMIT ?" % semne, chei + [a.top]).fetchall()
    st = dict(loc.execute("SELECT code, station FROM shelves").fetchall())
    loc.close()
    print("Istoric rafturi %s%s — ultimele %d zile pe acțiune: %s" % (
        a.sku, (" (sinonime: %s)" % ", ".join(alias)) if alias else "", a.zile,
        " · ".join("%s %d" % (x, n) for x, n, _ in sumar) or "nimic"))
    for ts, act, code, name, qb, q, u, o in ev:
        print("  %s %-8s %-11s %-18s %s→%s %s%s" % (ts[:16].replace("T", " "), act, st.get(code) or "?", name or code,
                                                   "" if qb is None else qb, "" if q is None else q, u or "",
                                                   ("  " + o) if o else ""))


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("neculese")
    x.add_argument("--ore", type=int, default=24)
    x.add_argument("--zile", type=int, default=7)
    x.add_argument("--statie", default="")
    x.add_argument("--top", type=int, default=30)
    x = sub.add_parser("anomalii")
    x.add_argument("--zile", type=int, default=1)
    x.add_argument("--top", type=int, default=20)
    x = sub.add_parser("produs")
    x.add_argument("--sku", required=True)
    x.add_argument("--zile", type=int, default=30)
    x.add_argument("--top", type=int, default=15)
    a = p.parse_args(argv)
    {"neculese": cmd_neculese, "anomalii": cmd_anomalii, "produs": cmd_produs}[a.cmd](a)


if __name__ == "__main__":
    main()
