# -*- coding: utf-8 -*-
"""
stoc_live.py — stocul unui produs / al unei stații, citit LIVE din stock-sync + rafturile AWB Arona.

Rulează PE serverul de scripturi (are nevoie de api/scan.py pentru stock-sync și de data/locations.db).
De pe laptop, MCP-ul arona-stock îl trimite prin ssh pe stdin (`.venv/bin/python - <subcomanda>`),
deci NU trebuie copiat pe server.

  produs --sku HA-0000          bazin stock-sync (total + per depozit + numărat/necontat), per magazin
                                (unități, per depozit, regula), listinguri nelegate (MISSING_BARCODE…)
                                și rafturile fizice per stație — cele trei cifre puse una lângă alta
  statie --statie "Uzina 2"     ce e pe rafturile unei stații: total, nr SKU, cele mai mari poziții
  erori [--cod RECONCILE_…]     coada de erori DESCHISE din stock-sync (bazine înghețate etc.)

DOAR CITIRE: doar GET pe stock-sync și locations.db deschis read-only.
"""
import argparse
import collections
import sqlite3
import sys
import urllib.parse

sys.path.insert(0, "/root/Scripturi")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from api.scan import _ss_get  # noqa: E402

LOC_DB = "file:/root/Scripturi/data/locations.db?mode=ro"
STATII = {"bartolomeu": "Bartolomeu", "uzina 2": "Uzina 2", "uzina2": "Uzina 2", "uzina_2": "Uzina 2",
          "teliu": "Teliu", "parfumuri": "Parfumuri"}


def _q(s):
    return urllib.parse.quote(str(s), safe="")


def _db():
    return sqlite3.connect(LOC_DB, uri=True)


def _magazine():
    d = _ss_get("/v1/stores") or {}
    return {s["id"]: s for s in d.get("stores") or []}


def _canonic(c, sku):
    """sku + sinonimele lui din sku_aliases (ambele sensuri), toate lower."""
    s = sku.lower()
    tot = {s}
    for a, k in c.execute("SELECT alias, canonical FROM sku_aliases WHERE lower(alias)=? OR lower(canonical)=?", (s, s)):
        tot |= {a.lower(), k.lower()}
    return tot


def rafturi(sku):
    c = _db()
    skus = _canonic(c, sku)
    ph = ",".join("?" * len(skus))
    rows = c.execute(
        "SELECT si.code, COALESCE(s.station,'(raft necunoscut)'), si.qty, si.sku FROM shelf_items si "
        "LEFT JOIN shelves s ON s.code=si.code WHERE lower(si.sku) IN (%s) AND si.qty<>0" % ph, sorted(skus)).fetchall()
    c.close()
    return skus, rows


def cmd_produs(a):
    sku = a.sku.strip()
    st = _magazine()
    L = []
    cur = None
    for _ in range(20):
        u = "/v1/listings?limit=100&search=" + _q(sku) + (("&cursor=" + _q(cur)) if cur else "")
        d = _ss_get(u) or {}
        L += d.get("listings") or []
        cur = d.get("nextCursor")
        if not cur:
            break
    ex = [l for l in L if (l.get("sku") or "").lower() == sku.lower()]
    alte = sorted({l.get("sku") for l in L if l not in ex and l.get("sku")})
    print("SKU %s — %d listinguri în stock-sync (potrivire exactă)%s"
          % (sku, len(ex), ("; alte SKU-uri care conțin textul: " + ", ".join(alte[:12])) if alte else ""))

    bcs = sorted({l["barcodeNormalized"] for l in ex if l.get("barcodeNormalized")})
    pe_magazin_listate = set()
    for bc in bcs:
        d = (_ss_get("/v1/stock/" + _q(bc)) or {}).get("product")
        if not d:
            print("\n[barcode %s] fără master în stock-sync (sau eroare de citire)" % bc)
            continue
        bw = ", ".join("%s %d" % (w.get("warehouse") or "fără depozit", w.get("units") or 0) for w in d.get("byWarehouse") or [])
        print("\n[barcode %s] MASTER: %s · %s · total %s buc · siguranță %s"
              % (bc, d.get("name"), d.get("status"), d.get("totalUnits"), d.get("safetyStock")))
        print("  pe depozite: %s" % (bw or "-"))
        cw = d.get("countedWarehouses") or []
        print("  NUMĂRAT în AWB Arona: %s%s" % (", ".join(cw) if cw else "NU (stocul vine din magazine)",
              "  → cifra pusă în Shopify e ignorată; marfa se pune pe raft" if cw else ""))
        print("  ultima alocare: %s · văzut: %s" % (d.get("deliveredAt"), d.get("lastSeenAt")))
        print("  %-22s %7s %7s  %-38s %s" % ("magazin", "are", "alocat", "per depozit (unități)", "regula"))
        for s in sorted(d.get("stores") or [], key=lambda x: -(x.get("currentUnits") or 0)):
            pe_magazin_listate.add(s.get("storeId"))
            dep = " ".join("%s:%d" % ((w.get("warehouse") or "fără")[:4], w.get("units") or 0) for w in s.get("byWarehouse") or [])
            r = s.get("rule") or {}
            rg = "%s %s" % (r.get("scope") or "", ("%.2f%%" % r["percent"]) if r.get("percent") is not None
                            else ("flat %s" % r.get("flatQuantity") if r.get("flatQuantity") is not None else (r.get("type") or "")))
            print("  %-22s %7s %7s  %-38s %s" % ((s.get("storeName") or "?")[:22], s.get("currentUnits"),
                  s.get("allocatedUnits"), dep[:38] or "(nu expune niciun depozit)", rg.strip()))

    neleg = [l for l in ex if l.get("matchStatus") != "MATCHED"]
    if neleg:
        print("\nListinguri NElegate (nu primesc stoc din bazin):")
        for l in neleg:
            print("  %-22s %-24s %-8s tracked=%s barcode=%s" % ((st.get(l.get("storeId"), {}).get("name") or l.get("storeId"))[:22],
                  l.get("matchStatus"), l.get("listingStatus"), l.get("tracked"), l.get("barcodeNormalized") or "-"))
    if not ex:
        print("  (niciun listing cu SKU-ul exact — verifică scrierea sau caută după barcode)")

    skus, rows = rafturi(sku)
    print("\nRAFTURI AWB Arona (fizic%s):" % ((", cu sinonime " + "/".join(sorted(skus - {sku.lower()}))) if len(skus) > 1 else ""))
    if not rows:
        print("  nimic pe raft")
    else:
        pe = collections.Counter()
        for code, stn, q, _s in rows:
            if not code.upper().startswith("LOC-REBUT"):
                pe[stn] += q
        for stn, q in pe.most_common():
            print("  %-26s %6d buc" % (stn, q))
        for code, stn, q, s_ in sorted(rows, key=lambda r: -r[2])[:a.top]:
            print("    %-26s %-14s %6d  %s" % (code, stn[:14], q, s_))
        reb = sum(q for code, _, q, _ in rows if code.upper().startswith("LOC-REBUT"))
        if reb:
            print("  (rebut, în afara vânzării: %d buc)" % reb)


def cmd_statie(a):
    stn = STATII.get(a.statie.strip().lower(), a.statie.strip())
    c = _db()
    cond, par = "s.station=?", [stn]
    if a.sku_prefix:
        cond += " AND lower(si.sku) LIKE ?"
        par.append(a.sku_prefix.lower() + "%")
    tot = c.execute("SELECT COUNT(DISTINCT lower(si.sku)), COALESCE(SUM(si.qty),0), COUNT(DISTINCT si.code) FROM shelf_items si "
                    "JOIN shelves s ON s.code=si.code WHERE %s AND si.qty>0" % cond, par).fetchone()
    if not tot[2]:
        ok = [r[0] for r in c.execute("SELECT DISTINCT station FROM shelves WHERE station IS NOT NULL")]
        print("nimic pe stația '%s'. Stații cunoscute: %s" % (stn, ", ".join(sorted(ok))))
        return
    print("Stația %s%s: %d SKU · %d buc · %d rafturi cu marfă"
          % (stn, (" (SKU %s*)" % a.sku_prefix) if a.sku_prefix else "", tot[0], tot[1], tot[2]))
    print("\nPe bucket-uri fără raft (LOC-*):")
    for code, q, n in c.execute("SELECT si.code, SUM(si.qty), COUNT(*) FROM shelf_items si JOIN shelves s ON s.code=si.code "
                                "WHERE %s AND si.qty>0 AND si.code LIKE 'LOC-%%' GROUP BY 1 ORDER BY 2 DESC" % cond, par):
        print("  %-28s %8d buc · %d SKU" % (code, q, n))
    print("\nTop %d SKU:" % a.top)
    for sku, q, n in c.execute("SELECT lower(si.sku), SUM(si.qty), COUNT(*) FROM shelf_items si JOIN shelves s ON s.code=si.code "
                               "WHERE %s AND si.qty>0 GROUP BY 1 ORDER BY 2 DESC LIMIT ?" % cond, par + [a.top]):
        print("  %-24s %8d buc pe %d poziții" % (sku, q, n))
    neg = c.execute("SELECT si.code, si.sku, si.qty FROM shelf_items si JOIN shelves s ON s.code=si.code "
                    "WHERE %s AND si.qty<0 ORDER BY si.qty LIMIT 10" % cond, par).fetchall()
    if neg:
        print("\n⚠ poziții NEGATIVE (de verificat): " + ", ".join("%s %s %d" % r for r in neg))
    c.close()


def cmd_erori(a):
    baza = "/v1/errors?limit=100" + (("&code=" + _q(a.cod)) if a.cod else "") + (("&severity=" + _q(a.severitate)) if a.severitate else "")
    E, cur, plin = [], None, True
    for _ in range(30):
        d = _ss_get(baza + (("&cursor=" + _q(cur)) if cur else ""))
        if d is None:
            if not E:
                print("n-am putut citi /v1/errors")
                return
            plin = False
            break
        E += d.get("errors") or []
        cur = d.get("nextCursor")
        if not cur:
            break
    else:
        plin = False
    st = _magazine()
    c = collections.Counter(e.get("code") for e in E)
    print("%d erori DESCHISE în stock-sync%s%s:" % (len(E), (" cu codul " + a.cod) if a.cod else "",
          "" if plin else " (listă trunchiată)"))
    for k, n in c.most_common():
        print("  %-40s %4d" % (k, n))
    E.sort(key=lambda e: e.get("lastSeenAt") or "", reverse=True)
    print("\nCele mai recente %d (după lastSeenAt):" % min(a.top, len(E)))
    for e in E[:a.top]:
        print("  %-8s %-34s %-16s x%-4s %s · %s — %s" % (e.get("severity"), (e.get("code") or "")[:34],
              (st.get(e.get("storeId"), {}).get("name") or "-")[:16], e.get("occurrenceCount"),
              (e.get("lastSeenAt") or "")[:16], (e.get("title") or "")[:50], (e.get("detail") or "")[:90]))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("produs")
    p.add_argument("--sku", required=True)
    p.add_argument("--top", type=int, default=12)
    p = sub.add_parser("statie")
    p.add_argument("--statie", required=True)
    p.add_argument("--sku-prefix", default="")
    p.add_argument("--top", type=int, default=25)
    p = sub.add_parser("erori")
    p.add_argument("--cod", default="")
    p.add_argument("--severitate", default="")
    p.add_argument("--top", type=int, default=25)
    a = ap.parse_args()
    {"produs": cmd_produs, "statie": cmd_statie, "erori": cmd_erori}[a.cmd](a)


if __name__ == "__main__":
    main()
