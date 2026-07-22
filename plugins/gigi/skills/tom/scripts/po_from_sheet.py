#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.0", "google-auth>=2.0", "psycopg2-binary", "requests"]
# ///
"""po_from_sheet.py — Google Sheet de comandă → payload gata de `tom.py po-create`.

Sheet-urile de PO ale echipei au mereu aceeași formă: un rând de header (SKU / Produs /
DE COMANDAT / Cost (USD) / Foto) și un rând TOTAL la final. Scriptul mapează coloanele
DUPĂ HEADER (nu după index — fiecare tab are alt layout), scoate poza fiecărei linii și
validează payload-ul înainte să comande marfă reală.

  po_from_sheet.py --sheet <ID> --tab "PO HA NOU 21.07" --source-po-id PO-0015 \
      --title "Black Friday 2026 3" --priority HIGH --requester Gigi --out payload.json
  tom.py po-create VIGO --json @payload.json --yes     # abia asta comandă

Trei capcane pe care le rezolvă (toate ne-au costat deja):
1. **Poza e o FORMULĂ `=IMAGE("...")`** în coloana Foto — invizibilă dacă citești valorile
   normal (`values.get` întoarce ""). Fără `valueRenderOption=FORMULA` pari fără poze și
   ajungi să trimiți placeholder-ul, care în TOM e un URL 404.
2. **Sheet-ul e editat în timp ce-l citești.** Citirile succesive dau alt număr de linii.
   De aceea comparăm suma calculată cu rândul TOTAL din ACELAȘI snapshot și refuzăm dacă
   nu se potrivește — mai bine reia, decât să comanzi altceva decât scrie în sheet.
3. **Un SKU are câte un rând `products` per magazin**, iar unele n-au deloc imagini. Când
   luăm poza din DB ne uităm peste TOATE rândurile SKU-ului, nu doar peste primul.

Secrete: DATABASE_URL_TOM / DATABASE_URL_AWBPRINT din KB. Nimic nu se printează.
"""
import argparse, json, os, re, subprocess, sys, urllib.parse as up
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SA_KEY = os.environ.get("GOOGLE_SA_KEY", str(Path.home() / "Downloads/Scripturi/google_credentials.json"))
SUBJECT = os.environ.get("GOOGLE_SA_SUBJECT", "gheorghe.beschea@overheat.agency")
PLACEHOLDER = "placeholder"


def kb_path():
    for p in [Path.home() / ".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py",
              Path(__file__).resolve().parents[4] / "core/scripts/kb.py"]:
        if p.exists():
            return str(p)
    return None


def secret(key):
    if os.environ.get(key):
        return os.environ[key]
    kb = kb_path()
    if not kb:
        return None
    return subprocess.run(["uv", "run", kb, "secret-get", key], capture_output=True, text=True).stdout.strip()


def db(key):
    import psycopg2
    dsn = secret(key)
    if not dsn:
        return None
    p = up.urlsplit(dsn)                       # DSN-ul TOM are ?schema= → psycopg2 crapă
    return psycopg2.connect(up.urlunsplit((p.scheme, p.netloc, p.path, "", "")))


def num(x):
    try:
        return float(re.sub(r"[^\d.\-]", "", str(x)) or 0)
    except ValueError:
        return 0.0


# ── coloane: mapate pe HEADER, nu pe index ────────────────────────────────
WANT = {
    "sku":   lambda h: h == "sku",
    "title": lambda h: h.startswith(("produs", "product", "denumire")),
    "qty":   lambda h: "de comandat" in h or h.startswith(("cantitate", "qty")),
    "usd":   lambda h: "cost" in h and "usd" in h,
    "foto":  lambda h: h.startswith(("foto", "photo", "image", "poza")),
}


def find_header(rows):
    for i, r in enumerate(rows[:15]):
        cells = [str(c).strip().lower() for c in r]
        if any(c == "sku" for c in cells):
            col = {}
            for key, test in WANT.items():
                for j, c in enumerate(cells):
                    if test(re.sub(r"\s+", " ", c)):
                        col.setdefault(key, j)
            return i, col
    sys.exit("nu găsesc rândul de header (nicio celulă 'SKU') — verifică tab-ul")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", required=True); ap.add_argument("--tab", required=True)
    ap.add_argument("--source-po-id", required=True); ap.add_argument("--title")
    ap.add_argument("--type", default="RESTOCK", choices=["RESTOCK", "NEW_PRODUCT"])
    ap.add_argument("--priority", choices=["STANDARD", "HIGH"])
    ap.add_argument("--requester", help="numele care apare în TOM ca requester (ex. Gigi)")
    ap.add_argument("--out", default="payload.json")
    ap.add_argument("--no-checksum", action="store_true", help="sari peste verificarea cu rândul TOTAL")
    a = ap.parse_args()

    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_service_account_file(
        SA_KEY, scopes=["https://www.googleapis.com/auth/drive"]).with_subject(SUBJECT)
    sv = build("sheets", "v4", credentials=creds).spreadsheets().values()
    rows = sv.get(spreadsheetId=a.sheet, range=f"'{a.tab}'").execute().get("values", [])
    if not rows:
        sys.exit("tab gol")
    hdr, col = find_header(rows)
    for need in ("sku", "qty"):
        if need not in col:
            sys.exit(f"nu găsesc coloana '{need}' în header: {rows[hdr]}")

    # poza e formulă =IMAGE("...") → invizibilă în render-ul normal
    formulas = []
    if "foto" in col:
        letter = chr(ord("A") + col["foto"])
        formulas = [(r[0] if r else "") for r in sv.get(
            spreadsheetId=a.sheet, range=f"'{a.tab}'!{letter}1:{letter}",
            valueRenderOption="FORMULA").execute().get("values", [])]
    formulas += [""] * (len(rows) - len(formulas))

    items, skipped = [], []
    for i, r in enumerate(rows[hdr + 1:], start=hdr + 1):
        r = list(r) + [""] * (max(col.values()) + 2 - len(r))
        sku, qty = str(r[col["sku"]]).strip(), int(num(r[col["qty"]]))
        if not sku or qty <= 0:
            if sku:
                skipped.append((sku, r[col["qty"]]))
            continue
        m = re.search(r'IMAGE\("([^"]+)"', str(formulas[i]))
        img = m.group(1) if m else next((str(c).strip() for c in r if str(c).strip().startswith("http")), "")
        items.append({"source_line_id": sku, "sku": sku,
                      "title": str(r[col["title"]]).strip() if "title" in col else sku,
                      "image_url": img, "requested_qty": qty,
                      "requested_unit_cost_usd": num(r[col["usd"]]) if "usd" in col else 0.0})

    # imagini lipsă: TOM → AWBprint (prima validă peste TOATE rândurile SKU-ului)
    need = [it["sku"].lower() for it in items if not it["image_url"].startswith("http")]
    found = {}
    if need:
        c = db("DATABASE_URL_TOM")
        if c:
            cur = c.cursor()
            cur.execute('SELECT lower(sku), "imageUrl" FROM products WHERE lower(sku) = ANY(%s)', (need,))
            found.update({s: u for s, u in cur.fetchall()
                          if u and u.startswith("http") and PLACEHOLDER not in u.lower()})
            c.close()
        rest = [s for s in need if s not in found]
        c = db("DATABASE_URL_AWBPRINT") if rest else None
        if c:
            cur = c.cursor()
            cur.execute("SELECT lower(sku), images FROM products WHERE lower(sku) = ANY(%s)", (rest,))
            for s, imgs in cur.fetchall():
                if s in found or not imgs:
                    continue
                if isinstance(imgs, str):
                    try:
                        imgs = json.loads(imgs)
                    except ValueError:
                        continue
                u = (imgs[0] or {}).get("src", "") if isinstance(imgs, list) and imgs and isinstance(imgs[0], dict) else ""
                if u.startswith("http") and PLACEHOLDER not in u.lower():
                    found[s] = u
            c.close()
    for it in items:
        if not it["image_url"].startswith("http"):
            it["image_url"] = found.get(it["sku"].lower(), "")

    qty = sum(i["requested_qty"] for i in items)
    val = sum(i["requested_qty"] * i["requested_unit_cost_usd"] for i in items)
    print(f"{len(items)} linii · {qty:,} buc · ${val:,.2f}")
    if skipped:
        print(f"sărite (cantitate 0/goală): {len(skipped)} → {[s for s, _ in skipped][:8]}")

    # checksum contra rândului TOTAL din ACELAȘI snapshot (sheet editat live)
    tot = next((r for r in rows if any("total" in str(c).lower() for c in r)), None)
    if tot and not a.no_checksum:
        nums = [num(c) for c in tot if num(c)]
        ok_q = any(abs(v - qty) < 1 for v in nums)
        ok_v = any(abs(v - val) < max(50, val * 0.001) for v in nums)
        print(f"checksum rând TOTAL: buc {'OK' if ok_q else 'NU'} · valoare {'OK' if ok_v else 'NU'}")
        if not (ok_q and ok_v):
            sys.exit("⛔ sumele nu se potrivesc cu rândul TOTAL — sheet-ul s-a schimbat sub mine "
                     "sau maparea coloanelor e greșită. Reia (sau --no-checksum dacă știi ce faci).")

    bad = [i["sku"] for i in items if not i["image_url"].startswith("http")]
    dupes = {i["sku"] for i in items if [x["sku"] for x in items].count(i["sku"]) > 1}
    nocost = [i["sku"] for i in items if not i["requested_unit_cost_usd"]]
    if bad:
        print(f"⛔ FĂRĂ POZĂ ({len(bad)}): {bad}  — TOM cere image_url valid (422 altfel)")
    if dupes:
        print(f"⛔ source_line_id duplicat: {sorted(dupes)}")
    if nocost:
        print(f"⚠ fără cost USD: {nocost}")
    if bad or dupes:
        sys.exit("nu scriu payload-ul cât timp există erori blocante")

    payload = {"source_po_id": a.source_po_id, "type": a.type, "items": items}
    if a.title:
        payload["title"] = a.title
    if a.priority:
        payload["priority"] = a.priority          # PO-level: STANDARD | HIGH
    if a.requester:
        payload["requester"] = {"external_name": a.requester}
    Path(a.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"✅ scris → {a.out}\n   trimite cu:  tom.py po-create VIGO --json @{a.out} --yes")


if __name__ == "__main__":
    main()
