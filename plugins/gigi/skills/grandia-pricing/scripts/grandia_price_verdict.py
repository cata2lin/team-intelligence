# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""
Grandia price-competitiveness VERDICT — "suntem ok pe preț sau nu?"

Read-only. Joins our live Shopify price/cost (Grandia Product/Variant) to the
FRESH competitor prices just re-scraped (prc_competitor_prices, source='rescrape'),
and per product computes: cheapest & median live competitor, our delta vs cheapest,
gross margin (ex-VAT RO 21%), and a verdict:
  SCUMP     — our price > cheapest competitor * (1+tol)   (we're pricier → losing)
  OK        — within ±tol of cheapest                     (competitive)
  HEADROOM  — our price < cheapest * (1-tol)              (we're cheaper → margin room to raise)
Plus a price_floor (min price that still keeps --min-margin) so any decrease stays profitable.

Usage:
  export DATABASE_URL_GRANDIA=...
  uv run grandia_price_verdict.py --fresh-days 3 --tol 0.05 --min-margin 20
  uv run grandia_price_verdict.py --only SCUMP --limit 40
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
import os, sys
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import psycopg2

def arg(name, default, cast=str):
    if name in sys.argv:
        return cast(sys.argv[sys.argv.index(name) + 1])
    return default
FRESH_DAYS = arg("--fresh-days", 3, int)     # competitor price must be newer than this
TOL = arg("--tol", 0.05, float)              # ±band around cheapest = "OK"
MIN_MARGIN = arg("--min-margin", 20.0, float)  # gross-margin floor for price_floor (%)
VAT = 1.21                                    # RO
ONLY = arg("--only", None, str)              # SCUMP / OK / HEADROOM
LIMIT = arg("--limit", 60, int)

_OK = {"host","port","dbname","user","password","sslmode","connect_timeout","application_name","channel_binding"}
def clean(d):
    p = urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme, p.netloc, p.path,
        urlencode([(x, y) for x, y in parse_qsl(p.query, keep_blank_values=True) if x.lower() in _OK]), p.fragment))

G = psycopg2.connect(clean(_secret("DATABASE_URL_GRANDIA")), connect_timeout=20)
G.set_session(readonly=True)

# Latest fresh competitor price per mapping + our live price; filter garbage in Python.
BAND_LO = arg("--band-lo", 0.35, float)   # keep competitor prices within [BAND_LO, BAND_HI] × our price
BAND_HI = arg("--band-hi", 2.8, float)    # outside = wrong-product match (accessory / bundle) → drop
SQL = f"""
WITH fresh AS (
  SELECT cp.id AS map_id, cp.product_id, pr.price, pr.recorded_at,
         row_number() OVER (PARTITION BY cp.id ORDER BY pr.recorded_at DESC) AS rn
  FROM prc_competitor_products cp
  JOIN prc_competitor_prices pr ON pr.competitor_product_id = cp.id
  WHERE pr.recorded_at > now() - interval '{FRESH_DAYS} days' AND pr.price > 0
)
SELECT p.id, p.title, v.price::float AS our_price, v."costPerItem"::float AS cost, f.price::float
FROM fresh f
JOIN "Product" p ON p.id = f.product_id
JOIN "Variant" v ON v."productId" = p.id AND v.price > 0
WHERE f.rn = 1 AND p.status = 'ACTIVE'
"""
from statistics import median as _median
with G.cursor() as c:
    c.execute(SQL)
    raw = c.fetchall()

# group competitor prices per product, apply plausibility band vs our price
byprod = {}
for pid, title, our, cost, cprice in raw:
    d = byprod.setdefault(pid, dict(title=title, our=our, cost=cost, comps=[]))
    if BAND_LO * our <= cprice <= BAND_HI * our:   # sanity gate → drop wrong-product matches
        d["comps"].append(cprice)

out = []
for pid, d in byprod.items():
    comps = d["comps"]
    if not comps:
        continue
    our, cost, title = d["our"], d["cost"], d["title"]
    cheapest = min(comps); median = _median(comps); n = len(comps)
    delta = our / cheapest - 1.0
    if delta > TOL:      verdict = "SCUMP"
    elif delta < -TOL:   verdict = "HEADROOM"
    else:                verdict = "OK"
    margin = None
    floor = None
    if cost and cost > 0:
        net = our / VAT
        margin = (net - cost) / net * 100 if net > 0 else None
        floor = round(cost / (1 - MIN_MARGIN / 100) * VAT, 2)   # min price keeping MIN_MARGIN, incl VAT
    out.append(dict(pid=pid, title=title, our=our, cost=cost, n=n, cheapest=cheapest,
                    median=median, delta=delta, verdict=verdict, margin=margin, floor=floor))

# summary
from collections import Counter
cnt = Counter(o["verdict"] for o in out)
print(f"VERDICT PREȚ Grandia — {len(out)} produse cu competitor LIVE (<{FRESH_DAYS}z), tol ±{TOL:.0%}, marjă min {MIN_MARGIN:.0f}%")
print("=" * 100)
print(f"  🔴 SCUMP (mai scump ca piața): {cnt.get('SCUMP',0)}   "
      f"🟢 OK (competitiv): {cnt.get('OK',0)}   "
      f"🔵 HEADROOM (loc de creștere): {cnt.get('HEADROOM',0)}")
# money-at-risk on SCUMP: sum of (our - cheapest) is meaningless; show avg overprice
scump = [o for o in out if o["verdict"] == "SCUMP"]
if scump:
    print(f"  media supra-preț pe SCUMP: +{sum(o['delta'] for o in scump)/len(scump):.0%}  (median +{sorted(o['delta'] for o in scump)[len(scump)//2]:.0%})")
print("=" * 100)

show = [o for o in out if (not ONLY or o["verdict"] == ONLY)][:LIMIT]
icon = {"SCUMP": "🔴", "OK": "🟢", "HEADROOM": "🔵"}
for o in show:
    mg = f"{o['margin']:.0f}%" if o["margin"] is not None else " —"
    fl = f" floor≈{o['floor']:.0f}" if o["floor"] else ""
    print(f"{icon[o['verdict']]} {o['verdict']:8} noi {o['our']:7.0f}  vs cel mai ieftin {o['cheapest']:7.0f} ({o['n']} comp, Δ{o['delta']:+.0%})  marjă {mg:>4}{fl}  {o['title'][:44]}")
