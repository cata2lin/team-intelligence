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
product_sales.py — Câte BUCĂȚI a vândut fiecare PRODUS, pe orice magazin(e) Arona
Shopify, pe orice perioadă — clasament top/bottom, scos direct într-un Google Sheet.

DE CE există: skill-ul "vânzări per produs" trage LIVE din Shopify (autoritativ,
complet), nu din metrics warehouse — warehouse-ul `orders/order_line_items` poate
fi INCOMPLET pentru unele branduri (observat: GT ~15% comenzi lipsă), deci pentru
"cele mai/puțin vândute N produse" cifrele de warehouse pot fi greșite.

Ce înseamnă "vândut":
  gross = sum(lineItem.quantity)          — cantitatea comandată (qty_sold clasic)
  net   = sum(lineItem.currentQuantity)   — după anulări / refund / editări de comandă
Comenzile VOIDED neanulate (plată eșuată) sunt sărite, ca în restul scripturilor.

Sursa tokenilor Shopify: stores.csv (env SHOPIFY_STORES_CSV / cwd / secret KB).
Output Google Sheet: tokenul OAuth personal din KB (GOOGLE_OAUTH_TOKEN_JSON) —
creează un Sheet nou în Drive-ul tău, headless (fără browser). NU partajezi nimic.

Folosire:
  uv run product_sales.py --stores EST,GT --months 3 --order bottom --limit 40
  uv run product_sales.py --stores EST --from 2026-01-01 --to 2026-03-31 --order top --limit 20
  uv run product_sales.py --stores EST,GT --scope per-store --order bottom --limit 25
  uv run product_sales.py --stores GT --metric net --no-sheet         # doar print
  uv run product_sales.py --stores EST,GT --sheet-id <ID existent>     # scrie într-un Sheet dat
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
# titluri care NU sunt parfumuri (cutii cadou, mostre, testere, carduri) — excluse implicit
NON_PERFUME_RE = r"cutie|cadou|ambalaj|mostr|sample|esantion|eșantion|tester|card|voucher|gift\s*card|pung[ăa]"

# prefix stores.csv → AWBprint stores.name (DB_Reference §3). Pt sursa --source awb.
PREFIX_TO_STORE = {
    "EST": "esteban.ro", "GT": "georgetalent.ro", "OFER": "ofertelezilei.ro",
    "BON": "casaofertelor.ro", "BELA": "belasil.ro", "LUX": "nocturnalux.ro",
    "NOC": "nocturna.ro", "GEN": "gento.ro", "ROSSI": "rossinails.ro",
    "APR": "apreciat.ro", "RED": "reduceribune.ro", "CARP": "carpetto.ro",
    "PAT": "cepatai.ro", "GRAN": "grandia.ro", "MAG": "magdeal.ro",
    "COV": "covoria.ro", "NUB": "nubra", "CZ": "bonhaus.cz", "PL": "bonhaus.pl",
    "BG": "bonhaus.bg",
}
# stări care anulează vânzarea (pt "net" în sursa awb): comandă anulată / refuzată / întoarsă
AWB_CANCEL_STATES = ("cancelled", "refused", "back_to_sender", "returning_to_sender")

# ── line items first:20 acoperă ~tot (comenzi de parfumuri au 1-6 linii); fără refunds (net=currentQuantity) ──
ORDERS_GQL = """
query($q: String!, $cursor: String) {
  orders(first: 250, query: $q, after: $cursor, sortKey: CREATED_AT) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      name createdAt cancelledAt displayFinancialStatus
      lineItems(first: 20) { edges { node {
        quantity currentQuantity sku name
        product { id title }
        discountedTotalSet { shopMoney { amount currencyCode } }
        discountAllocations { allocatedAmountSet { shopMoney { amount } } }
      } } }
    } }
  }
}
"""


# ════════════════════════ token resolution (stores.csv: env / cwd / KB) ════════════════════════
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


def _stores_csv_text():
    env = os.getenv("SHOPIFY_STORES_CSV")
    if env:
        return env if "\n" in env else open(env, encoding="utf-8-sig").read()
    if os.path.exists("stores.csv"):
        return open("stores.csv", encoding="utf-8-sig").read()
    sec = _kb_secret("SHOPIFY_STORES_CSV")
    if sec:
        return sec
    raise SystemExit("Nu pot rezolva stores.csv (env SHOPIFY_STORES_CSV / cwd / KB).")


def resolve_store(prefix):
    for row in csv.DictReader(io.StringIO(_stores_csv_text())):
        if (row.get("prefix") or "").strip().lstrip("﻿").upper() == prefix.upper():
            shop = (row.get("shop") or "").strip().replace("https://", "").replace("http://", "").strip("/")
            token = (row.get("token") or "").strip()
            return shop, token
    raise SystemExit(f"Magazin {prefix!r} negăsit în stores.csv")


def _shopify_post(client, url, headers, payload):
    """POST cu o conexiune reutilizată (httpx.Client = pooling/keep-alive => rapid)."""
    for attempt in range(6):
        try:
            r = client.post(url, headers=headers, json=payload, timeout=60.0)
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 2)) + attempt)
                continue
            if r.status_code != 200:
                raise SystemExit(f"HTTP {r.status_code}: {r.text[:300]}")
            return r.json()
        except httpx.HTTPError:
            time.sleep(1.0 + attempt)
    raise SystemExit("Renunț după erori repetate")


# ════════════════════════ fetch + aggregate per product ════════════════════════
def fetch_store(prefix, from_date, to_date):
    shop, token = resolve_store(prefix)
    if not token:
        raise SystemExit(f"Fără token pentru {prefix}")
    url = f"https://{shop}/admin/api/{API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    start = datetime.strptime(from_date, "%Y-%m-%d").replace(tzinfo=TZ)
    end = datetime.strptime(to_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=TZ)
    s_iso = start.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    e_iso = end.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    q = f"status:any created_at:>={s_iso} created_at:<={e_iso}"

    prod = defaultdict(lambda: {"store": prefix, "title": "", "skus": set(),
                                "gross": 0, "net": 0, "revenue": 0.0, "orders": set(), "currency": "RON"})
    n_orders = 0
    cursor = None
    client = httpx.Client(http2=False, limits=httpx.Limits(max_keepalive_connections=4, keepalive_expiry=60))
    while True:
        j = _shopify_post(client, url, headers, {"query": ORDERS_GQL, "variables": {"q": q, "cursor": cursor}})
        if "errors" in j:
            client.close()
            raise SystemExit(f"{prefix} GQL errors: {j['errors']}")
        # throttle politicos
        ts = ((j.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
        if ts.get("currentlyAvailable", 999) < 200:
            time.sleep(1.0)
        data = (j.get("data") or {}).get("orders") or {}
        for ed in data.get("edges") or []:
            node = (ed or {}).get("node") or {}
            fin = str(node.get("displayFinancialStatus") or "").upper()
            cancelled = bool(node.get("cancelledAt"))
            if fin == "VOIDED" and not cancelled:
                continue
            n_orders += 1
            oname = str(node.get("name") or "")
            for led in (node.get("lineItems") or {}).get("edges") or []:
                ln = (led or {}).get("node") or {}
                qty = int(ln.get("quantity") or 0)
                qty_net = int(ln.get("currentQuantity") or 0)
                sku = str(ln.get("sku") or "").strip()
                p = ln.get("product") or {}
                pid = p.get("id") or (f"sku:{sku}" if sku else ln.get("name") or "?")
                title = (p.get("title") or ln.get("name") or sku or "(necunoscut)").strip()
                money = (ln.get("discountedTotalSet") or {}).get("shopMoney") or {}
                rev = float(money.get("amount") or 0)
                disc = sum(float((da.get("allocatedAmountSet") or {}).get("shopMoney", {}).get("amount") or 0)
                           for da in (ln.get("discountAllocations") or []))
                a = prod[(prefix, pid)]
                a["title"] = title
                if sku:
                    a["skus"].add(sku)
                a["gross"] += qty
                a["net"] += qty_net
                a["revenue"] += rev - disc
                a["orders"].add(oname)
                a["currency"] = str(money.get("currencyCode") or a["currency"])
        pi = data.get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
    client.close()
    return prod, n_orders


# ════════════════════════ sursa AWBprint (Postgres, instant, ~99% complet) ════════════════════════
def _awb_conn():
    import pg8000.dbapi, urllib.parse as up
    url = os.getenv("DATABASE_URL_AWBPRINT") or _kb_secret("DATABASE_URL_AWBPRINT")
    if not url:
        raise SystemExit("Lipsește DATABASE_URL_AWBPRINT (env sau KB).")
    u = up.urlparse(url)
    return pg8000.dbapi.connect(user=up.unquote(u.username or ""), password=up.unquote(u.password or ""),
                                host=u.hostname, port=u.port or 5432,
                                database=(u.path or "/").lstrip("/"), ssl_context=True)


PRODUCTS_GQL = """
query($cursor: String) {
  products(first: 250, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    edges { node { title variants(first: 50) { edges { node { sku } } } } }
  }
}
"""


def fetch_titles(prefix):
    """sku(lower) → titlu produs, dintr-un singur pull de catalog Shopify (rapid: ~1 pagină)."""
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


def fetch_store_awb(prefix, from_date, to_date):
    """Bucăți per produs din AWBprint (instant). SKU din inventory_item.sku; titlul îl
    rezolvăm din catalogul Shopify. net = exclude comenzile anulate/refuzate/întoarse."""
    store_name = PREFIX_TO_STORE.get(prefix.upper())
    if not store_name:
        raise SystemExit(f"Nu știu numele AWBprint pt {prefix} — adaugă-l în PREFIX_TO_STORE.")
    conn = _awb_conn()
    cur = conn.cursor()
    cur.execute("""
      WITH li AS (
        SELECT lower(item->'inventory_item'->>'sku') AS sku,
               (item->>'quantity')::numeric AS qty,
               COALESCE((item->>'price')::numeric, 0) AS price,
               COALESCE((item->>'total_discount')::numeric, 0) AS disc,
               o.order_number AS oname,
               (o.aggregated_status = ANY(%s)) AS is_cancel
        FROM orders o JOIN stores s ON s.uid = o.store_uid
        CROSS JOIN LATERAL jsonb_array_elements(o.line_items::jsonb) AS item
        WHERE s.name = %s
          AND o.frisbo_created_at >= %s AND o.frisbo_created_at < %s
      )
      SELECT sku,
             sum(qty)::int AS gross,
             sum(qty) FILTER (WHERE NOT is_cancel)::int AS net,
             sum(price*qty - disc) AS revenue,
             count(DISTINCT oname) AS orders
      FROM li WHERE sku IS NOT NULL AND sku <> '' GROUP BY sku
    """, (list(AWB_CANCEL_STATES), store_name, from_date, to_date))
    rows = cur.fetchall()
    conn.close()
    # numărul de comenzi din fereastră (pt log), comparabil cu sursa shopify
    titles = fetch_titles(prefix)
    prod = {}
    n_units = 0
    for sku, gross, net, revenue, orders in rows:
        title = titles.get(sku, sku)
        prod[(prefix, sku)] = {"store": prefix, "title": title, "skus": {sku},
                               "gross": int(gross or 0), "net": int(net or 0),
                               "revenue": float(revenue or 0), "orders": int(orders or 0), "currency": "RON"}
        n_units += int(gross or 0)
    return prod, n_units


def months_ago(d, n):
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    day = min(d.day, [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
    return d.replace(year=y, month=m, day=day)


# ════════════════════════ Google Sheet output (OAuth token din KB) ════════════════════════
def write_sheet(header, rows, title, sheet_id=None):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    raw = _kb_secret("GOOGLE_OAUTH_TOKEN_JSON")
    if not raw:
        raise SystemExit("Lipsește GOOGLE_OAUTH_TOKEN_JSON din KB (nu pot scrie Google Sheet).")
    info = json.loads(raw)
    creds = Credentials.from_authorized_user_info(info, scopes=info.get("scopes"))
    if not creds.valid and creds.refresh_token:
        creds.refresh(Request())
    svc = build("sheets", "v4", credentials=creds).spreadsheets()
    if sheet_id:
        sid = sheet_id
        meta = svc.get(spreadsheetId=sid, fields="sheets.properties").execute()
        tab = meta["sheets"][0]["properties"]["title"]
        gid = meta["sheets"][0]["properties"]["sheetId"]
        svc.values().clear(spreadsheetId=sid, range=f"'{tab}'").execute()
    else:
        sh = svc.create(body={"properties": {"title": title}}).execute()
        sid = sh["spreadsheetId"]
        gid = sh["sheets"][0]["properties"]["sheetId"]
        tab = sh["sheets"][0]["properties"]["title"]
        # Sheet-ul e creat sub contul celui logat (token din KB) — fără share, restul
        # echipei nu-l poate deschide. Îl facem "anyone with link → editor" (drive.file).
        try:
            build("drive", "v3", credentials=creds).permissions().create(
                fileId=sid, body={"type": "anyone", "role": "writer"}).execute()
        except Exception as e:
            print(f"  ⚠ nu am putut seta share-ul (anyone-with-link): {str(e)[:120]}", file=sys.stderr)
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


# ════════════════════════ main ════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stores", default="EST,GT", help="prefixe, ex: EST,GT")
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--from", dest="from_date", help="YYYY-MM-DD (suprascrie --months)")
    ap.add_argument("--to", dest="to_date", help="YYYY-MM-DD (default azi)")
    ap.add_argument("--order", choices=["bottom", "top"], default="bottom", help="bottom=cele mai puțin vândute")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--scope", choices=["combined", "per-store"], default="combined")
    ap.add_argument("--metric", choices=["gross", "net"], default="gross", help="după ce ordonez")
    ap.add_argument("--exclude", default=NON_PERFUME_RE, help="regex titluri de exclus (cutii/mostre/etc)")
    ap.add_argument("--no-exclude", action="store_true", help="nu exclude non-parfumuri")
    ap.add_argument("--sheet-id", help="scrie într-un Sheet existent în loc să creeze unul nou")
    ap.add_argument("--sheet-title", help="titlul Sheet-ului nou")
    ap.add_argument("--no-sheet", action="store_true", help="doar print, fără Google Sheet")
    ap.add_argument("--json", help="scrie clasamentul complet ca JSON la calea dată")
    ap.add_argument("--source", choices=["awb", "shopify"], default="awb",
                    help="awb = AWBprint (instant, ~99%%, default); shopify = live (100%%, lent)")
    args = ap.parse_args()

    to_date = args.to_date or datetime.now(TZ).strftime("%Y-%m-%d")
    from_date = args.from_date or months_ago(datetime.strptime(to_date, "%Y-%m-%d"), args.months).strftime("%Y-%m-%d")
    stores = [s.strip().upper() for s in args.stores.split(",") if s.strip()]
    exclude_re = None if args.no_exclude else re.compile(args.exclude, re.IGNORECASE)

    all_prod = {}
    excluded = []
    print(f"sursă date: {args.source} ({'AWBprint — instant' if args.source=='awb' else 'Shopify live — lent'})", file=sys.stderr)
    for pfx in stores:
        prod, total = (fetch_store_awb(pfx, from_date, to_date) if args.source == "awb"
                       else fetch_store(pfx, from_date, to_date))
        kept = 0
        for k, p in prod.items():
            if exclude_re and exclude_re.search(p["title"]):
                excluded.append((pfx, p["title"], p["gross"]))
                continue
            all_prod[k] = p
            kept += 1
        unit = "buc gross (total)" if args.source == "awb" else "comenzi"
        print(f"[{pfx}] {total} {unit}, {len(prod)} produse ({kept} parfumuri păstrate, "
              f"{len(prod)-kept} excluse)", file=sys.stderr)
    if excluded:
        print("  excluse (non-parfum): " + "; ".join(f"{s}:{t[:30]}({g})" for s, t, g in excluded[:12]), file=sys.stderr)

    def _norders(p):
        return p["orders"] if isinstance(p["orders"], int) else len(p["orders"])
    rows_data = [{
        "store": p["store"], "product": p["title"],
        "sku": ",".join(sorted(p["skus"]))[:50],
        "gross": p["gross"], "net": p["net"], "orders": _norders(p),
        "revenue": round(p["revenue"], 2), "currency": p["currency"],
    } for p in all_prod.values()]

    key = (lambda r: (r[args.metric], r["net"] if args.metric == "gross" else r["gross"], r["product"]))
    reverse = (args.order == "top")

    def rank_and_emit(rows, label):
        rows = sorted(rows, key=key, reverse=reverse)[:args.limit]
        print(f"\n=== {label} — {args.order} {args.limit} după {args.metric} ({from_date} → {to_date}) ===")
        print(f"{'#':>3}  {'mag':4}  {'gross':>6}  {'net':>5}  {'cmd':>4}  produs")
        for i, r in enumerate(rows, 1):
            print(f"{i:>3}  {r['store']:4}  {r['gross']:>6}  {r['net']:>5}  {r['orders']:>4}  {r['product'][:58]}")
        return rows

    header = ["#", "Magazin", "Parfum", "SKU", "Bucăți (gross)", "Net (excl. anulate)", "Comenzi", "Venit", "Val."]
    sheet_rows, sheets = [], []
    if args.scope == "combined":
        ranked = rank_and_emit(rows_data, "EST+GT combinat" if len(stores) > 1 else stores[0])
        sheet_rows = [[i, r["store"], r["product"], r["sku"], r["gross"], r["net"], r["orders"], r["revenue"], r["currency"]]
                      for i, r in enumerate(ranked, 1)]
    else:
        for pfx in stores:
            ranked = rank_and_emit([r for r in rows_data if r["store"] == pfx], pfx)
            for i, r in enumerate(ranked, 1):
                sheet_rows.append([i, r["store"], r["product"], r["sku"], r["gross"], r["net"], r["orders"], r["revenue"], r["currency"]])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows_data, f, ensure_ascii=False, indent=2)
        print(f"\nJSON: {args.json}", file=sys.stderr)

    if not args.no_sheet:
        ordlabel = "cele mai puțin vândute" if args.order == "bottom" else "cele mai vândute"
        title = args.sheet_title or f"Vânzări/produs {'+'.join(stores)} — {ordlabel} {args.limit} — {from_date}→{to_date}"
        url = write_sheet(header, sheet_rows, title, sheet_id=args.sheet_id)
        print(f"\n✅ Google Sheet: {url}")


if __name__ == "__main__":
    main()
