# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
reviews_manager.py — Acoperire & management recenzii produse pe brand, prin Judge.me API.

Raporteaza per magazin: rating mediu, total recenzii, produse cu 0 recenzii,
recenzii recente si recenzii cu nota mica (<=3) care au nevoie de raspuns. In plus,
gaseste produsele cele mai vandute care NU au recenzii (cross cu metrics DB).

NU scrie nimic (read-only). Judge.me API:
  GET https://judge.me/api/v1/reviews?api_token=<priv>&shop_domain=<myshopify>&per_page=100
  GET https://judge.me/api/v1/reviews/count, /products/count, /products

Folosire:
  uv run reviews_manager.py coverage --brand esteban
  uv run reviews_manager.py coverage --brand all
  uv run reviews_manager.py recent   --brand gt --limit 15
  uv run reviews_manager.py low      --brand esteban --limit 20
  uv run reviews_manager.py bestsellers --brand esteban --limit 20   (top vandute fara recenzii)
"""
import sys, os, re, json, time, argparse, subprocess, urllib.parse, urllib.request

# brand_key (CLI) -> (JUDGEME suffix, shop_domain myshopify, metrics slug)
BRANDS = {
    "esteban":   ("ESTEBAN",    "6f9e22-9d.myshopify.com", "esteban"),
    "grandia":   ("GRANDIA",    "n12w89-yy.myshopify.com", "grandia"),
    "gt":        ("GT",         "ix5bxc-hr.myshopify.com", "george-talent"),
    "rossi":     ("ROSSI",      "1d2bce-2.myshopify.com",  "rossi-nails"),
    "gento":     ("GENTO",      "cn54vk-uz.myshopify.com", "gento"),
    "bonhaus_pl":("BONHAUS_PL", "f0yrmh-ia.myshopify.com", "bonhaus-pl"),
    "redbune":   ("REDBUNE",    "audusp-rf.myshopify.com", "reduceri-bune"),
}
ALIASES = {"george-talent": "gt", "george_talent": "gt", "bonhaus": "bonhaus_pl",
           "bonhaus-pl": "bonhaus_pl", "rossi-nails": "rossi", "reducerile-bune": "redbune",
           "reduceri-bune": "redbune", "reduceri": "redbune"}

KB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")
API = "https://judge.me/api/v1"


def secret(key):
    v = os.environ.get(key)
    if v:
        return v.strip()
    return subprocess.run(["uv", "run", KB, "secret-get", key], capture_output=True, text=True).stdout.strip()


def resolve_brand(name):
    k = name.strip().lower().replace(" ", "-")
    k = ALIASES.get(k, k)
    if k not in BRANDS:
        sys.exit("Brand necunoscut '%s'. Optiuni: %s" % (name, ", ".join(BRANDS)))
    return k


def jget(path, token, shop, params=None):
    p = {"api_token": token, "shop_domain": shop}
    if params:
        p.update(params)
    url = "%s/%s?%s" % (API, path, urllib.parse.urlencode(p))
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(1.5 * (attempt + 1)); continue
            raise
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError("Judge.me API failed pe %s" % path)


def fetch_all_reviews(token, shop, max_pages=25):
    """Streameaza recenziile (per_page=100, cap max_pages*100 ca sa nu atarne pe magazine mari
    ex. Esteban 7505 recenzii). Pt coverage e un esantion suficient pt rating mediu + no-review."""
    out = []
    page = 1
    while page <= max_pages:
        d = jget("reviews", token, shop, {"per_page": 100, "page": page})
        revs = d.get("reviews", [])
        if not revs:
            break
        out.extend(revs)
        if len(revs) < 100:
            break
        page += 1
    return out


def numeric_id(gid_or_num):
    m = re.search(r'(\d+)\s*$', str(gid_or_num))
    return m.group(1) if m else str(gid_or_num)


# ---------------- Postgres (metrics) for bestsellers ----------------
def get_metrics_conn():
    import pg8000.dbapi
    url = secret("DATABASE_URL_METRICS")
    u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def top_sellers(slug, limit=200):
    """[(numeric_product_id, title, units_total)] sortat desc dupa cantitate."""
    conn = get_metrics_conn(); cur = conn.cursor()
    cur.execute('SELECT id FROM brands WHERE slug=%s LIMIT 1', (slug,))
    row = cur.fetchone()
    if not row:
        conn.close(); return []
    bid = row[0]
    cur.execute('''SELECT "productId", MAX(title), SUM(quantity)
                   FROM order_line_items WHERE "brandId"=%s AND "productId" IS NOT NULL
                   GROUP BY "productId" ORDER BY SUM(quantity) DESC LIMIT %s''', (bid, limit))
    res = [(numeric_id(r[0]), r[1], float(r[2] or 0)) for r in cur.fetchall()]
    conn.close()
    return res


# ---------------- aggregation ----------------
def summarize(reviews):
    n = len(reviews)
    rated = [r for r in reviews if isinstance(r.get("rating"), (int, float))]
    avg = (sum(r["rating"] for r in rated) / len(rated)) if rated else 0.0
    dist = {i: 0 for i in range(1, 6)}
    for r in rated:
        rv = int(round(r["rating"]))
        if 1 <= rv <= 5:
            dist[rv] += 1
    per_prod = {}
    for r in reviews:
        pid = numeric_id(r.get("product_external_id"))
        per_prod[pid] = per_prod.get(pid, 0) + 1
    return n, avg, dist, per_prod


def fmt_stars(rating):
    try:
        r = int(round(rating))
    except Exception:
        r = 0
    return "*" * r + "." * (5 - r)


# ---------------- modes ----------------
def run_coverage(bkey):
    suffix, shop, slug = BRANDS[bkey]
    token = secret("JUDGEME_%s_PRIVATE_TOKEN" % suffix)
    rev_count = jget("reviews/count", token, shop).get("count", 0)
    prod_count = jget("products/count", token, shop).get("count", 0)
    reviews = fetch_all_reviews(token, shop)
    n, avg, dist, per_prod = summarize(reviews)
    products_with_reviews = len(per_prod)
    products_without = max(prod_count - products_with_reviews, 0)
    pct = (products_with_reviews / prod_count * 100) if prod_count else 0
    print("=== %s — acoperire recenzii (Judge.me) ===" % bkey.upper())
    print("  Recenzii totale (API count):   %8d" % rev_count)
    print("  Recenzii citite/agregate:      %8d" % n)
    print("  Rating mediu:                  %8.2f  %s" % (avg, fmt_stars(avg)))
    print("  Produse in Judge.me:           %8d" % prod_count)
    print("  Produse CU recenzii:           %8d" % products_with_reviews)
    print("  Produse FARA recenzii (0):     %8d" % products_without)
    print("  Acoperire produse:             %7.1f%%" % pct)
    print("  Distributie note: " + "  ".join("%d*:%d" % (s, dist[s]) for s in (5, 4, 3, 2, 1)))
    low = [r for r in reviews if isinstance(r.get("rating"), (int, float)) and r["rating"] <= 3]
    print("  Recenzii cu nota <=3 (atentie):%8d" % len(low))
    return {"brand": bkey, "rev_count": rev_count, "avg": avg, "prod": prod_count,
            "with": products_with_reviews, "without": products_without, "low": len(low)}


def run_recent(bkey, limit):
    suffix, shop, slug = BRANDS[bkey]
    token = secret("JUDGEME_%s_PRIVATE_TOKEN" % suffix)
    d = jget("reviews", token, shop, {"per_page": min(limit, 100), "page": 1})
    revs = d.get("reviews", [])
    revs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    print("=== %s — recenzii recente (top %d) ===" % (bkey.upper(), limit))
    for r in revs[:limit]:
        body = (r.get("body") or r.get("title") or "").replace("\n", " ").strip()
        print("  [%s] %s %-22s | %s" % (
            (r.get("created_at") or "")[:10], fmt_stars(r.get("rating", 0)),
            (r.get("product_title") or "")[:22], body[:60]))


def run_low(bkey, limit):
    suffix, shop, slug = BRANDS[bkey]
    token = secret("JUDGEME_%s_PRIVATE_TOKEN" % suffix)
    # filtru pe nota direct in API (1,2,3), apoi cele mai recente intai
    low = []
    for star in (1, 2, 3):
        page = 1
        while True:
            d = jget("reviews", token, shop, {"per_page": 100, "page": page, "rating": star})
            revs = d.get("reviews", [])
            low.extend(revs)
            if len(revs) < 100:
                break
            page += 1
            if page > 50:
                break
    low.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    print("=== %s — recenzii cu nota <=3 (necesita raspuns) — total %d ===" % (bkey.upper(), len(low)))
    for r in low[:limit]:
        body = (r.get("body") or r.get("title") or "").replace("\n", " ").strip()
        reviewer = (r.get("reviewer") or {}).get("name") or "anonim"
        print("  [%s] %s id=%s %-20s | %-18s | %s" % (
            (r.get("created_at") or "")[:10], fmt_stars(r.get("rating", 0)),
            r.get("id"), (r.get("product_title") or "")[:20], reviewer[:18], body[:50]))
    if not low:
        print("  (nicio recenzie cu nota <=3 — bravo)")


def run_bestsellers(bkey, limit):
    suffix, shop, slug = BRANDS[bkey]
    token = secret("JUDGEME_%s_PRIVATE_TOKEN" % suffix)
    reviews = fetch_all_reviews(token, shop)
    _, _, _, per_prod = summarize(reviews)  # numeric_id -> count
    sellers = top_sellers(slug, limit=300)
    if not sellers:
        print("=== %s — fara date de vanzari in metrics DB pentru slug '%s' ===" % (bkey.upper(), slug))
        return
    missing = [(pid, title, units) for pid, title, units in sellers if per_prod.get(pid, 0) == 0]
    print("=== %s — produse cele mai vandute FARA recenzii (top %d) ===" % (bkey.upper(), limit))
    print("  (din top %d produse vandute, %d nu au nicio recenzie)" % (len(sellers), len(missing)))
    print("  %-50s %10s" % ("produs", "buc vandute"))
    for pid, title, units in missing[:limit]:
        print("  %-50s %10d" % ((title or pid)[:50], int(units)))
    if not missing:
        print("  (toate produsele vandute au cel putin o recenzie)")


def main():
    ap = argparse.ArgumentParser(description="Management recenzii produse via Judge.me")
    ap.add_argument("mode", choices=["coverage", "recent", "low", "bestsellers"])
    ap.add_argument("--brand", required=True, help="nume brand sau 'all' (doar pt coverage)")
    ap.add_argument("--limit", type=int, default=15)
    a = ap.parse_args()

    if a.brand.strip().lower() == "all":
        if a.mode != "coverage":
            sys.exit("'--brand all' e suportat doar in modul 'coverage'.")
        rows = []
        for bkey in BRANDS:
            try:
                rows.append(run_coverage(bkey)); print()
            except Exception as e:
                print("  ! %s a esuat: %s\n" % (bkey, e))
        if rows:
            print("=== SUMAR toate brandurile ===")
            print("  %-12s %8s %6s %7s %7s %5s" % ("brand", "recenzii", "rating", "prod0", "prod", "low<=3"))
            for r in rows:
                print("  %-12s %8d %6.2f %7d %7d %5d" % (
                    r["brand"], r["rev_count"], r["avg"], r["without"], r["prod"], r["low"]))
        return

    bkey = resolve_brand(a.brand)
    if a.mode == "coverage":
        run_coverage(bkey)
    elif a.mode == "recent":
        run_recent(bkey, a.limit)
    elif a.mode == "low":
        run_low(bkey, a.limit)
    elif a.mode == "bestsellers":
        run_bestsellers(bkey, a.limit)


if __name__ == "__main__":
    main()
