# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pg8000>=1.30",
#   "google-api-python-client>=2.0",
#   "google-auth>=2.0",
# ]
# ///
"""
sourcing_radar.py — Radar de SOURCING din motorul de competitive-intelligence (arona-bi):
ce produse se vând cel mai REPEDE la competiție (din 50+ site-uri RO scrape-uite zilnic),
ca să decizi ce să aduci/lansezi. Viteza (ads30_cal) e inferată din scăderile de stoc.

Sursă: arona-bi public.mv_best_sellers_ranked (213k produse cu viteză live, fresh azi).
NU e pricewatch (ăla = listă de URL-uri urmărite manual); ăsta minează tot motorul.

ANTI-ZGOMOT (cheia ca să fie util): unele site-uri raportează STOC PLACEHOLDER (jysk
median 1016, eiluminat 945, souqshop 10906, 999999994...) → viteza inferată e gunoi. Le
excludem dinamic (parseri cu median latest_stock > --placeholder-stock) + plafon pe rând.
Vivre (stoc 0 dar 115k produse) e tratat separat (--include-vivre).

Usage:
  uv run sourcing_radar.py                          # top fast-movers, site-uri cu stoc real
  uv run sourcing_radar.py --search covor --limit 30   # ce covoare se vând la competiție
  uv run sourcing_radar.py --parser vevor --min-price 30 --max-price 150
  uv run sourcing_radar.py --search "raft|depozitare" --sheet
  uv run sourcing_radar.py --min-vel 20 --days 7 --sheet
"""
import argparse, json, os, subprocess, sys
from datetime import timedelta


def _find_kb():
    if os.getenv("KB_PY") and os.path.exists(os.getenv("KB_PY")):
        return os.getenv("KB_PY")
    d = os.getcwd()
    for _ in range(9):
        cand = os.path.join(d, "team-intelligence", "plugins", "core", "scripts", "kb.py")
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d)
    return None


def _kb_secret(key):
    kb = _find_kb()
    if not kb:
        return None
    out = subprocess.run(["uv", "run", kb, "secret-get", key], capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def bi_conn():
    import pg8000.dbapi, urllib.parse as up
    url = os.getenv("DATABASE_URL_ARONA_BI") or _kb_secret("DATABASE_URL_ARONA_BI")
    if not url:
        raise SystemExit("Lipsește DATABASE_URL_ARONA_BI (env sau KB).")
    u = up.urlparse(url)
    return pg8000.dbapi.connect(user=up.unquote(u.username or ""), password=up.unquote(u.password or ""),
                                host=u.hostname, port=u.port or 5432,
                                database=(u.path or "/").lstrip("/"), ssl_context=True)


def write_sheet(header, rows, title):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    raw = _kb_secret("GOOGLE_OAUTH_TOKEN_JSON")
    if not raw:
        raise SystemExit("Lipsește GOOGLE_OAUTH_TOKEN_JSON din KB.")
    info = json.loads(raw)
    creds = Credentials.from_authorized_user_info(info, scopes=info.get("scopes"))
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    svc = build("sheets", "v4", credentials=creds).spreadsheets()
    sh = svc.create(body={"properties": {"title": title}}).execute()
    sid = sh["spreadsheetId"]; gid = sh["sheets"][0]["properties"]["sheetId"]; tab = sh["sheets"][0]["properties"]["title"]
    try:
        build("drive", "v3", credentials=creds).permissions().create(
            fileId=sid, body={"type": "anyone", "role": "writer"}).execute()
    except Exception as e:
        print(f"  ⚠ share eșuat: {str(e)[:100]}", file=sys.stderr)
    svc.values().update(spreadsheetId=sid, range=f"'{tab}'!A1", valueInputOption="USER_ENTERED",
                        body={"values": [header] + rows}).execute()
    svc.batchUpdate(spreadsheetId=sid, body={"requests": [
        {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                 "backgroundColor": {"red": .93, "green": .93, "blue": .93}}},
                        "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        {"updateSheetProperties": {"properties": {"sheetId": gid, "gridProperties": {"frozenRowCount": 1}},
                                   "fields": "gridProperties.frozenRowCount"}},
        {"autoResizeDimensions": {"dimensions": {"sheetId": gid, "dimension": "COLUMNS",
                                  "startIndex": 0, "endIndex": len(header)}}},
    ]}).execute()
    return f"https://docs.google.com/spreadsheets/d/{sid}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--search", help="cuvânt-cheie în numele produsului (regex POSIX, ex: 'covor|presul')")
    ap.add_argument("--parser", help="filtru pe un site/parser (ex: vevor, aosom.ro, Bonami)")
    ap.add_argument("--vendor", help="filtru pe vendor")
    ap.add_argument("--min-vel", type=float, default=0, help="viteză minimă (ads30_cal)")
    ap.add_argument("--min-price", type=float)
    ap.add_argument("--max-price", type=float)
    ap.add_argument("--max-stock", type=int, default=5000, help="plafon stoc/rând (anti-outlier placeholder)")
    ap.add_argument("--placeholder-stock", type=int, default=500,
                    help="parserii cu median latest_stock peste asta = placeholder, excluși")
    ap.add_argument("--days", type=int, default=14, help="doar produse vândute în ultimele N zile")
    ap.add_argument("--include-placeholder", action="store_true", help="NU exclude site-urile cu stoc placeholder")
    ap.add_argument("--include-vivre", action="store_true", help="include Vivre (stoc netrack-uit, doar viteză)")
    ap.add_argument("--sheet", action="store_true")
    args = ap.parse_args()

    conn = bi_conn(); cur = conn.cursor()
    cur.execute("SELECT max(last_sold_day) FROM public.mv_best_sellers_ranked")
    max_day = cur.fetchone()[0]
    cutoff = max_day - timedelta(days=args.days)
    fresh = str(max_day)

    where = ["m.ads30_cal > %s", "m.latest_stock <= %s", "m.last_sold_day >= %s"]
    params = [max(args.min_vel, 0.0001), args.max_stock, cutoff]
    if not args.include_placeholder:
        where.append("""m.parser_name NOT IN (
            SELECT parser_name FROM public.mv_best_sellers_ranked WHERE ads30_cal>0
            GROUP BY parser_name HAVING percentile_cont(0.5) WITHIN GROUP (ORDER BY latest_stock) > %s)""")
        params.append(args.placeholder_stock)
    if not args.include_vivre:
        where.append("lower(m.parser_name) <> 'vivre'")
    if args.search:
        where.append("m.name ~* %s"); params.append(args.search)
    if args.parser:
        where.append("lower(m.parser_name) = lower(%s)"); params.append(args.parser)
    if args.vendor:
        where.append("lower(m.vendor) = lower(%s)"); params.append(args.vendor)
    if args.min_price is not None:
        where.append("m.price >= %s"); params.append(args.min_price)
    if args.max_price is not None:
        where.append("m.price <= %s"); params.append(args.max_price)

    sql = f"""
      SELECT m.parser_name, m.vendor, m.name, m.price, m.latest_stock,
             round(m.ads30_cal,1), m.last_sold_day::text, m.url
      FROM public.mv_best_sellers_ranked m
      WHERE {' AND '.join(where)}
      ORDER BY m.ads30_cal DESC LIMIT %s"""
    params.append(args.limit)

    cur.execute(sql, params)
    rows = cur.fetchall()
    conn.close()

    flt = []
    if args.search: flt.append(f"search='{args.search}'")
    if args.parser: flt.append(f"parser={args.parser}")
    if args.min_vel: flt.append(f"vel≥{args.min_vel}")
    if args.min_price or args.max_price: flt.append(f"preț {args.min_price or 0}-{args.max_price or '∞'}")
    print(f"Radar sourcing · arona-bi (date la zi {fresh[:10]}) · {', '.join(flt) or 'fără filtre'} · "
          f"placeholder excluși: {not args.include_placeholder}", file=sys.stderr)

    header = ["#", "Site", "Vendor", "Produs", "Preț", "Stoc", "Viteză 30z", "Ultima vânz.", "URL"]
    out = []
    for i, (pn, vn, nm, pr, st, vel, lsd, url) in enumerate(rows, 1):
        out.append([i, pn, (vn or "")[:22], (nm or "")[:60], round(float(pr or 0), 2),
                    st, vel, (lsd or "")[:10], url or ""])
    print(f"\n=== Top {len(out)} produse care se vând cel mai repede la competiție ===")
    print("  " + " | ".join(["#", "Site", "Produs", "Preț", "Stoc", "Vel30"]))
    for r in out:
        print(f"  {r[0]:>3} | {r[1][:12]:12} | {r[3][:46]:46} | {r[4]:>7} | {r[5]:>6} | {r[6]}")

    if args.sheet:
        title = "Sourcing radar competiție" + (f" — {args.search}" if args.search else "") + f" — {fresh[:10]}"
        print(f"\n✅ Google Sheet: {write_sheet(header, [list(map(str, r)) for r in out], title)}")


if __name__ == "__main__":
    main()
