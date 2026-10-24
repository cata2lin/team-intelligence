# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary", "requests"]
# ///
"""Importul INIȚIAL de cost din TOM în `produs_cost` (Order Hub) — pe LOT, cu curs BNR ISTORIC.

De ce există: costul mărfii stă azi în patru locuri care nu seamănă între ele (raportul cost OH /
`sku_costs` pe aceleași 23 de SKU-uri: median 1,19, dar de la 1,01 la 2,24 — deci nu-s două versiuni
ale aceluiași număr, ci două numere). Singura sursă PRIMARĂ a banilor plătiți e TOM: `tomUnitCost` pe
linia de PO, în USD. Scriptul îl duce în obiectul unic, ca ISTORIC pe lot, nu ca un câmp care se
suprascrie — altfel nu putem răspunde niciodată la „cât a costat marfa vândută în august".

Ce NU face (măsurat 22-oct-2026, ca să nu se presupună):
  • **TOM nu are transportul**: `shipments.freightUsd` e NULL pe toate cele 13 containere și
    `purchase_order_items.extraCost` e NULL pe toate cele 1.041 de linii. Deci `transport = 0` și
    rândul pleacă cu `nota = "transport lipsă în TOM"`. Costul e MAI MIC decât cel real până când
    transportul vine din altă parte (KDocs / factura forwarderului).
  • **nu scrie în Shopify** și nu atinge niciun consumator: implicit `dry_run`, adică Order Hub
    validează și întoarce planul, fără să scrie.
  • nu inventează cursuri: dacă nu există curs BNR ≤ data lotului, linia e SĂRITĂ și raportată.

Cursul: `fx_rates` din metrics (BNR, pe unitate; identic cu `exchange_rates` din AWB Arona de la 2026-04-17,
verificat pe 24-oct pe toate cele 4.961 de zile×monede comune). Începe pe 2026-04-13, iar cel mai vechi lot TOM
e din 2026-05 — deci ajunge. `FX_SURSA=awb` = calea veche (AWB Arona), doar la cerere: baza aceea rămâne
fără cititori de pe laptopuri. ⚠️ `bnr.ro/files/xml/years/nbrfxrates<an>.xml` face 302 către pagina principală
și întoarce HTML (verificat azi de pe Mac ȘI de pe serverul din RO), deci NU se citește de acolo.

    uv run import_cost_tom.py                      # probă: scrie planul, nu trimite nimic
    uv run import_cost_tom.py --trimite            # trimite la OH cu dry_run=true (tot fără scriere)
    uv run import_cost_tom.py --trimite --aplica   # SCRIE în produs_cost (cere OH_PRODUS_COST_TOKEN)
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys

import psycopg2
import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KB = os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
OH = os.environ.get("OH_URL", "https://orderhub.arona.ro")
VAMA = 1.10                      # taxa vamală medie, cum e în formula aprobată (sheet-ul „COGS 2026")
IESIRE = os.path.expanduser("~/Downloads/Scripturi/data/cost_tom_import.json")


def secret(cheie: str) -> str:
    """Valoarea din seif. NU se tipărește niciodată."""
    r = subprocess.run(["uv", "run", KB, "secret-get", cheie], capture_output=True, text=True)
    return (r.stdout or "").strip()


def _con(dsn: str):
    c = psycopg2.connect(dsn.split("?")[0])
    c.set_session(readonly=True)
    return c


def sursa_curs() -> str:
    return "awb" if (os.environ.get("FX_SURSA") or "").strip().lower() == "awb" else "metrics"


def cursuri_usd() -> list:
    """[(data, curs)] USD→RON, crescător, BNR. Implicit metrics.fx_rates; FX_SURSA=awb = AWB Arona."""
    if sursa_curs() == "awb":
        with _con(secret("DATABASE_URL_AWBPRINT")) as c, c.cursor() as q:
            q.execute("select rate_date, rate, multiplier from exchange_rates "
                      "where currency = 'USD' order by rate_date")
            return [(r[0], float(r[1]) / (r[2] or 1)) for r in q.fetchall()]
    dsn = os.environ.get("DATABASE_URL_METRICS") or secret("DATABASE_URL_METRICS")
    if not dsn:
        sys.exit("lipsește DATABASE_URL_METRICS (curs BNR din fx_rates)")
    try:
        with _con(dsn) as c, c.cursor() as q:
            q.execute('select "rateDate", rate from fx_rates '
                      "where \"fromCurrency\" = 'USD' and \"toCurrency\" = 'RON' order by \"rateDate\"")
            return [(r[0], float(r[1])) for r in q.fetchall()]
    except Exception as ex:     # fără DSN în mesaj
        sys.exit("cursul BNR din metrics.fx_rates nu se poate citi (%s)" % type(ex).__name__)


def curs_la(serie: list, zi: dt.date):
    """Cursul BNR al zilei, sau al ultimei zile lucrătoare dinainte. None dacă nu există nimic înainte."""
    ales = None
    for data, val in serie:
        if data <= zi:
            ales = (data, val)
        else:
            break
    return ales


def linii_tom() -> list:
    """Liniile de PO cu cost, cu containerul lor (dacă e legat) — doar citire."""
    with _con(secret("DATABASE_URL_TOM")) as c, c.cursor() as q:
        q.execute("""
          select i."externalSku", i."externalBarcode", i."orderedQty", i."receivedQty",
                 i."tomUnitCost", i."receivedAt", p."tomNumber",
                 s.name, s."arrivedAt", s."freightUsd", i."extraCost"
          from purchase_order_items i
          join purchase_orders p on p.id = i."poId"
          left join shipment_lines l on l."poItemId" = i.id
          left join shipments s on s.id = l."shipmentId"
          where i."tomUnitCost" is not null and i."cancelledAt" is null
          order by s.name nulls last, i."externalSku" """)
        return q.fetchall()


_CACHE_COD: dict = {}


def rezolva(cod: str, token: str):
    """`GET /api/produs/{cod}` — întoarce master_id dacă OH leagă codul, altfel None. Doar citire.

    De ce: 275 din barcode-urile TOM sunt EAN-uri din registrul nostru (`ean_alocari`), care NU se scriu
    în Shopify (stock-sync leagă pe barcode) — deci o parte nu există în `produs_alias`. Măsurat azi:
    barcode leagă 210/275, SKU leagă 147/156, împreună 357/431 (83%). Trimiterea codului care NU leagă
    ar produce rânduri refuzate; alegem codul care chiar leagă și le raportăm pe celelalte.
    """
    if cod in _CACHE_COD:
        return _CACHE_COD[cod]
    out = None
    try:
        r = requests.get(OH + "/api/produs/" + str(cod), timeout=30,
                         headers={"Authorization": "Bearer " + token})
        if r.status_code == 200:
            j = r.json()
            if j.get("gasit"):
                out = (j.get("produs") or {}).get("master_id")
    except Exception:
        out = None
    _CACHE_COD[cod] = out
    return out


def construieste(serie: list) -> tuple:
    """(rânduri de trimis, sărite, rezumat). Un rând = un lot × un produs."""
    randuri, sarite = [], []
    fara_transport = 0
    token = secret("OH_CS_SB_TOKEN")        # doar citire, ca să alegem codul care leagă
    for (sku, barcode, ord_q, rec_q, cost_u, rec_at, po, container, sosit, freight, extra) in linii_tom():
        cant = int(rec_q or ord_q or 0)
        coduri = [x for x in ((barcode or "").strip(), (sku or "").strip()) if x]
        if not coduri or cant <= 0:
            sarite.append({"sku": sku, "motiv": "fără cod sau fără cantitate"})
            continue
        cod = master = None
        if token:
            for x in coduri:
                master = rezolva(x, token)
                if master:
                    cod = x
                    break
        if not cod:
            if token:
                sarite.append({"sku": sku, "barcode": barcode,
                               "motiv": "codul nu e legat de niciun master în Order Hub"})
                continue
            cod = coduri[0]                 # fără cheie de citire: trimitem barcode-ul, OH decide
        # data lotului: sosirea containerului; dacă nu e, recepția liniei; dacă nici atât, nimic
        zi = (sosit or rec_at)
        zi = zi.date() if isinstance(zi, dt.datetime) else zi
        if zi is None:
            sarite.append({"sku": sku, "lot": container or po, "motiv": "fără dată de recepție/sosire"})
            continue
        c = curs_la(serie, zi)
        if c is None:
            sarite.append({"sku": sku, "lot": container or po, "motiv": "fără curs BNR ≤ %s" % zi})
            continue
        curs_data, curs = c
        marfa = float(cost_u)
        transport = float(extra or 0) + (0.0 if freight in (None, 0) else float(freight) / max(cant, 1))
        if not transport:
            fara_transport += 1
        randuri.append({
            "cod": cod,
            "lot": container or ("PO %s" % po),
            "tip_lot": "container" if container else "po",
            "data_lot": str(zi),
            "cantitate": cant,
            "cost_marfa": round(marfa, 4),
            "transport": round(transport, 4),
            "moneda": "USD",
            "curs": round(curs, 4),
            "curs_data": str(curs_data),
            "curs_sursa": "BNR (exchange_rates, AWB Arona)" if sursa_curs() == "awb" else "BNR (fx_rates, metrics)",
            "vama": VAMA,
            # ex-TVA, cum cere schema: TVA se adaugă la CITIRE, nu se stochează
            "cost_lei_buc": round((marfa + transport) * curs * VAMA, 4),
            "master_id": master,
            "sursa": "tom",
            "nota": ("PO %s" % po) + ("" if transport else " · transport lipsă în TOM"),
        })
    rez = {"randuri": len(randuri), "sarite": len(sarite), "fara_transport": fara_transport,
           "loturi": len({r["lot"] for r in randuri}),
           "produse": len({r["cod"] for r in randuri})}
    return randuri, sarite, rez


def trimite(randuri: list, aplica: bool) -> dict:
    # ⚠️ POST cere scopul `produs.cost` CHIAR ȘI la dry_run (cheia CS întoarce 403, verificat 22-oct):
    # siguranța e `dry_run`, nu cheia.
    tok = secret("OH_PRODUS_COST_TOKEN")
    if not tok:
        return {"eroare": "lipsește OH_PRODUS_COST_TOKEN din seif (scop produs.cost)"}
    r = requests.post(OH + "/api/produs/cost",
                      headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
                      json={"loturi": randuri, "dry_run": not aplica,
                            "introdus_de": "import_cost_tom (TOM + curs BNR istoric)"}, timeout=300)
    try:
        return {"cod_http": r.status_code, "raspuns": r.json()}
    except ValueError:
        return {"cod_http": r.status_code, "raspuns": r.text[:400]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--trimite", action="store_true", help="trimite la Order Hub (implicit dry_run)")
    p.add_argument("--aplica", action="store_true", help="SCRIE în produs_cost (nu doar probă)")
    p.add_argument("--iesire", default=IESIRE)
    a = p.parse_args()

    serie = cursuri_usd()
    if not serie:
        print("Nu am niciun curs USD (%s) — opresc" % sursa_curs() + " (un curs ghicit e mai rău decât lipsa lui).")
        return 2
    print("curs USD: %d zile, %s → %s" % (len(serie), serie[0][0], serie[-1][0]))

    randuri, sarite, rez = construieste(serie)
    print("rânduri de scris: %(randuri)d · produse: %(produse)d · loturi: %(loturi)d · "
          "sărite: %(sarite)d · fără transport: %(fara_transport)d" % rez)
    for r in randuri[:10]:
        print("  %-18s %-22s %s  %6d buc × (%.4f + %.4f) USD × %.4f × %.2f = %.4f lei/buc (ex-TVA)"
              % (r["cod"], r["lot"], r["data_lot"], r["cantitate"], r["cost_marfa"], r["transport"],
                 r["curs"], r["vama"], r["cost_lei_buc"]))
    motive = {}
    for s in sarite:
        motive[s["motiv"]] = motive.get(s["motiv"], 0) + 1
    for m, n in sorted(motive.items(), key=lambda x: -x[1]):
        print("  SĂRIT ×%d: %s" % (n, m))

    os.makedirs(os.path.dirname(a.iesire), exist_ok=True)
    with open(a.iesire, "w", encoding="utf-8") as f:
        json.dump({"generat": dt.datetime.now().isoformat(timespec="seconds"), "rezumat": rez,
                   "randuri": randuri, "sarite": sarite}, f, ensure_ascii=False, indent=1)
    print("scris", a.iesire)

    if a.trimite:
        print("trimit la %s (%s)" % (OH, "APLIC" if a.aplica else "dry_run"))
        rasp = trimite(randuri, a.aplica)
        j = (rasp.get("raspuns") or {}) if isinstance(rasp.get("raspuns"), dict) else {}
        print("http %s · scrise %s · sumar %s" % (rasp.get("cod_http"), j.get("scrise"), j.get("sumar")))
        if j.get("regula"):
            print("regula OH:", j["regula"])
        # `rezultate` vine în ORDINEA loturilor trimise ⇒ verificare rând cu rând
        for i, r in enumerate(j.get("rezultate") or []):
            if r.get("rezultat") in ("nelegat", "respins"):
                t = randuri[i] if i < len(randuri) else {}
                print("  %-9s %-16s lot %-22s %s" % (r.get("rezultat"), r.get("cod") or t.get("cod"),
                                                     r.get("lot") or t.get("lot"), r.get("motiv")))
        if not j:
            print(json.dumps(rasp, ensure_ascii=False)[:800])
    else:
        print("nu am trimis nimic (fără --trimite)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
