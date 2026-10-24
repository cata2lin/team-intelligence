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
    # prefixele noi (aceleași domenii ca `stores.name` din AWBprint), ca OH și AWBprint să aibă aceeași etichetă
    "BUC": "delabucsa.ro", "BUCSA": "delabucsa.ro", "DUPBG": "duppo.bg", "DUP": "duppo.bg",
    "ORC": "oriceredus.ro", "SK": "bonhaus.sk", "HU": "bonhaus.hu", "ART": "artevita.ro",
    "LAB": "labnoir.ro", "MD": "duppo.md",
}

S_DELIVERED = {"delivered", "customer_pickup", "administrative_closure"}
S_RETURNED = {"back_to_sender", "returning_to_sender", "incorrect_address", "lost", "received_by_sender"}
S_REFUSED = {"refused", "unsuccessful_delivery"}
S_TRANSIT = {"in_transit", "fulfilled", "redirected", "deferred_delivery", "on_hold", "out_for_delivery"}
S_PENDING = {"waiting_for_courier", "not_fulfilled", "new", "ready_for_pickup", "not_created", "created_awb"}
# COD = ramburs (plata la livrare); restul = prepaid. payment_gateway are 6+ variante → ILIKE.
COD_SQL = ("(lower(coalesce(o.payment_gateway,'')) LIKE '%ramburs%' OR "
           "lower(coalesce(o.payment_gateway,'')) LIKE '%cod%' OR "
           "lower(coalesce(o.payment_gateway,'')) LIKE '%numerar%' OR "
           "lower(coalesce(o.payment_gateway,'')) LIKE '%cash%' OR "
           "lower(coalesce(o.payment_gateway,'')) LIKE '%plata la livrare%')")
# „în zbor" = nici livrat, nici eșuat/anulat → bani COD încă neîncasați
S_INFLIGHT = S_TRANSIT | S_PENDING
# comenzi de TESTARE produs (mai ales Magdeal/Oferte) — tag-ul "test" în tags jsonb;
# NU sunt cash real, le excludem din COD-at-risk
NOT_TEST = "coalesce(o.tags::text,'') NOT ILIKE '%\"test\"%'"


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
        GRP = {
            "brand": "s.name",
            "courier": "coalesce(nullif(o.courier_name,''),'(fără)')",
            "payment": f"CASE WHEN {COD_SQL} THEN 'COD' ELSE 'PREPAID' END",
            "discount": ("CASE WHEN coalesce(o.total_price,0)<=0 THEN 'n/a' "
                         "WHEN coalesce(o.total_discounts,0)=0 THEN '0%' "
                         "WHEN o.total_discounts/(o.total_price+o.total_discounts) < 0.15 THEN '1-15%' "
                         "WHEN o.total_discounts/(o.total_price+o.total_discounts) < 0.30 THEN '15-30%' "
                         "WHEN o.total_discounts/(o.total_price+o.total_discounts) < 0.40 THEN '30-40%' "
                         "ELSE '40%+' END"),
        }
        grp = GRP.get(by, "s.name")
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


def rep_cod(cur, frm, to, stores, limit):
    """COD value-at-risk: bani ramburs ÎN ZBOR (nici livrat, nici eșuat) = cash neîncasat.
    Pe vârstă, ca să separi cash-ul proaspăt (de urmărit) de „stale" 30z+ (blocat/abandonat)."""
    sf, sp = store_filter(stores)
    inflight = list(S_INFLIGHT)
    cur.execute(f"""
      SELECT s.name,
        greatest(0, date_part('day', now() - o.frisbo_created_at))::int AS age,
        o.total_price, o.aggregated_status
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.aggregated_status = ANY(%s) AND {COD_SQL} AND {NOT_TEST}
        AND o.frisbo_created_at >= %s AND o.frisbo_created_at < %s {sf}
    """, [inflight, frm, to] + sp)
    agg = defaultdict(lambda: {"orders": 0, "ron": 0.0, "fresh_ron": 0.0, "stale_ron": 0.0, "stale_orders": 0})
    for nm, age, price, st in cur.fetchall():
        a = agg[nm]; p = float(price or 0)
        a["orders"] += 1; a["ron"] += p
        if age is not None and age >= 30:
            a["stale_ron"] += p; a["stale_orders"] += 1
        else:
            a["fresh_ron"] += p
    rows = [(nm, v["orders"], round(v["ron"]), round(v["fresh_ron"]), v["stale_orders"], round(v["stale_ron"]))
            for nm, v in agg.items()]
    rows.sort(key=lambda r: r[2], reverse=True)
    total = ("TOTAL (toate)", sum(r[1] for r in rows), round(sum(r[2] for r in rows)),
             round(sum(r[3] for r in rows)), sum(r[4] for r in rows), round(sum(r[5] for r in rows)))
    rows = rows[:limit] + [total]  # TOTAL e mereu peste TOATE brandurile, nu doar top-N
    header = ["Brand", "Comenzi COD", "RON în zbor", "din care proaspăt (<30z)", "Comenzi stale 30z+", "RON stale 30z+"]
    return header, rows, "COD value-at-risk (cash neîncasat în zbor)"


def rep_geo(cur, frm, to, by, stores, limit):
    """Heatmap refuz pe județ/oraș (DOAR RO — brandurile străine n-au province).
    refuz % = (returnate+refuzate)/rezolvate. Prag minim de comenzi rezolvate = anti-zgomot."""
    sf, sp = store_filter(stores)
    col = "shipping_address->>'city'" if by == "city" else "shipping_address->>'province'"
    cur.execute(f"""
      SELECT {col} g, o.aggregated_status agg, count(*)
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE o.frisbo_created_at >= %s AND o.frisbo_created_at < %s
        AND upper(coalesce(o.shipping_address->>'country_code', o.shipping_address->>'country','RO')) IN ('RO','ROMANIA')
        AND nullif({col},'') IS NOT NULL {sf}
      GROUP BY g, agg
    """, [frm, to] + sp)
    agg = defaultdict(lambda: defaultdict(int))
    for g, st, c in cur.fetchall():
        agg[g][bucket(st)] += c
    rows = []
    natdeliv = natfail = 0
    for g, b in agg.items():
        resolved = b["delivered"] + b["returned"] + b["refused"]
        fail = b["returned"] + b["refused"]
        natdeliv += b["delivered"]; natfail += fail
        if resolved < 40:  # prag anti-zgomot pe județ/oraș
            continue
        rows.append((g, resolved, b["delivered"], fail, round(100.0 * fail / resolved, 1)))
    rows.sort(key=lambda r: r[-1], reverse=True)
    nat = round(100.0 * natfail / (natdeliv + natfail), 1) if (natdeliv + natfail) else 0
    header = [by.capitalize(), "Rezolvate", "Livrate", "Returnate+Refuzate", f"Refuz % (național {nat}%)"]
    return header, rows[:limit], f"heatmap refuz pe {by} (RO)"


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


def rep_repeat(cur, frm, to, stores, limit):
    """Retenție: clienți noi vs revenit, returning-rate, comenzi/client. Cheia = telefonul
    normalizat (ultimele 9 cifre; 100% populat, vs email ~66%). „first_o" = prima comandă
    EVER a clientului pe ACEL magazin → returning = avea comandă înainte de fereastră."""
    sf, sp = store_filter(stores)
    cur.execute(f"""
      WITH firsts AS (
        SELECT store_uid, right(regexp_replace(coalesce(shipping_address->>'phone',''),'\\D','','g'),9) ph,
               min(frisbo_created_at) first_o
        FROM orders WHERE shipping_address->>'phone' IS NOT NULL GROUP BY store_uid, ph
      ), w AS (
        SELECT s.name brand, o.store_uid,
               right(regexp_replace(coalesce(o.shipping_address->>'phone',''),'\\D','','g'),9) ph
        FROM orders o JOIN stores s ON s.uid=o.store_uid
        WHERE o.frisbo_created_at >= %s AND o.frisbo_created_at < %s {sf}
      )
      SELECT w.brand, count(*) orders, count(DISTINCT w.ph) customers,
        count(DISTINCT w.ph) FILTER (WHERE f.first_o >= %s) new_customers,
        count(*) FILTER (WHERE f.first_o < %s) returning_orders
      FROM w JOIN firsts f ON f.store_uid=w.store_uid AND f.ph=w.ph
      WHERE w.ph <> ''
      GROUP BY w.brand
    """, [frm, to] + sp + [frm, frm])
    rows = []
    for brand, orders, customers, new_cust, ret_orders in cur.fetchall():
        rate = 100.0 * ret_orders / orders if orders else 0
        opc = orders / customers if customers else 0
        rows.append((brand, orders, customers, new_cust, ret_orders, round(rate, 1), round(opc, 2)))
    rows.sort(key=lambda r: r[5], reverse=True)
    header = ["Brand", "Comenzi", "Clienți", "Clienți noi", "Cmd. de la revenit", "Returning %", "Cmd/client"]
    return header, rows[:limit], "repeat / retenție clienți (cheie: telefon)"


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


# ════════════════════════ Order Hub (implicit din 24-oct-2026) ════════════════════════
# AWBprint rămâne fără cititori pe laptopuri: statusurile intl sunt înghețate acolo din 27-sep și `transport_cost` e gol
# din 28-aug. Order Hub dă starea din COLETE (`GET /api/cs/livrabilitate`, o comandă pe rând, ziua de București) și
# transportul real FĂRĂ TVA. Ce NU are ruta, dinadins (fără PII / plată): metoda de plată, reducerea, orașul, telefonul,
# numărul AWB-ului. Rapoartele care au nevoie de ele rămân pe AWBprint, doar cu `--source awb`, pe serverul de scripturi.
OH_BAZA = os.getenv("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")
OH_CHEI = ("OH_TEAM_READ_TOKEN", "OH_PROFIT_CITIRE_TOKEN", "OH_CS_SB_TOKEN")
_OH_NORM = {"GRAND": "GRAN", "NUBRA": "NUB", "BONBG": "BG"}   # prefixul comenzii în OH → prefixul de aici
OH_BUCKET = {"Livrata": "delivered", "Refuzata": "refused", "In curs de livrare": "in_transit",
             "Netrimisa": "pending", "Anulata": "other"}
DOAR_AWB = {  # (raport, by) → ce lipsește din Order Hub
    ("refuse", "payment"): "metoda de plată (COD/prepaid)",
    ("refuse", "discount"): "reducerea comenzii",
    ("cod", None): "metoda de plată (COD/prepaid)",
    ("geo", "city"): "orașul (ruta dă doar județul)",
    ("repeat", None): "telefonul clientului (date personale)",
}


def _oh_token():
    for k in OH_CHEI:
        if (os.getenv(k) or "").strip():
            return os.getenv(k).strip()
    t = _kb_secret("OH_TEAM_READ_TOKEN")
    if not t:
        raise SystemExit("Order Hub: lipsește cheia de citire (env %s sau KB OH_TEAM_READ_TOKEN). "
                         "Nu cad pe AWBprint în tăcere; pentru calea veche: --source awb (doar pe server)." % " / ".join(OH_CHEI))
    return t


_oh_tok = None


def _oh_get(path):
    import urllib.request, urllib.error
    global _oh_tok
    if _oh_tok is None:
        _oh_tok = _oh_token()
    req = urllib.request.Request(OH_BAZA + path, headers={"Authorization": "Bearer " + _oh_tok, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit("Order Hub %s → HTTP %s. Nu cad pe AWBprint în tăcere." % (path.split("?")[0], e.code))
    except Exception as e:
        raise SystemExit("Order Hub %s nu răspunde (%s). Nu cad pe AWBprint în tăcere." % (path.split("?")[0], type(e).__name__))


def oh_prefix(nume):
    m = re.match(r"^[A-Za-z]+", nume or "")
    p = (m.group(0) if m else "").upper()
    return _OH_NORM.get(p, p)


def oh_label(prefix):
    """Eticheta brandului = domeniul (ca `stores.name` din AWBprint), altfel prefixul."""
    p = _OH_NORM.get(prefix.upper(), prefix.upper())
    return PREFIX_TO_STORE.get(p, p)


def oh_rows(frm, to_excl, stores):
    """Rândurile OH (dict) din fereastra [frm, to_excl), filtrate pe magazine. Lunile se iau întregi, ziua e de București."""
    d0, d1 = datetime.strptime(frm, "%Y-%m-%d").date(), datetime.strptime(to_excl, "%Y-%m-%d").date()
    luni, z = [], d0.replace(day=1)
    while z < d1:
        luni.append(z.strftime("%Y-%m"))
        z = (z.replace(day=28) + timedelta(days=4)).replace(day=1)
    vreau = {oh_label(s) for s in stores} if stores and stores != ["ALL"] else None
    out = []
    for luna in luni:
        d = _oh_get("/api/cs/livrabilitate?luna=" + luna)
        col = {c: i for i, c in enumerate(d["coloane"])}
        for x in d["randuri"]:
            if not (frm <= x[col["zi"]] < to_excl):
                continue
            p = oh_prefix(x[col["comanda"]])
            if not p:
                continue
            lab = oh_label(p)
            if vreau is not None and lab not in vreau:
                continue
            out.append({"comanda": x[col["comanda"]], "brand": lab, "total": float(x[col["total"]] or 0),
                        "moneda": x[col["moneda"]] or "RON", "judet": (x[col["judet"]] or "").strip(),
                        "tara": x[col["tara"]] or "", "stare": x[col["stare"]] or "",
                        "bucket": OH_BUCKET.get(x[col["stare"]], "other"),
                        "curier": x[col["curier"]] or "(fără)", "ultim_status": x[col["ultim_status"]] or "",
                        "transport": x[col["transport_fara_tva"]], "skus": x[col["skus"]] or "", "zi": x[col["zi"]]})
    return out


def _refuz_rows(agg, extra_total=False):
    rows = []
    for g, b in agg.items():
        resolved = b["delivered"] + b["returned"] + b["refused"]
        rate = 100.0 * (b["returned"] + b["refused"]) / resolved if resolved else 0
        base = (g, sum(b.values())) if extra_total else (g,)
        rows.append(base + (resolved, b["delivered"], b["returned"], b["refused"],
                            b["in_transit"] + b["pending"], round(rate, 1)))
    rows.sort(key=lambda r: r[-1], reverse=True)
    return rows


def oh_rep_refuse(R, by, stores, limit):
    if by == "product":
        # OH dă SKU-urile distincte ale comenzii, fără cantitate → numărăm COMENZI cu SKU-ul, nu bucăți.
        agg = defaultdict(lambda: defaultdict(int))
        for r in R:
            for sku in {s.strip() for s in r["skus"].split(";") if s.strip()}:
                agg[sku][r["bucket"]] += 1
        titles = {}
        for p in (stores or []):
            if p != "ALL":
                titles.update(fetch_titles(p))
        rows = []
        for sku, b in agg.items():
            resolved = b["delivered"] + b["refused"]
            if resolved < 5:
                continue
            rows.append((titles.get(sku, sku), sku, resolved, b["delivered"], 0, b["refused"],
                         b["in_transit"] + b["pending"], round(100.0 * b["refused"] / resolved, 1)))
        rows.sort(key=lambda r: r[-1], reverse=True)
        header = ["Produs", "SKU", "Rezolvate (comenzi)", "Livrate", "Returnate", "Refuzate", "În curs", "Refuz %"]
        return header, rows[:limit], "refuz per produs (comenzi cu SKU-ul, Order Hub)"
    key = "curier" if by == "courier" else "brand"
    agg = defaultdict(lambda: defaultdict(int))
    for r in R:
        agg[r[key]][r["bucket"]] += 1
    header = [by.capitalize(), "Total", "Rezolvate", "Livrate", "Returnate", "Refuzate", "În curs", "Refuz %"]
    return header, _refuz_rows(agg, extra_total=True)[:limit], f"refuz per {by} (Order Hub)"


def oh_rep_geo(R, limit):
    agg = defaultdict(lambda: defaultdict(int))
    for r in R:
        if r["tara"] not in ("Romania", "RO", "") or not r["judet"] or r["judet"] == "?":
            continue
        agg[r["judet"]][r["bucket"]] += 1
    rows, natdeliv, natfail = [], 0, 0
    for g, b in agg.items():
        resolved = b["delivered"] + b["refused"]
        natdeliv += b["delivered"]; natfail += b["refused"]
        if resolved < 40:
            continue
        rows.append((g, resolved, b["delivered"], b["refused"], round(100.0 * b["refused"] / resolved, 1)))
    rows.sort(key=lambda r: r[-1], reverse=True)
    nat = round(100.0 * natfail / (natdeliv + natfail), 1) if (natdeliv + natfail) else 0
    header = ["Province", "Rezolvate", "Livrate", "Returnate+Refuzate", f"Refuz % (național {nat}%)"]
    return header, rows[:limit], "heatmap refuz pe province (RO, Order Hub)"


def _shop_la_eticheta():
    """{domeniu myshopify: eticheta brandului} din stores.csv (prefix, shop). vanzari-pe-sku dă magazinul ca domeniu
    myshopify, iar numele magazinelor din OH nu sunt uniforme (esteban.ro nu se rezolvă, grandia.ro da)."""
    try:
        txt = _stores_csv_text()
    except SystemExit:
        return {}
    out = {}
    for row in csv.DictReader(io.StringIO(txt)):
        p = (row.get("prefix") or "").strip().lstrip("﻿").upper()
        shop = (row.get("shop") or "").strip().replace("https://", "").replace("http://", "").strip("/").lower()
        if p and shop:
            out[shop] = oh_label(p)
    return out


def _oh_bucati(frm, to_excl, labels):
    """{eticheta_brand: bucăți} din /api/cs/vanzari-pe-sku (comenzi plasate, cu anulate, ca rândurile de comenzi).
    Ruta numără de azi înapoi: fereastra [frm, to] = zile(frm..azi) − zile(to+1..azi)."""
    azi = datetime.now(TZ).date()
    d0 = datetime.strptime(frm, "%Y-%m-%d").date()
    d1 = datetime.strptime(to_excl, "%Y-%m-%d").date()          # exclusiv
    if (azi - d0).days + 1 > 400:
        return None
    shop2lab = _shop_la_eticheta()
    if not shop2lab:
        return None

    def _sum(zile):
        if zile <= 0:
            return {}
        d = _oh_get("/api/cs/vanzari-pe-sku?zile=%d&cu_anulate=true" % zile)
        if d.get("taiat"):
            return None
        out = defaultdict(int)
        for x in d.get("randuri") or []:
            mag = (x.get("magazin") or "").lower()
            out[shop2lab.get(mag, mag)] += int(x.get("bucati") or 0)
        return out

    tot = _sum((azi - d0).days + 1)
    dupa = _sum((azi - d1).days + 1) if d1 <= azi else {}
    if tot is None or dupa is None:
        return None
    return {k: v - dupa.get(k, 0) for k, v in tot.items()}


def oh_rep_sales(R, frm, to_excl, daily, limit):
    if daily:
        z = defaultdict(lambda: [0, 0.0])
        for r in R:
            z[r["zi"]][0] += 1; z[r["zi"]][1] += r["total"]
        rows = [(d, v[0], round(v[1], 2)) for d, v in sorted(z.items(), reverse=True)]
        return ["Zi", "Comenzi", "Venit"], rows[:limit], "vânzări pe zi (Order Hub)"
    b = defaultdict(lambda: [0, 0.0, "RON"])
    for r in R:
        v = b[r["brand"]]; v[0] += 1; v[1] += r["total"]; v[2] = r["moneda"]
    buc = _oh_bucati(frm, to_excl, sorted(b, key=len, reverse=True))
    rows = [(nm, v[0], (buc.get(nm, "n/a") if buc is not None else "n/a"), round(v[1], 2), v[2]) for nm, v in b.items()]
    rows.sort(key=lambda r: r[3], reverse=True)
    return ["Brand", "Comenzi", "Bucăți", "Venit", "Val."], rows[:limit], "vânzări per brand (Order Hub)"


def oh_rep_transport(R, limit):
    g = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for r in R:
        v = g[(r["brand"], r["curier"])]
        v[0] += 1; v[3] += r["total"]
        if r["transport"] and r["transport"] > 0:
            v[1] += 1; v[2] += float(r["transport"])
    rows = []
    for (brand, courier), (parcels, priced, tcost, rev) in g.items():
        rows.append((brand, courier, parcels, round(tcost / priced, 2) if priced else 0, round(tcost, 2),
                     round(100.0 * tcost / rev, 1) if rev else 0))
    rows.sort(key=lambda r: r[4], reverse=True)
    return (["Brand", "Curier", "Colete", "Cost/colet fără TVA", "Cost total fără TVA", "% din venit"], rows[:limit],
            "transport per brand×curier (Order Hub, fără TVA)")


def oh_rep_stuck(R, days, limit):
    cutoff = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now(TZ).date()
    rows = []
    for r in sorted(R, key=lambda r: r["zi"]):
        if r["bucket"] not in ("in_transit", "pending") or r["zi"] > cutoff:
            continue
        age = (today - datetime.strptime(r["zi"], "%Y-%m-%d").date()).days
        # ghost = are etichetă la un curier, dar coletul n-a mișcat (Netrimisa cu curier)
        ghost = "DA" if (r["bucket"] == "pending" and r["curier"] not in ("", "(fără)")) else ""
        rows.append((r["brand"], r["comanda"], r["ultim_status"] or r["stare"], age, r["curier"], ghost))
    return (["Brand", "Comandă", "Status", "Zile vechime", "Curier", "Ghost(AWB nescanat)"], rows[:limit],
            f"blocate > {days} zile (Order Hub)")


def sursa_implicita(arg):
    if arg:
        return arg
    return "awb" if (os.getenv("FULFILLMENT_SURSA") or "").strip().lower() == "awb" else "oh"


def lipsa_oh(report, by):
    """Ce câmp lipsește din Order Hub pentru (report, by), sau None dacă raportul merge pe OH."""
    return DOAR_AWB.get((report, by)) or DOAR_AWB.get((report, None))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", choices=["refuse", "sales", "transport", "stuck", "repeat", "cod", "geo"], default="refuse")
    ap.add_argument("--by", choices=["brand", "courier", "product", "payment", "discount", "province", "city"],
                    default="brand", help="refuse: brand|courier|product|payment|discount ; geo: province|city")
    ap.add_argument("--stores", help="prefixe (EST,GT) sau implicit toate")
    ap.add_argument("--months", type=int)
    ap.add_argument("--days", type=int, help="fereastră în zile (sau pragul de vechime la stuck)")
    ap.add_argument("--from", dest="from_date")
    ap.add_argument("--to", dest="to_date")
    ap.add_argument("--daily", action="store_true", help="sales: defalcat pe zi")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--sheet", action="store_true", help="scrie și un Google Sheet partajat")
    ap.add_argument("--sheet-title")
    ap.add_argument("--source", choices=["oh", "awb"], default=None,
                    help="oh = Order Hub (implicit); awb = AWBprint, doar pe serverul de scripturi "
                         "(întoarcere explicită, sau FULFILLMENT_SURSA=awb)")
    args = ap.parse_args()
    args.source = sursa_implicita(args.source)

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

    by_geo = args.by if args.by in ("province", "city") else "province"
    if args.source == "oh":
        lipsa = lipsa_oh(args.report, by_geo if args.report == "geo" else args.by)
        if lipsa:
            raise SystemExit(f"Raportul {args.report}" + (f" --by {args.by}" if args.report == "refuse" else
                             (f" --by {by_geo}" if args.report == "geo" else "")) +
                             f" are nevoie de {lipsa}, pe care Order Hub nu-l dă (/api/cs/livrabilitate). "
                             "Rulează-l pe serverul de scripturi (84.46) cu --source awb.")
        print(f"sursă: Order Hub · {from_date} → {to_date} · magazine: {args.stores or 'toate'}", file=sys.stderr)
        R = oh_rows(from_date, to_excl, stores)
        if args.report == "refuse":
            header, rows, label = oh_rep_refuse(R, args.by, stores, args.limit)
        elif args.report == "sales":
            header, rows, label = oh_rep_sales(R, from_date, to_excl, args.daily, args.limit)
        elif args.report == "transport":
            header, rows, label = oh_rep_transport(R, args.limit)
        elif args.report == "geo":
            header, rows, label = oh_rep_geo(R, args.limit)
        else:
            header, rows, label = oh_rep_stuck(R, stuck_days, args.limit)
        _afiseaza(header, rows, label, from_date, to_date, args)
        return

    try:
        conn = awb_conn()
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"AWBprint nu se deschide de aici ({type(e).__name__}); calea --source awb merge doar pe "
                         "serverul de scripturi (84.46).")
    cur = conn.cursor()
    print(f"sursă: AWBprint (--source awb) · {from_date} → {to_date} · magazine: {args.stores or 'toate'}", file=sys.stderr)
    if args.report == "refuse":
        header, rows, label = rep_refuse(cur, from_date, to_excl, args.by, stores, args.limit)
    elif args.report == "sales":
        header, rows, label = rep_sales(cur, from_date, to_excl, stores, args.daily, args.limit)
    elif args.report == "transport":
        header, rows, label = rep_transport(cur, from_date, to_excl, stores, args.limit)
    elif args.report == "repeat":
        header, rows, label = rep_repeat(cur, from_date, to_excl, stores, args.limit)
    elif args.report == "cod":
        header, rows, label = rep_cod(cur, from_date, to_excl, stores, args.limit)
    elif args.report == "geo":
        header, rows, label = rep_geo(cur, from_date, to_excl, by_geo, stores, args.limit)
    else:
        header, rows, label = rep_stuck(cur, from_date, to_excl, stores, stuck_days, args.limit)
    conn.close()
    _afiseaza(header, rows, label, from_date, to_date, args)


def _afiseaza(header, rows, label, from_date, to_date, args):
    print(f"\n=== {label} ({from_date} → {to_date}) — top {min(len(rows), args.limit)} ===")
    print("  " + " | ".join(str(h) for h in header))
    for r in rows:
        print("  " + " | ".join(str(x) for x in r))

    if args.sheet:
        title = args.sheet_title or f"AWB {label} — {from_date}→{to_date}"
        print(f"\n✅ Google Sheet: {write_sheet(header, [list(r) for r in rows], title)}")


if __name__ == "__main__":
    main()
