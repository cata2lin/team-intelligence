# /// script
# requires-python = ">=3.11"
# dependencies = ["xlrd==1.2.0", "openpyxl"]
# ///
"""Deconturi de ramburs de la curieri → un rând per AWB.

Un singur loc care știe cum arată fișierele trimise de curieri pe contact@rossinails.ro, ca să nu
rescrie fiecare raport parsarea. Ieșirea e stabilă și e contractul cu starea „Ramburs virat" din
Order Hub:

    curier · awb · suma · moneda · data_virarii · data_livrarii · referinta_client · referinta_decont · fisier

Formate recunoscute (măsurate pe aug–oct 2026):
- **DPD** `COD_<contract>_<AAAA.LL.ZZ>_<id>[_<nr>]_<nr>.xls` — decont pe AWB: `Waybill`, `Amount`,
  `Delivery date`, `Reference 1` (numărul comenzii noastre). ~2.900 rânduri/fișier.
- **Sameday** `<AAAA-LL-ZZ>-arona-srl-ramburs-de-transferat-ron.xlsx` — foaia `client` are totalul
  virat, foaia **`expeditii`** are detaliul: `AWB`, `Suma ramburs`, `Referinta client`.
- **FAN** `Expeditii_FAN_*.xlsx` / `Comenzi_FAN_*.xlsx` — ⚠️ NU sunt deconturi: sunt liste de
  expediții, respectiv de comenzi de ridicare. Coloana `Ramburs` de acolo e suma DE încasat, nu una
  virată. Sunt recunoscute explicit și întoarse cu `tip="expeditii"`, ca să nu intre din greșeală în
  reconciliere.

Folosire:
    from deconturi_ramburs import parseaza_fisier, parseaza_director
    randuri = parseaza_director("/cale/catre/atasamente")      # list[dict]
    python3 deconturi_ramburs.py <director> [iesire.csv]       # CLI, pentru probă
"""
from __future__ import annotations
import csv, datetime as dt, os, re, sys

CAMPURI = ["curier", "awb", "suma", "moneda", "data_virarii", "data_livrarii",
           "referinta_client", "referinta_decont", "tip", "fisier"]


def _zi(v):
    """Datele vin ca text (zz.ll.aaaa), ca dată Excel sau ca datetime. Întoarce AAAA-LL-ZZ sau None."""
    if v in (None, "", " "):
        return None
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    s = str(v).strip()
    for f in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    return None


def _suma(v):
    if v in (None, "", " "):
        return None
    s = str(v).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _awb(v):
    """AWB-urile vin din Excel ca float ('81386029211.0') — se taie zecimalele, nu se rotunjește."""
    if v is None:
        return None
    s = str(v).strip()
    s = re.sub(r"\.0+$", "", s)
    s = re.sub(r"[^0-9A-Za-z]", "", s)
    return s or None


def _data_din_nume(nume):
    m = re.search(r"(\d{4})[.\-_](\d{2})[.\-_](\d{2})", nume)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(\d{2})[.\-_](\d{2})[.\-_](\d{4})", nume)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def _randuri_xls(cale):
    import xlrd
    sh = xlrd.open_workbook(cale).sheet_by_index(0)
    for i in range(sh.nrows):
        yield [c.value for c in sh.row(i)]


def _randuri_xlsx(cale, foaie=None):
    import openpyxl
    wb = openpyxl.load_workbook(cale, read_only=True, data_only=True)
    ws = wb[foaie] if foaie and foaie in wb.sheetnames else wb.worksheets[0]
    for row in ws.iter_rows(values_only=True):
        yield list(row)


def _indice(antet, *nume):
    """Coloanele pot fi scrise diferit de la o lună la alta: caut pe potrivire parțială, fără diacritice."""
    curat = [re.sub(r"[^a-z0-9]", "", str(x or "").lower()) for x in antet]
    for n in nume:
        cheie = re.sub(r"[^a-z0-9]", "", n.lower())
        for i, c in enumerate(curat):
            if c == cheie:
                return i
        for i, c in enumerate(curat):
            if cheie and cheie in c:
                return i
    return None


def parseaza_fisier(cale):
    """Un fișier → list[dict] cu CAMPURI. Fișierele nerecunoscute întorc listă goală."""
    nume = os.path.basename(cale)
    jos = nume.lower()
    ref = re.sub(r"^[0-9a-f]{8}__", "", nume)          # prefixul pus la descărcarea din Gmail
    data_fis = _data_din_nume(ref)

    # ── DPD: decont pe AWB ────────────────────────────────────────────────────
    if re.search(r"cod_\d+_", jos) and jos.endswith(".xls"):
        randuri, antet, ies = list(_randuri_xls(cale)), None, []
        for r in randuri:
            if antet is None:
                if _indice(r, "waybill") is not None and _indice(r, "amount") is not None:
                    antet = r
                continue
            i_awb, i_sum = _indice(antet, "waybill"), _indice(antet, "amount")
            i_liv, i_ref = _indice(antet, "deliverydate"), _indice(antet, "reference1")
            awb, suma = _awb(r[i_awb]) if i_awb is not None else None, _suma(r[i_sum]) if i_sum is not None else None
            if not awb or suma is None:
                continue
            ies.append({"curier": "DPD", "awb": awb, "suma": suma, "moneda": "RON",
                        "data_virarii": data_fis,
                        "data_livrarii": _zi(r[i_liv]) if i_liv is not None else None,
                        "referinta_client": (str(r[i_ref]).strip() or None) if i_ref is not None else None,
                        "referinta_decont": ref, "tip": "decont", "fisier": nume})
        return ies

    # ── Sameday: foaia `expeditii` ────────────────────────────────────────────
    if "ramburs-de-transferat" in jos and jos.endswith(".xlsx"):
        randuri, antet, ies = list(_randuri_xlsx(cale, "expeditii")), None, []
        for r in randuri:
            if antet is None:
                if _indice(r, "awb") is not None and _indice(r, "sumaramburs") is not None:
                    antet = r
                continue
            i_awb, i_sum = _indice(antet, "awb"), _indice(antet, "sumaramburs")
            i_ref = _indice(antet, "referintaclient")
            awb, suma = _awb(r[i_awb]) if i_awb is not None else None, _suma(r[i_sum]) if i_sum is not None else None
            if not awb or suma is None:
                continue
            ies.append({"curier": "SAMEDAY", "awb": awb, "suma": suma, "moneda": "RON",
                        "data_virarii": data_fis, "data_livrarii": None,
                        "referinta_client": (str(r[i_ref]).strip() or None) if i_ref is not None else None,
                        "referinta_decont": ref, "tip": "decont", "fisier": nume})
        return ies

    # ── FAN: liste de expediții / comenzi, NU deconturi ───────────────────────
    if re.search(r"(expeditii|comenzi)_fan", jos):
        # FAN trimite și .xls vechi, pe care openpyxl nu-l deschide
        citeste = _randuri_xls if jos.endswith(".xls") else _randuri_xlsx
        randuri, antet, ies = list(citeste(cale)), None, []
        for r in randuri:
            if antet is None:
                if _indice(r, "awb") is not None:
                    antet = r
                continue
            i_awb, i_sum = _indice(antet, "awb"), _indice(antet, "ramburs")
            awb = _awb(r[i_awb]) if i_awb is not None else None
            if not awb:
                continue
            ies.append({"curier": "FAN", "awb": awb,
                        "suma": _suma(r[i_sum]) if i_sum is not None else None, "moneda": "RON",
                        "data_virarii": None, "data_livrarii": None, "referinta_client": None,
                        "referinta_decont": ref, "tip": "expeditii", "fisier": nume})
        return ies
    return []


def parseaza_director(director):
    ies = []
    for n in sorted(os.listdir(director)):
        if n.startswith("_") or not re.search(r"\.(xls|xlsx)$", n, re.I):
            continue
        try:
            ies += parseaza_fisier(os.path.join(director, n))
        except Exception as e:                      # un fișier stricat nu oprește restul lotului
            print(f"  ! {n}: {type(e).__name__}: {str(e)[:70]}", file=sys.stderr)
    return ies


if __name__ == "__main__":
    randuri = parseaza_director(sys.argv[1])
    import collections
    c = collections.Counter((r["curier"], r["tip"]) for r in randuri)
    print(f"{len(randuri)} rânduri", file=sys.stderr)
    for (cur, tip), n in sorted(c.items()):
        s = sum(r["suma"] or 0 for r in randuri if r["curier"] == cur and r["tip"] == tip)
        print(f"   {cur:<8} {tip:<10} {n:>7} rânduri · {s:>14,.2f} lei", file=sys.stderr)
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CAMPURI); w.writeheader(); w.writerows(randuri)
        print("scris ->", sys.argv[2], file=sys.stderr)
