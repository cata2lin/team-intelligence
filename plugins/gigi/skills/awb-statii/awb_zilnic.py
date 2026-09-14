# -*- coding: utf-8 -*-
"""Trimite ZILNIC (L-V) pe grupul de AWB etichetele facute pe Bonhaus.hu, Bonhaus.sk si Orice Redus.

CATE UN PDF PE STATIE (Bartolomeu / Uzina 2), niciodata cate un fisier pe comanda: omul face un
Ctrl+P per statie, nu N descarcari ([[awb-send-single-pdf]]).

Rutarea pe statii NU e rescrisa aici — se IMPORTA din `print_queue.py`, care e sursa unica.
HU/SK/ORC sunt in `SPLIT_STORES_M`: HA+LAVETE -> Bartolomeu, restul -> Uzina 2, decis dupa PRIMUL
sku al comenzii (`primary()`), exact cum ruteaza coada de print azi.

Trimite DOAR etichetele coletelor care NU au plecat inca (status `generated` la curier). Starea
vine din `awb_track.py`, nu din `dispatched`-ul xConnector, care e bugat.

Tine evidenta in `/root/awb_zilnic_trimise.json` — o eticheta nu pleaca de doua ori, deci scriptul
se poate relua fara grija. Rulat cu `--dry` doar listeaza.

⚠️ Descarcarea etichetei o marcheaza `downloaded` in xConnector, deci iese din coada de print a
depozitului. Corect aici: merge pe WhatsApp, nu la imprimanta statiei.
"""
import sys, os, json, time, subprocess, datetime
sys.path.insert(0, "/root/Scripturi/xc_preview")
os.chdir("/root/Scripturi/xc_preview")
import urllib.request, urllib.parse
import xconnector as X

# Regula de rutare pe statii apartine print-queue-ului. O IMPORT ca sa nu am doua adevaruri:
# daca depozitul isi schimba maine impartirea, cronul asta o urmeaza singur.
import importlib.util as _il
_PQ_PATH = "/root/Scripturi/team-intelligence/plugins/gigi/skills/print-queue/print_queue.py"
_spec = _il.spec_from_file_location("print_queue", _PQ_PATH)
PQ = _il.module_from_spec(_spec)
_spec.loader.exec_module(PQ)

STATII = ("BARTOLOMEU", "UZINA2")


def statia(o):
    """Ce statie printeaza comanda. O comanda = o eticheta, deci merge INTREAGA intr-un singur loc;
    daca are si HA si non-HA, decide primul sku — aceeasi conventie ca `print_queue`."""
    sku, _qty = PQ.primary(o)
    dep = PQ.category(sku) in PQ.DEPOZIT_CATS or PQ._is_lavete(sku)
    return "BARTOLOMEU" if dep else "UZINA2"

GRUP = "120363418826898523@g.us"          # grupul „AWB"
MAGAZINE = {"63e901-2f": "HU", "16w7xv-0w": "SK", "oriceredus": "ORC"}
EVIDENTA = "/root/awb_zilnic_trimise.json"
IESIRE = "/root/wa-bridge"
DRY = "--dry" in sys.argv
ZILE = 14                                  # fereastra e pe DATA COMENZII, nu a etichetei: o comanda
                                           # veche poate primi eticheta azi (HU1963/SK2387, plasate pe
                                           # 21-aug, etichetate pe 25). Evidenta opreste dublurile,
                                           # deci fereastra larga nu costa nimic.


def deja_trimise():
    try:
        return set(json.load(open(EVIDENTA)))
    except Exception:
        return set()


def salveaza(s):
    json.dump(sorted(s), open(EVIDENTA, "w"))


def eticheta_pdf(xc, o):
    """PDF-ul etichetei, construit ca in `awb-label`: linkul e in documentul din LISTARE."""
    doc = X.awb_doc(o)
    if not doc:
        return None
    trk = X.doc_tracking(doc)
    url = doc.get("url") or doc.get("awbPdfUrl")
    incearca = []
    if url:
        incearca.append((url, False))
    if trk:
        incearca.append((X.XBASE + "/api/document/shipping-label?connectorId=%s&trackingNumber=%s"
                         % (doc.get("connectorId"), urllib.parse.quote(str(trk))), True))
    for u, cu_token in incearca:
        try:
            req = urllib.request.Request(u)
            if cu_token:
                for k, v in xc.h.items():
                    req.add_header(k, v)
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
            if b[:4] == b"%PDF":
                return b
        except Exception:
            continue
    return None


PY = "/root/Scripturi/.venv/bin/python"


def stare_curier(trks):
    """Starea REALA la curier, per AWB. Cheia e statusul normalizat: `generated` = eticheta facuta
    dar coletul NU a fost ridicat inca — doar alea au rost sa ajunga la depozit.

    NU folosim `dispatched` din xConnector: e bugat si arata plecate comenzi care n-au plecat.
    """
    lista = []
    for t in trks:
        if not t:
            continue
        lista.append(str(t).split("-")[0])   # AWB compus (multi-colet) -> coletul principal
    if not lista:
        return {}
    try:
        r = subprocess.run([PY, "/root/Scripturi/awb_track.py", "--awb", ",".join(lista), "--json"],
                           capture_output=True, text=True, timeout=300)
        return {str(x.get("awb")): x.get("status") for x in json.loads(r.stdout or "[]")}
    except Exception as e:
        print("  ATENTIE: trackerul n-a raspuns (%s)" % str(e)[:60])
        return {}


def main():
    trimise = deja_trimise()
    azi = datetime.date.today()
    de_la = (azi - datetime.timedelta(days=ZILE)).isoformat()
    shops = X.load_shops()
    candidati = []
    for sh in shops:
        dom = (sh.get("shopDomain") or "")
        slug = dom.split(".")[0]
        if slug not in MAGAZINE:
            continue
        xc = X.XC(sh.get("apiKey") or "")
        try:
            arr = xc.orders(de_la, azi.isoformat())
        except Exception as e:
            print("  %-12s listarea a picat: %s" % (slug, str(e)[:70]))
            continue
        for o in arr:
            if not X.has_awb(o):
                continue
            nume = o.get("orderName") or ""
            oid = o.get("orderId")
            cheie = "%s:%s" % (slug, nume)
            if cheie in trimise:
                continue
            doc = X.awb_doc(o)
            candidati.append((slug, nume, cheie, xc, o, X.doc_tracking(doc) if doc else None))
    if not candidati:
        print("nimic nou de trimis")
        return

    # Filtrul pe starea de la curier se aplica INAINTE de descarcare: descarcarea marcheaza
    # eticheta `downloaded` in xConnector si o scoate din coada de print a statiei, deci nu vrem
    # s-o atingem pe una pe care n-o trimitem oricum.
    stare = stare_curier([c[5] for c in candidati])
    bucati, rezumat, plecate, nesigure = [], [], [], []
    for slug, nume, cheie, xc, o, trk in candidati:
        st = stare.get(str(trk).split("-")[0]) if trk else None
        if st in ("in_transit", "delivered", "returned", "refused"):
            plecate.append("%s(%s)" % (nume, st))
            continue
        if st != "generated":
            # tracker mort / AWB necunoscut: TRIMIT totusi. O eticheta in plus e o suparare;
            # una lipsa e un colet care nu pleaca. Dar o spun tare, sa nu treaca tacut.
            nesigure.append("%s(%s)" % (nume, st or "fara raspuns"))
        pdf = eticheta_pdf(xc, o)
        if not pdf:
            print("  %-12s %s — n-am putut lua eticheta" % (slug, nume))
            continue
        bucati.append((nume, pdf, statia(o), cheie))
        rezumat.append("%s %s" % (MAGAZINE[slug], nume))
    if plecate:
        print("sarite, deja plecate: %d — %s" % (len(plecate), ", ".join(plecate)))
    if nesigure:
        print("ATENTIE, stare necunoscuta (le trimit oricum): %d — %s" % (len(nesigure), ", ".join(nesigure)))
    if not bucati:
        print("nimic nou de trimis")
        return
    from collections import defaultdict
    pe_statie = defaultdict(list)
    for nume, b, st, ch in bucati:
        pe_statie[st].append((nume, b, ch))
    print("de trimis: %d etichete — %s" % (len(bucati), ", ".join(rezumat)))
    for st in STATII:
        lot = pe_statie.get(st) or []
        print("  %-11s %d" % (st, len(lot)))
    if DRY:
        return

    from pypdf import PdfWriter, PdfReader
    import io as _io
    trimise_ok = 0
    for st in STATII:
        lot = pe_statie.get(st) or []
        if not lot:
            continue
        w = PdfWriter()
        for _n, b, _ch in lot:
            for p in PdfReader(_io.BytesIO(b)).pages:
                w.add_page(p)
        cale = os.path.join(IESIRE, "AWB-%s-%d-%s.pdf" % (st, len(lot), azi.strftime("%d.%m")))
        with open(cale, "wb") as f:
            w.write(f)
        nume_lot = ", ".join(n for n, _b, _ch in lot)
        cap = "AWB %s · %s · %d etichete · %s" % (azi.strftime("%d.%m"), st, len(lot), nume_lot)
        r = subprocess.run(["node", "/root/wa-bridge/wa_send.js", GRUP, "doc", cale, cap],
                           capture_output=True, text=True, timeout=180)
        out = (r.stdout or "") + (r.stderr or "")
        print(out.strip()[:300])
        if "SENT doc" not in out:
            # esecul unei statii nu o marcheaza pe cealalta: fiecare lot se poate relua singur
            print("%s: NU s-a trimis — nu marchez lotul ca trimis" % st)
            continue
        for _n, _b, ch in lot:
            trimise.add(ch)
        salveaza(trimise)
        trimise_ok += 1
        print("trimis: %s" % cale)
    if not trimise_ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
