# /// script
# requires-python = ">=3.10"
# dependencies = ["google-auth>=2.29", "requests>=2.31", "psycopg2-binary>=2.9", "python-dotenv>=1.0"]
# ///
"""landing_inventory.py — inventar zilnic al TUTUROR paginilor care primesc trafic.

Sursă pagini = GA4 (dimensiune landingPage + hostName + canal), per magazin. Pentru fiecare pagină:
  • rezolvă produsul (URL /products/<handle> → variants → barcode)
  • STOC din InventorySync (pool_states pe barcode = stocul poolat care se sync-uiește în Shopify)
  • VITEZĂ (timp de încărcare: TTFB + total) — Lighthouse pe top-trafic se face separat
Scrie în cache.landing_inventory (upsert pe zi+url). Semnalează paginile cu trafic dar FĂRĂ stoc + lente.

  uv run landing_inventory.py sync --brand esteban --days 7          # DRY (nu scrie)
  uv run landing_inventory.py sync --brand esteban --days 7 --apply  # scrie în tabel
  uv run landing_inventory.py sync --all --days 7 --apply            # toate magazinele cu GA4
"""
import os, sys, json, time, argparse, subprocess, datetime as dt
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

import requests, psycopg2, psycopg2.extras
from google.oauth2 import service_account
import google.auth.transport.requests

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "..", "core", "scripts", "kb.py")

# Pe VPS (cron) secretele vin din /root/Scripturi/.env; local vin din KB via kb.py. kb() verifică os.environ ÎNTÂI.
try:
    from dotenv import load_dotenv
    for _envp in (os.path.join(HERE, ".env"), "/root/Scripturi/.env"):
        if os.path.exists(_envp):
            load_dotenv(_envp); break
except Exception:
    pass

# GA4 brand → property. Cele 20 de magazine ACTIVE ARONA (lista canonică a owner-ului).
# Descoperă/reîmprospătează property-urile accesibile cu:  landing_inventory.py properties
# Notă: „Bonhaus RO" = Casa Ofertelor (casaofertelor.ro). Nocturna PL/GR/BG = OPRITE, scoase din listă.
BRANDS = {
    "esteban": "510626424", "grandia": "510760223", "nubra": "541249929",
    "george-talent": "541255080", "gt": "541255080", "belasil": "487042770",
    "gento": "486992931", "covoria": "491785347", "casa-ofertelor": "501613337",  # = Bonhaus RO
    "rossi": "402470642", "nocturna": "460807314", "nocturna-lux": "460815169",
    "carpetto": "542211305", "ofertele-zilei": "542911821",
    "bonhaus-cz": "543350297", "bonhaus-pl": "544308926", "bonhaus-bg": "544918571",
    "lab-noir": "546097081", "magdeal": "546115264", "apreciat": "546093071", "reduceri-bune": "546112430",
}
PAID = {"Paid Search", "Paid Shopping", "Paid Social", "Cross-network", "Display", "Paid Other"}


def kb(k):
    v = os.environ.get(k)
    if v: return v
    return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True, timeout=60).stdout.strip()


def clean(u):
    p = urlsplit(u); OK = {"host", "port", "dbname", "user", "password", "sslmode", "connect_timeout"}
    if p.query:
        u = urlunsplit((p.scheme, p.netloc, p.path, urlencode([(x, y) for x, y in parse_qsl(p.query, True) if x.lower() in OK]), p.fragment))
    return u


def mconn():
    return psycopg2.connect(clean(kb("DATABASE_URL_METRICS")), connect_timeout=25)


def iconn():
    return psycopg2.connect(clean(kb("DATABASE_URL_INVENTORYSYNC")), connect_timeout=25)


def ga_creds():
    raw = kb("GA4_SA_JSON")
    if not raw:
        sys.exit("Lipsește GA4_SA_JSON")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(raw), scopes=["https://www.googleapis.com/auth/analytics.readonly"])
    creds.refresh(google.auth.transport.requests.Request())
    return creds


def ga_landing(creds, pid, start, end, limit=2000):
    """Toate landing pages (host + path + canal) cu sesiuni, pe fereastra dată."""
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "hostName"}, {"name": "landingPage"}, {"name": "sessionDefaultChannelGroup"}],
        "metrics": [{"name": "sessions"}, {"name": "keyEvents"}, {"name": "purchaseRevenue"}],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": limit}
    for attempt in range(5):
        r = requests.post(f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport",
                          headers={"Authorization": f"Bearer {creds.token}"}, json=body, timeout=120)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 * (attempt + 1)); continue
        if r.status_code != 200:
            sys.stderr.write(f"  GA4 {pid} err {r.status_code}: {r.text[:200]}\n"); return []
        rows = []
        for row in (r.json() or {}).get("rows", []):
            d = [x["value"] for x in row.get("dimensionValues", [])]
            m = [x["value"] for x in row.get("metricValues", [])]
            rows.append((d[0], d[1], d[2], int(m[0]), int(float(m[1])), float(m[2])))
        return rows
    return []


def norm_path(p):
    # strip query + trailing slash; păstrează /products/<handle>
    p = (p or "/").split("?")[0].split("#")[0]
    if len(p) > 1 and p.endswith("/"): p = p.rstrip("/")
    return p or "/"


def brand_barcodes(mcur, slug):
    """handle → [barcode] pentru un brand (prin brands.slug → products/variants)."""
    mcur.execute("SELECT id FROM brands WHERE slug=%s OR lower(name)=%s", (slug, slug))
    row = mcur.fetchone()
    if not row: return {}, None
    bid = row[0]
    mcur.execute("""
        SELECT p.handle, array_remove(array_agg(DISTINCT v.barcode), NULL)
        FROM products p JOIN variants v ON v."productId"=p.id
        WHERE p."brandId"=%s AND p.handle IS NOT NULL AND v.barcode IS NOT NULL AND v.barcode<>''
        GROUP BY p.handle""", (bid,))
    return {h: bc for h, bc in mcur.fetchall()}, bid


def stock_for(icur, barcodes):
    """barcode(s) → cantitate poolată (InventorySync). in_stock dacă vreo variantă > 0."""
    bcs = [b for b in barcodes if b]
    if not bcs: return None, None
    icur.execute("SELECT barcode, quantity FROM pool_states WHERE barcode = ANY(%s)", (bcs,))
    qmap = {b: q for b, q in icur.fetchall()}
    if not qmap: return None, None
    total = sum(int(q or 0) for q in qmap.values())
    return total, total > 0


# UA de browser real — storefront-urile (Cloudflare/bot-protection) dau 503 la UA-uri „de bot"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def measure(url, timeout=20):
    """Timp de încărcare: total ms + status + bytes. (Web Vitals/Lighthouse pe top-N se face separat.)"""
    try:
        t0 = time.time()
        r = requests.get(url, timeout=timeout, headers={
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8"})
        ms = int((time.time() - t0) * 1000)
        return ms, r.status_code, len(r.content)
    except Exception as e:
        return None, None, str(e)[:60]


DDL = """
CREATE TABLE IF NOT EXISTS cache.landing_inventory (
  day date NOT NULL, url text NOT NULL, brand text, host text, path text,
  sessions int, sessions_paid int, sessions_organic int, key_events int, revenue numeric,
  is_product bool, handle text, barcode text, stock_qty int, in_stock bool,
  load_ms int, http_status int, page_bytes int,
  perf_score numeric, lcp_ms int, cls numeric,
  flag_traffic_no_stock bool, flag_slow bool, checked_at timestamptz DEFAULT now(),
  PRIMARY KEY (day, url)
);
ALTER TABLE cache.landing_inventory ADD COLUMN IF NOT EXISTS perf_score numeric;
ALTER TABLE cache.landing_inventory ADD COLUMN IF NOT EXISTS lcp_ms int;
ALTER TABLE cache.landing_inventory ADD COLUMN IF NOT EXISTS cls numeric;
"""

# Meta per rulare: ce magazine au dat GA4 în ziua respectivă (ca să semnalezi „GA4 lipsă" în UI)
DDL_RUNS = """
CREATE TABLE IF NOT EXISTS cache.landing_inventory_runs (
  day date NOT NULL, brand text NOT NULL, property_id text,
  ga4_pages int, ga4_sessions int, ga4_ok bool,
  checked_at timestamptz DEFAULT now(),
  PRIMARY KEY (day, brand)
);
"""


def psi(url, key=None):
    """PageSpeed Insights (Lighthouse mobil) → (perf_score 0-100, lcp_ms, cls).
    Degradare grațioasă: pe 403 (API neactivat) / 429 (quota) întoarce None-uri, NU crapă cronul."""
    params = {"url": url, "strategy": "mobile", "category": "performance"}
    if key: params["key"] = key
    try:
        r = requests.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params, timeout=90)
        if r.status_code != 200:
            return None, None, None
        lr = (r.json() or {}).get("lighthouseResult", {}); au = lr.get("audits", {})
        score = lr.get("categories", {}).get("performance", {}).get("score")
        lcp = au.get("largest-contentful-paint", {}).get("numericValue")
        cls = au.get("cumulative-layout-shift", {}).get("numericValue")
        return (round(score * 100) if score is not None else None,
                int(lcp) if lcp is not None else None,
                round(cls, 3) if cls is not None else None)
    except Exception:
        return None, None, None


def cmd_sync(a):
    end = a.to or (dt.date.today() - dt.timedelta(days=1)).isoformat()
    start = a.from_ or (dt.date.today() - dt.timedelta(days=a.days)).isoformat()
    day = end
    brands = ([(a.brand.lower(), BRANDS[a.brand.lower()])] if a.brand
              else [(k, v) for k, v in BRANDS.items() if k not in ("gt",)])
    if a.brand and a.brand.lower() not in BRANDS:
        sys.exit(f"Brand necunoscut '{a.brand}'. Știu: {', '.join(sorted(set(BRANDS)))}")
    creds = ga_creds()
    mc = mconn(); mcur = mc.cursor()
    ic = iconn(); icur = ic.cursor()
    slow_ms = a.slow_ms
    psi_key = kb("GADS_GOOGLE_API_KEY") if a.lighthouse else None
    all_rows = []
    runs = []  # meta per magazin: a dat GA4 în ziua asta sau nu (pt „GA4 lipsă" în UI)
    print("═" * 74)
    print(f"  LANDING INVENTORY — {len(brands)} magazin(e) · GA4 {start}→{end} · slow>{slow_ms}ms")
    print("═" * 74)
    for slug, pid in brands:
        ga = ga_landing(creds, pid, start, end, a.limit)
        ga_sessions = sum(s for _, _, _, s, _, _ in ga)
        if not ga:
            runs.append((day, slug, pid, 0, 0, False))
            print(f"  {slug:16} — fără date GA4"); continue
        hmap, bid = brand_barcodes(mcur, slug)
        # agregă pe URL (host+path), split paid/organic
        agg = defaultdict(lambda: {"s": 0, "sp": 0, "so": 0, "ke": 0, "rev": 0.0, "host": "", "path": ""})
        for host, lp, ch, s, ke, rev in ga:
            path = norm_path(lp)
            url = f"https://{host}{path}"
            x = agg[url]; x["host"] = host; x["path"] = path
            x["s"] += s; x["ke"] += ke; x["rev"] += rev
            x["sp"] += s if ch in PAID else 0
            x["so"] += s if ch not in PAID else 0
        # top după sesiuni, cap la a.pages pt fetch (viteză)
        items = sorted(agg.items(), key=lambda kv: -kv[1]["s"])[:a.pages]
        oos = 0
        for rank, (url, x) in enumerate(items):
            handle = x["path"].split("/products/")[1].split("/")[0] if "/products/" in x["path"] else None
            barcodes = hmap.get(handle, []) if handle else []
            qty, in_stock = stock_for(icur, barcodes) if barcodes else (None, None)
            load_ms, status, pbytes = measure(url) if a.fetch else (None, None, None)
            page_bytes = pbytes if isinstance(pbytes, int) else None
            perf, lcp, cls = psi(url, psi_key) if (psi_key and rank < a.lighthouse) else (None, None, None)
            no_stock = (in_stock is False)
            slow = (load_ms is not None and load_ms > slow_ms) or (lcp is not None and lcp > slow_ms)
            if no_stock: oos += 1
            all_rows.append((day, url, slug, x["host"], x["path"], x["s"], x["sp"], x["so"], x["ke"],
                             round(x["rev"], 2), bool(handle), handle, (barcodes[0] if barcodes else None),
                             qty, in_stock, load_ms, status, page_bytes, perf, lcp, cls, no_stock, slow))
        runs.append((day, slug, pid, len(items), ga_sessions, True))
        print(f"  {slug:16} {len(items):4} pagini · {sum(x['s'] for _,x in items):>7,} sesiuni · {oos} fără stoc")
    missing = [s for _, s, _, _, _, ok in runs if not ok]
    print("─" * 74)
    if missing:
        print(f"  ⚠ GA4 lipsă ({len(missing)}): {', '.join(missing)}")
    print(f"  TOTAL: {len(all_rows)} pagini · {sum(1 for r in all_rows if r[-2])} cu trafic FĂRĂ STOC · {sum(1 for r in all_rows if r[-1])} lente")
    if not a.apply:
        print("\n  DRY-RUN — nu am scris. Adaugă --apply."); return
    mcur.execute("CREATE SCHEMA IF NOT EXISTS cache"); mcur.execute(DDL)
    psycopg2.extras.execute_values(mcur, """
        INSERT INTO cache.landing_inventory
          (day,url,brand,host,path,sessions,sessions_paid,sessions_organic,key_events,revenue,
           is_product,handle,barcode,stock_qty,in_stock,load_ms,http_status,page_bytes,
           perf_score,lcp_ms,cls,flag_traffic_no_stock,flag_slow)
        VALUES %s
        ON CONFLICT (day,url) DO UPDATE SET
          sessions=EXCLUDED.sessions, sessions_paid=EXCLUDED.sessions_paid, sessions_organic=EXCLUDED.sessions_organic,
          key_events=EXCLUDED.key_events, revenue=EXCLUDED.revenue, is_product=EXCLUDED.is_product,
          handle=EXCLUDED.handle, barcode=EXCLUDED.barcode, stock_qty=EXCLUDED.stock_qty, in_stock=EXCLUDED.in_stock,
          load_ms=EXCLUDED.load_ms, http_status=EXCLUDED.http_status, page_bytes=EXCLUDED.page_bytes,
          perf_score=COALESCE(EXCLUDED.perf_score, cache.landing_inventory.perf_score),
          lcp_ms=COALESCE(EXCLUDED.lcp_ms, cache.landing_inventory.lcp_ms),
          cls=COALESCE(EXCLUDED.cls, cache.landing_inventory.cls),
          flag_traffic_no_stock=EXCLUDED.flag_traffic_no_stock, flag_slow=EXCLUDED.flag_slow, checked_at=now()
    """, all_rows, page_size=1000)
    mcur.execute(DDL_RUNS)
    psycopg2.extras.execute_values(mcur, """
        INSERT INTO cache.landing_inventory_runs (day,brand,property_id,ga4_pages,ga4_sessions,ga4_ok)
        VALUES %s
        ON CONFLICT (day,brand) DO UPDATE SET
          property_id=EXCLUDED.property_id, ga4_pages=EXCLUDED.ga4_pages,
          ga4_sessions=EXCLUDED.ga4_sessions, ga4_ok=EXCLUDED.ga4_ok, checked_at=now()
    """, runs, page_size=200)
    mc.commit()
    print(f"  ✓ cache.landing_inventory: {len(all_rows)} rânduri (zi {day}) · runs: {len(runs)} magazine")


def cmd_properties(a):
    """Listează toate property-urile GA4 la care are acces service account-ul (ca să extinzi BRANDS)."""
    creds = ga_creds()
    r = requests.get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=200",
                     headers={"Authorization": f"Bearer {creds.token}"}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"Admin API {r.status_code}: {r.text[:200]}")
    known = {v: k for k, v in BRANDS.items()}
    print(f"{'PROPERTY':14} {'NUME':28} SLUG (în BRANDS?)")
    for acc in (r.json() or {}).get("accountSummaries", []):
        for p in acc.get("propertySummaries", []):
            pid = p.get("property", "").replace("properties/", "")
            print(f"  {pid:14} {p.get('displayName',''):28} {known.get(pid, '— LIPSEȘTE —')}")


def main():
    ap = argparse.ArgumentParser(description="Inventar landere cu trafic + stoc + viteză")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync", help="construiește inventarul")
    s.add_argument("--brand"); s.add_argument("--all", action="store_true")
    s.add_argument("--days", type=int, default=7); s.add_argument("--from", dest="from_"); s.add_argument("--to")
    s.add_argument("--limit", type=int, default=2000, help="max landing pages din GA4 per magazin")
    s.add_argument("--pages", type=int, default=200, help="câte pagini top (după sesiuni) verific pt stoc/viteză")
    s.add_argument("--slow-ms", type=int, default=3000, help="prag „lent (load_ms sau LCP)")
    s.add_argument("--lighthouse", type=int, default=0, metavar="N", help="rulează Lighthouse (PSI) pe top-N pagini/magazin (0=off; necesită PSI activat pe cheia Google)")
    s.add_argument("--no-fetch", dest="fetch", action="store_false", help="nu măsura viteza (doar GA4+stoc)")
    s.add_argument("--apply", action="store_true", help="scrie în cache.landing_inventory")
    s.set_defaults(fetch=True)
    sub.add_parser("properties", help="listează property-urile GA4 accesibile (extinde BRANDS)")
    a = ap.parse_args()
    if a.cmd == "properties":
        cmd_properties(a)
    else:
        cmd_sync(a)


if __name__ == "__main__":
    main()
