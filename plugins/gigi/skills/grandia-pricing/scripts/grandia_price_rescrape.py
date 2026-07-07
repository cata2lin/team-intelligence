# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9","requests>=2.31","beautifulsoup4>=4.12","lxml>=5"]
# ///
"""
Grandia competitor-price RE-SCRAPER — relights the alive prc_* verdict engine.

The verdict engine (prc_product_status_daily, nightly 02:30) is ALIVE but its
competitor-price INPUT went stale (~90d): the old Google-Shopping scraper died.
This refreshes prices for the EXISTING product↔competitor mappings
(prc_competitor_products, 94% direct retailer URLs incl eMAG) by fetching each
URL and extracting the price (Shopify .js → JSON-LD → og/product:price → itemprop).

Writes: INSERT prc_competitor_prices(source='rescrape') + UPDATE
prc_competitor_products(last_price,last_scraped_at,scrape_status,scrape_error).
Dry-run by default; --apply to write. Idempotent time-series (a new price row/run).

Usage:
  export DATABASE_URL_GRANDIA=...          # from kb.py secret-get
  uv run grandia_price_rescrape.py --limit 20            # dry sample
  uv run grandia_price_rescrape.py --apply               # full refresh, write
  uv run grandia_price_rescrape.py --include-google --apply
"""

# --- shared secret helper (env-first, KB fallback) via core/scripts/arona_pg.py ---
import os as _os, sys as _sys
from pathlib import Path as _Path
_here = _Path(__file__).resolve()
for _up in range(2, 8):
    _c = _here.parents[_up] / "core" / "scripts"
    if (_c / "arona_pg.py").exists():
        _sys.path.insert(0, str(_c)); break
try:
    import arona_pg as _apg
    _secret = _apg.secret
    def _secret_opt(k):
        try: return _apg.secret(k)
        except Exception: return _os.environ.get(k)
except Exception:
    _secret = lambda k: _os.environ[k]
    _secret_opt = lambda k: _os.environ.get(k)
# --- end helper ---
import os, re, sys, json, uuid, time, socket
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2, psycopg2.extras
import requests
from bs4 import BeautifulSoup

socket.setdefaulttimeout(10)  # best-effort cap on socket ops (dead domains)

# ---- args ----
def arg(name, default=None, cast=str):
    if name in sys.argv:
        i = sys.argv.index(name)
        if cast is bool: return True
        return cast(sys.argv[i + 1])
    return default
LIMIT = arg("--limit", None, int)
WORKERS = arg("--workers", 8, int)
TIMEOUT = (4, 8)                       # (connect, read) — caps most hangs at requests level
HARD_CAP = arg("--hard-cap", 18, int)  # abandon any single URL that exceeds this (DNS hangs)
APPLY = "--apply" in sys.argv
INCLUDE_GOOGLE = "--include-google" in sys.argv
ONLY_STALE_DAYS = arg("--only-stale-days", None, int)   # re-scrape only mappings older than N days

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
      "Accept-Language": "ro-RO,ro;q=0.9,en;q=0.8",
      "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

_OK = {"host","port","dbname","user","password","sslmode","sslrootcert","sslcert","sslkey","connect_timeout","application_name","channel_binding"}
def clean(d):
    p = urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme, p.netloc, p.path,
        urlencode([(x, y) for x, y in parse_qsl(p.query, keep_blank_values=True) if x.lower() in _OK]), p.fragment))

# ---- price extraction ----
def _num(x):
    if x is None: return None
    s = re.sub(r"[^\d,.\-]", "", str(x))
    if not s: return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        v = float(s); return round(v, 2) if 0 < v < 1e7 else None
    except Exception:
        return None

def from_jsonld(soup):
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            n = stack.pop()
            if not isinstance(n, dict): continue
            if isinstance(n.get("@graph"), list): stack.extend(n["@graph"])
            offers = n.get("offers")
            if offers:
                offs = offers if isinstance(offers, list) else [offers]
                for o in offs:
                    if isinstance(o, dict):
                        ps = o.get("priceSpecification")
                        if isinstance(ps, list): ps = ps[0] if ps else {}
                        if not isinstance(ps, dict): ps = {}
                        p = _num(o.get("price") or o.get("lowPrice") or ps.get("price"))
                        if p: return p
            for v in n.values():
                if isinstance(v, dict): stack.append(v)
                elif isinstance(v, list): stack.extend([x for x in v if isinstance(x, dict)])
    return None

def from_meta(soup):
    for prop in ("product:price:amount", "og:price:amount"):
        m = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if m and m.get("content"):
            p = _num(m["content"])
            if p: return p
    m = soup.find(attrs={"itemprop": "price"})
    if m:
        return _num(m.get("content") or m.get_text())
    return None

def from_shopify_js(url, sess):
    if "/products/" not in url: return None
    base = url.split("?")[0].rstrip("/")
    try:
        r = sess.get(base + ".js", headers=UA, timeout=TIMEOUT)
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            d = r.json()
            var = None
            m = re.search(r"variant=(\d+)", url)
            if m and d.get("variants"):
                var = next((v for v in d["variants"] if str(v.get("id")) == m.group(1)), None)
            price = (var or {}).get("price") or (d.get("variants") or [{}])[0].get("price") or d.get("price")
            if price: return round(float(price) / 100.0, 2)
    except Exception:
        pass
    return None

def extract(url):
    if not INCLUDE_GOOGLE and "google.com/aclk" in url:
        return None, "skip-google"
    sess = requests.Session()
    p = from_shopify_js(url, sess)
    if p: return p, "shopify.js"
    try:
        r = sess.get(url, headers=UA, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code != 200:
            return None, f"HTTP{r.status_code}"
        soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:
        return None, f"ERR:{type(e).__name__}"
    p = from_jsonld(soup)
    if p: return p, "jsonld"
    p = from_meta(soup)
    if p: return p, "meta"
    return None, "no-price"

# ---- load mappings ----
G = psycopg2.connect(clean(_secret("DATABASE_URL_GRANDIA")), connect_timeout=20)
G.autocommit = False
where = ["competitor_url IS NOT NULL", "competitor_url <> ''"]
if not INCLUDE_GOOGLE:
    where.append("competitor_url NOT LIKE '%%google.com/aclk%%'")
if ONLY_STALE_DAYS:
    where.append(f"(last_scraped_at IS NULL OR last_scraped_at < now() - interval '{ONLY_STALE_DAYS} days')")
sql = f"SELECT id, product_id, competitor_name, competitor_url, last_price FROM prc_competitor_products WHERE {' AND '.join(where)} ORDER BY last_scraped_at ASC NULLS FIRST"
if LIMIT: sql += f" LIMIT {LIMIT}"
with G.cursor() as c:
    c.execute(sql)
    rows = c.fetchall()

print(f"{'APPLY' if APPLY else 'DRY'} — re-scraping {len(rows)} competitor mappings ({WORKERS} workers)\n" + "=" * 92)
now = datetime.now(timezone.utc)

def work(row):
    cid, pid, name, url, oldp = row
    price, src = extract(url)
    return (cid, pid, name, url, oldp, price, src)

results = []
t0 = time.time()
ex = ThreadPoolExecutor(max_workers=WORKERS)
futs = [ex.submit(work, r) for r in rows]
for i, (fut, row) in enumerate(zip(futs, rows), 1):
    try:
        res = fut.result(timeout=HARD_CAP)   # abandon threads that hang past HARD_CAP (leak, OS reaps)
    except FutTimeout:
        res = (row[0], row[1], row[2], row[3], row[4], None, "timeout")
    except Exception as e:
        res = (row[0], row[1], row[2], row[3], row[4], None, f"ERR:{type(e).__name__}")
    results.append(res)
    if i % 25 == 0:
        ok = sum(1 for r in results if r[5] is not None)
        print(f"  ...{i}/{len(rows)}  ok={ok}  ({time.time()-t0:.0f}s)", flush=True)
ex.shutdown(wait=False)

ok = [r for r in results if r[5] is not None]
fail = [r for r in results if r[5] is None]
print("=" * 92)
print(f"EXTRACTED {len(ok)}/{len(results)} ({100*len(ok)//max(1,len(results))}%)  |  failed {len(fail)}")
# fail reason breakdown
from collections import Counter
fr = Counter(r[6] for r in fail)
print("  fail reasons:", dict(fr.most_common()))
# sample of price moves
print("\nSample refreshed prices (old → new):")
for cid, pid, name, url, oldp, price, src in ok[:12]:
    op = f"{float(oldp):.2f}" if oldp is not None else "—"
    print(f"  {op:>8} → {price:>8.2f}  [{src:10}] {(name or '')[:52]}")

if not APPLY:
    print(f"\n[DRY] no DB write. Re-run with --apply to write {len(ok)} fresh prices.")
    sys.exit(0)

# ---- write ----
ins = 0; upd = 0
with G.cursor() as c:
    for cid, pid, name, url, oldp, price, src in results:
        if price is not None:
            c.execute("INSERT INTO prc_competitor_prices (id, competitor_product_id, price, source, recorded_at) VALUES (%s,%s,%s,%s,%s)",
                      (uuid.uuid4().hex, cid, price, "rescrape", now))
            c.execute("UPDATE prc_competitor_products SET last_price=%s, last_scraped_at=%s, scrape_status='ok', scrape_error=NULL WHERE id=%s",
                      (price, now, cid))
            ins += 1; upd += 1
        else:
            c.execute("UPDATE prc_competitor_products SET last_scraped_at=%s, scrape_status='error', scrape_error=%s WHERE id=%s",
                      (now, (src or "")[:200], cid))
            upd += 1
G.commit()
print(f"\n[APPLY] committed: {ins} fresh price rows, {upd} mappings updated.")
