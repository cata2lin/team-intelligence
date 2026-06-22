# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
breakeven.py — REAL breakeven CPA / ROAS per brand, from real data (no guesses).

Pulls everything from the source of truth instead of flat assumptions:
  • AOV, delivery rate, refuse rate, transport/parcel  → AWB Arona (AWBprint DB)
  • COGS%  → computed per-SKU from each order's line_items × the live Shopify
             unitCost (so 2+1 free units and the real sales mix are captured —
             a flat catalog % understates it, e.g. Esteban catalog 20% → real 25.5%)

Model (contribution per PLACED-and-shipped order):
  rev_exVAT = AOV / (1 + VAT_country)         # revenue VAT is the DESTINATION country's
  cogs_lei  = COGS% × AOV                      # COGS paid in RO (cost basis)
  T_exVAT   = transport_median / 1.21          # courier billed in RO, "Cost cu TVA" → strip RO VAT
  D         = delivered / (delivered + refused)
  RO   :  contribution = D·(rev_exVAT − cogs_lei) − T_exVAT             # nu plătim retur pe RO
  intl :  contribution = D·(rev_exVAT − cogs_lei) − T_exVAT·(2 − D)     # retur = +1× livrare
  breakeven CPA  = contribution        breakeven ROAS = AOV / CPA

Read-only. Usage:
  uv run breakeven.py --store all
  uv run breakeven.py --store belasil --days 60
  uv run breakeven.py --store esteban --sample 6000
"""
import argparse, os, subprocess, sys
from pathlib import Path
from statistics import median

# ── standard VAT on REVENUE, by destination country (COGS + transport stay RO 21%) ──
VAT = {"RO": 0.21, "BG": 0.20, "CZ": 0.21, "PL": 0.23, "HU": 0.27, "HR": 0.25, "SK": 0.23}
VAT_RO = 0.21
# FX fallback (BNR ~iun 2026, RON per 1 unit) — overridden live from metrics.fx_rates if reachable
FX_FALLBACK = {"RON": 1.0, "EUR": 5.234, "CZK": 0.2165, "PLN": 1.2293, "HUF": 0.014883, "BGN": 2.676}

# ── store registry: AWB store-name filter (ILIKE) + Shopify prefix + country + currency ──
STORES = {
    # RO domestic (tot RON, fără retur)
    "esteban":   {"awb": "esteban.ro",      "shop": "EST",   "country": "RO", "cur": "RON"},
    "belasil":   {"awb": "belasil.ro",      "shop": "BELA",  "country": "RO", "cur": "RON"},
    "carpetto":  {"awb": "carpetto.ro",     "shop": "CARP",  "country": "RO", "cur": "RON"},
    "gento":     {"awb": "gento.ro",        "shop": "GEN",   "country": "RO", "cur": "RON"},
    "gt":        {"awb": "georgetalent.ro", "shop": "GT",    "country": "RO", "cur": "RON"},
    "grandia":   {"awb": "grandia.ro",      "shop": "GRAN",  "country": "RO", "cur": "RON"},
    "ofertele":  {"awb": "ofertelezilei.ro","shop": "OFER",  "country": "RO", "cur": "RON"},
    "magdeal":   {"awb": "magdeal.ro",      "shop": "MAG",   "country": "RO", "cur": "RON"},
    "reduceri":  {"awb": "reduceribune.ro", "shop": "RED",   "country": "RO", "cur": "RON"},
    "casaofertelor": {"awb": "casaofertelor.ro", "shop": "BON", "country": "RO", "cur": "RON"},
    "covoria":   {"awb": "covoria.ro",      "shop": "COV",   "country": "RO", "cur": "RON"},
    "apreciat":  {"awb": "apreciat.ro",     "shop": "APR",   "country": "RO", "cur": "RON"},
    "nocturnalux": {"awb": "nocturnalux.ro","shop": "LUX",   "country": "RO", "cur": "RON"},
    "rossi":     {"awb": "rossinails.ro",   "shop": "ROSSI", "country": "RO", "cur": "RON"},
    "nubra":     {"awb": "nubra",           "shop": "NUB",   "country": "RO", "cur": "RON"},  # token CSV mort → COGS n/a
    # internațional (FX→RON, TVA destinație, retur = +1× livrare)
    "bonhaus_cz": {"awb": "bonhaus.cz", "shop": "CZ",    "country": "CZ", "cur": "CZK"},
    "bonhaus_pl": {"awb": "bonhaus.pl", "shop": "PL",    "country": "PL", "cur": "PLN"},
    "bonhaus_bg": {"awb": "bonhaus.bg", "shop": "BONBG", "country": "BG", "cur": "EUR"},
}
DELIVERED = ("delivered", "customer_pickup")
REFUSED = ("back_to_sender", "returning_to_sender", "refused")


def _fx_map():
    """RON per 1 local unit. Live from metrics.fx_rates if reachable, else fallback."""
    fx = dict(FX_FALLBACK)
    url = os.getenv("DATABASE_URL_METRICS") or _kb_secret("DATABASE_URL_METRICS")
    if not url:
        return fx
    try:
        import pg8000.dbapi, urllib.parse as up
        u = up.urlparse(url)
        cn = pg8000.dbapi.connect(user=up.unquote(u.username or ""), password=up.unquote(u.password or ""),
                                  host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/"),
                                  ssl_context=True)
        c = cn.cursor()
        c.execute("""SELECT DISTINCT ON ("fromCurrency") "fromCurrency", rate FROM fx_rates
                     WHERE "toCurrency"='RON' ORDER BY "fromCurrency", "rateDate" DESC""")
        for code, rate in c.fetchall():
            fx[code] = float(rate)
        cn.close()
    except Exception:
        pass
    return fx


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
                                database=(u.path or "/").lstrip("/"))


def _shopify_cost_map(prefix):
    """SKU -> unitCost from the live Shopify catalog (via the shopify-stores helper)."""
    here = Path(__file__).resolve()
    for up_ in range(1, 6):
        cand = here.parents[up_] / "shopify-stores" / "scripts"
        if (cand / "shopify_gql.py").exists():
            sys.path.insert(0, str(cand)); break
    from shopify_gql import resolve_store, gql
    Q = ("query($c:String){ productVariants(first:250, after:$c){ pageInfo{hasNextPage endCursor} "
         "nodes{ sku inventoryItem{ unitCost{ amount } } } } }")
    m = {}
    try:                                          # dead/rotated token (e.g. NUB) → {} → COGS n/a, no crash
        shop, tok = resolve_store(prefix)
        cur = None
        for _ in range(80):
            r = gql(shop, tok, Q, {"c": cur})
            d = (r.get("data") or {}).get("productVariants") or {}
            for v in d.get("nodes", []):
                sku = (v.get("sku") or "").strip()
                uc = (v.get("inventoryItem") or {}).get("unitCost") or {}
                if sku and uc.get("amount"):
                    m[sku] = float(uc["amount"])
            pi = d.get("pageInfo") or {}
            if not pi.get("hasNextPage"):
                break
            cur = pi.get("endCursor")
    except SystemExit:
        return {}
    return m


def analyze(cur, key, days, sample, fx):
    cfg = STORES[key]; awb = f"%{cfg['awb']}%"
    W = "frisbo_created_at >= now() - interval '%d days'" % days

    # delivery / refuse (terminal statuses only) + AOV + transport
    cur.execute(f"""
      SELECT count(*) FILTER (WHERE aggregated_status = ANY(%s)) delivered,
             count(*) FILTER (WHERE aggregated_status = ANY(%s)) refused,
             round(avg(total_price) FILTER (WHERE total_price>0)::numeric,2) aov,
             round(percentile_cont(0.5) within group (order by transport_cost)
                   FILTER (WHERE transport_cost>0)::numeric,2) t_med
      FROM orders o JOIN stores s ON s.uid=o.store_uid
      WHERE s.name ILIKE %s AND {W}""", [list(DELIVERED), list(REFUSED), awb])
    delivered, refused, aov, t_med = cur.fetchone()
    delivered, refused = int(delivered or 0), int(refused or 0)
    aov, t_med = float(aov or 0), float(t_med or 0)
    D = delivered / (delivered + refused) if (delivered + refused) else 0

    # COGS% from line_items × Shopify cost
    cm = _shopify_cost_map(cfg["shop"])
    cur.execute(f"""SELECT line_items, total_price FROM orders o JOIN stores s ON s.uid=o.store_uid
                    WHERE s.name ILIKE %s AND line_items IS NOT NULL AND total_price>0 AND {W}
                    LIMIT %s""", [awb, sample])
    tot_cogs = tot_rev = 0.0; mu = miss = 0
    for li, price in cur.fetchall():
        for it in (li or []):
            sku = ((it.get("inventory_item") or {}).get("sku") or "").strip()
            q = float(it.get("quantity") or 0)
            if sku in cm:
                tot_cogs += cm[sku] * q; mu += q
            else:
                miss += q
        tot_rev += float(price)
    cogs_pct = (tot_cogs / tot_rev) if tot_rev else None     # ratio, currency-agnostic
    match = mu / (mu + miss) if (mu + miss) else 0
    if not cm or match < 0.5:                                # token mort (NUB) sau mapare slabă
        cogs_pct = None

    # economics — convert AOV (local currency) to RON; transport is already RON
    rate = fx.get(cfg["cur"], 1.0)
    aov_ron = aov * rate
    vat_rev = VAT.get(cfg["country"], VAT_RO)
    rev_ex = aov_ron / (1 + vat_rev)
    t_ex = t_med / (1 + VAT_RO)
    if cogs_pct is None:
        return dict(key=key, aov_ron=aov_ron, cur=cfg["cur"], cogs_pct=None, match=match,
                    D=D, t_ex=t_ex, contrib=None, be_cpa=None, be_roas=None)
    cogs_lei = cogs_pct * aov_ron
    if cfg["country"] == "RO":
        contrib = D * (rev_ex - cogs_lei) - t_ex                 # fără retur
    else:
        contrib = D * (rev_ex - cogs_lei) - t_ex * (2 - D)       # retur = +1× livrare
    be_roas = aov_ron / contrib if contrib and contrib > 0 else float("inf")
    return dict(key=key, aov_ron=aov_ron, cur=cfg["cur"], cogs_pct=cogs_pct, match=match,
                D=D, delivered=delivered, refused=refused, t_med=t_med, t_ex=t_ex,
                vat_rev=vat_rev, contrib=contrib, be_cpa=contrib, be_roas=be_roas)


def main():
    ap = argparse.ArgumentParser(description="Breakeven CPA/ROAS real per brand.")
    ap.add_argument("--store", default="all", help="esteban|belasil|carpetto|gento|all")
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--sample", type=int, default=4000, help="comenzi pt COGS (line_items)")
    a = ap.parse_args()
    keys = list(STORES) if a.store == "all" else [a.store.lower()]
    cn = awb_conn(); cur = cn.cursor()
    fx = _fx_map()
    print(f"\n=== Breakeven real ({a.days}z) — AOV în RON ===")
    print(f"{'Brand':13} {'cur':>4} {'AOV_RON':>8} {'COGS%':>6} {'match':>6} {'Livr%':>6} "
          f"{'Tr_exTVA':>9} {'Contrib':>8} {'BE_CPA':>7} {'BE_ROAS':>8}")
    for k in keys:
        if k not in STORES:
            print(f"  {k}: necunoscut (alege din {', '.join(STORES)})"); continue
        r = analyze(cur, k, a.days, a.sample, fx)
        if r["cogs_pct"] is None:
            print(f"{k:13} {r['cur']:>4} {r['aov_ron']:8.0f} {'  n/a':>6} {r['match']*100:5.0f}% "
                  f"{r['D']*100:5.0f}% {r['t_ex']:9.2f} {'COGS indisponibil (token Shopify?)':>0}")
            continue
        print(f"{k:13} {r['cur']:>4} {r['aov_ron']:8.0f} {r['cogs_pct']*100:5.1f}% {r['match']*100:5.0f}% "
              f"{r['D']*100:5.0f}% {r['t_ex']:9.2f} {r['contrib']:8.1f} "
              f"{r['be_cpa']:7.0f} {r['be_roas']:8.2f}")
    cn.close()
    print("\nModel: contrib = livrare%·(AOV/(1+TVA_țară) − COGS%·AOV) − transport_exTVA"
          "  (RO: fără retur; intl: −transport·(2−livrare%)).  COGS% = line_items×cost Shopify.")


if __name__ == "__main__":
    main()
