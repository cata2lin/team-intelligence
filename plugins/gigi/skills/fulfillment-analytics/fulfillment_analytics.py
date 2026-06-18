# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx>=0.27",
#   "pg8000>=1.30",
#   "google-api-python-client>=2.0",
#   "google-auth>=2.0",
# ]
# ///
"""
fulfillment_analytics.py — Analitică RAPIDĂ din AWBprint (DB AWB/Frisbo) pe TOATE
magazinele Arona. Postgres = instant, ~99% complet (mult peste metrics warehouse).

4 rapoarte (--report):
  refuse     — rată de refuz / retur per brand | curier | produs (line_items × status)
  sales      — venit + comenzi + bucăți per brand (sau --daily pe zi)
  transport  — cost REAL de curier per brand × curier (avg/colet, % din venit)
  stuck      — colete blocate (in_transit/pending) de > N zile, + "ghost" (AWB emis, nescanat)

Bucket-uri de status (din deliverability_calculation_reference.md):
  DELIVERED = delivered, customer_pickup
  RETURNED  = back_to_sender, returning_to_sender, incorrect_address, lost
  REFUSED   = refused, unsuccessful_delivery
  IN_TRANSIT= in_transit, fulfilled, redirected, deferred_delivery, on_hold, out_for_delivery
  PENDING   = waiting_for_courier, not_fulfilled, new, ready_for_pickup, not_created, created_awb
  refuse_rate = (RETURNED + REFUSED) / (DELIVERED + RETURNED + REFUSED)   [doar comenzi rezolvate]

Folosire:
  uv run fulfillment_analytics.py --report refuse --by brand --months 3
  uv run fulfillment_analytics.py --report refuse --by courier --days 30
  uv run fulfillment_analytics.py --report refuse --by product --stores EST,GT --limit 30
  uv run fulfillment_analytics.py --report sales --months 1 --daily
  uv run fulfillment_analytics.py --report transport --months 1
  uv run fulfillment_analytics.py --report stuck --days 7 --limit 50
"""
import argparse, csv, io, json, os, re, subprocess, sys, time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

import httpx

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Bucharest")
except Exception:
    TZ = timezone(timedelta(hours=2))

API_VERSION = "2024-10"

PREFIX_TO_STORE = {
    "EST": "esteban.ro", "GT": "georgetalent.ro", "OFER": "ofertelezilei.ro",
    "BON": "casaofertelor.ro", "BELA": "belasil.ro", "LUX": "nocturnalux.ro",
    "NOC": "nocturna.ro", "GEN": "gento.ro", "ROSSI": "rossinails.ro",
    "APR": "apreciat.ro", "RED": "reduceribune.ro", "CARP": "carpetto.ro",
    "PAT": "cepatai.ro", "GRAN": "grandia.ro", "MAG": "magdeal.ro",
    "COV": "covoria.ro", "NUB": "nubra", "CZ": "bonhaus.cz", "PL": "bonhaus.pl",
    "BG": "bonhaus.bg",
}

S_DELIVERED = {"delivered", "customer_pickup", "administrative_closure"}
S_RETURNED = {"back_to_sender", "returning_to_sender", "incorrect_address", "lost", "received_by_sender"}
S_REFUSED = {"refused", "unsuccessful_delivery"}
S_TRANSIT = {"in_transit", "fulfilled", "redirected", "deferred_delivery", "on_hold", "out_for_delivery"}
S_PENDING = {"waiting_for_courier", "not_fulfilled", "new", "ready_for_pickup", "not_created", "created_awb"}


def bucket(status):
    s = (status or "").lower()
    if s in S_DELIVERED: return "delivered"
    if s in S_RETURNED: return "returned"
    if s in S_REFUSED: return "refused"
    if s in S_TRANSIT: return "in_transit"
    if s in S_PENDING: return "pending"
    return "other"


# ════════════════════════ plumbing ════════════════════════
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


def awb_conn():
    import pg8000.dbapi, urllib.parse as up
    url = os.getenv("DATABASE_URL_AWBPRINT") or _kb_secret("DATABASE_URL_AWBPRINT")
    if not url:
        raise SystemExit("Lipsește DATABASE_URL_AWBPRINT (env sau KB).")
    u = up.urlparse(url)
    return pg8000.dbapi.connect(user=up.unquote(u.username or ""), password=up.unquote(u.password or ""),
                                host=u.hostname, port=u.port or 5432,
                                database=(u.path or "/").lstrip("/"), ssl_context=True)


def _stores_csv_text():
    env = os.getenv("SHOPIFY_STORES_CSV")
    if env:
        return env if "\n" in env else open(env, encoding="utf-8-sig").read()
    if os.path.exists("stores.csv"):
        return open("stores.csv", encoding="utf-8-sig").read()
    sec = _kb_secret("SHOPIFY_STORES_CSV")
    if sec:
        return sec
    raise SystemExit("Nu pot rezolva stores.csv pt titluri (env/cwd/KB).")


def resolve_store(prefix):
    for row in csv.DictReader(io.StringIO(_stores_csv_text())):
        if (row.get("prefix") or "").strip().lstrip("﻿").upper() == prefix.upper():
            shop = (row.get("shop") or "").strip().replace("https://", "").replace("http://", "").strip("/")
            return shop, (row.get("token") or "").strip()
    return None, None


def _shopify_post(client, url, headers, payload):
    for attempt in range(6):
        try:
            r = client.post(url, headers=headers, json=payload, timeout=60.0)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 2)) + attempt); continue
            if r.status_code != 200:
                raise SystemExit(f"HTTP {r.status_code}: {r.text[:200]}")
            return r.json()
        except httpx.HTTPError:
            time.sleep(1.0 + attempt)
    raise SystemExit("Renunț după erori repetate")


PRODUCTS_GQL = """
query($cursor: String) { products(first: 250, after: $cursor) {
  pageInfo { hasNextPage endCursor }
  edges { node { title variants(first: 50) { edges { node { sku } } } } } } }
"""


def fetch_titles(prefix):
    shop, token = resolve_store(prefix)
    if not token:
        return {}
    url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    titles, cursor = {}, None
    client = httpx.Client()
    while True:
        j = _shopify_post(client, url, headers, {"query": PRODUCTS_GQL, "variables": {"cursor": cursor}})
        data = (j.get("data") or {}).get("products") or {}
        for ed in data.get("edges") or []:
            node = (ed or {}).get("node") or {}
            t = (node.get("title") or "").strip()
            for ve in (node.get("variants") or {}).get("edges") or []:
                sku = str(((ve or {}).get("node") or {}).get("sku") or "").strip().lower()
                if sku and t:
                    titles.setdefault(sku, t)
        pi = data.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
    client.close()
    return titles


def months_ago(d, n):
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12; y -= 1
    leap = (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0))
    day = min(d.day, [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return d.replace(year=y, month=m, day=day)


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


def store_filter(stores):
    """Returnează (sql_clause, params) pt filtrul de magazine; stores=None/['ALL'] => toate."""
    if not stores or stores == ["ALL"]:
        return "", []
    names = [PREFIX_TO_STORE.get(p.upper(), p) for p in stores]
    return " AND s.name = ANY(%s)", [names]


# ════════════════════════ rapoarte ════════════════════════
def rep_refuse(cur, frm, to, by, stores, limit):
    sf, sp = store_filter(stores)
    if by == "product":
        cur.execute(f"""
          WITH li AS (
            SELECT lower(item->'inventory_item'->>'sku') sku, (item->>'quantity')::numeric qty,
                   o.aggregated_status agg
            FROM orders o JOIN stores s ON s.uid=o.store_uid
            CROSS JOIN LATERAL jsonb_array_elements(o.line_items::jsonb) item
            WHERE o.frisbo_created_at >= %s AND o.frisbo_created_at < %s {sf})
          SELECT sku, agg, sum(qty)::int FROM li WHERE sku IS NOT NULL AND sku<>'' GROUP BY sku, agg
        """, [frm, to] + sp)
        agg = defaultdict(lambda: defaultdict(int))
        for sku, st, q in cur.fetchall():
            agg[sku][bucket(st)] += q
        titles = {}
        for p in (stores or []):
            if p != "ALL":
                titles.update(fetch_titles(p))
        rows = []
        for sku, b in agg.items():
            resolved = b["delivered"] + b["returned"] + b["refused"]
            if resolved < 5:  # prag anti-zgomot
                continue
            rate = 100.0 * (b["returned"] + b["refused"]) / resolved
            rows.append((titles.get(sku, sku), sku, resolved, b["delivered"],
                         b["returned"], b["refused"], b["in_transit"] + b["pending"], round(rate, 1)))
        rows.sort(key=lambda r: r[-1], reverse=True)
        header = ["Produs", "SKU", "Rezolvate", "Livrate", "Returnate", "Refuzate", "În curs", "Refuz %"]
        return header, rows[:limit], "refuz per produs"
    else:
        grp = "s.name" if by == "brand" else "o.courier_name"
        cur.execute(f"""
          SELECT {grp} g, o.aggregated_status agg, count(*)
          FROM orders o JOIN stores s ON s.uid=o.store_uid
          WHERE o.frisbo_created_at >= %s AND o.frisbo_created_at < %s {sf}
          GROUP BY g, agg
        """, [frm, to] + sp)
        agg = defaultdict(lambda: defaultdict(int))
        for g, st, c in cur.fetchall():
            agg[g or "(necunoscut)"][bucket(st)] += c
        rows = []
        for g, b in agg.items():
            resolved = b["delivered"] + b["returned"] + b["refused"]
            total = sum(b.values())
            rate = 100.0 * (b["returned"] + b["refused"]) / resolved if resolved else 0
            rows.append((g, total, resolved, b["delivered"], b["returned"], b["refused"],
                         b["in_transit"] + b["pending"], round(rate, 1)))
        rows.sort(key=lambda r: r[-1], reverse=True)
        header = [by.capitalize(), "Total", "Rezolvate", "Livrate", "Returnate", "Refuzate", "În curs", "Refuz %"]
        return header, rows[:limit], f"refuz per {by}"


def rep_sales(cur, frm, to, stores, daily, limit):
    sf, sp = store_filter(stores)
    if daily:
        cur.execute(f"""
          SELECT o.frisbo_created_at::date d, count(*) orders, coalesce(sum(o.total_price),0) rev
          FROM orders o JOIN stores s ON s.uid=o.store_uid
          WHERE o.frisbo_created_at >= %s AND o.frisbo_created_at < %s {sf}
          GROUP BY d ORDER BY d DESC""", [frm, to] + sp)
        rows = [(str(d), o, round(float(r), 2)) for d, o, r in cur.fetchall()]
        return ["Zi", "Comenzi", "Venit"], rows[:limit], "vânzări pe zi"
    cur.execute(f"""
      SELECT s.name, count(*) orders, coalesce(sum(o.total_price),0) rev, max(o.currency)
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.frisbo_created_at >= %s AND o.frisbo_created_at < %s {sf}
      GROUP BY s.name""", [frm, to] + sp)
    base = {r[0]: [r[1], float(r[2]), r[3] or "RON"] for r in cur.fetchall()}
    cur.execute(f"""
      WITH li AS (SELECT s.name nm, (item->>'quantity')::numeric qty
        FROM orders o JOIN stores s ON s.uid=o.store_uid
        CROSS JOIN LATERAL jsonb_array_elements(o.line_items::jsonb) item
        WHERE o.frisbo_created_at >= %s AND o.frisbo_created_at < %s {sf})
      SELECT nm, sum(qty)::int FROM li GROUP BY nm""", [frm, to] + sp)
    units = {r[0]: r[1] for r in cur.fetchall()}
    rows = [(nm, v[0], units.get(nm, 0), round(v[1], 2), v[2]) for nm, v in base.items()]
    rows.sort(key=lambda r: r[3], reverse=True)
    return ["Brand", "Comenzi", "Bucăți", "Venit", "Val."], rows[:limit], "vânzări per brand"


def rep_transport(cur, frm, to, stores, limit):
    sf, sp = store_filter(stores)
    cur.execute(f"""
      SELECT s.name brand, coalesce(nullif(o.courier_name,''),'(fără)') courier,
        count(*) parcels,
        count(*) FILTER (WHERE o.transport_cost>0) priced,
        coalesce(sum(o.transport_cost) FILTER (WHERE o.transport_cost>0),0) tcost,
        coalesce(sum(o.total_price),0) rev
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.frisbo_created_at >= %s AND o.frisbo_created_at < %s {sf}
      GROUP BY brand, courier""", [frm, to] + sp)
    rows = []
    for brand, courier, parcels, priced, tcost, rev in cur.fetchall():
        tcost, rev = float(tcost), float(rev)
        avg = tcost / priced if priced else 0
        pct = 100.0 * tcost / rev if rev else 0
        rows.append((brand, courier, parcels, round(avg, 2), round(tcost, 2), round(pct, 1)))
    rows.sort(key=lambda r: r[4], reverse=True)
    return ["Brand", "Curier", "Colete", "Cost/colet", "Cost total", "% din venit"], rows[:limit], "transport per brand×curier"


def rep_stuck(cur, frm, to, stores, days, limit):
    sf, sp = store_filter(stores)
    cutoff = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    active = list(S_TRANSIT | S_PENDING)
    cur.execute(f"""
      SELECT s.name, o.order_number, o.aggregated_status, o.frisbo_created_at::date,
             coalesce(nullif(o.courier_name,''),'(fără)'), o.tracking_number
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status = ANY(%s)
        AND o.frisbo_created_at >= %s AND o.frisbo_created_at < %s
        AND o.frisbo_created_at::date <= %s {sf}
      ORDER BY o.frisbo_created_at ASC""", [active, frm, to, cutoff] + sp)
    today = datetime.now(TZ).date()
    rows = []
    for nm, onum, st, d, courier, awb in cur.fetchall():
        age = (today - d).days
        ghost = "DA" if (awb and bucket(st) == "pending") else ""
        rows.append((nm, onum, st, age, courier, ghost))
    return ["Brand", "Comandă", "Status", "Zile vechime", "Curier", "Ghost(AWB nescanat)"], rows[:limit], f"blocate > {days} zile"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", choices=["refuse", "sales", "transport", "stuck"], default="refuse")
    ap.add_argument("--by", choices=["brand", "courier", "product"], default="brand", help="doar pt refuse")
    ap.add_argument("--stores", help="prefixe (EST,GT) sau implicit toate")
    ap.add_argument("--months", type=int)
    ap.add_argument("--days", type=int, help="fereastră în zile (sau pragul de vechime la stuck)")
    ap.add_argument("--from", dest="from_date")
    ap.add_argument("--to", dest="to_date")
    ap.add_argument("--daily", action="store_true", help="sales: defalcat pe zi")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--sheet", action="store_true", help="scrie și un Google Sheet partajat")
    ap.add_argument("--sheet-title")
    args = ap.parse_args()

    to_date = args.to_date or datetime.now(TZ).strftime("%Y-%m-%d")
    if args.from_date:
        from_date = args.from_date
    elif args.days and args.report != "stuck":
        from_date = (datetime.strptime(to_date, "%Y-%m-%d") - timedelta(days=args.days)).strftime("%Y-%m-%d")
    else:
        from_date = months_ago(datetime.strptime(to_date, "%Y-%m-%d"), args.months or 3).strftime("%Y-%m-%d")
    to_excl = (datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    stores = [s.strip().upper() for s in args.stores.split(",")] if args.stores else None
    stuck_days = args.days or 7

    conn = awb_conn(); cur = conn.cursor()
    print(f"sursă: AWBprint (instant) · {from_date} → {to_date} · magazine: {args.stores or 'toate'}", file=sys.stderr)
    if args.report == "refuse":
        header, rows, label = rep_refuse(cur, from_date, to_excl, args.by, stores, args.limit)
    elif args.report == "sales":
        header, rows, label = rep_sales(cur, from_date, to_excl, stores, args.daily, args.limit)
    elif args.report == "transport":
        header, rows, label = rep_transport(cur, from_date, to_excl, stores, args.limit)
    else:
        header, rows, label = rep_stuck(cur, from_date, to_excl, stores, stuck_days, args.limit)
    conn.close()

    print(f"\n=== {label} ({from_date} → {to_date}) — top {min(len(rows), args.limit)} ===")
    print("  " + " | ".join(str(h) for h in header))
    for r in rows:
        print("  " + " | ".join(str(x) for x in r))

    if args.sheet:
        title = args.sheet_title or f"AWB {label} — {from_date}→{to_date}"
        print(f"\n✅ Google Sheet: {write_sheet(header, [list(r) for r in rows], title)}")


if __name__ == "__main__":
    main()
