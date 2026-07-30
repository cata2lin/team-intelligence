# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client","google-auth"]
# ///
"""
sheet_forensics.py — de ce arată sheet-ul cifra ASTA?

Citește un Google Sheet ca FORMULE (nu ca valori) și scoate la iveală ce nu se vede în UI:
constante hardcodate, celule „editabile" care nu sunt referite de nimic, potriviri pe text
care nu se potrivesc, și taburi care se contrazic pe aceeași cheie.

    sheet_forensics.py tabs   <SHEET_ID>
    sheet_forensics.py formulas <SHEET_ID> --tab "Raport azi" [--row 21] [--cols A:H]
    sheet_forensics.py consts <SHEET_ID> --tab "COGS 2026"     # constante numerice în formule
    sheet_forensics.py deadrefs <SHEET_ID> --tab "Raport azi"  # celule pe care nu le referă nimeni
    sheet_forensics.py lookups <SHEET_ID> --tab "Raport azi"   # literalii căutați + dacă EXISTĂ în tabul sursă
    sheet_forensics.py compare <SHEET_ID> --key SKU --col COST --tabs "COGS,1 iulie,COGS 2026"
"""
import argparse, re, sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def svc(creds):
    cr = Credentials.from_service_account_file(creds, scopes=SCOPES)
    return build("sheets", "v4", credentials=cr, cache_discovery=False).spreadsheets()


def get(s, sid, rng, mode):
    return s.values().get(spreadsheetId=sid, range=rng,
                          valueRenderOption=mode).execute().get("values", [])


def colname(j):
    n, out = j + 1, ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def cmd_tabs(a, s):
    m = s.get(spreadsheetId=a.sheet, fields="properties.title,sheets.properties(title,sheetId,index)").execute()
    print("TITLU:", m["properties"]["title"])
    for sh in m["sheets"]:
        p = sh["properties"]
        print(f"  [{p.get('index')}] {p['title']!r}  gid={p['sheetId']}")


def cmd_formulas(a, s):
    rng = f"'{a.tab}'!{a.cols}" if a.cols else f"'{a.tab}'"
    vals = get(s, a.sheet, rng, "FORMULA")
    shown = get(s, a.sheet, rng, "UNFORMATTED_VALUE")
    for i, row in enumerate(vals, 1):
        if a.row and i != a.row:
            continue
        for j, c in enumerate(row):
            cs = str(c)
            if not cs.strip():
                continue
            val = ""
            if i - 1 < len(shown) and j < len(shown[i - 1]):
                val = str(shown[i - 1][j])[:24]
            tag = "ƒ" if cs.startswith("=") else " "
            print(f"  {tag} {colname(j)}{i}: {cs[:190]}")
            if cs.startswith("=") and val:
                print(f"        → {val}")


def cmd_consts(a, s):
    """Constante numerice hardcodate în formule — cursuri, TVA, taxe, praguri."""
    vals = get(s, a.sheet, f"'{a.tab}'", "FORMULA")
    hits = defaultdict(list)
    for i, row in enumerate(vals, 1):
        for j, c in enumerate(row):
            cs = str(c)
            if not cs.startswith("="):
                continue
            for num in re.findall(r"(?<![A-Za-z0-9_.:$])(\d+[.,]\d+|\d{2,})(?![0-9.:])", cs):
                hits[num].append(f"{colname(j)}{i}")
    print(f"constante numerice hardcodate în '{a.tab}':\n")
    for num, cells in sorted(hits.items(), key=lambda kv: -len(kv[1]))[:30]:
        print(f"  {num:>12}  în {len(cells):>4} celule   ex: {', '.join(cells[:5])}")
    print("\n⚠️  O constantă repetată în sute de celule = un curs/procent care NU se poate schimba")
    print("    dintr-o singură celulă, oricât ar sugera antetul contrariul (vezi `deadrefs`).")


def cmd_deadrefs(a, s):
    """Celule cu ETICHETĂ de parametru („curs", „editabil"…) pe care NICIO formulă nu le referă."""
    vals = get(s, a.sheet, f"'{a.tab}'", "FORMULA")
    text = "\n".join(str(c) for row in vals for c in row)
    suspects = []
    for i, row in enumerate(vals, 1):
        for j, c in enumerate(row):
            cs = str(c)
            low = cs.lower()
            if any(k in low for k in ("editabil", "curs", "modifica", "schimb", "parametr", "setare")):
                # caută vecinii pe rând care ar putea fi valoarea
                for jj in range(max(0, j - 2), min(len(row), j + 3)):
                    ref = f"{colname(jj)}{i}"
                    if re.search(rf"[^A-Z0-9]\${{0,1}}{colname(jj)}\${{0,1}}{i}(?![0-9])", text):
                        continue
                    v = str(row[jj]) if jj < len(row) else ""
                    if v.strip() and not v.startswith("="):
                        suspects.append((ref, v[:24], cs[:60]))
    seen = set()
    print("celule care PAR parametri dar NU sunt referite de nicio formulă:\n")
    for ref, v, label in suspects:
        if ref in seen:
            continue
        seen.add(ref)
        print(f"  {ref:>6} = {v:<24} — eticheta zice: {label!r}")
    if not seen:
        print("  (niciuna)")
    print("\n⚠️  Astea sunt COMENZI FALSE: le editezi și nu se schimbă nimic în calcul.")


def _is_lookup_key(lit: str) -> bool:
    """Ține doar literalii care arată a CHEIE de căutat (nume de brand/magazin/SKU).

    Formulele conțin și fragmente de REGEX și operatori ("[^A-Z0-9]", ");", ">=") care
    ajung în ghilimele dar nu sunt chei — fără filtrul ăsta raportul e plin de fals-pozitive.
    """
    l = lit.strip()
    if len(l) < 3:
        return False
    if any(ch in l for ch in "[]^|*+\\$(){}"):          # metacaractere regex
        return False
    if l in (">=", "<=", "<>", "!=") or l.startswith((";", ")", "(")):
        return False
    return any(ch.isalnum() for ch in l)                 # trebuie să aibă litere/cifre


def cmd_lookups(a, s):
    """Literalii de text căutați în formule (SUMIFS/FILTER/VLOOKUP) + dacă EXISTĂ în tabul sursă."""
    vals = get(s, a.sheet, f"'{a.tab}'", "FORMULA")
    pat = re.compile(r"'([^']+)'!\s*([A-Z]+):\1?[A-Z]*|'([^']+)'!")
    found = []
    for i, row in enumerate(vals, 1):
        for j, c in enumerate(row):
            cs = str(c)
            if not cs.startswith("="):
                continue
            tabs = set(re.findall(r"'([^']+)'!", cs))
            lits = re.findall(r'"([^"]{2,60})"', cs)
            for t in tabs:
                for lit in lits:
                    if not _is_lookup_key(lit):
                        continue
                    found.append((f"{colname(j)}{i}", t, lit))
    # verifică existența literalilor în taburile sursă
    cache = {}
    print(f"{'celulă':>7}  {'caută în tab':28} {'literal':32} există?")
    print("-" * 86)
    seen = set()
    for cell, tab, lit in found:
        k = (tab, lit)
        if k in seen:
            continue
        seen.add(k)
        if tab not in cache:
            try:
                rows = get(s, a.sheet, f"'{tab}'", "UNFORMATTED_VALUE")
            except Exception:
                rows = []
            # set de VALORI DE CELULĂ, lowercase — SUMIFS/FILTER din Sheets potrivesc
            # case-INSENSITIV pe celula întreagă, nu pe substring. O comparație
            # case-sensitive sau pe substring dă fals-pozitive ("Casa ofertelor"
            # vs "Casa Ofertelor" se potrivesc de fapt perfect în Sheets).
            cache[tab] = {str(x).strip().lower() for r in rows for x in r if str(x).strip()}
        low = lit.strip().lower()
        if low in cache[tab]:
            ok = "✅"
        else:
            near = [v for v in cache[tab] if low in v or v in low][:1]
            ok = f"❌ LIPSEȘTE (aproape: {near[0][:26]!r})" if near else "❌ LIPSEȘTE"
        print(f"{cell:>7}  {tab[:28]:28} {lit[:32]:32} {ok}")
    print("\n⚠️  Un ❌ = SUMIFS/FILTER care nu potrivește nimic → rezultat 0, fără nicio eroare vizibilă.")
    print("   (potrivirea e case-INSENSITIVĂ, ca în Sheets — diferența de MAJUSCULE nu e bug)")


def cmd_compare(a, s):
    tabs = [t.strip() for t in a.tabs.split(",")]
    data = {}
    for t in tabs:
        try:
            vals = get(s, a.sheet, f"'{t}'", "UNFORMATTED_VALUE")
        except Exception as e:
            print(f"  {t}: eroare {str(e)[:60]}")
            continue
        hi = next((i for i, r in enumerate(vals[:12]) if sum(1 for c in r if str(c).strip()) >= 2), 0)
        hdr = [str(c).strip().lower() for c in vals[hi]]
        if a.key.lower() not in hdr or a.col.lower() not in hdr:
            print(f"  {t}: nu are coloanele {a.key}/{a.col} (are: {hdr[:8]})")
            continue
        ki, ci = hdr.index(a.key.lower()), hdr.index(a.col.lower())
        d = {}
        for r in vals[hi + 1:]:
            if len(r) > ki and str(r[ki]).strip():
                d[str(r[ki]).strip().lower()] = r[ci] if len(r) > ci else None
        data[t] = d
    keys = set().union(*[set(d) for d in data.values()]) if data else set()
    diff = []
    for k in sorted(keys):
        vals_ = {t: data[t].get(k) for t in data if k in data[t]}
        uniq = {str(v) for v in vals_.values() if v not in (None, "")}
        if len(uniq) > 1:
            diff.append((k, vals_))
    print(f"chei totale: {len(keys)} | CONTRAZICERI între taburi: {len(diff)}\n")
    for k, vals_ in diff[:40]:
        print(f"  {k[:34]:34} " + "  ".join(f"{t[:12]}={v}" for t, v in vals_.items()))
    print("\n⚠️  Un tab care se contrazice cu altul pe aceeași cheie NU poate fi sursă de adevăr.")


def main():
    ap = argparse.ArgumentParser(description="Forensics pe Google Sheets — de ce iese cifra asta.")
    ap.add_argument("--creds", default="/Users/gheorghebeschea/Downloads/Scripturi/google_credentials.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("tabs", "formulas", "consts", "deadrefs", "lookups", "compare"):
        p = sub.add_parser(name)
        p.add_argument("sheet")
        if name != "tabs":
            p.add_argument("--tab", required=True)
        if name == "formulas":
            p.add_argument("--row", type=int)
            p.add_argument("--cols", default="A1:Z200")
        if name == "compare":
            p.add_argument("--key", required=True)
            p.add_argument("--col", required=True)
            p.add_argument("--tabs", required=True)
    a = ap.parse_args()
    s = svc(a.creds)
    {"tabs": cmd_tabs, "formulas": cmd_formulas, "consts": cmd_consts,
     "deadrefs": cmd_deadrefs, "lookups": cmd_lookups, "compare": cmd_compare}[a.cmd](a, s)


if __name__ == "__main__":
    main()
