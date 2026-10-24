#!/usr/bin/env python3
"""Comenzile din marketplace-uri (eMAG, Trendyol, OLX, Okazii, Pepita) intră în `profit_orders`, pe prefixe proprii,
ca motorul de profit și `cache.brand_pnl_monthly` să le vadă alături de magazine.

De ce (Orchestrator, 24-oct-2026): marketplace-urile n-au token Shopify, deci sincronizarea motorului nu le-a adus
niciodată; P&L-ul lor stătea doar în `trendyol_profitability.py` (Trendyol) sau nicăieri (eMAG, OLX). Sursa aici e
Orders (`GET /api/cs/livrabilitate?cu_linii=true`): starea din colete (sau starea finală la importul istoric), totalul,
moneda, țara, transportul real și liniile. Comisionul marketplace-ului merge pe comandă, în `profit_orders.comision`
(lei, fără TVA), cu sursa lângă (`comision_sursa`):
  - Trendyol: REAL, din decontările Trendyol (`commissionAmount`, vitrinele RO / BG / GR); comenzile încă nedecontate
    primesc cota reală medie din fereastră → `estimat`;
  - eMAG: regula de preț (17% din prețul cu TVA) → `regula`; Pepita 15% → `regula`; OLX / Okazii 17% → `estimat`.
Transportul îl ia motorul din Orders, pe comandă; comenzile istorice Trendyol (fără colete) primesc media facturilor de
transport Trendyol pe colet, scrisă ca tarif în `profit_transport_costs`. Marketing 0: nu există reclame pe ele.

Portal parteneri NU intră: comenzile au total 0 în Orders (marfa se facturează în afara lui), deci ar ieși doar COGS.

  marketplace_profit_sync.py [--luni 6] [--aplica]     # fără --aplica = probă, nu scrie
"""
import argparse
import collections
import datetime as dt
import json
import sqlite3
import sys
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "/root/Scripturi")

DB = "/root/Scripturi/data/profitability.db"
OH = "https://orderhub.arona.ro"
MARCAJ = "marketplace-sync"
BAZA = {"emag": "EMAG", "trendyol": "TY", "olx": "OLX", "okazii": "OKZ", "pepita": "PEP", "cel": "CEL"}
SUFIX_TARA = {"Bulgaria": "BG", "Hungary": "HU", "Greece": "GR"}
SUFIX_MONEDA = {"HUF": "HU"}
O_SINGURA_MONEDA = {"olx", "okazii", "pepita", "cel"}   # RON prin definiție (Orders, #32)
COMISION_REGULA = {"emag": (0.17, "regula"), "pepita": (0.15, "regula"), "olx": (0.17, "estimat"),
                   "okazii": (0.17, "estimat"), "cel": (0.17, "estimat")}
PRAG_PASTRARE = 0.5   # o lună care vine cu sub jumătate din rândurile de ieri nu se rescrie (Orders a dat ceva parțial)


def luni(n):
    y, m = dt.date.today().year, dt.date.today().month
    out = []
    for _ in range(n):
        out.append("%04d-%02d" % (y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def prefix(canal, tara, moneda):
    """Prefixul de P&L: canalul + țara (eMAG / Trendyol vând și în BG, HU, GR din același cont)."""
    return BAZA[canal] + (SUFIX_TARA.get(tara or "") or SUFIX_MONEDA.get(moneda or "") or "")


def linii(text):
    """'HA-0001×2;GT-33×1' → [('HA-0001', 2), ('GT-33', 1)]"""
    out = []
    for p in (text or "").split(";"):
        p = p.strip()
        if not p:
            continue
        sku, _, q = p.rpartition("×")
        out.append((sku.strip(), int(q)) if sku and q.isdigit() else (p, 1))
    return out


def comision_trendyol(comenzi, decontari):
    """{nume comandă: comision} — comisionul real pe `orderNumber`, împărțit pe pachetele LIVRATE după venit.

    Funcție pură (testată). `comenzi` = [(nume, orderNumber, venit, livrata)]; `decontari` = {orderNumber: comision}.
    Pachetele fără decontare nu apar în rezultat (apelantul le pune cota estimată)."""
    pe_comanda = collections.defaultdict(list)
    for nume, nr, venit, livrata in comenzi:
        if livrata:
            pe_comanda[nr].append((nume, venit))
    out = {}
    for nr, pachete in pe_comanda.items():
        if nr not in decontari:
            continue
        tot = sum(v or 0 for _, v in pachete)
        for nume, v in pachete:
            out[nume] = round(decontari[nr] * ((v or 0) / tot if tot > 0 else 1 / len(pachete)), 2)
    return out


def perioade_transport(colete, facturi):
    """{perioadă: (lunile ei, lei facturați)} — funcție pură (testată).

    Facturile de transport Trendyol vin la 1–4 săptămâni după livrare, deci nu se pot pune pe luna lor. Lunile cu colete
    fără cost se grupează în perioade continue (fără lună goală între ele: apr | iul–aug | oct), iar fiecare factură merge
    pe perioada ultimei luni cu colete de dinaintea ei. Fără asta, 23k lei facturați în mai pentru aprilie s-ar fi
    împărțit și pe coletele din iulie. Facturile dinaintea primei luni cu colete (comenzi din afara ferestrei) se ignoră."""
    luni_c = sorted({l for l, _ in colete})
    if not luni_c:
        return {}

    def urm(l):
        y, m = map(int, l.split("-"))
        return "%04d-%02d" % (y + (m == 12), m % 12 + 1)
    perioade, cur = [], [luni_c[0]]
    for l in luni_c[1:]:
        if l == urm(cur[-1]):
            cur.append(l)
        else:
            perioade.append(cur); cur = [l]
    perioade.append(cur)
    out = {"%s..%s" % (p[0], p[-1]): [set(p), 0.0] for p in perioade}
    for luna, lei in facturi.items():
        ale = [p for p in perioade if p[0] <= luna]
        if ale:
            out["%s..%s" % (ale[-1][0], ale[-1][-1])][1] += lei
    return {k: (v[0], v[1]) for k, v in out.items()}


def decontari_trendyol(start, end):
    """({orderNumber: comision lei}, cota medie comision/vânzare, {luna factură: transport lei}) din API-ul Trendyol."""
    import trendyol_profitability as T
    com, vanz = collections.defaultdict(float), 0.0
    for sf in ("RO", "BG", "GR"):
        T.session.headers["storeFrontCode"] = sf
        for r in T.fetch_settlements(start, end, ["Sale", "Return"]):
            nr = str(r.get("orderNumber") or "")
            c = float(r.get("commissionAmount") or 0)
            if r.get("transactionType") == "Sale":
                com[nr] += c
                vanz += float(r.get("credit") or 0)
            elif r.get("transactionType") == "Return":
                com[nr] -= c
    cota = (sum(com.values()) / vanz) if vanz else None
    transport = collections.defaultdict(float)
    vazute = set()
    for r in T.fetch_other_financials(start, end + dt.timedelta(days=45)):
        tip = str(r.get("transactionType") or "").upper()
        # transportul + taxele Trendyol (produs lipsă / greșit / nelivrat): ambele se rețin din plată. Facturile de
        # comision NU: comisionul e deja reținut în `sellerRevenue` (verificat pe plăți, 24-oct)
        if r.get("id") in vazute or not ("SHIPPING" in tip or "FEE" in tip):
            continue
        vazute.add(r.get("id"))
        # factura vine săptămânal, pe coletele din săptămâna de dinainte: cea din 1–7 ale lunii e a lunii trecute
        luna = (dt.datetime.fromtimestamp((r.get("transactionDate") or 0) / 1000) - dt.timedelta(days=7)).strftime("%Y-%m")
        transport[luna] += float(r.get("debt") or 0) - float(r.get("credit") or 0)
    return dict(com), cota, dict(transport)


def livrabilitate(luna, token):
    u = "%s/api/cs/livrabilitate?%s" % (OH, urllib.parse.urlencode({"luna": luna, "cu_linii": "true"}))
    req = urllib.request.Request(u, headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        d = json.loads(r.read().decode("utf-8"))
    ci = {c: i for i, c in enumerate(d["coloane"])}
    return [{c: row[i] for c, i in ci.items()} for row in d["randuri"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--luni", type=int, default=6)
    ap.add_argument("--aplica", action="store_true")
    a = ap.parse_args()
    import profit_core as pc
    from core import status_oh
    token = status_oh.token()
    if not token:
        sys.exit("lipsește cheia de citire Orders (OH_PROFIT_CITIRE_TOKEN)")
    fereastra = sorted(luni(a.luni))
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    for col, tip in (("comision", "REAL DEFAULT 0"), ("comision_sursa", "TEXT DEFAULT ''")):
        try:
            cx.execute("ALTER TABLE profit_orders ADD COLUMN %s %s" % (col, tip))
        except sqlite3.OperationalError:
            pass
    curs = {(r["month"], r["currency"]): r["rate_to_ron"] for r in cx.execute(
        "SELECT month, currency, rate_to_ron FROM profit_exchange_rates WHERE month >= ?", (fereastra[0],))}
    cost_recent = {r["s"]: r["u"] for r in cx.execute(
        "SELECT lower(sku) s, sum(line_cogs) / sum(qty) u FROM profit_order_lines WHERE month >= ? AND qty > 0 "
        "AND line_cogs > 0 GROUP BY lower(sku)", (fereastra[0],))}

    comenzi, nesigure = {}, collections.Counter()
    for luna in fereastra:
        try:
            rand = livrabilitate(luna, token)
        except Exception as e:  # noqa: BLE001
            print("%s: Orders n-a răspuns (%s) — luna rămâne cum e" % (luna, e))
            continue
        comenzi[luna] = []
        for r in rand:
            canal = r.get("canal")
            if canal not in BAZA:
                continue
            moneda = r.get("moneda") or ("RON" if canal in O_SINGURA_MONEDA else None)
            if not moneda:
                nesigure[(luna, canal, "fără monedă")] += 1
                continue
            comenzi[luna].append(dict(r, moneda=moneda, prefix=prefix(canal, r.get("tara"), moneda)))

    start = dt.datetime.strptime(fereastra[0] + "-01", "%Y-%m-%d")
    try:
        com_ty, cota_ty, transp_ty = decontari_trendyol(start, dt.datetime.now())
    except Exception as e:  # noqa: BLE001
        print("decontările Trendyol n-au venit (%s) — comisionul Trendyol rămâne pe cota estimată 12,5%%" % e)
        com_ty, cota_ty, transp_ty = {}, None, {}
    cota_ty = cota_ty or 0.125

    coduri = sorted({s for lst in comenzi.values() for o in lst for s, _ in linii(o.get("linii"))})
    try:
        loturi = pc.cost_loturi_oh(coduri, token)
    except Exception as e:  # noqa: BLE001
        print("loturile din Orders n-au venit (%s) — COGS doar din liniile recente" % e)
        loturi = {}

    randuri, sumar, fara_cost = collections.defaultdict(list), collections.defaultdict(collections.Counter), collections.Counter()
    ty_fara_colet = collections.Counter()
    for luna, lst in comenzi.items():
        ty = [(o["comanda"], o["comanda"][2:].split("-")[0], float(o.get("total") or 0)
               * curs.get((luna, o["moneda"]), 1.0 if o["moneda"] == "RON" else 0), o.get("stare") == "Livrata")
              for o in lst if o.get("canal") == "trendyol"]
        real = comision_trendyol(ty, com_ty)
        for o in lst:
            zi = o.get("zi") or (luna + "-01")
            cogs, lipsa, skus = 0.0, [], []
            for sku, q in linii(o.get("linii")):
                skus.append(sku)
                u, _src = pc.cost_unitar_linie(sku, zi, loturi, None, cost_recent.get(sku.lower()))
                if u is None:
                    lipsa.append("%sx%d" % (sku, q))
                    continue
                cogs += u * q
            rata = curs.get((luna, o["moneda"]), 1.0 if o["moneda"] == "RON" else None)
            if rata is None:
                nesigure[(luna, o["canal"], "fără curs %s" % o["moneda"])] += 1
                continue
            venit_ron = float(o.get("total") or 0) * rata
            if o["canal"] == "trendyol":
                if o["comanda"] in real:
                    com, sursa = real[o["comanda"]], "real"
                else:
                    com, sursa = round(venit_ron * cota_ty, 2), "estimat"
                if o.get("transport_fara_tva") in (None, "") and o.get("stare") in ("Livrata", "Refuzata", "In curs de livrare"):
                    ty_fara_colet[(luna, o["prefix"])] += 1
            else:
                r_, sursa = COMISION_REGULA[o["canal"]]
                com = round(venit_ron * r_, 2)
            if o.get("stare") != "Livrata":
                com = 0.0
            if lipsa:
                fara_cost[o["prefix"]] += 1
            randuri[luna].append((luna, o["prefix"], o.get("domeniu") or "", o["comanda"], zi + "T00:00:00Z",
                                  float(o.get("total") or 0), o["moneda"], round(cogs, 2), len(lipsa), "; ".join(lipsa),
                                  "", "", "", o.get("curier") or "", o.get("ultim_status") or "", o.get("stare") or "",
                                  "%s,%s" % (MARCAJ, o["canal"]), ",".join(skus), "", com, sursa))
            s = sumar[(luna, o["prefix"])]
            s["n"] += 1
            s[o.get("stare") or "?"] += 1
            if o.get("stare") == "Livrata":
                s["venit_ron"] += venit_ron
                s["cogs"] += cogs
                s["comision"] += com
                s["com_" + sursa] += 1

    # transportul istoric Trendyol: facturile Trendyol de transport (TVA 0%) pe coletele fără cost în Orders
    tarif = {}
    for per, (luni_per, cost) in perioade_transport(ty_fara_colet, transp_ty).items():
        n = sum(v for (l, _), v in ty_fara_colet.items() if l in luni_per)
        if not n:
            continue
        if cost <= 0:
            # fără factură Trendyol (coletele le-am trimis noi, cu DPD): tariful fix al motorului, nu 0 lei
            for (luna, pfx) in ty_fara_colet:
                if luna in luni_per:
                    tarif[(luna, pfx)] = None
            print("transport Trendyol %s: nicio factură pe %d colete fără cost în Orders → tariful fix (estimat)" % (per, n))
            continue
        for (luna, pfx) in ty_fara_colet:
            if luna in luni_per:
                tarif[(luna, pfx)] = round(cost / n * 1.21, 4)   # motorul scoate TVA-ul de 21% din tarif
        print("transport Trendyol %s: facturi %.2f lei pe %d colete fără cost în Orders → %.2f lei/colet"
              % (per, cost, n, cost / n))

    print("comision Trendyol: %d comenzi cu decontare, cota reală %.1f%%" % (len(com_ty), 100 * cota_ty))
    for k in sorted(sumar):
        s = sumar[k]
        print("%s %-7s n=%-4d livr=%-4d venit=%9.0f cogs=%8.0f comision=%7.0f  (real %d / regula %d / estimat %d)"
              % (k[0], k[1], s["n"], s["Livrata"], s["venit_ron"], s["cogs"], s["comision"], s["com_real"],
                 s["com_regula"], s["com_estimat"]))
    if fara_cost:
        print("comenzi cu cost lipsă (cogs_missing):", dict(fara_cost))
    if nesigure:
        print("sărite:", dict(nesigure))
    if not a.aplica:
        print("PROBĂ — nimic scris. Rulează cu --aplica.")
        return 0

    pfx_ale_noastre = sorted({r[1] for lst in randuri.values() for r in lst} | {p for p in
                             (BAZA[c] + s for c in BAZA for s in ("", "BG", "HU", "GR"))})
    marks = ",".join("?" * len(pfx_ale_noastre))
    with cx:
        for luna in comenzi:
            vechi = cx.execute("SELECT count(*) FROM profit_orders WHERE month=? AND prefix IN (%s) AND tags LIKE ?"
                               % marks, [luna] + pfx_ale_noastre + ["%" + MARCAJ + "%"]).fetchone()[0]
            if vechi and len(randuri[luna]) < PRAG_PASTRARE * vechi:
                print("%s: Orders a dat %d rânduri față de %d ieri — NU rescriu luna" % (luna, len(randuri[luna]), vechi))
                continue
            cx.execute("DELETE FROM profit_orders WHERE month=? AND prefix IN (%s) AND tags LIKE ?" % marks,
                       [luna] + pfx_ale_noastre + ["%" + MARCAJ + "%"])
            cx.executemany(
                "INSERT OR REPLACE INTO profit_orders (month, prefix, shop, order_name, created_at, revenue, currency, cogs, "
                "cogs_missing, cogs_missing_skus, payment_status, fulfillment_status, awb, courier_key, courier_status, "
                "status_category, tags, skus, shopify_delivery_status, comision, comision_sursa) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", randuri[luna])
        for (luna, pfx), cost in tarif.items():
            if cost is None:
                cx.execute("DELETE FROM profit_transport_costs WHERE month = ? AND prefix = ?", (luna, pfx))
                continue
            cx.execute("INSERT OR REPLACE INTO profit_transport_costs (month, prefix, cost_per_parcel, vat_included) "
                       "VALUES (?, ?, ?, 0)", (luna, pfx, cost))
    print("SCRIS: %d rânduri pe %d luni" % (sum(len(v) for v in randuri.values()), len(randuri)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
