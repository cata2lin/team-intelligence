---
name: scraper-construction
description: Battle-tested playbook for building robust e-commerce / product-data web scrapers. Covers recon (platform fingerprinting — Shopify, WooCommerce, Magento, Gomag, Avanticart, SPAs), URL discovery (sitemap walking, robots.txt, GraphQL pagination), parsing (JSON-LD, embedded JS hydration, brace-balanced extraction, JS-object-to-JSON normalization), the 8 stock-extraction techniques (per-store API, add-to-cart probing with binary search, DOM aggregation, JS inventory maps, multi-candidate aggregation), the 9-rung anti-bot escalation ladder (proxy rotation with health scoring, token-bucket rate limiting, adaptive concurrency, Selenium-with-stealth, hybrid cookie-bootstrap), concurrency architectures (async + producer-consumer queues, ThreadPool for Selenium, subprocess isolation), and robustness patterns (failure taxonomies, checkpoints, early-stop heuristics, stuck detection via DB activity, append-only price/stock history schemas). Use whenever building a new scraper, debugging a broken one, designing a scraping pipeline, picking concurrency/proxy settings, choosing between async aiohttp and Selenium, extracting stock/price from a stubborn site, or evaluating anti-bot risks. Synthesized from reading ~60 production scrapers in AronaDev/scrapers.
---

# Scraper Construction Playbook

A condensed, actionable reference for building product-data scrapers. Every pattern here was extracted from production scrapers — no theory.

## When to load this skill

- Building a new scraper for an e-commerce / product-data site
- Debugging a scraper that broke (use the failure-mode checklist)
- Choosing concurrency, proxy strategy, or anti-bot approach
- Picking between async aiohttp and Selenium
- Extracting stock or price from a site that hides it
- Designing a scraping pipeline or schema

## The mental model

**The HTML you see is rarely where you should be scraping from.** Always go up the data stack:

1. GraphQL or REST API → cleanest, paginated, all data in one call
2. JSON-LD `<script type="application/ld+json">` → structured, stable
3. Embedded JS globals (`window.__INITIAL_STATE__`, `var meta = {...}`) → hydration data
4. Per-page AJAX endpoints the site calls itself → check DevTools network tab
5. DOM (CSS selectors) → last resort, most fragile

Going up this stack is what separates a 1-week scraper from a 1-day one.

---

## Phase 1 — Recon (30–45 min before writing any code)

Open DevTools and answer these in order:

### 1. What platform is this?

Look at `<script>` tags, generated CSS classes, network requests, `Set-Cookie` headers. Signatures:

| Platform | Tells | Data location |
|---|---|---|
| Shopify | `cdn.shopify.com`, `script#ProductJson`, `/products/{handle}.js` | `inventory_quantity`, price in cents |
| WooCommerce | `wp-content/`, `<meta property="product:price:amount">` | `form.variations_form[data-product_variations]`, `input[name=quantity][max]` |
| Magento | `/rest/all/V1/...` endpoints, SKU-based IDs | `/rest/all/V1/product/get-stock-per-stores?sku=...` |
| PrestaShop | `prestashop` JS global, `/themes/...` URLs | Inline JS product object |
| Gomag (RO) | `$.Gomag.getEnvData()` JS global | `realStock` field |
| Avanticart (RO) | `window.avanticart.product` JS global | `product_available_stock` |
| Custom SPA | `window.__INITIAL_STATE__`, `window.__NUXT__`, `window.__NEXT_DATA__` | Hydration JSON blob |

Recognizing the platform tells you where the data lives. Once you know it, 80% of the scraper is template work.

### 2. Does it have JSON-LD?

`view-source:` → `Ctrl+F` `application/ld+json`. If yes and it's a `Product` schema with `offers`, you're 60% done. Check both Product and BreadcrumbList types.

### 3. Does it have a sitemap?

**Always probe multiple paths even if robots.txt is silent.** Some marketplaces (e.g., Trendyol) deliberately omit the `Sitemap:` declaration to hide the catalog from casual crawlers, but the sitemap exists at a non-standard URL.

Try in order:
- `/sitemap.xml` (standard)
- `/sitemap_index.xml` (alternate convention — Trendyol uses this)
- `/sitemap-index.xml`
- `/sitemaps.xml`
- `/sitemap_products.xml`
- `/sitemap-products.xml`
- Localized paths: `/<locale>/sitemap.xml`, `/<locale>/sitemap_index.xml`
- `/robots.txt` for explicit `Sitemap:` declarations (may be absent)

Probe with HEAD or short GET and check for 200. If robots.txt has no `Sitemap:` line but you find a working sitemap path anyway, that's intentional hiding — not a bug in your discovery.

Note if sitemaps are gzip-compressed (`.xml.gz`) or partitioned (`sitemap_products-1.xml`, `sitemap_products-2.xml`, ...). Marketplace sitemaps can be huge — single files of 20K–50K URLs at 30–50 MB are common. **Stream-parse with `xml.etree.ElementTree.iterparse`** instead of loading the whole file:

```python
import requests, xml.etree.ElementTree as ET
def stream_urls(sitemap_url, url_filter=lambda u: True):
    with requests.get(sitemap_url, stream=True, timeout=120) as r:
        r.raise_for_status()
        for _, elem in ET.iterparse(r.raw, events=("end",)):
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag == "loc" and elem.text and url_filter(elem.text):
                yield elem.text.strip()
                elem.clear()  # critical — frees memory
```

### 4. What does Network tab show on a product page?

Filter by XHR. Sites often call their own JSON API. Look for:
- `/products/{handle}.js` (Shopify)
- `/cart/add.json` or `add-to-cart` POSTs
- `/rest/V1/...` (Magento)
- GraphQL: any POST to `/graphql`-like endpoint
- Per-store/inventory endpoints

### 5. What's the URL pattern for a product?

Critical for sitemap filtering. Examples:
- `/products/{slug}` (Shopify)
- `/{slug}-p{id}.html` or `/{slug}-p{id}` (Magento-ish)
- `/p-{id}/{slug}` (vivre)
- `/p/{id}` (some Avanticart)

Write a single regex now — you'll use it both to filter sitemap URLs and to extract IDs.

### 6. Is there Cloudflare?

Look for `cf-ray` response header or "Just a moment..." challenge page. If yes: residential proxies or Selenium with `uc=True`.

### 7. What does the site do under load?

Open 10 tabs to the same product page rapidly. If all serve → high concurrency ceiling. If 429 or temp blocks → need rate limiting.

**Output of recon**: one page of notes (platform, JSON sources, URL regex, sitemap structure, AJAX endpoints, risk). Don't skip this.

---

## Phase 2 — URL discovery

Decision tree, in priority order:

```
1. Public catalog API (GraphQL or REST)?
   → Use it. One endpoint, paginated, all data. Skip the rest.

2. Sitemap at /sitemap.xml or in /robots.txt?
   → Yes (90%+ of e-commerce):
     a) Parse sitemap index → find product-specific sub-sitemaps
     b) Filter sub-sitemap URLs by a product-URL regex
   → No:
     a) Crawl category pages with pagination
     b) Set early-stop after N pages with no new IDs

3. Gzip-compressed sitemaps (.xml.gz)?
   → fetch + gzip.decompress() + BS4-xml-parse

4. Partitioned sitemaps (sitemap_products-N.xml)?
   → Probe 1..N if index doesn't list them all
```

### Sitemap discovery from robots.txt

```python
def discover_sitemaps_from_robots(base_url):
    r = requests.get(f"{base_url}/robots.txt", timeout=30)
    return [
        line.split(":", 1)[1].strip()
        for line in r.text.splitlines()
        if line.lower().startswith("sitemap:")
    ]
```

### BFS sitemap walk (handles nesting)

```python
def walk_sitemaps(initial_urls, product_url_re):
    to_visit, seen, products = list(initial_urls), set(), {}
    while to_visit:
        url = to_visit.pop()
        if url in seen: continue
        seen.add(url)
        try:
            soup = BeautifulSoup(requests.get(url, timeout=60).content, "xml")
        except Exception:
            continue
        for nested in soup.find_all("sitemap"):  # sitemap index
            if (loc := nested.find("loc")):
                to_visit.append(loc.text.strip())
        for url_tag in soup.find_all("url"):  # leaf entries
            loc, img = url_tag.find("loc"), url_tag.find("image:loc")
            if loc and product_url_re.search(loc.text):
                products[loc.text.strip()] = img.text.strip() if img else None
    return products
```

### Gzip-compressed sitemap handling

```python
import gzip
resp = requests.get(gz_url, timeout=60)
if gz_url.endswith(".gz") and resp.content[:2] == b'\x1f\x8b':
    xml_content = gzip.decompress(resp.content)
else:
    xml_content = resp.content
soup = BeautifulSoup(xml_content, "xml")
```

### Partitioned sitemap probing

```python
m = re.search(r"(sitemap_products)([-_])(\d+)\.xml$", url)
if m:
    base, sep, last = m.group(1), m.group(2), int(m.group(3))
    prefix = url.rsplit(m.group(0), 1)[0]
    for i in range(1, last + 1):
        candidates.add(f"{prefix}{base}{sep}{i}.xml")
```

### URL filter regex (single source of truth)

```python
PRODUCT_URL_RE = re.compile(r"-p(\d+)(?:\.html)?$")  # both filter + ID extraction
def is_product_url(u): return PRODUCT_URL_RE.search(u) is not None
def product_id_from_url(u):
    m = PRODUCT_URL_RE.search(u)
    return m.group(1) if m else None
```

### Always grab image from sitemap

`<image:image><image:loc>` in sitemap entries saves you from re-parsing pages just for images. Pass it as a fallback through to the parser.

### Cache URLs with 24h TTL

```python
URL_CACHE_FILE = f"scraper_data/{site}_urls.json"
if os.path.exists(URL_CACHE_FILE) and (time.time() - os.path.getmtime(URL_CACHE_FILE)) < 86400:
    return json.load(open(URL_CACHE_FILE))
# else fetch + save
```

---

## Phase 3 — Data extraction (parsing decision tree)

### 3.1 GraphQL / REST API (always prefer)

If the site has a public catalog endpoint, use it. One paginated call gets everything:

```python
async def fetch_all_nodes(session, query, page_size=500):
    nodes, offset = [], 0
    while True:
        variables = {"after": str(offset), "limit": page_size, "domain": "RO", ...}
        payload = {"query": query, "variables": variables}
        async with session.post(ENDPOINT, json=payload, headers=auth_header()) as r:
            data = await r.json()
        edges = data.get("data", {}).get("getProductsEs", {}).get("edges", [])
        if not edges: break
        nodes.extend(e["node"] for e in edges)
        if len(edges) < page_size: break
        offset += page_size
    return nodes
```

When images are hash-keyed (cdn-style), reconstruct URLs without fetching:

```python
def image_url_from_hash(h):  # https://cdn/path/<h[0:2]>/<h[2:4]>/<h>-<size>.webp
    if not h or len(h) < 4: return None
    return f"{IMG_HOST}/{IMG_MODE}/{h[0:2]}/{h[2:4]}/{h}-{IMG_SIZE}.webp"
```

### 3.2 JSON-LD `<script type="application/ld+json">`

The cleanest in-page source. Handle the three shapes (single dict, list, `@graph` wrapper):

```python
def extract_json_ld_product(soup):
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text())
            candidates = []
            if isinstance(data, list): candidates = data
            elif isinstance(data, dict):
                candidates = data.get("@graph", [data]) if "@graph" in data else [data]
            for obj in candidates:
                if isinstance(obj, dict):
                    t = obj.get("@type")
                    if t == "Product" or (isinstance(t, list) and "Product" in t):
                        return obj
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
    return None
```

JSON-LD gotchas:
- `offers` can be `dict` OR `list` — handle both: `if isinstance(offers, list) and offers: offers = offers[0]`
- `image` can be string OR list: `image[0] if isinstance(image, list) else image`
- `brand` can be `{"@type": "Brand", "name": "X"}` OR just `"X"`
- `availability` is a URL string: `http://schema.org/InStock` / `OutOfStock` (NOT numeric)

### 3.3 Embedded JS globals

**Shopify patterns** (try in order):

```python
# var meta = {...};
m = re.search(r"var\s+meta\s*=\s*(\{.*?\});", html, re.DOTALL)
# window.ShopifyAnalytics.meta = {...};
m = re.search(r"ShopifyAnalytics\.meta\s*=\s*(\{.*?\});", html, re.DOTALL)
# <script id="WH-ProductJson-product-template" type="application/json">
script = soup.select_one("script#WH-ProductJson-product-template")
# data-variant-inventory map
inv_div = soup.select_one("div.product_form[data-variant-inventory]")
```

**SPA hydration with brace-balanced extraction** — when the JSON has nested objects and quotes, regex `\{.*?\}` won't work. Walk char-by-char:

```python
def balance_braces(s):
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(s):
        if in_str:
            if esc: esc = False
            elif ch == "\\": esc = True
            elif ch in ('"', "'"): in_str = False
        else:
            if ch in ('"', "'"): in_str = True
            elif ch == "{": depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0: return s[:i+1]
    return s

# Usage:
script_text = next(s.string for s in soup.find_all("script") if "__INITIAL_STATE__" in (s.string or ""))
blob_start = script_text.index("=") + 1
blob = balance_braces(script_text[blob_start:].lstrip())
data = json.loads(blob)
```

**JS object with numeric keys** — illegal in JSON, but common in Shopify inventory maps like `{47370229678361:"100"}`:

```python
def js_object_to_json(text):
    text = text.strip().rstrip(";")
    text = re.sub(r'(\s|^)(\d+)\s*:', r'\1"\2":', text)  # quote numeric keys
    text = re.sub(r"'", '"', text)  # single → double quotes
    return text
```

### 3.4 Fast/slow parser split (Pepita pattern)

`BeautifulSoup(html, "lxml")` parsing is the bottleneck in async scrapers. Try regex first:

```python
def parse_fast(html, url):
    # Tier 1: regex only, no BS4 — ~10x faster
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            if data.get("@type") == "Product":
                result = extract_from_ld_fast(data, url)
                if result and result.get("title") and result.get("price") is not None:
                    return result
        except json.JSONDecodeError: pass
    return None

def parse_full(html, url):
    if (fast := parse_fast(html, url)): return fast
    # Tier 2: BS4 + layered fallbacks
    soup = BeautifulSoup(html, "lxml")
    # ... slow path
```

### 3.5 DOM as last resort with layered fallbacks

```python
def first_text(soup, selectors):
    for sel in selectors:
        if (el := soup.select_one(sel)):
            txt = el.get_text(" ", strip=True)
            if txt: return txt
    return None

name = first_text(soup, [
    "h1.product-title", "h1[itemprop='name']", "h1",
    "meta[property='og:title']::attr(content)",  # last resort
])
```

---

## Phase 4 — Stock extraction (the hardest problem)

Stock is the part that breaks first in production. Pick the technique that matches your site.

### 4.1 From structured data (best)

Already covered by the parsing layer:
- JSON-LD: `offers.availability` → string `InStock`/`OutOfStock` (NOT numeric)
- Shopify variants: `variants[i].inventory_quantity`
- WooCommerce: `<input name="quantity" max="N">`
- Gomag: `realStock` field
- Avanticart: `product_available_stock` or `product_stock - product_ordered_stock`

### 4.2 From a custom JS inventory map

When inventory is keyed by variant ID in a separate JS global:

```python
inv_pat = re.compile(
    rf"product_inven_array_{re.escape(str(product_id))}\s*=\s*(\{{.*?\}});",
    re.DOTALL,
)
if (m := inv_pat.search(html)):
    inv_map = json.loads(js_object_to_json(m.group(1)))
    stock = int(inv_map.get(str(variant_id), 0))
```

### 4.3 Per-store DOM attribute aggregation

Pharmacies, retail chains — sum across physical locations:

```python
total = 0
for el in soup.find_all(attrs={"data-store-quantity": True}):
    try: total += int(el["data-store-quantity"])
    except (ValueError, TypeError): pass
```

### 4.4 Per-store API call (Magento pattern)

```python
async with session.get(
    f"{BASE_URL}/rest/all/V1/product/get-stock-per-stores?sku={sku}",
    proxy=proxy_url, proxy_auth=proxy_auth, timeout=15,
) as r:
    if r.status == 200:
        data = await r.json()  # [{"store_id": 1, "qty": 5}, ...]
        stock = sum(int(s.get("qty", 0)) for s in data) if isinstance(data, list) else 0
```

Open DevTools network tab on a product page and look for this kind of XHR before assuming it's not there.

### 4.5 Add-to-cart probing — big quantity

The site refuses, error message reveals stock:

```python
ajax_headers = {**base_headers,
    "X-Requested-With": "XMLHttpRequest",
    "Referer": product_url,
    "Origin": base_url.rstrip("/"),
}
async with session.post(add_cart_url, data={"id_produs": pid, "cantitate": "99999"},
                        headers=ajax_headers) as r:
    txt = await r.text()
    for pat in [r"stocul de\s+(\d+)\s+buc", r"Doar\s+(\d+)\s+r[ăa]mas",
                r"Only\s+(\d+)\s+left", r'data-current-inventory="(\d+)"']:
        if (m := re.search(pat, txt, re.I)):
            return int(m.group(1))
```

### 4.6 Add-to-cart probing — exponential + binary search

When error doesn't include the number, only "out of stock":

```python
async def probe_stock(session, product_url, pid, add_url, headers):
    async def try_qty(q):
        async with session.post(add_url, data={"id_produs": pid, "cantitate": str(q)},
                                headers=headers) as r:
            txt = await r.text()
            return "eroare" not in txt.lower() and "indisponibil" not in txt.lower()

    # Exponential up
    hi, ok_last = 1, None
    for _ in range(10):
        if await try_qty(hi):
            ok_last = hi
            hi *= 2
        else: break

    if not ok_last: return 0

    # Binary search the boundary
    lo, hi = ok_last, hi
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if await try_qty(mid): lo = mid
        else: hi = mid - 1
    return lo
```

### 4.7 Add-to-cart endpoint discovery (don't hardcode)

Sites embed URL constants in JS — read them:

```python
m = re.search(r"add_constants\(\[(.*?)\]\);", html, re.S)
constants = dict(re.findall(r"\{name:\s*'([^']+)',\s*value:\s*'([^']*)'\}", m.group(1))) if m else {}
add_url = next((v for k,v in constants.items() if "cos" in k.lower() and "adauga" in k.lower()), None)
# Fallback: try common paths
for path in ["/adauga-in-cos/", "/cart/add", "/cart/add.json", "/cos/adauga/"]:
    candidate = urljoin(base_url, path)
    # probe candidate
```

### 4.8 Cart-API JSON response (cleanest cart probe)

When the site has a JSON cart endpoint:

```python
# Use Selenium session (with cookies) OR aiohttp with cookie bootstrap
driver.get(f"{BASE_URL}/cart/clear.json")
driver.get(f"{BASE_URL}/cart/add.json?items[product][product_id]={pid}&qty=999")
data = json.loads(driver.find_element(By.TAG_NAME, "pre").text)
stock = data["items"][0]["stock"]
```

### 4.9 Multi-candidate aggregation (when no single source is reliable)

Three sources, sum them, but a flag can force zero:

```python
candidates = []
if (q := data.get("stock", {}).get("quantity")) is not None: candidates.append(int(q))
if (q := data.get("dataStock", {}).get("stock", {}).get("quantity")) is not None: candidates.append(int(q))
for k in ("stockAvailable", "stockSupplier", "stockFast"):
    if (v := datalayer.get(k)) is not None: candidates.append(int(v))

forced_sold_out = bool(data.get("stock", {}).get("forcedSoldOut"))
stock = 0 if forced_sold_out else sum(c for c in candidates if c >= 0)
```

Rules:
- `sum(candidates)` when sources represent different warehouses/sellers
- `max(candidates)` when sources are alternatives to each other ("some widgets cap display")

### 4.10 Text-pattern regex fallback (always multi-language)

```python
STOCK_PATTERNS = [
    re.compile(r"stocul de\s+(\d+)\s+buc", re.I),  # RO error message
    re.compile(r"Doar\s+(\d+)\s+r[ăa]mas", re.I),   # RO "Only N left"
    re.compile(r"Only\s+(\d+)\s+left", re.I),       # EN
    re.compile(r"În\s*stoc:\s*(\d+)\s*buc", re.I),  # RO
    re.compile(r"Rakt[aá]ron:\s*(\d+)\s*db", re.I), # HU
    re.compile(r'data-current-inventory="(\d+)"', re.I),
]
def extract_stock_from_text(text):
    for pat in STOCK_PATTERNS:
        if (m := pat.search(text)):
            try: return int(m.group(1))
            except ValueError: pass
    return None
```

---

## Phase 5 — Number parsing (the locale trap)

`float("1.234,56")` raises. `float("1,234.56")` returns 1.0. Build a real normalizer:

```python
_NUM = re.compile(r"([0-9]{1,3}(?:[ .]?[0-9]{3})*(?:[.,][0-9]{2})|[0-9]+)")

def to_float(s):
    if s is None: return None
    if isinstance(s, (int, float)): return float(s)
    s = str(s).strip().replace(" ", "").replace(" ", "")
    # Strip common currency suffixes BEFORE format detection
    for suffix in [" lei", "lei", " RON", "RON", "€", "$", " buc", "buc", " db", " pcs"]:
        if s.endswith(suffix): s = s[:-len(suffix)].strip()
    # Detect format
    if "," in s and "." in s:
        # Heuristic: assume European if comma is closer to end
        if s.rfind(",") > s.rfind("."): s = s.replace(".", "").replace(",", ".")
        else: s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try: return float(s)
    except ValueError:
        m = _NUM.search(s)
        return float(m.group(1).replace(".", "").replace(",", ".")) if m else None
```

---

## Phase 6 — Anti-bot escalation ladder

Start at rung 1, escalate only when you get blocked. Each higher rung adds latency/cost.

### Rung 1 — Sane defaults (works on 30% of sites)

```python
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ... Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) ... Firefox/128.0",
    # 4-6 real, current Chrome/Firefox UAs — NEVER one mentioning "python"/"curl"
]
def headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",  # MATCH site's locale
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }
```

### Rung 2 — Proxy rotation

Webshare format `IP:PORT:USER:PASS`:

```python
parts = proxy_str.split(":")
proxy_url = f"http://{parts[0]}:{parts[1]}"
proxy_auth = aiohttp.BasicAuth(parts[2], parts[3])
async with session.get(url, proxy=proxy_url, proxy_auth=proxy_auth) as r: ...
```

### Rung 3 — Proxy health scoring (don't rotate blindly)

```python
@dataclass
class ProxyStats:
    score: int = 100
    consecutive_failures: int = 0
    last_failure: float = 0.0
    last_used: float = 0.0

class ProxyManager:
    def __init__(self, proxies, cooldown=300, min_score=10):
        self.proxies = proxies
        self.stats = {p: ProxyStats() for p in proxies}
        self.cooldown = cooldown
        self.min_score = min_score
        self._cycler = itertools.cycle(proxies)

    def get_next(self):
        for _ in range(len(self.proxies)):
            p = next(self._cycler)
            s = self.stats[p]
            # Skip banned
            if s.score <= self.min_score and (time.time() - s.last_failure) < self.cooldown:
                continue
            # Enforce 1 req/sec/proxy
            if time.time() - s.last_used < 1.0:
                continue
            s.last_used = time.time()
            return p
        # Fallback: least-recently-used
        return min(self.proxies, key=lambda p: self.stats[p].last_used)

    def record(self, proxy, success, response_time=0):
        s = self.stats[proxy]
        if success:
            s.consecutive_failures = 0
            boost = max(1, int(50 - response_time * 10))  # fast = big boost
            s.score = min(100, s.score + boost)
        else:
            s.consecutive_failures += 1
            s.last_failure = time.time()
            s.score = max(0, s.score - 25 * s.consecutive_failures)  # escalating penalty
```

### Rung 4 — Token-bucket rate limiting (global RPS cap)

```python
class RateLimiter:
    def __init__(self, rate_per_sec, capacity):
        self.rate, self.capacity = rate_per_sec, capacity
        self.tokens = float(capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
            if self.tokens < 1.0:
                await asyncio.sleep((1.0 - self.tokens) / self.rate)
                self.tokens = 0
            self.tokens -= 1.0
            self.updated = time.monotonic()
```

Typical settings: `rate=1.5/s, capacity=8` for stricter sites; `rate=20/s, capacity=50` for lenient ones.

### Rung 5 — Adaptive concurrency (the killer pattern)

Track failure rate in a sliding window, adjust live:

```python
class AdaptiveConcurrency:
    def __init__(self, initial=50, min_=10, max_=300, window=250):
        self.current, self.min, self.max, self.window_size = initial, min_, max_, window
        self.results = []  # rolling success/failure
        self.last_adjust = time.time()
        self.consecutive_good = 0

    def record(self, success):
        self.results.append(success)
        if len(self.results) > self.window_size: self.results.pop(0)

    def failure_rate(self):
        if not self.results: return 0.0
        return sum(1 for x in self.results[-200:] if not x) / min(200, len(self.results))

    def maybe_adjust(self):
        if time.time() - self.last_adjust < 60: return self.current
        if len(self.results) < 100: return self.current
        fr = self.failure_rate()
        old = self.current
        if fr > 0.10:  # > 10% failure → cut hard
            self.current = max(self.min, int(self.current * 0.6))
            self.consecutive_good = 0
        elif fr < 0.02 and self.current < self.max:  # < 2% failure → boost
            self.consecutive_good += 1
            boost = 1.5 + 0.1 * min(self.consecutive_good, 5)
            self.current = min(self.max, int(self.current * boost))
        self.last_adjust = time.time()
        return self.current
```

### Rung 6 — Session rotation by request count

For sites that fingerprint sessions:

```python
session_request_count += 1
if session_request_count >= random.randint(15, 25):
    await session.close()
    session = aiohttp.ClientSession()  # fresh cookies + connection
    session_request_count = 0
```

### Rung 7 — Selenium with stealth

When the page is empty without JS, or has browser-fingerprint detection:

```python
from seleniumbase import Driver

STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['ro-RO','ro','en-US','en']});
window.chrome = {runtime: {}};
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter(p);
};
delete navigator.__proto__.webdriver;
"""

def get_stealth_driver(headless=True):
    driver = Driver(uc=True, headless=headless, agent=random.choice(USER_AGENTS), locale_code="ro_RO")
    driver.set_page_load_timeout(60)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": STEALTH_JS})
    return driver
```

### Rung 8 — Hybrid (Selenium for cookies, aiohttp for speed)

The pattern that beats most defenses without paying full Selenium cost:

```python
# Step 1: One-time Selenium visit to acquire valid cookies
driver = get_stealth_driver()
driver.get(base_url)
time.sleep(random.uniform(3, 5))  # let Cloudflare clear, JS run
cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
driver.quit()

# Step 2: Reuse cookies in fast aiohttp scraping
session = aiohttp.ClientSession(cookies=cookies, headers=headers())
# scrape thousands of pages at HTTP speed
```

### Rung 9 — Cloudflare-specific

If you see 520/522/524 status codes — that's Cloudflare. Approaches:
- Retry these at HTTP level (might pass the next time)
- Rotate IPs aggressively
- Detect challenge page in HTML (`"Just a moment..."`) and skip — better to lose 5–10% than burn budget retrying

```python
RETRY_STATUS = {429, 500, 502, 503, 504, 520, 522, 524}
async with session.get(url) as r:
    if r.status in RETRY_STATUS:
        await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
        continue
    html = await r.text()
    if "just a moment" in html.lower():
        return None  # skip Cloudflare challenge
```

---

## Phase 7 — Concurrency architectures

Three patterns. Pick by JS-rendering requirement:

### 7.1 Pure async (HTTP-only sites)

```python
async def run(self):
    sem = asyncio.Semaphore(self.concurrency)
    results_queue = asyncio.Queue()

    async def worker(url):
        async with sem:
            data = await self.fetch_and_parse(session, url)
            if data: await results_queue.put(data)

    # Producer-consumer split: workers parse, writer batches DB inserts
    db_writer = asyncio.create_task(self.db_writer_loop(results_queue))
    workers = [asyncio.create_task(worker(u)) for u in urls]
    await asyncio.gather(*workers)
    await results_queue.put(None)  # sentinel signals shutdown
    await db_writer
```

**Why producer-consumer**: PostgreSQL commits don't stall network I/O. Each item goes on a queue; a single writer batches and flushes.

```python
async def db_writer_loop(self, queue):
    batch = []
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=5.0)
            if item is None:  # sentinel
                if batch: await asyncio.to_thread(self.save_batch, batch)
                return
            batch.append(item)
            if len(batch) >= DB_BATCH_SIZE:
                await asyncio.to_thread(self.save_batch, batch)
                batch = []
        except asyncio.TimeoutError:
            # Periodic flush even if not full
            if batch:
                await asyncio.to_thread(self.save_batch, batch)
                batch = []
```

**Always wrap blocking DB writes in `asyncio.to_thread`** so PostgreSQL doesn't stall the event loop.

### 7.2 Selenium-required (JS-rendered)

Each thread owns its own driver. Use ThreadPoolExecutor:

```python
with ThreadPoolExecutor(max_workers=3) as pool:
    futures = [pool.submit(scrape_one_url, url) for url in urls]
    for f in as_completed(futures):
        result = f.result()
```

Lower concurrency (1–10 typical). Each driver is ~100MB RAM.

### 7.3 Subprocess-per-scraper (multi-scraper orchestration)

When you have N scrapers and need *kill-ability*:

```python
process = subprocess.Popen(
    [sys.executable, "-m", f"scraper.{name}_scraper"],
    stdout=log_file, stderr=log_file,
)
# Monitor with poll(), kill on timeout or stuck-detection
if (datetime.now() - start_time) > timedelta(hours=23):
    process.kill()
```

This is the only way to handle "one scraper hung" without taking down the whole system.

### Connection pool tuning

```python
connector = aiohttp.TCPConnector(
    limit=concurrency * 2,         # total connections
    limit_per_host=concurrency,    # per-host cap
    use_dns_cache=True,
    ttl_dns_cache=300,
    keepalive_timeout=30,
    enable_cleanup_closed=True,
    force_close=True,              # avoids SSL abort errors on close
    ssl=False,                     # if proxies don't support SSL passthrough
)
session = aiohttp.ClientSession(connector=connector, timeout=ClientTimeout(total=25))
```

---

## Phase 8 — Robustness patterns

### 8.1 Layered fallbacks for every field

```python
def extract_name(soup, html):
    if (j := extract_json_ld(soup)) and j.get("name"): return collapse_ws(j["name"])
    if (m := re.search(r'var\s+meta\s*=\s*(\{.*?\});', html)):
        try: return json.loads(m.group(1))["product"]["title"]
        except (json.JSONDecodeError, KeyError): pass
    if (h := soup.find("h1")): return collapse_ws(h.get_text())
    if (og := soup.find("meta", property="og:title")): return og.get("content")
    return None
```

### 8.2 Failure taxonomy (not just None)

```python
parse_stats = {
    "ok": 0,
    "fail_fetch": 0,             # network error
    "fail_http_status": 0,       # non-200
    "fail_timeout": 0,
    "fail_parse": 0,             # exception in parser
    "fail_missing_required": 0,  # parser ran, but required data missing
    "fail_cloudflare": 0,        # CF challenge page
    "fail_other": 0,
}

# Parser returns dict, not bare value:
return {"ok": False, "reason": "fail_missing_required", "url": url,
        "have_name": bool(name), "have_pid": bool(pid), "have_price": price is not None}
```

In production, grep logs by reason to see exactly what spiked.

### 8.3 Checkpoint files for resume

```python
URL_CHECKPOINT = f"scraper_data/{site}_urls_checkpoint.txt"
ID_CHECKPOINT = f"scraper_data/{site}_ids_checkpoint.txt"

# Startup
processed = set(open(URL_CHECKPOINT).read().split("\n")) if os.path.exists(URL_CHECKPOINT) else set()

# Periodic flush (every 100 items)
if processed_in_session % 100 == 0:
    with open(URL_CHECKPOINT, "w") as f: f.write("\n".join(sorted(processed)))
```

### 8.4 Early-stop heuristic

Stop crawling once new IDs dry up:

```python
EARLY_STOP_THRESHOLD = 150
no_new_streak = 0
for url in urls:
    new_ids = scrape_one(url)
    if new_ids: no_new_streak = 0
    else: no_new_streak += 1
    if no_new_streak >= EARLY_STOP_THRESHOLD:
        print("Early stop — no new IDs for 150 consecutive pages")
        break
```

### 8.5 Stuck detection (the most important operational pattern)

**Process liveness ≠ doing useful work.** Query the DB to verify recent writes:

```python
def is_parser_stuck(parser_id, start_time, threshold_minutes=25):
    if (datetime.now() - start_time).total_seconds() / 60 < threshold_minutes:
        return False  # not running long enough to evaluate
    cutoff = datetime.utcnow() - timedelta(minutes=threshold_minutes)
    recent_count = session.query(func.count(PriceHistory.id)) \
        .join(Product, Product.id == PriceHistory.product_id) \
        .filter(Product.parser_id == parser_id, PriceHistory.timestamp >= cutoff) \
        .scalar() or 0
    return recent_count == 0
```

When this returns True, kill the subprocess. This catches infinite loops, hanging network calls, and silent parse-failure cascades that liveness checks miss.

### 8.6 Structured stats marker

Print a parseable marker your orchestrator can grep:

```python
print(f"###PARSER_STATS###:{json.dumps({'processed': N, 'successful': S, 'failed': F, 'speed': speed})}")
```

Parser stays simple; orchestrator/scheduler reads the marker from the log to record run statistics.

### 8.7 Append-only schema for price/stock history

```sql
products (
    id SERIAL PRIMARY KEY,
    original_id VARCHAR UNIQUE,  -- "{site_sku}_{parser_id}" composite key
    name, url, image, slug, vendor,
    parser_id INT REFERENCES parsers(id)
);
price_history (
    id SERIAL PRIMARY KEY,
    product_id INT REFERENCES products(id),
    value FLOAT,
    timestamp TIMESTAMP
);  -- INSERT ONLY, NEVER UPDATE
stock_history (
    id SERIAL PRIMARY KEY,
    product_id INT REFERENCES products(id),
    quantity INT,
    timestamp TIMESTAMP
);  -- INSERT ONLY, NEVER UPDATE
parser_run_logs (
    parser_id, started_at, finished_at, status, products_found,
    price_entries_saved, stock_entries_saved, error_message
);
```

`original_id = f"{site_sku}_{parser_id}"` lets two stores share SKU "ABC123" without collision. Append-only history lets you answer "what was the price last Tuesday."

### 8.8 Composite ID at the boundary

```python
def package(self, raw_pid, ...):
    if not str(raw_pid).endswith(f"_{self.parser_id}"):
        original_id = f"{raw_pid}_{self.parser_id}"
    else:
        original_id = str(raw_pid)
    return {"original_id": original_id, ...}
```

Idempotent — safe to call twice.

### 8.9 Variant handling strategies

Pick one explicitly:

```python
# Strategy A: Flatten (each variant is a separate product row)
for variant in variants:
    yield package(variant_id=variant["id"], parent_id=str(product["id"]), ...)

# Strategy B: First-only (use selected_or_first_available_variant)
v = product.get("selected_or_first_available_variant") or product["variants"][0]
yield package(...)

# Strategy C: Aggregate (sum stock, average price across variants)
total_stock = sum(v["inventory_quantity"] for v in variants)
avg_price = sum(v["price"] for v in variants) / len(variants) / 100  # cents
yield package(stock=total_stock, price=avg_price, ...)
```

---

## Phase 9 — Starter template

Drop in, swap config, adapt parser. ~150 lines for a complete scraper.

```python
import asyncio, aiohttp, json, re, random, os, time, sys, itertools
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from typing import Optional
from db.models import Product, PriceHistory, StockHistory
from db.session import SessionLocal

# ====== Config ======
SITE = "yoursite"
PARSER_ID = 99
BASE_URL = "https://example.com"
MAX_CONCURRENT = 20
DB_BATCH_SIZE = 200
RETRY_STATUS = {429, 500, 502, 503, 504, 520, 522, 524}
USER_AGENTS = [...]  # 4-6 real ones, current Chrome/Firefox
PROXY_FILE = "Webshare 100 proxies.txt"

PRODUCT_URL_RE = re.compile(r"/products/[^/]+$")  # adapt per site

# ====== Parsing ======
def parse_product(html: str, url: str, sitemap_image: Optional[str]) -> dict:
    """Returns {'ok': True, 'item': {...}} or {'ok': False, 'reason': '...'}"""
    soup = BeautifulSoup(html, "lxml")

    # Tier 1: JSON-LD
    name, price, image, vendor, original_id = None, None, sitemap_image, SITE, None
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text())
            candidates = data if isinstance(data, list) else (data.get("@graph", [data]) if isinstance(data, dict) else [])
            for obj in candidates:
                if isinstance(obj, dict) and obj.get("@type") == "Product":
                    name = name or obj.get("name")
                    original_id = original_id or obj.get("sku") or obj.get("productID")
                    offers = obj.get("offers")
                    if isinstance(offers, list) and offers: offers = offers[0]
                    if isinstance(offers, dict) and price is None:
                        price = to_float(offers.get("price"))
                    if not image and (img := obj.get("image")):
                        image = img[0] if isinstance(img, list) else img
                    if (b := obj.get("brand")):
                        vendor = b.get("name") if isinstance(b, dict) else b
                    break
        except (json.JSONDecodeError, ValueError): continue

    # Tier 2: meta / DOM fallbacks
    if not name and (og := soup.find("meta", property="og:title")): name = og.get("content")
    if not name and (h1 := soup.find("h1")): name = h1.get_text(strip=True)
    if not image and (og := soup.find("meta", property="og:image")): image = og.get("content")
    if not original_id and (m := PRODUCT_URL_RE.search(url)): original_id = m.group(1) if m.groups() else None

    # Stock (platform-specific — pick technique from Phase 4)
    stock = extract_stock(soup, html, url)

    canonical = (soup.find("link", rel="canonical") or {}).get("href", url) if soup.find("link", rel="canonical") else url

    if not (name and original_id and price is not None):
        return {"ok": False, "reason": "fail_missing_required",
                "have": {"name": bool(name), "id": bool(original_id), "price": price is not None}}

    return {"ok": True, "item": {
        "original_id": f"{original_id}_{PARSER_ID}",
        "name": name, "url": canonical, "image": image, "vendor": vendor or SITE,
        "slug": slugify(name), "stock_policy": "continue",
        "parser_id": PARSER_ID, "parent_id": None, "shortlisted": False,
        "price": price, "stock_quantity": stock or 0,
    }}

# ====== Helpers ======
def slugify(name):
    s = (name or "").lower().strip()
    s = re.sub(r"\s+", "-", s); s = re.sub(r"[^\w\-]", "", s)
    return re.sub(r"-+", "-", s).strip("-")

def to_float(v):
    if v is None: return None
    if isinstance(v, (int, float)): return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."): s = s.replace(".", "").replace(",", ".")
        else: s = s.replace(",", "")
    elif "," in s: s = s.replace(",", ".")
    try: return float(s)
    except ValueError: return None

# ====== Scraper ======
class Scraper:
    def __init__(self):
        self.results = asyncio.Queue()
        self.start = datetime.now(timezone.utc)
        self.proxies = self._load_proxies()
        self.cycler = itertools.cycle(self.proxies) if self.proxies else None
        self.stats = {"ok": 0, "fail_fetch": 0, "fail_parse": 0, "fail_missing_required": 0}
        self.processed_count = 0
        self.total = 0

    def _load_proxies(self):
        if not os.path.exists(PROXY_FILE): return []
        return [l.strip() for l in open(PROXY_FILE) if l.strip()]

    def _next_proxy(self):
        if not self.cycler: return None, None
        p = next(self.cycler).split(":")
        return f"http://{p[0]}:{p[1]}", aiohttp.BasicAuth(p[2], p[3]) if len(p) == 4 else None

    async def fetch(self, session, url):
        for attempt in range(3):
            proxy, auth = self._next_proxy()
            try:
                async with session.get(url, proxy=proxy, proxy_auth=auth,
                                       headers={"User-Agent": random.choice(USER_AGENTS)},
                                       timeout=25) as r:
                    if r.status in RETRY_STATUS:
                        await asyncio.sleep(2 ** attempt + random.uniform(0, 1))
                        continue
                    r.raise_for_status()
                    return await r.text()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 2: return None
                await asyncio.sleep(2 ** attempt)

    async def process(self, session, url, sitemap_image):
        await asyncio.sleep(random.uniform(0.05, 0.3))
        for _ in range(5):
            html = await self.fetch(session, url)
            if not html:
                self.stats["fail_fetch"] += 1
                continue
            result = parse_product(html, url, sitemap_image)
            if result.get("ok"):
                self.stats["ok"] += 1
                await self.results.put(result["item"])
                break
            else:
                self.stats[result["reason"]] = self.stats.get(result["reason"], 0) + 1
                await asyncio.sleep(random.uniform(1, 3))
        self.processed_count += 1
        if self.processed_count % 100 == 0:
            print(f"\r{self.processed_count}/{self.total} | OK: {self.stats['ok']}", end="", flush=True)

    async def db_writer(self):
        batch = []
        while True:
            try:
                item = await asyncio.wait_for(self.results.get(), timeout=5.0)
                if item is None:
                    if batch: await asyncio.to_thread(self.save_batch, batch)
                    return
                batch.append(item)
                if len(batch) >= DB_BATCH_SIZE:
                    await asyncio.to_thread(self.save_batch, batch)
                    batch = []
            except asyncio.TimeoutError:
                if batch:
                    await asyncio.to_thread(self.save_batch, batch)
                    batch = []

    def save_batch(self, batch):
        db = SessionLocal()
        try:
            for p in batch:
                existing = db.query(Product).filter_by(original_id=p["original_id"]).first()
                if not existing:
                    obj = Product(**{k: v for k, v in p.items() if k not in ["price", "stock_quantity"]})
                    db.add(obj); db.flush()
                    pid = obj.id
                else:
                    for k in ["name", "url", "image", "vendor", "slug"]: setattr(existing, k, p[k])
                    pid = existing.id
                db.add_all([
                    PriceHistory(product_id=pid, value=p["price"], timestamp=self.start),
                    StockHistory(product_id=pid, quantity=p["stock_quantity"], timestamp=self.start),
                ])
            db.commit()
        except Exception as e:
            db.rollback(); print(f"[DB ERROR] {e}")
        finally: db.close()

    async def run(self):
        # 1. Discover URLs (implement walk_sitemaps for your site)
        product_map = await self.discover_urls()
        self.total = len(product_map)
        print(f"To process: {self.total} URLs")

        # 2. Process with bounded concurrency + producer-consumer
        sem = asyncio.Semaphore(MAX_CONCURRENT)
        connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT*2, limit_per_host=MAX_CONCURRENT, ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            writer = asyncio.create_task(self.db_writer())
            async def bound(u, img):
                async with sem: await self.process(session, u, img)
            await asyncio.gather(*[bound(u, img) for u, img in product_map.items()])
            await self.results.put(None)
            await writer

        # 3. Emit stats for orchestrator
        elapsed = (datetime.now(timezone.utc) - self.start).total_seconds()
        speed = self.stats["ok"] / elapsed * 60 if elapsed else 0
        print(f"\n###PARSER_STATS###:{json.dumps({**self.stats, 'total': self.total, 'speed': speed})}")

if __name__ == "__main__":
    asyncio.run(Scraper().run())
```

---

## Phase 10 — Failure-mode checklist

When (not if) a scraper breaks in production, check these in order:

1. **Sitemap structure changed?** New sub-sitemaps not in index, URL pattern shifted, gzip wrapping added/removed.
2. **URL filter regex still matches?** Trivial to silently start scraping 0 products.
3. **JSON-LD shape drifted?** `offers` dict↔list, `image` string↔list, `availability` URL changed.
4. **CSS class renamed?** Especially after site redesigns.
5. **Embedded JS variable renamed?** `var meta` → `var __initialData`.
6. **Rate limiting kicked in?** Plot failure rate over time — gradual increase = throttled.
7. **Cloudflare added?** New 520/522 responses, check `cf-ray` header.
8. **Stock endpoint moved?** `/rest/all/V1/...` paths shift between Magento versions.
9. **Proxy provider rotated IPs?** Healthy proxies suddenly fail = provider issue.
10. **Database schema drifted?** New NOT NULL column? Saves silently fail.

**Monitor stock fill-rate as your canary, not request success rate.** A scraper can return 200 on every page and produce 0 useful data.

---

## The 80/20 workflow

When sitting down to write a new scraper:

1. **5 min**: DevTools recon (Phase 1). Identify platform, find JSON-LD, scan AJAX, check sitemap.
2. **5 min**: Pick the closest template. Shopify-clone → use cudetoate/tenq pattern. WooCommerce → hambebe/armedacarpet. Custom SPA → vivre. Has API → bonami.
3. **15 min**: Copy template, swap config, swap selectors. 80% done.
4. **30 min**: Test on 100 URLs, examine failures by reason. Fix the edge cases.
5. **15 min**: Tune concurrency, verify proxy health, run 1000-URL sample.
6. **Production**: Wire into orchestrator. Watch first 24h for stuck-detection / failure rates.

**The framework's job: abstract the boring stuff (HTTP, proxies, retries, batching, history schema) so a new scraper is just `parse_product(html, url) -> dict` and `get_product_urls() -> list[str]`.**

---

## Two principles to internalize

1. **Always look for the API the site is calling itself.** GraphQL > REST > JSON-LD > embedded JS > DOM. Going up this stack is what separates a 1-week scraper project from a 1-day one.

2. **Stock is where everything breaks.** Build infrastructure flexible enough that adding a new stock-extraction strategy doesn't mean rewriting the scraper. The 10 techniques in Phase 4 cover ~95% of e-commerce sites.
