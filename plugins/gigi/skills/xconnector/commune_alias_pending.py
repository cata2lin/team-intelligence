#!/usr/bin/env python3
"""Worklist pentru extinderea `RO_DPD_LOCALITY_ALIAS` (oraș-comună → sat livrabil DPD).

DE CE: un oraș/comună mică nu-i indexat de DPD sub numele scris de client → AWB-ul pică pe localitate.
Satul corect NU e auto-derivabil (comuna are multe sate livrabile, satele rurale n-au străzi în nomenclator),
dar se rezolvă O SINGURĂ DATĂ per oraș: CS confirmă satul → intră în tabel → toate comenzile viitoare din orașul
ăla se corectează singure. Scriptul ăsta strânge orașele care AU PICAT (din `.awb_events.jsonl`, `result=miss`) +
listează satele candidate din SIRUTA (probate LIVE pe DPD) → linie gata de pus în tabel.

MOD 1 (default): scanează log-ul de miss → worklist orașe de mapat.
MOD 2 (`--city "Baile Olanesti" --judet Valcea`): candidați pt UN oraș (ajută CS să aleagă).

Rulează din folderul skill-ului: `python3 commune_alias_pending.py` sau cu `--city/--judet`.
"""
import sys, os, json, argparse, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xconnector as xc


def deliverable_villages(city, judet):
    """Satele (niv 3) ale comunei/orașului `city` din SIRUTA care sunt LIVRABILE pe DPD (findSite → postCode).
    Întoarce [(nume_sat, zip_dpd), ...]. Gol dacă orașul nu-i în SIRUTA sau n-are sate livrabile."""
    mc = xc.metrics_cursor_live()
    cn, jn = xc._fold(city).lower(), xc._fold(judet).lower()
    mc.execute("select cod_siruta, niv, sirsup from public.romania_siruta "
               "where localitate_norm=%s and judet_norm=%s order by niv limit 5", (cn, jn))
    rows = mc.fetchall()
    parent = None
    for cod, niv, sup in rows:
        if niv == 3:
            parent = sup; break
    if parent is None:
        for cod, niv, sup in rows:
            if niv == 2:
                parent = cod; break
    if parent is None:
        return []
    mc.execute("select denumire from public.romania_siruta where sirsup=%s and niv=3", (parent,))
    kids = [r[0] for r in mc.fetchall()]
    out = []
    for den in kids:
        site, _ = xc.dpd_site_by_city(642, den, judet, None)
        if site:
            out.append((site.get("name"), site.get("postCode")))
        time.sleep(0.2)
    return out


def scan_missed():
    """Orașe care au picat pe localitate (din event-log), grupate + count. Cheie (city, region)."""
    log = xc.AWB_EVENT_LOG
    agg = {}
    if not os.path.exists(log):
        return agg
    known = set(xc.RO_DPD_LOCALITY_ALIAS.keys())
    for line in open(log, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("kind") == "zip-city-fill" and r.get("result") == "miss":
            city = (r.get("city") or "").strip()
            reg = (r.get("region") or "").strip()
            if not city:
                continue
            if (xc._fold(city), xc._fold(reg)) in known:
                continue   # deja mapat
            k = (city, reg)
            agg.setdefault(k, {"n": 0, "orders": set()})
            agg[k]["n"] += 1
            if r.get("order"):
                agg[k]["orders"].add(r["order"])
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city"); ap.add_argument("--judet")
    a = ap.parse_args()
    if a.city:
        vs = deliverable_villages(a.city, a.judet or "")
        print("Sate livrabile DPD pt %r (%s):" % (a.city, a.judet or "?"))
        for name, zc in vs:
            print("   %-24s %s" % (name, zc))
        print("\n→ după ce CS alege satul X, adaugă în RO_DPD_LOCALITY_ALIAS:")
        print('   ("%s", "%s"): "X",' % (xc._fold(a.city), xc._fold(a.judet or "")))
        return
    agg = scan_missed()
    if not agg:
        print("Niciun oraș-comună nemapat în log (`zip-city-fill result=miss`). Backlog gol ✅")
        print("(se populează pe măsură ce pică comenzi din orașe noi neindexate de DPD)")
        return
    print("ORAȘE de mapat (au picat pe localitate, nu-s încă în tabel) — CS confirmă satul:\n")
    for (city, reg), v in sorted(agg.items(), key=lambda x: -x[1]["n"]):
        print("═" * 70)
        print("  %s / %s   — %d comenzi picate: %s" % (city, reg, v["n"], ", ".join(sorted(v["orders"])[:6])))
        vs = deliverable_villages(city, reg)
        if vs:
            print("   sate livrabile DPD (alege UNUL):")
            for name, zc in vs:
                print("      %-24s %s" % (name, zc))
        else:
            print("   (SIRUTA n-are sate pt orașul ăsta — verifică numele/județul)")
        print('   → RO_DPD_LOCALITY_ALIAS:  ("%s", "%s"): "???",' % (xc._fold(city), xc._fold(reg)))


if __name__ == "__main__":
    main()
