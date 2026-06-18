# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "pg8000>=1.30",
#   "google-api-python-client>=2.0",
#   "google-auth>=2.0",
# ]
# ///
"""
grandia_lifecycle.py — Kill-list / Scale-list + drop-off de funnel GA4 pentru Grandia,
dintr-o singură citire pre-agregată din DB-ul Grandia (instant), nu din 3 API-uri lente
(Shopify + Google Ads + GA4). Grandia ține deja un panel zilnic per-produs
(rpt_product_status_daily: profit_30d, ad_spend_30d, days_of_stock, conversion_rate_30d)
+ funnel GA4 (ga4_daily_product_metrics: sessions/viewed/addToCart/purchased).

Join pe 3 tabele (cheile NU se potrivesc direct): rpt.product_id (CUID) → Product.id →
Product.shopifyNumericId (bigint) → ga4.shopifyProductId.

Rapoarte (--report):
  summary  — câte produse de tăiat / de scalat / cu drop-off + impact RON (default)
  kill     — cheltuie pe ads dar pierd bani (ad_spend_30d>0 AND profit_30d<0) → de oprit/exclus
  scale    — profitabile dar aproape rupte de stoc (profit_30d>0 AND days_of_stock<N) → de reaprovizionat & scalat
  cro      — cerere irosită: oameni adaugă în coș dar NU cumpără (GA4 addToCart vs purchased)

Usage:
  uv run grandia_lifecycle.py                       # summary
  uv run grandia_lifecycle.py --report kill --limit 30
  uv run grandia_lifecycle.py --report scale --stock-days 14
  uv run grandia_lifecycle.py --report cro --min-atc 3
  uv run grandia_lifecycle.py --report kill --sheet
"""
import argparse, json, os, subprocess, sys


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


def gconn():
    import pg8000.dbapi, urllib.parse as up
    url = os.getenv("DATABASE_URL_GRANDIA") or _kb_secret("DATABASE_URL_GRANDIA")
    if not url:
        raise SystemExit("Lipsește DATABASE_URL_GRANDIA (env sau KB).")
    u = up.urlparse(url)
    return pg8000.dbapi.connect(user=up.unquote(u.username or ""), password=up.unquote(u.password or ""),
                                host=u.hostname, port=u.port or 5432,
                                database=(u.path or "/").lstrip("/"), ssl_context=True)


# CTE comun: ultima zi din panel + funnel GA4 pe 30 zile, join CUID→shopifyNumericId→ga4
CTE = """
WITH latest AS (SELECT max(date) d FROM rpt_product_status_daily),
ga AS (
  SELECT "shopifyProductId" spid,
         sum("sessionsWithProduct") sess, sum("itemsViewed") viewed,
         sum("itemsAddedToCart") atc, sum("itemsPurchased") purch
  FROM ga4_daily_product_metrics
  WHERE "reportDate" >= (SELECT max("reportDate") FROM ga4_daily_product_metrics) - 29
  GROUP BY 1
),
base AS (
  SELECT p.title, r.marketing_status, r.profit_status, r.inventory_status,
         r.ad_spend_30d, r.revenue_30d, r.profit_30d, r.profit_margin_30d,
         r.days_of_stock, r.stock_qty, r.conversion_rate_30d,
         g.sess, g.viewed, g.atc, g.purch
  FROM rpt_product_status_daily r
  JOIN latest ON r.date = latest.d
  JOIN "Product" p ON p.id = r.product_id
  LEFT JOIN ga g ON g.spid = p."shopifyNumericId"
)
"""


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


def r0(x):
    return round(float(x)) if x is not None else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", choices=["summary", "kill", "scale", "cro"], default="summary")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--stock-days", type=int, default=14, help="scale: prag days_of_stock")
    ap.add_argument("--min-atc", type=int, default=2, help="cro: min add-to-cart ca să nu fie zgomot")
    ap.add_argument("--sheet", action="store_true")
    args = ap.parse_args()

    conn = gconn(); cur = conn.cursor()
    cur.execute("SELECT max(date)::text, (SELECT max(\"reportDate\")::text FROM ga4_daily_product_metrics) FROM rpt_product_status_daily")
    rpt_d, ga4_d = cur.fetchone()
    print(f"Grandia lifecycle · panel {rpt_d} · GA4 {ga4_d} (Grandia-only)", file=sys.stderr)

    if args.report == "summary":
        cur.execute(CTE + """
          SELECT count(*),
            count(*) FILTER (WHERE ad_spend_30d>0 AND profit_30d<0),
            round(sum(profit_30d) FILTER (WHERE ad_spend_30d>0 AND profit_30d<0)),
            count(*) FILTER (WHERE profit_30d>0 AND days_of_stock<%s),
            round(sum(profit_30d) FILTER (WHERE profit_30d>0 AND days_of_stock<%s)),
            count(*) FILTER (WHERE atc>0 AND coalesce(purch,0)=0),
            coalesce(sum(atc) FILTER (WHERE atc>0 AND coalesce(purch,0)=0),0)
          FROM base""", (args.stock_days, args.stock_days))
        tot, nk, kloss, nsc, sprof, ncro, croatc = cur.fetchone()
        header = ["Bucket", "Produse", "Impact 30z"]
        rows = [
            ["TOTAL produse cu panel", tot, ""],
            ["🔴 KILL (ads + pierdere)", nk, f"{r0(kloss):,} RON pierdere/30z"],
            ["🟢 SCALE (profit + stoc<%dz)" % args.stock_days, nsc, f"+{r0(sprof):,} RON profit/30z"],
            ["🟡 CRO drop-off (coș fără cumpărare)", ncro, f"{r0(croatc):,} add-to-cart irosite"],
        ]
        label = "rezumat lifecycle Grandia"
    elif args.report == "kill":
        cur.execute(CTE + """
          SELECT title, marketing_status, profit_status, days_of_stock, ad_spend_30d, revenue_30d,
                 profit_30d, profit_margin_30d, conversion_rate_30d
          FROM base WHERE ad_spend_30d>0 AND profit_30d<0
          ORDER BY profit_30d ASC LIMIT %s""", (args.limit,))
        header = ["Produs", "Marketing", "Profit st.", "Zile stoc", "Spend 30z", "Venit 30z", "Profit 30z", "Marjă %", "Conv %"]
        rows = [[t[:46], ms, ps, r0(dos), r0(sp), r0(rv), r0(pr), round(float(pm or 0), 1), round(float(cr or 0), 2)]
                for t, ms, ps, dos, sp, rv, pr, pm, cr in cur.fetchall()]
        label = "KILL-list (ads dar pierd bani)"
    elif args.report == "scale":
        cur.execute(CTE + """
          SELECT title, profit_30d, profit_margin_30d, days_of_stock, stock_qty, ad_spend_30d,
                 marketing_status, conversion_rate_30d
          FROM base WHERE profit_30d>0 AND days_of_stock<%s
          ORDER BY profit_30d DESC LIMIT %s""", (args.stock_days, args.limit))
        header = ["Produs", "Profit 30z", "Marjă %", "Zile stoc", "Stoc buc", "Spend 30z", "Marketing", "Conv %"]
        rows = [[t[:46], r0(pr), round(float(pm or 0), 1), r0(dos), r0(sq), r0(sp), ms, round(float(cr or 0), 2)]
                for t, pr, pm, dos, sq, sp, ms, cr in cur.fetchall()]
        label = f"SCALE-list (profitabile, stoc < {args.stock_days}z)"
    else:  # cro
        cur.execute(CTE + """
          SELECT title, sess, viewed, atc, coalesce(purch,0),
                 round(100.0*coalesce(purch,0)/nullif(atc,0),1), profit_status, conversion_rate_30d
          FROM base WHERE atc >= %s
          ORDER BY (coalesce(purch,0)::float/nullif(atc,0)) ASC, atc DESC LIMIT %s""", (args.min_atc, args.limit))
        header = ["Produs", "Sesiuni", "Vizualizări", "Add-to-cart", "Cumpărate", "ATC→buy %", "Profit st.", "Conv %"]
        rows = [[t[:42], r0(se), r0(vi), r0(atc), r0(pu), (rate if rate is not None else 0), ps, round(float(cr or 0), 2)]
                for t, se, vi, atc, pu, rate, ps, cr in cur.fetchall()]
        label = "CRO drop-off (coș → fără cumpărare)"
    conn.close()

    print(f"\n=== {label} · Grandia · {rpt_d} ===")
    print("  " + " | ".join(str(h) for h in header))
    for r in rows:
        print("  " + " | ".join(str(x) for x in r))

    if args.sheet:
        url = write_sheet(header, [list(map(str, r)) for r in rows], f"Grandia {label} — {rpt_d}")
        print(f"\n✅ Google Sheet: {url}")


if __name__ == "__main__":
    main()
