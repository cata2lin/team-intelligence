# /// script
# requires-python = ">=3.11"
# dependencies = ["google-api-python-client", "google-auth", "google-auth-oauthlib"]
# ///
"""
ecorom_ambalaje.py — cantitatea de carton (ambalaj de transport) de declarat la Ecorom
pentru containerele sosite intr-o luna, calculata ca BRUT − NET din sheet-urile
"Container NN Analysis".

Exemple:
  uv run ecorom_ambalaje.py --luna august --sheet <url1> <url2> ...
  uv run ecorom_ambalaje.py --luna august --sheet-file linkuri.txt
  uv run ecorom_ambalaje.py --sheet 12XcZC-tbEIXj9-lmx1C670eAMop9_uMLt2k68BKHpoo --json
"""
import argparse, json, os, re, sys
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TOKEN = os.path.expanduser("~/.config/gcp/sheets-token.json")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
PACK_RE = re.compile(r"^\s*\d+\s*box\s*=", re.I)


def sheets():
    c = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if c.expired and c.refresh_token:
        c.refresh(Request())
    return build("sheets", "v4", credentials=c).spreadsheets()


def sheet_id(s):
    m = re.search(r"/d/([A-Za-z0-9_-]{20,})", s)
    return m.group(1) if m else s.strip()


def num(v):
    if v is None or v == "":
        return None
    s = str(v).replace(",", "").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return None


def pick_tab(meta):
    """Tab-ul principal = titlul care e un numar (52) sau C+numar (C47); fallback primul tab."""
    titles = [s["properties"]["title"] for s in meta["sheets"]]
    for t in titles:
        if re.fullmatch(r"\s*C?\d+\s*", t, re.I):
            return t
    return titles[0]


def find_col(header, *needles):
    for i, h in enumerate(header):
        hl = str(h).lower()
        if any(n in hl for n in needles):
            return i
    return None


def analyze(svc, sid):
    meta = svc.get(spreadsheetId=sid, fields="properties.title,sheets.properties.title").execute()
    title = meta["properties"]["title"]
    tab = pick_tab(meta)
    rows = svc.values().get(spreadsheetId=sid, range=f"'{tab}'!A1:T200").execute().get("values", [])
    # header = primul rand care contine "gross"
    hi = next((i for i, r in enumerate(rows) if any("gross" in str(c).lower() for c in r)), None)
    if hi is None:
        raise RuntimeError(f"{title}: nu gasesc coloana 'Gross Weight' in tab-ul '{tab}'")
    header = rows[hi]
    cg = find_col(header, "gross")
    cn = find_col(header, "net weight", "net")
    cb = find_col(header, "total boxes", "boxes")
    cp = find_col(header, "packaging")
    if None in (cg, cn):
        raise RuntimeError(f"{title}: header fara Gross/Net: {header}")

    gross = net = boxes = 0.0
    lines = 0
    tot_excel = None
    for r in rows[hi + 1:]:
        cell = lambda c: r[c] if c is not None and c < len(r) else ""
        label = " ".join(str(x) for x in r).lower()
        if "total excel" in label:
            # Total excel: cutii | brut | net | cbm  (valorile vin dupa eticheta)
            vals = [num(x) for x in r if num(x) is not None]
            if len(vals) >= 2:
                tot_excel = (vals[0], vals[1])
            continue
        is_line = bool(PACK_RE.match(str(cell(cp)))) if cp is not None else (num(cell(cg)) is not None and num(cell(cn)) is not None and num(cell(0)) is not None)
        if not is_line:
            continue
        g, n = num(cell(cg)), num(cell(cn))
        if g is None or n is None:
            continue
        gross += g
        net += n
        boxes += num(cell(cb)) or 0
        lines += 1

    m = re.search(r"(\d+)", title) or re.search(r"(\d+)", tab)
    cont = f"C{m.group(1)}" if m else title
    warn = []
    if tot_excel and (abs(tot_excel[0] - gross) > 1 or abs(tot_excel[1] - net) > 1):
        warn.append(f"suma liniilor ({gross:.0f}/{net:.0f}) ≠ Total excel ({tot_excel[0]:.0f}/{tot_excel[1]:.0f})")
    if lines == 0:
        warn.append("0 linii recunoscute")
    return dict(container=cont, sheet=title, tab=tab, linii=lines, cutii=int(boxes),
                brut=gross, net=net, carton=gross - net, warn=warn, id=sid)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--luna", default="")
    ap.add_argument("--sheet", nargs="*", default=[], help="URL-uri sau ID-uri de Google Sheet")
    ap.add_argument("--sheet-file", help="fisier text cu un URL/ID pe linie")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    ids = [sheet_id(s) for s in a.sheet]
    if a.sheet_file:
        ids += [sheet_id(l) for l in open(a.sheet_file, encoding="utf-8") if l.strip() and not l.startswith("#")]
    if not ids:
        ap.error("da cel putin un --sheet sau --sheet-file")

    svc = sheets()
    res, errs = [], []
    for sid in ids:
        try:
            res.append(analyze(svc, sid))
        except Exception as e:  # noqa
            errs.append(f"{sid}: {e}")
    res.sort(key=lambda r: r["container"])

    if a.json:
        print(json.dumps(dict(luna=a.luna, containere=res, erori=errs), ensure_ascii=False, indent=2))
        return

    tb = tn = tc = 0.0
    tbox = 0
    print(f"\nECOROM — carton (brut − net) {a.luna}".rstrip())
    print(f"{'Container':<10}{'Brut kg':>10}{'Net kg':>10}{'Carton kg':>11}{'Cutii':>8}  Sheet")
    for r in res:
        tb += r["brut"]; tn += r["net"]; tc += r["carton"]; tbox += r["cutii"]
        flag = "  ⚠ " + "; ".join(r["warn"]) if r["warn"] else ""
        print(f"{r['container']:<10}{r['brut']:>10,.0f}{r['net']:>10,.0f}{r['carton']:>11,.0f}{r['cutii']:>8}  {r['sheet']}{flag}")
    print(f"{'TOTAL':<10}{tb:>10,.0f}{tn:>10,.0f}{tc:>11,.0f}{tbox:>8}")
    for e in errs:
        print(f"EROARE {e}")
    print(f"\nDe introdus in portalul Ecorom (luna {a.luna or '...'}):")
    print(f"  rand HARTIE → coloana 'Ambalaj secundar si de transport' → 'in flux comercial' = {tc:,.0f} kg")
    print("  restul randurilor goale; 'din care reutilizabil' gol. Salveaza.")


if __name__ == "__main__":
    main()
