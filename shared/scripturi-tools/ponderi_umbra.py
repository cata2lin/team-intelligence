#!/usr/bin/env python3
"""UMBRĂ pentru S3 pasul 4: ponderile de vânzări din stock-sync, din AWBprint față de Orders. Doar citire.

Ce înseamnă „ponderile” azi: `scripts/sync-sales-stats.ts` din stock-sync (timerul `stock-sync-sales`, 01:15) scrie
`master_store_sales` (master × magazin: units_sold_30d / 90d, source='awbprint') din comenzile LIVRATE din AWBprint.
Alocarea le citește într-un singur loc: RESTUL nealocat al unui master (după procente și rotunjire) merge ÎNTREG la
magazinul cu cele mai multe vânzări pe 30 de zile (apoi 90), cu histerezis TOP_SELLER_HYSTERESIS_UNITS (3) față de
deținătorul de la rularea trecută (packages/domain/src/allocation.ts, distributeRemainderToTopSeller).

Umbra calculează, pe master × magazin, vânzările pe sursa veche (master_store_sales de azi) și pe ruta din Orders
(`GET /api/cs/livrate-pe-sku?zile=90`, legată de master prin listările stock-sync ale magazinului), cota pe 30 de zile,
diferența în puncte și cine ar lua bonusul de rest pe fiecare sursă. Marcaj (regula Orchestratorului, 24-oct): un
master × magazin cu cota mutată peste 5 pp, sau un bonus de rest de peste 20 de bucăți care schimbă magazinul.
NU scrie nimic în stock-sync. Rulează pe serverul stock-sync (baza e locală acolo); cheia Orders e cea a aplicației AWB.

  cd /opt/awb-print/backend && venv/bin/python /root/ponderi_umbra/ponderi_umbra.py
"""
import collections
import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT = os.environ.get("PONDERI_OUT", "/root/ponderi_umbra")
PRAG_PP = 5.0
PRAG_BUC = 20
HISTEREZIS = int(os.environ.get("TOP_SELLER_HYSTERESIS_UNITS", "3"))
PARFUM = re.compile(r"^(\d{1,4}(-\d+ml)?|gt-.*|zn-.*|nub.*|surpriza-.*|est-.*)$", re.I)


def _q(sql):
    r = subprocess.run(["sudo", "-u", "postgres", "psql", "-d", "stock_sync_prod", "-At", "-F", "\t", "-c", sql],
                       capture_output=True, text=True, check=True, cwd="/tmp")
    return [l.split("\t") for l in r.stdout.splitlines() if l]


def _oh():
    sys.path.insert(0, "/opt/awb-print/backend")
    from app.core.config import settings  # cheia cs.citire a aplicației AWB; nu se afișează
    url = settings.order_hub_api_url.rstrip("/") + "/api/cs/livrate-pe-sku?zile=90"
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + settings.order_hub_token})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.load(r)
    if not d.get("ok"):
        raise SystemExit("Orders a refuzat livrate-pe-sku: %s" % str(d)[:200])
    return d["randuri"]


def castigator(eligibile, vanzari, detinator, nume):
    """Magazinul care ia restul: aceeași ordine ca alocarea (30d, 90d, nume) + histerezis față de deținător."""
    if not eligibile:
        return None
    v = lambda s: vanzari.get(s, (0, 0))
    if not any(s in vanzari for s in eligibile):
        return detinator if detinator in eligibile else None   # fără vânzări: alocarea ordonează pe țintă
    rang = sorted(eligibile, key=lambda s: (-v(s)[0], -v(s)[1], nume.get(s, ""), s))
    w = rang[0]
    if detinator and detinator != w and detinator in eligibile and v(w)[0] < v(detinator)[0] + HISTEREZIS:
        return detinator
    return w


def compara(vechi, nou, eligibile, detinator, bonus, nume):
    """Pe un master: rânduri pe magazin (u30/u90 vechi și nou, cotă, pp) și verdictul pe bonusul de rest. Pură."""
    magazine = sorted(set(vechi) | set(nou))
    s_v = sum(x[0] for x in vechi.values()) or 0
    s_n = sum(x[0] for x in nou.values()) or 0
    randuri = []
    for s in magazine:
        a, b = vechi.get(s, (0, 0)), nou.get(s, (0, 0))
        cv = 100.0 * a[0] / s_v if s_v else 0.0
        cn = 100.0 * b[0] / s_n if s_n else 0.0
        randuri.append({"magazin": s, "u30_vechi": a[0], "u30_nou": b[0], "u90_vechi": a[1], "u90_nou": b[1],
                        "cota_vechi": round(cv, 1), "cota_nou": round(cn, 1), "pp": round(cn - cv, 1)})
    top_v = castigator(eligibile, vechi, detinator, nume)
    top_n = castigator(eligibile, nou, detinator, nume)
    mutat = bonus if (top_v != top_n and bonus) else 0
    return randuri, top_v, top_n, mutat


def main():
    stores = {r[0]: (r[1].lower(), r[2], r[3]) for r in _q(
        "select id, coalesce(shop_domain,''), name, coalesce(status::text,'') from stores where deleted_at is null")}
    pe_domeniu = {d: sid for sid, (d, _, _) in stores.items() if d}
    nume = {sid: n for sid, (_, n, _) in stores.items()}
    # Aceeași legare ca sync-sales-stats (resolveSales): SKU-ul magazinului (btrim, cu majuscule) → barcode-urile
    # listărilor lui → masterul cu acel barcode. Un SKU care duce la mastere diferite e ambiguu și se sare.
    master_pe_barcode = {bc: m for m, bc in _q("select id, barcode_normalized from master_products "
                                              "where coalesce(barcode_normalized,'')<>''")}
    bc_pe_sku = collections.defaultdict(set)
    for sid, sku, bc in _q("select store_id, btrim(sku), barcode_normalized from store_listings where deleted_at is null "
                           "and coalesce(btrim(sku),'')<>'' and coalesce(barcode_normalized,'')<>''"):
        bc_pe_sku[(sid, sku)].add(bc)
    listari = {}
    skus_master = collections.defaultdict(set)
    for k, bcs in bc_pe_sku.items():
        mids = {master_pe_barcode[b] for b in bcs if b in master_pe_barcode}
        if len(mids) == 1:
            mid = mids.pop()
            listari[k] = mid
            skus_master[mid].add(k[1].lower())
    vechi = collections.defaultdict(dict)
    for mid, sid, u30, u90, src in _q("select master_product_id, store_id, units_sold_30d, units_sold_90d, source "
                                      "from master_store_sales"):
        vechi[mid][sid] = (int(u30), int(u90))
    nou = collections.defaultdict(dict)
    # rândurile care NU vin din AWBprint (feed manual / agent) rămân la fel în ambele surse
    for mid, sid, u30, u90 in _q("select master_product_id, store_id, units_sold_30d, units_sold_90d "
                                 "from master_store_sales where source <> 'awbprint'"):
        nou[mid][sid] = (int(u30), int(u90))
    nemapate = collections.Counter()
    for r in _oh():
        sid = pe_domeniu.get(str(r.get("magazin") or "").lower())
        mid = listari.get((sid, str(r.get("sku") or "").strip())) if sid else None
        if not mid:
            nemapate["magazin" if not sid else "sku"] += 1
            continue
        a = nou[mid].get(sid, (0, 0))
        nou[mid][sid] = (a[0] + int(r.get("units30") or 0), a[1] + int(r.get("units90") or 0))
    snap = _q("select id from allocation_snapshots where coalesce(total_master_products,0) > 100 "
              "order by created_at desc limit 1")[0][0]
    eligibile, detinator, bonus = collections.defaultdict(set), {}, collections.Counter()
    for mid, sid, rt, top, b in _q(
            "select master_product_id, store_id, rule_type, coalesce(calculation_details->>'topSellerStoreId',''), "
            "coalesce((calculation_details->>'topSellerBonus')::int,0) from allocation_lines "
            "where allocation_snapshot_id = '%s'" % snap):
        if rt == "PERCENTAGE":
            eligibile[mid].add(sid)
        if top:
            detinator[mid] = top
        bonus[mid] = max(bonus[mid], int(b))
    nume_master = {m: n for m, n in _q("select id, coalesce(name,'') from master_products")}
    zi = date.today().isoformat()
    os.makedirs(OUT, exist_ok=True)
    con = sqlite3.connect(os.path.join(OUT, "ponderi_umbra.db"))
    con.execute("""CREATE TABLE IF NOT EXISTS rand (zi TEXT, master TEXT, nume TEXT, magazin TEXT, u30_vechi INT,
        u30_nou INT, u90_vechi INT, u90_nou INT, cota_vechi REAL, cota_nou REAL, pp REAL, top_vechi TEXT, top_nou TEXT,
        bonus INT, mutat INT, parfum INT, marcat INT, PRIMARY KEY (zi, master, magazin))""")
    con.execute("DELETE FROM rand WHERE zi = ?", (zi,))
    perechi = marcate_pp = 0
    marcate, parfum_n, mastere_mutate = [], 0, []
    for mid in sorted(set(vechi) | set(nou)):
        randuri, tv, tn, mutat = compara(vechi.get(mid, {}), nou.get(mid, {}), eligibile.get(mid, set()),
                                         detinator.get(mid), bonus.get(mid, 0), nume)
        parf = any(PARFUM.match(s) for s in skus_master.get(mid, ()))
        parfum_n += 1 if parf else 0
        if mutat:
            mastere_mutate.append((mid, mutat))
        for r in randuri:
            perechi += 1
            m = abs(r["pp"]) > PRAG_PP or (mutat > PRAG_BUC and r["magazin"] in (tv, tn))
            marcate_pp += 1 if abs(r["pp"]) > PRAG_PP else 0
            if m:
                marcate.append((parf, nume_master.get(mid, mid)[:40], nume.get(r["magazin"], r["magazin"]), r, mutat))
            con.execute("INSERT INTO rand VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (zi, mid, nume_master.get(mid, ""), r["magazin"], r["u30_vechi"], r["u30_nou"], r["u90_vechi"],
                         r["u90_nou"], r["cota_vechi"], r["cota_nou"], r["pp"], tv, tn, bonus.get(mid, 0), mutat,
                         int(parf), int(m)))
    con.commit(); con.close()
    mari = [x for x in marcate if not x[0] and max(x[3]["u30_vechi"], x[3]["u30_nou"]) >= 10]
    linii = ["# Umbra ponderilor (S3 pasul 4) — %s" % zi, "",
             "Sursa veche: master_store_sales (AWBprint, 01:15). Sursa nouă: Orders /api/cs/livrate-pe-sku?zile=90. "
             "Snapshot de alocare: %s, histerezis %d." % (snap, HISTEREZIS), "",
             "- master × magazin comparate: %d (mastere: %d, din care cu parfum: %d)" % (perechi, len(set(vechi) | set(nou)), parfum_n),
             "- rânduri Orders nelegate: %s" % dict(nemapate),
             "- master × magazin cu cota mutată > %.0f pp: %d" % (PRAG_PP, marcate_pp),
             "- mastere al căror bonus de rest ar schimba magazinul: %d, din care > %d buc: %d (%d buc în total)"
             % (len(mastere_mutate), PRAG_BUC, sum(1 for _, b in mastere_mutate if b > PRAG_BUC),
                sum(b for _, b in mastere_mutate)),
             "- marcate fără parfumuri și cu ≥ 10 buc/30 zile pe o parte: %d" % len(mari), "",
             "| Master | Magazin | 30z vechi → nou | Cotă vechi → nou | pp | bonus mutat |", "|---|---|--:|--:|--:|--:|"]
    for parf, nm, mag, r, mutat in sorted(mari, key=lambda x: (-x[4], -abs(x[3]["pp"])))[:60]:
        linii.append("| %s | %s | %d → %d | %.1f → %.1f | %+.1f | %s |" % (
            nm, mag, r["u30_vechi"], r["u30_nou"], r["cota_vechi"], r["cota_nou"], r["pp"], mutat or ""))
    open(os.path.join(OUT, zi + ".md"), "w").write("\n".join(linii) + "\n")
    print("\n".join(linii[:12]))


if __name__ == "__main__":
    main()
