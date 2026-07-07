# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9"]
# ///
"""
Grandia REPRICING plan — demand × margin × competitiveness, CPA-safe.

Two asymmetric moves (user intent 2026-07-07):
  RAISE  — good sales + thin margin  → capture margin; cap at market price so
           conversion/CPA don't break. Raising ↑contribution → CPA MORE sustainable.
  LOWER  — weak sales + fat margin (headroom) → stimulate demand; but FLOOR the cut
           so contribution/order still covers target CPA + min profit ("menține CPA").

Contribution/order (ex-VAT RO 1.21):  price/1.21 - COGS - transport
CPA-safe floor (min price to still afford CPA):  1.21 * (COGS + transport + CPA + min_profit)

Read-only (no writes). Source: prc_product_status_daily (sales/COGS/margin) + fresh
competitor prices (prc_competitor_prices <fresh-days, plausibility-banded).

Usage:
  export DATABASE_URL_GRANDIA=...
  uv run grandia_reprice.py --target-cpa 70 --min-profit 15 --transport 20 \
     --good-sales 8 --thin-margin 45 --slow-sales 2 --fat-margin 60
  uv run grandia_reprice.py --list RAISE      # or LOWER
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
from statistics import median as _median
import psycopg2

def arg(n, d, cast=str):
    return cast(sys.argv[sys.argv.index(n)+1]) if n in sys.argv else d
VAT        = 1.21
TARGET_CPA = arg("--target-cpa", 70.0, float)   # Grandia breakeven ~69 (agency); tune
MIN_PROFIT = arg("--min-profit", 15.0, float)   # net RON/order we insist on keeping
TRANSPORT  = arg("--transport", 20.0, float)    # flat est/order (bulky items higher → floor optimistic, flagged)
GOOD_SALES = arg("--good-sales", 6, int)        # orders_30d >= => proven seller
THIN_MARG  = arg("--thin-margin", 20.0, float)  # NET margin % (după CPA) below => "not ok"
SLOW_SALES = arg("--slow-sales", 2, int)        # orders_30d <= => weak demand
FAT_MARG   = arg("--fat-margin", 40.0, float)   # NET margin % (după CPA) >= => room to cut
MIN_STOCK  = arg("--min-stock", 1, int)         # only reprice products with stock >= this
MAX_STEP   = arg("--max-step", 0.20, float)     # cap single price move at ±20%
FRESH_DAYS = arg("--fresh-days", 3, int)
BAND_LO, BAND_HI = 0.35, 2.8
ONLY = arg("--list", None, str)                 # RAISE / LOWER
LIMIT = arg("--limit", 40, int)

_OK={"host","port","dbname","user","password","sslmode","connect_timeout","application_name","channel_binding"}
def clean(d):
    p=urlsplit(d)
    return d if not p.query else urlunsplit((p.scheme,p.netloc,p.path,urlencode([(x,y) for x,y in parse_qsl(p.query,keep_blank_values=True) if x.lower() in _OK]),p.fragment))

G=psycopg2.connect(clean(_secret("DATABASE_URL_GRANDIA")), connect_timeout=20); G.set_session(readonly=True)

# fresh cheapest competitor per product (plausibility band applied later vs our price)
with G.cursor() as c:
    c.execute(f"""
      WITH latest AS (
        SELECT DISTINCT ON (cp.id) cp.product_id, pr.price
        FROM prc_competitor_products cp
        JOIN prc_competitor_prices pr ON pr.competitor_product_id=cp.id
        WHERE pr.recorded_at > now()-interval '{FRESH_DAYS} days' AND pr.price>0
        ORDER BY cp.id, pr.recorded_at DESC)
      SELECT product_id, price::float FROM latest""")
    comp = {}
    for pid, price in c.fetchall():
        comp.setdefault(pid, []).append(price)

    c.execute("""
      SELECT d.product_id, p.title, d.current_price::float, d.cost_per_item::float,
             d.orders_30d, d.revenue_30d::float, d.gross_margin_30d::float,
             (SELECT string_agg(DISTINCT v.sku, ', ') FROM "Variant" v WHERE v."productId"=p.id AND v.sku<>'') AS sku,
             d.stock_qty, d.days_of_stock::float
      FROM prc_product_status_daily d
      JOIN "Product" p ON p.id=d.product_id
      WHERE d.date=(SELECT max(date) FROM prc_product_status_daily) AND p.status='ACTIVE'
            AND d.current_price>0 AND d.cost_per_item>0""")
    rows = c.fetchall()

def contrib(price, cost):          # ex-VAT contribution/order before ads
    return price/VAT - cost - TRANSPORT
def cpa_floor(cost):               # min price so contribution still covers TARGET CPA + min profit
    return VAT*(cost + TRANSPORT + TARGET_CPA + MIN_PROFIT)
def margin_pct(price, cost):       # gross margin (no ads)
    net=price/VAT; return (net-cost)/net*100 if net>0 else 0
def net_margin(price, cost, cpa):  # NET margin AFTER the product's REAL CPA — "marja cu CPA"
    net=price/VAT; return (net-cost-TRANSPORT-cpa)/net*100 if net>0 else 0

# ---- real per-product CPA from metrics cache.product_ad_spend (30d) ----
GRANDIA_BRAND = "cmo5ulyl80003h1w2xlzfzhvh"
spend_by_sku = {}
_mdsn = _secret_opt("DATABASE_URL_METRICS")
if _mdsn:
    M = psycopg2.connect(clean(_mdsn), connect_timeout=20); M.set_session(readonly=True)
    with M.cursor() as mc:
        mc.execute("""SELECT upper(sku), sum(spend_ron) FROM cache.product_ad_spend
                      WHERE brand_id=%s AND date > current_date-30 AND sku IS NOT NULL
                      GROUP BY 1""", (GRANDIA_BRAND,))
        spend_by_sku = {k: float(v) for k, v in mc.fetchall() if k and v}

raise_l, lower_l = [], []
skipped_nostock = 0
for pid, title, price, cost, orders, rev, gm, sku, stock, dos in rows:
    orders = orders or 0
    stock = stock or 0
    if stock < MIN_STOCK:               # only reprice products in stock
        skipped_nostock += 1
        continue
    comps = [x for x in comp.get(pid, []) if BAND_LO*price <= x <= BAND_HI*price]
    cheapest = min(comps) if comps else None
    # real CPA = product's 30d ad spend / its orders; no spend => 0 (organic, no ad cost)
    skus = [s.strip().upper() for s in (sku or "").split(",") if s.strip()]
    p_spend = sum(spend_by_sku.get(s, 0.0) for s in skus)
    real_cpa = (p_spend/orders) if orders > 0 else 0.0
    m = net_margin(price, cost, real_cpa)      # marja CU CPA real = criteriul
    base = dict(pid=pid, title=title, sku=sku or "", price=price, cost=cost, orders=orders, rev=rev or 0,
                stock=stock, dos=dos, margin=m, grossm=margin_pct(price,cost), cheapest=cheapest, cpa=real_cpa,
                spend=p_spend, contrib=contrib(price, cost), cpa_head=contrib(price, cost)-real_cpa)

    # RAISE: good sales + thin margin. New price = toward market but capped; never below current.
    # No market reference → conservative step (+8%) + flag, to protect conversion/CPA.
    if orders >= GOOD_SALES and m < THIN_MARG:
        if cheapest:
            new = min(price*(1+MAX_STEP), max(price, cheapest))  # raise toward market, capped
            flag = ""
        else:
            new = price*1.08                                     # blind raise → conservative
            flag = "⚠verifică piața"
        new = round(max(new, price), 0)
        if new > price*1.005:
            base.update(new=new, mnow=net_margin(price,cost,real_cpa), newmargin=net_margin(new,cost,real_cpa),
                        newcontrib=contrib(new,cost), flag=flag, why="vânzări bune + marjă (cu CPA) subțire")
            raise_l.append(base)

    # LOWER: weak sales + fat margin. New price = toward market undercut, floored at CPA-safe & -MAX_STEP.
    elif orders <= SLOW_SALES and m >= FAT_MARG:
        floor = max(cpa_floor(cost), price*(1-MAX_STEP))
        target = (cheapest*0.98) if cheapest else price*(1-MAX_STEP)  # undercut market a touch, else step down
        new = max(floor, target)
        new = round(new, 0)
        if new < price*0.995:                                    # only if a real cut is possible above floor
            base.update(new=new, mnow=net_margin(price,cost,real_cpa), newmargin=net_margin(new,cost,real_cpa),
                        newcontrib=contrib(new,cost), flag="",
                        why="vânzări slabe + marjă (cu CPA) grasă (headroom)")
            lower_l.append(base)

raise_l.sort(key=lambda r: -r["orders"])         # biggest sellers first (most margin to capture)
lower_l.sort(key=lambda r: -r["price"])          # biggest-ticket slow movers first

def show(lst, tag, icon):
    print(f"\n{icon} {tag} — {len(lst)} produse")
    tot = 0
    for r in lst[:LIMIT]:
        ch = f"{r['cheapest']:.0f}" if r['cheapest'] else "—"
        dprice = r["new"]-r["price"]
        tot += dprice*r["orders"]
        struct = "🔴CPA-problemă" if ("RAISE" in tag and r['newmargin'] < 0) else ""
        print(f"  {r['price']:6.0f} → {r['new']:6.0f} ({dprice:+.0f})  {r['orders']:>2} cmd  stoc {int(r['stock']):>3}  "
              f"CPA {r['cpa']:>3.0f}  marjă/CPA {r['mnow']:+.0f}%→{r['newmargin']:+.0f}%  contrib {r['contrib']:.0f}→{r['newcontrib']:.0f}  "
              f"piață {ch:>4}  {r['title'][:30]} {r['flag']}{struct}")
    return tot

def export_rows():
    """All rows (both lists), for Google Sheet — header + rows."""
    hdr = ["Acțiune","Produs","SKU","Preț acum","Preț nou","Δ preț","Cmd/30z","Stoc","CPA real",
           "Marjă/CPA acum %","Marjă/CPA nouă %","Marjă brută %","Preț piață","Contrib acum","Contrib nou","Floor CPA","Câștig/lună","Flag","Motiv"]
    data = []
    for tag, lst in (("CREȘTE", raise_l), ("SCADE", lower_l)):
        for r in lst:
            struct = "CPA-problemă" if (tag=="CREȘTE" and r["newmargin"]<0) else r["flag"]
            data.append([tag, r["title"], r["sku"], round(r["price"]), round(r["new"]), round(r["new"]-r["price"]),
                         r["orders"], int(r["stock"]), round(r["cpa"]),
                         round(r["mnow"]), round(r["newmargin"]), round(r["grossm"]), round(r["cheapest"]) if r["cheapest"] else "",
                         round(r["contrib"]), round(r["newcontrib"]), round(cpa_floor(r["cost"])),
                         round((r["new"]-r["price"])*r["orders"]), struct, r["why"]])
    return hdr, data

print("="*104)
print(f"REPRICING Grandia — CPA țintă {TARGET_CPA:.0f} · profit min {MIN_PROFIT:.0f} · transport {TRANSPORT:.0f} · pas max {MAX_STEP:.0%}")
print(f"  Marjă = NETĂ CU CPA (contrib − CPA)/preț.  RAISE: orders≥{GOOD_SALES} & marjă/CPA<{THIN_MARG:.0f}%   |   LOWER: orders≤{SLOW_SALES} & marjă/CPA≥{FAT_MARG:.0f}% (floor = CPA-safe)")
print("="*104)
if ONLY in (None,"RAISE"): rt=show(raise_l,"RAISE (crește preț — captează marjă, contrib↑ = CPA mai sustenabil)","🔼")
if ONLY in (None,"LOWER"): lt=show(lower_l,"LOWER (scade preț — stimulează, floor păstrează CPA)","🔽")
print("\n" + "="*104)
print(f"TOTAL: {len(raise_l)} de crescut, {len(lower_l)} de scăzut. "
      f"Sărite (fără stoc <{MIN_STOCK}): {skipped_nostock}. "
      f"Toate LOWER-urile păstrează contribuție ≥ CPA {TARGET_CPA:.0f}+profit {MIN_PROFIT:.0f} (mențin CPA).")

if "--tsv" in sys.argv:
    import csv
    path = sys.argv[sys.argv.index("--tsv")+1]
    hdr, data = export_rows()
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t"); w.writerow(hdr); w.writerows(data)
    print(f"\n[TSV] {len(data)} rânduri → {path}")
