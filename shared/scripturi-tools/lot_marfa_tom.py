# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary", "requests"]
# ///
"""LOTUL DE MARFĂ din TOM → Order Hub (`lot_marfa` / `lot_marfa_linie`, prin POST /api/lot-marfa) — în UMBRĂ.

De ce există: TOM ține CE S-A CERUT și CE S-A ÎNCĂRCAT (purchase_order_items: requestedQty / orderedQty /
shippedQty, tomUnitCost în USD, shippedAt = încărcarea în container), dar lotul îl ține ca GRUP de text
(„Container 54-55-56", 13 shipments, toate Grandia + 78-79-80), fără dată de sosire în RO și fără freight.
Scriptul desface grupurile în containere reale (C54, C55, C56 = foaia KDocs #N = tabul foii Google) și
duce în OH, pe fiecare lot, liniile de PO cu cantitățile și costul lor. Data sosirii NU vine de aici: o
scrie tura din scaner (foaia) în același rând — fiecare sursă scrie doar câmpurile ei.

Legătura linie ↔ container (harta §tom-kdocs-lot, regulile din gigi/skills/tom/scripts/containers.py):
  • `dovada`   — linia e în `shipment_lines` ȘI containerul e unul singur în grup (ex. „Container 49"),
                 sau foaia (liniile `foaie` deja în OH) are SKU-ul într-un singur container al grupului;
  • `candidat` — linia e în grup dar foaia o arată în mai multe containere ale lui; SAU linia n-are
                 shipment (toate containerele HA: TOM nu le are) și e legată EURISTIC de un container al
                 cărui foaie are SKU-ul (egal, sau sufix numeric ≥ 4 cifre: „20247" = „GD-DEP-20247", sau
                 codul de bare cu/fără zero în față), sosit DUPĂ încărcare (shippedAt ≤ sosire ≤ +120 zile)
                 și după crearea PO-ului; dacă se potrivesc mai multe, cel cu tranzitul cel mai aproape de
                 48 de zile, restul în notă;
  • liniile de grup pe care foaia nu le poate așeza într-un container rămân în `detalii` pe fiecare lot al
    grupului („linii_grup_nerepartizate"), NU ca linii — altfel cantitățile s-ar tripla;
  • liniile fără niciun container rămân pe „PO TOM-nnn" DOAR în raport (și în JSON), NU ca lot_marfa: lotul
    e containerul real, nu PO-ul (TOM-045/046/049 s-au încărcat în 5 valuri distincte).

Ce NU face: nu scrie în TOM, în scaner, în stock-sync sau în Shopify; implicit PROBĂ (afișează planul și
trimite la OH cu dry_run=true — OH validează și întoarce nou/actualizat/neschimbat/respins, fără să scrie).
Secretele (DATABASE_URL_TOM, OH_LOT_MARFA_TOKEN, OH_CS_SB_TOKEN) vin din env sau din seif prin kb.py și nu
se afișează niciodată. `normalizeaza_cod` de aici e COPIA regulii din services/lot_marfa.py (OH) — dacă o
schimbi, schimbă-le pe amândouă (scriptul rulează singur, cu uv, nu are cum să importe OH).

    uv run lot_marfa_tom.py                   # probă: planul + POST cu dry_run=true (nimic scris)
    uv run lot_marfa_tom.py --doar-plan       # doar planul, fără nicio cerere de scriere la OH
    uv run lot_marfa_tom.py --aplica          # SCRIE în lot_marfa (cere OH_LOT_MARFA_TOKEN, scop lot.marfa)
"""
import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KB = os.environ.get("KB_PY") or os.path.expanduser(
    "~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
OH = os.environ.get("OH_URL", "https://orderhub.arona.ro")
IESIRE = os.path.expanduser("~/Downloads/Scripturi/data/lot_marfa_tom.json")
ZILE_TRANZIT = 48            # median măsurat pe cele 5 containere cu sosire în TOM (29–51 zile)
ZILE_TRANZIT_MAX = 120
PRODUSA_TOTUSI = "tables in multiple sizes"   # liniile CANCELLED care au venit totuși (KDocs), 42 linii / 24.250 buc


def secret(cheie: str) -> str:
    """Valoarea din env sau din seif. NU se tipărește niciodată."""
    v = (os.environ.get(cheie) or "").strip()
    if v:
        return v
    r = subprocess.run(["uv", "run", "-q", KB, "secret-get", cheie], capture_output=True, text=True)
    return (r.stdout or "").strip()


# ── codul de lot: COPIA regulii din services/lot_marfa.normalizeaza_cod ─────────────────────────────────

_PREFIX = re.compile(r"^\s*(?:container(?:ul|e)?\s*#?\s*|#\s*|c\s?)?(\d{1,3})(?![\d.,/])", re.I)
_LISTA = re.compile(r"^(?:\s*[-–/,]\s*(?:\d{1,3})(?![\d.,/]))+\s*$")


def normalizeaza_cod(text_lot) -> list:
    """„Container 43-44-45" → [C43, C44, C45]; „Container 37-40" → C37…C40; „Container 49" / „C43-08 Sept" → un cod."""
    t = str(text_lot or "").split("·")[0].strip()
    m = _PREFIX.match(t)
    if not m:
        return []
    n = int(m.group(1))
    if n <= 0:
        return []
    rest = t[m.end():]
    numere = [int(x) for x in re.findall(r"\d{1,3}", rest)] if _LISTA.match(rest) else []
    if not numere or numere[0] <= n:
        return ["C%d" % n]
    if len(numere) == 1:
        if numere[0] - n > 12:
            return ["C%d" % n]
        return ["C%d" % i for i in range(n, numere[0] + 1)]
    return ["C%d" % i for i in sorted(set([n] + numere))]


def numar(cod: str) -> int:
    m = re.search(r"\d+", cod or "")
    return int(m.group(0)) if m else 0


# ── TOM (doar citire) ───────────────────────────────────────────────────────────────────────────────────

def _con(dsn: str):
    import psycopg2
    c = psycopg2.connect(dsn.split("?")[0])
    c.set_session(readonly=True)
    return c


def linii_tom() -> list:
    """Toate liniile de PO cu grupul lor (dacă au) și produsul: coloanele TOM sunt camelCase, deci cu ghilimele."""
    with _con(secret("DATABASE_URL_TOM")) as c, c.cursor() as q:
        q.execute("""
          select i.id, p."tomNumber", p."createdAt", p.status::text, i."externalSku", i."externalBarcode",
                 i."requestedQty", i."orderedQty", i."receivedQty", i."shippedQty", i."tomUnitCost",
                 i."receivedAt", i."shippedAt", i.status::text, i."cancelNote",
                 s.name, s.code, s."arrivedAt", s.eta, s."freightUsd",
                 pr.sku, pr.barcode, pr."transportUnitUsd", pr.title
          from purchase_order_items i
          join purchase_orders p on p.id = i."poId"
          left join shipment_lines l on l."poItemId" = i.id
          left join shipments s on s.id = l."shipmentId"
          left join products pr on pr.id = i."productId"
          order by s.name nulls last, p."tomNumber", i."externalSku" """)
        col = ("po_item_id", "po_tom", "po_creat", "po_status", "sku", "barcode", "ceruta", "comandata",
               "receptionata_cn", "incarcata", "cost", "receivedAt", "shippedAt", "status", "cancelNote",
               "grup", "grup_cod", "grup_sosit", "grup_eta", "freight", "pr_sku", "pr_barcode", "transport_buc",
               "titlu")
        return [dict(zip(col, r)) for r in q.fetchall()]


# ── OH (doar citire pentru foaie și identitate; scriere doar la --aplica) ───────────────────────────────

def _get(cale: str, token: str):
    import requests
    try:
        r = requests.get(OH + cale, timeout=60, headers={"Authorization": "Bearer " + token})
        if r.status_code == 200:
            return r.json()
        return {"_http": r.status_code}
    except Exception as e:
        return {"_eroare": type(e).__name__}


def foaie_din_oh(token: str) -> dict:
    """{C66: {data_sosire, coduri: {COD_SURSA}, sufixe: {cifre ≥ 4: COD}}} din liniile `foaie` deja în OH (tura din
    scaner). Fără OH (ruta încă nemontată, 404) → {} și legătura euristică nu se face deloc — spus pe față."""
    lista = _get("/api/lot-marfa", token)
    out = {}
    if not isinstance(lista, dict) or not lista.get("ok"):
        print("foaia din OH necitită (%s) → fără legătură euristică: liniile grupurilor multi-container rămân în "
              "detalii (nerepartizate), liniile fără shipment rămân pe PO"
              % (lista.get("_http") or lista.get("_eroare") or lista.get("motiv") if isinstance(lista, dict) else "?"))
        return out
    for l in lista.get("loturi") or []:
        cod = l["cod"]
        d = _get("/api/lot-marfa/" + cod, token)
        # `sursa` e unită în OH din 24-oct („foaie+tom"): rândul foii rămâne al foii după ce TOM i-a pus PO-ul
        linii = [x for x in (d.get("linii") or [])
                 if set(str(x.get("sursa") or "").split("+")) & {"foaie", "receptie"}] if isinstance(d, dict) else []
        coduri, sufixe = set(), {}
        for x in linii:
            c = str(x.get("cod_sursa") or "").strip().upper()
            if not c or x.get("tip_cod") == "fara":
                continue
            coduri.add(c)
            m = re.search(r"(?:^|-)(\d{4,})$", c)
            if m:
                sufixe[m.group(1)] = c
            if c.isdigit():
                sufixe[c.lstrip("0")] = c
        out[cod] = {"data_sosire": l.get("data_sosire"), "coduri": coduri, "sufixe": sufixe,
                    "stare": l.get("stare"), "sursa": l.get("sursa")}
    print("foaia din OH: %d loturi, %d cu dată de sosire, %d cu linii"
          % (len(out), sum(1 for v in out.values() if v["data_sosire"]), sum(1 for v in out.values() if v["coduri"])))
    return out


def potriveste(linie: dict, foaie: dict) -> bool:
    """SKU egal, sufix numeric ≥ 4 cifre („20247" = „GD-DEP-20247"), sau codul de bare cu/fără zero în față.
    NICIODATĂ pe cifra finală a unui nume („set-6-textil-crem-5" nu e „5")."""
    coduri = [str(linie.get(k) or "").strip().upper() for k in ("sku", "pr_sku", "barcode", "pr_barcode")]
    for c in coduri:
        if not c:
            continue
        if c in foaie["coduri"]:
            return True
        m = re.search(r"(?:^|-)(\d{4,})$", c)
        if m and m.group(1) in foaie["sufixe"]:
            return True
        if c.isdigit() and c.lstrip("0") in foaie["sufixe"]:
            return True
    return False


_CACHE_COD: dict = {}


def rezolva(cod: str, token: str):
    """`GET /api/produs/{cod}` → master_id dacă OH leagă codul (doar citire; cache). Ca în import_cost_tom: barcode-ul
    TOM leagă 210/275, SKU-ul 147/156 — alegem codul care chiar leagă, ca linia să poarte master de la început."""
    if not token or not cod:
        return None
    if cod in _CACHE_COD:
        return _CACHE_COD[cod]
    j = _get("/api/produs/" + str(cod), token)
    out = (j.get("produs") or {}).get("master_id") if isinstance(j, dict) and j.get("gasit") else None
    _CACHE_COD[cod] = out
    return out


# ── planul ──────────────────────────────────────────────────────────────────────────────────────────────

def _zi(v):
    if v is None:
        return None
    return v.date() if isinstance(v, dt.datetime) else v


def _linie_oh(l: dict, cod_sursa: str, master, legatura: str, nota: str) -> dict:
    out = {"cod_sursa": cod_sursa, "po_tom": l["po_tom"], "po_item_id": l["po_item_id"], "sursa": "tom",
           "legatura": legatura, "cantitate_ceruta": l["ceruta"], "cantitate_comandata": l["comandata"],
           "cantitate_incarcata": l["incarcata"], "nota": nota[:500]}
    if master:
        out["master_id"] = master
    if l["cost"] is not None:
        out["cost_marfa_buc"] = str(l["cost"])
        out["moneda"] = "USD"
    if l["transport_buc"] is not None:
        out["transport_buc"] = str(l["transport_buc"])      # products.transportUnitUsd (445 produse), nefolosit până azi
    return out


def construieste(linii: list, foaie: dict, token_citire: str) -> tuple:
    """(loturi de trimis, raport). Un lot = un container real; liniile TOM sunt așezate pe container cu dovadă sau
    ca candidat (vezi docstring-ul modulului)."""
    loturi: dict = {}
    fara_container: dict = collections.defaultdict(list)
    neincarcate = collections.Counter()
    anulate_produse = []
    nerepartizate = collections.defaultdict(list)
    sosiri_foaie = {c: dt.date.fromisoformat(v["data_sosire"]) for c, v in foaie.items() if v.get("data_sosire")}

    def lot(cod: str, l: dict):
        if cod not in loturi:
            loturi[cod] = {"cod": cod, "sursa": "tom", "nr_foaie_kdocs": "#%d" % numar(cod), "linii": [],
                           "detalii": {"tom": {"po": set(), "grup_cod": None}}}
        x = loturi[cod]
        if l.get("grup"):
            x["grup_tom"] = l["grup"]
            x["detalii"]["tom"]["grup_cod"] = l.get("grup_cod")
            if l.get("grup_sosit"):
                # data sosirii e a FOII (tura din scaner, pe container); TOM o are doar pe GRUP, pusă de
                # fix_shipments.py din aceeași foaie — rămâne în detalii, altfel două surse și-ar rescrie același câmp
                x["detalii"]["tom"]["grup_sosit"] = str(_zi(l["grup_sosit"]))
            if l.get("grup_eta") and "eta" not in x:
                x["eta"] = str(_zi(l["grup_eta"]))
                x["eta_estimat"] = True
        x["detalii"]["tom"]["po"].add(l["po_tom"])
        inc = _zi(l.get("shippedAt"))
        if inc and (x.get("data_incarcare") is None or str(inc) > x["data_incarcare"]):
            x["data_incarcare"] = str(inc)                      # încărcarea lotului = ultima linie încărcată
        return x

    def cod_si_master(l: dict):
        # SKU-ul întâi: foaia scrie SKU-uri, iar OH (lot_marfa.cheie_linie, 24-oct) pune linia TOM pe rândul FĂRĂ PO al
        # foii pentru același cod — un singur rând pe produs, cu PO-ul de la TOM și cantitățile foii;
        # codul de bare (EAN din ean_alocari, leagă prin aliasul 'ean') doar când SKU-ul nu leagă
        coduri = [c for c in (str(l.get("sku") or "").strip(), str(l.get("pr_sku") or "").strip(),
                              str(l.get("barcode") or "").strip(), str(l.get("pr_barcode") or "").strip()) if c]
        for c in coduri:
            m = rezolva(c, token_citire)
            if m:
                return c.upper(), m
        return (str(l.get("sku") or l.get("barcode") or "").strip().upper() or None), None

    for l in linii:
        anulata = l["status"] == "CANCELLED"
        produsa_totusi = anulata and PRODUSA_TOTUSI in (l.get("cancelNote") or "").lower()
        if anulata and not produsa_totusi:
            continue
        if produsa_totusi:
            anulate_produse.append({"po_tom": l["po_tom"], "sku": l["sku"], "ceruta": l["ceruta"]})
            continue                                           # vin doar din KDocs (pasul 4), nu din TOM
        cod_sursa, master = cod_si_master(l)
        if not cod_sursa:
            continue
        nota = "TOM %s %s" % (l["po_tom"], l["status"])
        if l.get("grup"):
            containere = normalizeaza_cod(l["grup"])
            if not containere:
                fara_container[l["po_tom"]].append(l)
                continue
            for c in containere:                                # lotul există pentru fiecare container al grupului
                lot(c, l)
            if len(containere) == 1:
                lot(containere[0], l)["linii"].append(_linie_oh(l, cod_sursa, master, "dovada", nota + " · shipment"))
                continue
            potriviri = [c for c in containere if c in foaie and potriveste(l, foaie[c])]
            if len(potriviri) == 1:
                lot(potriviri[0], l)["linii"].append(_linie_oh(l, cod_sursa, master, "dovada",
                                                               nota + " · shipment %s + foaie" % l["grup"]))
            elif potriviri:
                for c in potriviri:
                    nota_c = nota + " · %s: foaia îl are în %s; cantitățile sunt ale liniei PO, neîmpărțite" % (
                        l["grup"], ", ".join(potriviri))
                    lot(c, l)["linii"].append(_linie_oh(l, cod_sursa, master, "candidat", nota_c))
            else:
                nerepartizate[l["grup"]].append({"po_tom": l["po_tom"], "po_item_id": l["po_item_id"],
                                                 "cod_sursa": cod_sursa, "cantitate_incarcata": l["incarcata"]})
            continue
        if l["status"] not in ("SHIPPED", "PARTIALLY_SHIPPED") or not l.get("shippedAt"):
            neincarcate[l["status"]] += 1
            continue
        # fără shipment în TOM (containerele HA): candidat pe un container al cărui foaie are codul, sosit după încărcare
        inc = _zi(l["shippedAt"])
        po_creat = _zi(l.get("po_creat"))
        cand = []
        for c, f in foaie.items():
            if not potriveste(l, f):
                continue
            sos = sosiri_foaie.get(c)
            if sos is not None and (sos < inc or (sos - inc).days > ZILE_TRANZIT_MAX or (po_creat and sos < po_creat)):
                continue
            cand.append(c)
        if not cand:
            fara_container[l["po_tom"]].append(l)
            continue
        cand.sort(key=lambda c: (abs((sosiri_foaie[c] - inc).days - ZILE_TRANZIT) if c in sosiri_foaie else 10 ** 6,
                                 numar(c)))
        ales = cand[0]
        lot(ales, l)["linii"].append(_linie_oh(
            l, cod_sursa, master, "candidat",
            nota + " · fără shipment în TOM; foaia %s are codul, încărcat %s" % (ales, inc)
            + (", alternative: " + ", ".join(cand[1:6]) if len(cand) > 1 else "")))

    # Un container pe care foaia nu-l are deloc (C41: „Container 41-42" sosit 2026-07-09 în TOM, fără tab în foaie) ar
    # rămâne „incarcat" pe veci și ar apărea fals în briefing („încărcat fără sosire > 60 zile"). Doar pentru el, sosirea
    # grupului din TOM devine `data_sosire`; unde foaia are lotul, data rămâne a foii (o sursă pe câmp). După primul
    # import lotul există în OH doar cu sursa „tom": se retrimite la fel, ca rularea să fie stabilă.
    for c, x in loturi.items():
        sos = x["detalii"]["tom"].get("grup_sosit")
        f = foaie.get(c)
        fara_foaie = f is None or f.get("sursa") == "tom"
        if fara_foaie and sos and foaie:
            x["data_sosire"] = sos
            x["detalii"]["tom"]["sosire_din_tom"] = True

    for grup, lista in nerepartizate.items():
        for c in normalizeaza_cod(grup):
            if c in loturi:
                loturi[c]["detalii"]["tom"]["linii_grup_nerepartizate"] = lista
    for x in loturi.values():
        x["detalii"]["tom"]["po"] = sorted(x["detalii"]["tom"]["po"])
        x["detalii"]["tom"]["linii"] = len(x["linii"])
        if not x["linii"]:
            x["nota"] = "grup TOM %s: liniile nu s-au putut așeza pe container (vezi detalii)" % x.get("grup_tom")
    po_bucket = {po: {"linii": len(ls), "incarcat": sum(int(l["incarcata"] or 0) for l in ls),
                      "cu_cost": sum(1 for l in ls if l["cost"] is not None),
                      "ultima_incarcare": str(max((_zi(l["shippedAt"]) for l in ls if l.get("shippedAt")), default=""))}
                 for po, ls in fara_container.items()}
    raport = {"loturi": len(loturi), "linii": sum(len(x["linii"]) for x in loturi.values()),
              "dovada": sum(1 for x in loturi.values() for l in x["linii"] if l["legatura"] == "dovada"),
              "candidat": sum(1 for x in loturi.values() for l in x["linii"] if l["legatura"] == "candidat"),
              "cu_master": sum(1 for x in loturi.values() for l in x["linii"] if l.get("master_id")),
              "cu_transport_buc": sum(1 for x in loturi.values() for l in x["linii"] if l.get("transport_buc")),
              "linii_grup_nerepartizate": sum(len(v) for v in nerepartizate.values()),
              "po_fara_container": po_bucket, "neincarcate_pe_status": dict(neincarcate),
              "anulate_dar_produse": len(anulate_produse)}
    ordonate = [loturi[k] for k in sorted(loturi, key=numar)]
    return ordonate, raport, anulate_produse


def trimite(loturi: list, aplica: bool) -> dict:
    """POST /api/lot-marfa — cere scopul `lot.marfa` și la dry_run (ca la produs.cost); siguranța e dry_run, nu cheia."""
    import requests
    tok = secret("OH_LOT_MARFA_TOKEN")
    if not tok:
        return {"eroare": "lipsește OH_LOT_MARFA_TOKEN din env/seif (scop lot.marfa)"}
    r = requests.post(OH + "/api/lot-marfa", timeout=300,
                      headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"},
                      json={"loturi": loturi, "dry_run": not aplica, "introdus_de": "lot_marfa_tom (TOM → lot_marfa)"})
    try:
        return {"cod_http": r.status_code, "raspuns": r.json()}
    except ValueError:
        return {"cod_http": r.status_code, "raspuns": r.text[:400]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--aplica", action="store_true", help="SCRIE în lot_marfa (altfel POST cu dry_run=true)")
    p.add_argument("--doar-plan", action="store_true", help="nu trimite nimic la OH, nici în probă")
    p.add_argument("--fara-oh", action="store_true", help="nu citi OH (fără foaie, fără identitate): plan brut")
    p.add_argument("--iesire", default=IESIRE)
    a = p.parse_args()

    linii = linii_tom()
    print("TOM: %d linii, %d cu grup (shipment), %d PO" % (len(linii), sum(1 for l in linii if l["grup"]),
                                                             len({l["po_tom"] for l in linii})))
    token_citire = "" if a.fara_oh else secret("OH_CS_SB_TOKEN")
    foaie = {} if a.fara_oh else foaie_din_oh(token_citire)
    loturi, raport, anulate = construieste(linii, foaie, token_citire)

    print("loturi: %(loturi)d · linii: %(linii)d (dovadă %(dovada)d, candidat %(candidat)d, cu master %(cu_master)d, "
          "cu transport/buc %(cu_transport_buc)d) · linii de grup nerepartizate: %(linii_grup_nerepartizate)d · "
          "anulate dar produse (doar KDocs): %(anulate_dar_produse)d" % raport)
    for x in loturi:
        print("  %-5s %-22s încărcat %-10s sosit(grup TOM) %-10s linii %3d  PO %s%s"
              % (x["cod"], x.get("grup_tom") or "-", x.get("data_incarcare") or "-",
                 x["detalii"]["tom"].get("grup_sosit") or "-", len(x["linii"]), ",".join(x["detalii"]["tom"]["po"]),
                 "  ⚠ fără linii" if not x["linii"] else ""))
    if raport["po_fara_container"]:
        print("PO fără container (rămân în raport, NU devin loturi):")
        for po, v in sorted(raport["po_fara_container"].items()):
            print("  %-9s %3d linii, %8d buc încărcate, ultima încărcare %s" % (po, v["linii"], v["incarcat"],
                                                                                 v["ultima_incarcare"]))
    if raport["neincarcate_pe_status"]:
        print("linii neîncărcate încă (nu intră în niciun lot):", raport["neincarcate_pe_status"])

    os.makedirs(os.path.dirname(a.iesire), exist_ok=True)
    with open(a.iesire, "w", encoding="utf-8") as f:
        json.dump({"generat": dt.datetime.now().isoformat(timespec="seconds"), "raport": raport, "loturi": loturi,
                   "anulate_dar_produse": anulate}, f, ensure_ascii=False, indent=1, default=str)
    print("scris", a.iesire)

    if a.doar_plan or not loturi:
        print("nu am trimis nimic (--doar-plan sau niciun lot)")
        return 0
    print("trimit la %s (%s)" % (OH, "APLIC" if a.aplica else "dry_run"))
    rasp = trimite(loturi, a.aplica)
    j = rasp.get("raspuns") if isinstance(rasp.get("raspuns"), dict) else {}
    print("http %s · scrise %s · loturi %s · linii %s" % (rasp.get("cod_http"), j.get("scrise"), j.get("sumar"),
                                                         j.get("sumar_linii")))
    if j.get("regula"):
        print("regula OH:", j["regula"])
    for r in j.get("rezultate") or []:
        if r.get("rezultat") == "respins" or (r.get("linii") or {}).get("nelegate"):
            print("  %-5s %-10s %s nelegate=%s %s" % (r.get("cod"), r.get("rezultat"), r.get("motiv") or "",
                                                     (r.get("linii") or {}).get("nelegate"),
                                                     ", ".join(x["cod_sursa"] for x in (r.get("nelegate") or [])[:6])))
    if not j:
        print(json.dumps(rasp, ensure_ascii=False, default=str)[:800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
