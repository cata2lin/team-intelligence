# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31", "google-api-python-client>=2.0", "google-auth>=2.0"]
# ///
"""
LIVE per-SKU/product ad spend from Meta + TikTok, mapped via the KB Nomenclator rules.
The fresh source that replaces the stale AWBprint sku_ad_spend_daily for cache.product_ad_spend.

Mapping per campaign/ad (prodmap.product_of, reading rules from KB):
  - HA-<digits> in campaign/ad name  -> that SKU (per-SKU, e.g. 'SET SURUBELNITE HA-0040')
  - else Nomenclator rule            -> product_group; resolved to a SKU if it's a single-product group (WMS)
  - else                             -> 'UNMAPPED:<account>' (kept, visible)

  uv run ad_spend_live.py [--days 14]            # DRY-RUN: print rows summary
  uv run ad_spend_live.py [--days 14] --apply    # upsert into cache.product_ad_spend (source='meta_tiktok_campaign_map')
Exposes live_rows(days) for build_cache.py to call.
"""
import os, sys, re, json, argparse, subprocess
from pathlib import Path
from collections import defaultdict

# skills dir resolved relative to this file (skills/metrics-cache/scripts/ad_spend_live.py) for portability (VPS)
SKILLS = Path(os.environ.get("ARONA_SKILLS_DIR") or Path(__file__).resolve().parents[2])
for d in ("meta-ads", "tiktok-ads"):
    sys.path.insert(0, str(SKILLS / d))
KB = Path.home() / ".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"
if not KB.exists():  # VPS / alt layout
    for cand in [SKILLS.parent / "core/scripts/kb.py", Path.home() / ".claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"]:
        if cand.exists(): KB = cand; break


def kb_secret(k):
    v = os.environ.get(k)            # VPS/cron: secrets provided via env (no kb.py/uv needed)
    if v:
        return v
    try:
        return subprocess.run(["uv", "run", str(KB), "secret-get", k], capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


def _clean(dsn):
    dsn = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", dsn)
    return re.sub(r"[?&]+(&|$)", r"\1", dsn).rstrip("?&")


def metrics_conn():
    import psycopg2
    return psycopg2.connect(_clean(os.environ.get("DATABASE_URL_METRICS") or kb_secret("DATABASE_URL_METRICS")))


def brand_name_to_id(cur):
    cur.execute("SELECT name, id FROM brands")
    return {n.strip().lower(): i for n, i in cur.fetchall()}


RZ2_SID = "1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo"

def load_fb_mapping():
    """Exact account->brand from the Raport Zilnic 2 'Mapping' tab (Conturi Facebook col).
    Returns [(account_name_lower, brand)] for best-match resolution."""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    sa = json.loads(kb_secret("GA4_SA_JSON"))
    cr = Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    svc = build("sheets", "v4", credentials=cr).spreadsheets()
    v = svc.values().get(spreadsheetId=RZ2_SID, range="'Mapping'!A2:B").execute().get("values", [])
    out = []
    for r in v:
        if len(r) >= 2 and r[0].strip() and r[1].strip():
            for acc in r[1].split(","):
                acc = acc.strip().lower()
                if acc:
                    out.append((acc, r[0].strip()))
    return out

def resolve_brand(acct, entries):
    """Most-specific Mapping entry whose name is a substring of the account name (lower)."""
    a = (acct or "").strip().lower()
    best, blen = None, -1
    for nm, brand in entries:
        if nm and (nm == a or nm in a) and len(nm) > blen:
            blen, best = len(nm), brand
    return best


def single_sku_groups():
    """From WMS 'Product Group' (SKU->Group): groups that map to exactly one SKU -> resolve group to that SKU."""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
        sa = json.loads(kb_secret("GA4_SA_JSON"))
        cr = Credentials.from_service_account_info(sa, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        svc = build("sheets", "v4", credentials=cr).spreadsheets()
        sid = kb_secret("NOMENCLATOR_SHEET_ID")
        pg = svc.values().get(spreadsheetId=sid, range="'Product Group'!A2:B").execute().get("values", [])
        g2 = defaultdict(set)
        for r in pg:
            if len(r) >= 2 and r[0].strip() and r[1].strip():
                g2[r[1].strip()].add(r[0].strip())
        return {g: list(s)[0] for g, s in g2.items() if len(s) == 1}
    except Exception as e:
        sys.stderr.write(f"[ad_spend_live] WMS group->sku indisponibil ({type(e).__name__}); grupurile rămân etichetă\n")
        return {}


def live_rows(days=14, since=None, until=None):
    """[(date, brand_id, sku_or_group, product_title, platform, spend_ron, source)] aggregated.
    since='YYYY-MM-DD' overrides days; until='YYYY-MM-DD' caps the end (default today)."""
    import meta, tiktok, prodmap, datetime
    end = until or datetime.date.today().isoformat()
    start = since or (datetime.date.today() - datetime.timedelta(days)).isoformat()
    mconn = metrics_conn(); mcur = mconn.cursor()
    name2id = brand_name_to_id(mcur); mconn.close()
    g2sku = single_sku_groups()
    brands = list(json.loads((SKILLS / "meta-ads/brand_map.json").read_text()).keys())

    agg = defaultdict(float)       # (date, brand_id, key, platform) -> spend_ron
    title = {}                     # key -> human label
    seen = set()                   # dedup shared accounts: (platform, acct, date, campaign, ad)

    def classify(platform, account, campaign, ad):
        g = prodmap.product_of(platform, account, campaign, ad)
        if g == "Unmapped":
            return f"UNMAPPED:{account}", "(nemapat)"
        if re.match(r"HA-\d+$", g):       # already a SKU
            return g, campaign[:60]
        sku = g2sku.get(g)
        return (sku, g) if sku else (g, g)  # single-product group -> SKU; else keep group label

    def bid_for(brand):
        return name2id.get(brand.strip().lower()) or name2id.get(brand.replace(" RO", "").strip().lower())

    # ---- Meta (Facebook/Instagram): pull each ad account ONCE, attribute via exact Mapping ----
    fb_entries = load_fb_mapping()
    mc2 = metrics_conn(); c2 = mc2.cursor()
    c2.execute('SELECT a.name, a."metaAccountId", a.currency, t."accessToken" '
               'FROM meta_ad_accounts a JOIN meta_access_tokens t ON t.id=a."tokenId" '
               'WHERE a."isActive" AND t."isActive"')
    fb_accts = c2.fetchall(); mc2.close()
    idx_meta = meta.fx_index({c for _, _, c, _ in fb_accts}, start, end)  # per-day USD/EUR→RON (like RZ2)
    for nm, aid, cur, tok in fb_accts:
        brand = resolve_brand(nm, fb_entries)
        if not brand:
            continue  # non-ARONA account (BauBax, intl Rossi, etc.)
        # metrics has no 'Bonhaus RO/SK', 'Esteban Parfum' etc → fold into parent (first word)
        bid = name2id.get(brand.strip().lower()) or name2id.get(brand.strip().lower().split()[0])
        for r in meta.graph(f"https://graph.facebook.com/{meta.VER}/{aid}/insights",
                {"level": "ad", "fields": "campaign_name,ad_name,spend", "time_increment": "1",
                 "time_range": json.dumps({"since": start, "until": end}), "limit": "800", "access_token": tok}):
            d = r.get("date_start"); camp = r.get("campaign_name", ""); adn = r.get("ad_name", "")
            sp = meta.conv(float(r.get("spend", 0)), cur, meta._pdate(d), idx_meta)  # per-day FX
            if sp <= 0: continue
            # TEST campaigns tracked SEPARATELY (bucket 'TEST'), excluded from real products (ca RZ2)
            key, lbl = ("TEST", "(produse de test)") if prodmap.is_test(camp) else classify("facebook", nm, camp, adn)
            agg[(d, bid, key, "meta")] += sp; title[key] = lbl

    # ---- TikTok ----
    idx_tt = tiktok.fx_index({"USD", "EUR", "PLN", "HUF", "CZK", "RON"}, start, end)  # per-day FX
    for brand in brands:
        try: accts, rows = tiktok.report_rows(brand, "ad", *(start, end))
        except SystemExit: continue
        except Exception: continue
        bid = bid_for(brand)
        for r in rows:
            if not tiktok._passes(r, "ad"): continue
            m = r.get("metrics", {}); dim = r.get("dimensions", {})
            d = dim.get("stat_time_day", "")[:10]
            camp = m.get("campaign_name", ""); adn = m.get("ad_name", "")
            k = ("tiktok", r["_acct"], d, camp, adn)
            if k in seen: continue
            seen.add(k)
            try: _day = datetime.date.fromisoformat(d)
            except Exception: _day = None
            sp = tiktok.conv(tiktok._f(m, "spend"), r["_cur"], _day, idx_tt)
            if sp <= 0: continue
            key, lbl = ("TEST", "(produse de test)") if prodmap.is_test(camp) else classify("tiktok", r["_acct"], camp, adn)
            agg[(d, bid, key, "tiktok")] += sp; title[key] = lbl

    out = [(d, bid, key, title.get(key, key), plat, round(sp, 2), "meta_tiktok_campaign_map")
           for (d, bid, key, plat), sp in agg.items() if d]
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD (overrides --days), ex. 2026-01-01")
    ap.add_argument("--until", default=None, help="YYYY-MM-DD end cap (default today)")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    rows = live_rows(a.days, a.since, a.until)
    byplat = defaultdict(float); skus = set(); groups = set(); unmapped = 0.0
    for d, bid, key, lbl, plat, sp, src in rows:
        byplat[plat] += sp
        if key.startswith("UNMAPPED:"): unmapped += sp
        elif re.match(r"HA-\d+$|.*-", key) and "-" in key and not " " in key: skus.add(key)
        else: groups.add(key)
    print(f"[ad_spend_live] {len(rows)} rânduri (date×brand×key×platformă)")
    print(f"  spend: " + ", ".join(f"{p}={round(v)}" for p, v in byplat.items()))
    print(f"  chei SKU-like: {len(skus)} | grup/categorie: {len(groups)} | UNMAPPED spend: {round(unmapped)} RON")
    print("  exemple:")
    for row in sorted(rows, key=lambda x: -x[5])[:12]:
        print(f"    {row[0]} [{row[4]}] {row[2][:34]:34} {row[5]:>9.0f} RON  ({row[3][:30]})")
    if not a.apply:
        print("\nDRY-RUN — nimic scris. (--apply pentru upsert în cache.product_ad_spend)")
        return
    from psycopg2.extras import execute_values
    mconn = metrics_conn(); mcur = mconn.cursor()
    execute_values(mcur,
        "INSERT INTO cache.product_ad_spend (date,brand_id,sku,product_title,platform,spend_ron,source) VALUES %s "
        "ON CONFLICT (date,sku,platform) DO UPDATE SET spend_ron=EXCLUDED.spend_ron, brand_id=COALESCE(EXCLUDED.brand_id,cache.product_ad_spend.brand_id), source=EXCLUDED.source",
        rows, page_size=2000)
    mconn.commit(); n = mcur.rowcount; mconn.close()
    print(f"\nAPPLIED — {len(rows)} rânduri upsert în cache.product_ad_spend (source=meta_tiktok_campaign_map).")


if __name__ == "__main__":
    main()
