# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30", "requests>=2.31"]
# ///
"""
profitability.py — P&L REAL per brand ȘI per SKU, din date reale (varianta corectă
și automată a sheet-ului „Profitabilitate per brand" / „Calcule status").

Aceeași metodologie ca breakeven.py (reutilizată), dar agregat (nu prag):
  Incasari (livrate, ex-TVA destinație) − COGS (per-SKU real) − Transport (ex-TVA, imputat
  pt coletele fără cost) − Marketing(opțional) = Contribuție.  Tot în RON.

  • Incasari   = Σ total_price pe LIVRATE × FX / (1+TVA_țară)         (TVA = al destinației)
  • COGS       = COGS%·Σ total_price livrate × FX  (COGS% = line_items×unitCost Shopify, ratio)
  • Transport  = (Σ transport_cost pe EXPEDIATE + lipsă×mediană) / 1.21   (curier RO, „Cost cu TVA")
                 — pe RO transportul e plătit o dată/colet (fără retur); intl include returul în cost.
  • Contribuție = Incasari − COGS − Transport − Marketing

Read-only. Usage:
  uv run profitability.py --by brand --days 30
  uv run profitability.py --by brand --from 2026-05-01 --to 2026-06-01 --marketing "esteban=95000,belasil=46000"
  uv run profitability.py --by sku --store belasil --days 60 --limit 40
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import breakeven as be   # STORES, VAT, VAT_RO, DELIVERED, REFUSED, _fx_map, awb_conn, _shopify_cost_map
import profit_config as cfg

SHIPPED = be.DELIVERED + be.REFUSED + ("in_transit", "unsuccessful_delivery", "deferred_delivery", "redirected")


def _period(days, frm, to):
    if frm:
        return "frisbo_created_at >= %s AND frisbo_created_at < %s", [frm, (to or "2999-01-01")]
    return "frisbo_created_at >= now() - interval '%d days'" % days, []


def _period_days(days, frm, to):
    if not frm:
        return days
    from datetime import date
    y1, m1, d1 = map(int, frm.split("-")); a = date(y1, m1, d1)
    if to:
        y2, m2, d2 = map(int, to.split("-")); b = date(y2, m2, d2)
    else:
        b = date.today()
    return max(1, (b - a).days)


def _metrics_conn():
    import pg8000.dbapi, os, urllib.parse as up
    url = os.getenv("DATABASE_URL_METRICS") or be._kb_secret("DATABASE_URL_METRICS")
    if not url:
        return None
    u = up.urlparse(url)
    return pg8000.dbapi.connect(user=up.unquote(u.username or ""), password=up.unquote(u.password or ""),
                                host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/"),
                                ssl_context=True)


def fetch_marketing(days, frm, to):
    """{key: spend_RON} pe perioadă, din metrics (conturile sincronizate) + override din config.
    NB: maparea brand→cont e parțială → completează MARKETING_OVERRIDE pt brandurile lipsă."""
    out = {k: 0.0 for k in be.STORES}
    auto = {}
    cn = None
    try:
        cn = _metrics_conn()
        if cn:
            from datetime import date, timedelta
            c = cn.cursor()
            if frm:
                start, end = frm, (to or "2999-01-01")
            else:
                start = (date.today() - timedelta(days=days)).isoformat()
                end = (date.today() + timedelta(days=1)).isoformat()
            # HIBRID (datele campaign-level sunt rare): cont DEDICAT (campaignFilter NULL) → sumă pe CONT
            # (account-level, populat); cont PARTAJAT (token) → sumă pe CAMPANIE filtrată pe token (fără dublu-numărare).
            def TOK(t):
                return f"upper(regexp_replace(i.\"campaignName\",'[^A-Za-z0-9]','','g')) LIKE '%%'||{t}.\"campaignFilter\"||'%%'"
            c.execute(f"""
              WITH spend AS (
                SELECT bg."brandId" b, i."costRon" s FROM brand_google_ads_accounts bg
                  JOIN google_ads_insights_daily i ON i."customerAccountId"=bg."customerAccountId"
                  WHERE bg."isActive" AND bg."campaignFilter" IS NULL AND i.date>=%s AND i.date<%s
                UNION ALL
                SELECT bg."brandId", i."costRon" FROM brand_google_ads_accounts bg
                  JOIN google_ads_campaign_insights_daily i ON i."customerAccountId"=bg."customerAccountId"
                  WHERE bg."isActive" AND bg."campaignFilter" IS NOT NULL AND i.date>=%s AND i.date<%s AND {TOK('bg')}
                UNION ALL
                SELECT bt."brandId", i."spendRon" FROM brand_tiktok_ad_accounts bt
                  JOIN tiktok_ad_insights_daily i ON i."adAccountId"=bt."adAccountId"
                  WHERE bt."isActive" AND bt."campaignFilter" IS NULL AND i.date>=%s AND i.date<%s
                UNION ALL
                SELECT bt."brandId", i."spendRon" FROM brand_tiktok_ad_accounts bt
                  JOIN tiktok_campaign_insights_daily i ON i."adAccountId"=bt."adAccountId"
                  WHERE bt."isActive" AND bt."campaignFilter" IS NOT NULL AND i.date>=%s AND i.date<%s AND {TOK('bt')})
              SELECT b.name, sum(spend.s) FROM brands b JOIN spend ON spend.b=b.id
              GROUP BY b.name HAVING sum(spend.s) > 0""",
              [start, end] * 4)
            name2key = {v.strip(): k for k, v in cfg.BRAND_NAME.items()}
            for name, spend in c.fetchall():
                k = name2key.get((name or "").strip())
                if k:
                    auto[k] = float(spend or 0)
    except Exception:
        pass
    finally:
        if cn:
            cn.close()
    for k in be.STORES:
        out[k] = auto.get(k, 0.0)
    return out, auto


def fetch_meta(days, frm, to, fx):
    """Spend Meta per brand, HIBRID fără dublu-numărare: conturile la care tokenul KB are acces →
    LIVE (Graph, token-aware); restul → din metrics (account/campaign-level). Fiecare cont = o sursă."""
    import re, requests
    from datetime import date, timedelta
    out = {k: 0.0 for k in be.STORES}; auto = {}
    if frm:
        start, end = frm, (to or "2999-01-01"); since = frm
        until = ((date(*map(int, to.split("-"))) - timedelta(days=1)).isoformat() if to else date.today().isoformat())
    else:
        start = (date.today() - timedelta(days=days)).isoformat(); end = (date.today() + timedelta(days=1)).isoformat()
        since = start; until = date.today().isoformat()
    cn = _metrics_conn()
    if not cn:
        return out, auto
    c = cn.cursor()
    c.execute('''SELECT b.name, a."metaAccountId", a.currency, bm."campaignFilter"
                 FROM brand_meta_ad_accounts bm JOIN brands b ON b.id=bm."brandId"
                 JOIN meta_ad_accounts a ON a.id=bm."adAccountId" WHERE bm."isActive"''')
    rows = c.fetchall()
    name2key = {v.strip(): k for k, v in cfg.BRAND_NAME.items()}
    B = "https://graph.facebook.com/v21.0"
    # mai multe tokenuri de sistem (acoperă conturi diferite) → act_id -> token care-l vede
    reach = {}
    for tname in ["META_SYSTEM_TOKEN"] + [f"META_SYSTEM_TOKEN_{i}" for i in range(2, 9)]:
        tk = be._kb_secret(tname)
        if not tk:
            continue
        try:
            d = requests.get(f"{B}/me/adaccounts", params={"fields": "account_id", "limit": "200", "access_token": tk}, timeout=45).json()
            for x in d.get("data", []):
                if x.get("account_id"):
                    reach.setdefault("act_" + x["account_id"], tk)
        except Exception:
            pass
    tr = '{"since":"%s","until":"%s"}' % (since, until)
    non_reach = []
    for bname, act, ccy, cf in rows:
        key = name2key.get((bname or "").strip())
        if not key or not act:
            continue
        tok = reach.get(act)
        if tok:
            rate = fx.get((ccy or "USD").upper(), fx.get("USD", 4.43))
            try:
                if cf:
                    p = {"fields": "spend,campaign_name", "level": "campaign", "time_range": tr, "limit": "500", "access_token": tok}
                    data = requests.get(f"{B}/{act}/insights", params=p, timeout=45).json().get("data", [])
                    s = sum(float(x.get("spend") or 0) for x in data
                            if cf in re.sub(r"[^A-Za-z0-9]", "", (x.get("campaign_name") or "")).upper())
                else:
                    p = {"fields": "spend", "time_range": tr, "access_token": tok}
                    data = requests.get(f"{B}/{act}/insights", params=p, timeout=45).json().get("data", [])
                    s = sum(float(x.get("spend") or 0) for x in data)
                if s:
                    out[key] += s * rate; auto[key] = auto.get(key, 0.0) + s * rate
            except Exception:
                non_reach.append(act)
        else:
            non_reach.append(act)
    if non_reach:                                   # conturile fără acces live → metrics
        try:
            c.execute('''
              WITH mspend AS (
                SELECT bm."brandId" b, i."spendRon" s FROM brand_meta_ad_accounts bm
                  JOIN meta_ad_accounts a ON a.id=bm."adAccountId"
                  JOIN meta_ad_insights_daily i ON i."adAccountId"=bm."adAccountId"
                  WHERE bm."isActive" AND bm."campaignFilter" IS NULL AND a."metaAccountId"=ANY(%s) AND i.date>=%s AND i.date<%s
                UNION ALL
                SELECT bm."brandId", i."spendRon" FROM brand_meta_ad_accounts bm
                  JOIN meta_ad_accounts a ON a.id=bm."adAccountId"
                  JOIN meta_campaign_insights_daily i ON i."adAccountId"=bm."adAccountId"
                  WHERE bm."isActive" AND bm."campaignFilter" IS NOT NULL AND a."metaAccountId"=ANY(%s) AND i.date>=%s AND i.date<%s
                    AND upper(regexp_replace(i."campaignName",'[^A-Za-z0-9]','','g')) LIKE '%%'||bm."campaignFilter"||'%%')
              SELECT b.name, sum(mspend.s) FROM brands b JOIN mspend ON mspend.b=b.id GROUP BY b.name''',
              [non_reach, start, end, non_reach, start, end])
            for name, sp in c.fetchall():
                key = name2key.get((name or "").strip())
                if key and sp:
                    out[key] += float(sp); auto[key] = auto.get(key, 0.0) + float(sp)
        except Exception:
            pass
    cn.close()
    return out, auto


def _agency_cost(key, inc_local_withvat, transport_withvat, months):
    """Comision agenție pe (Incasari−Transport) cu TVA. months = lunile din perioadă (pt fee fix)."""
    if key in cfg.AGENCY_NONE:
        return 0.0
    a = cfg.AGENCY.get(key, cfg.AGENCY["_default"])
    base = max(0.0, inc_local_withvat - transport_withvat)
    return a.get("pct", 0.0) * base + (a.get("fixed_extra") or 0.0) * months


def _brand_fixed(key, fx, months):
    """Abonamente per brand (RON + USD→RON), prorate pe lunile perioadei."""
    tot = sum(cfg.SUBSCRIPTIONS_RON.get(key, {}).values())
    usd = sum(cfg.SUBSCRIPTIONS_USD.get(key, {}).values()) * fx.get("USD", 4.43)
    return (tot + usd) * months


def _cogs_pct(cur, cfg, where, wargs, sample):
    """COGS% real = Σ(cost×qty)/Σ(price) pe un eșantion de livrate (ratio, currency-agnostic)."""
    cm = be._shopify_cost_map(cfg["shop"])
    if not cm:
        return None, 0.0
    cur.execute(f"""SELECT line_items FROM orders o JOIN stores s ON s.uid=o.store_uid
                    WHERE s.name ILIKE %s AND line_items IS NOT NULL AND total_price>0
                    AND aggregated_status = ANY(%s) AND {where} LIMIT %s""",
                [f"%{cfg['awb']}%", list(be.DELIVERED)] + wargs + [sample])
    tc = tr = 0.0; mu = miss = 0
    for (li,) in cur.fetchall():
        for it in (li or []):
            sku = ((it.get("inventory_item") or {}).get("sku") or "").strip()
            q = float(it.get("quantity") or 0); p = float(it.get("price") or 0)
            if sku in cm:
                tc += cm[sku] * q; mu += q
            else:
                miss += q
            tr += p * q
    match = mu / (mu + miss) if (mu + miss) else 0
    if match < 0.5:
        return None, match
    return (tc / tr if tr else None), match


def brand_pnl(cur, keys, days, frm, to, fx, sample, marketing, auto_mkt):
    where, wargs = _period(days, frm, to)
    months = _period_days(days, frm, to) / 30.0
    hdr = ["Brand", "Livr", "Incasari", "COGS", "Transp", "Mkt", "Agentie", "Subs", "Contrib", "%Marja"]
    print("  " + " | ".join(f"{h:>12}" if i else f"{h:<12}" for i, h in enumerate(hdr)))
    tot = {k: 0.0 for k in ("inc", "cogs", "tr", "mkt", "ag", "subs", "contrib")}
    nomkt = []
    for key in keys:
        sc = be.STORES[key]; awb = f"%{sc['awb']}%"
        cur.execute(f"""
          SELECT count(*) FILTER (WHERE aggregated_status = ANY(%s)) shipped,
                 count(*) FILTER (WHERE aggregated_status = ANY(%s)) delivered,
                 coalesce(sum(total_price) FILTER (WHERE aggregated_status = ANY(%s)),0) inc_local,
                 coalesce(sum(transport_cost) FILTER (WHERE aggregated_status = ANY(%s) AND transport_cost>0),0) tr_priced,
                 count(*) FILTER (WHERE aggregated_status = ANY(%s) AND (transport_cost IS NULL OR transport_cost=0)) n_missing,
                 coalesce(percentile_cont(0.5) within group (order by transport_cost)
                   FILTER (WHERE aggregated_status = ANY(%s) AND transport_cost>0),0) tr_med
          FROM orders o JOIN stores s ON s.uid=o.store_uid
          WHERE s.name ILIKE %s AND {where}""",
          [list(SHIPPED), list(be.DELIVERED), list(be.DELIVERED),
           list(SHIPPED), list(SHIPPED), list(SHIPPED), awb] + wargs)
        shipped, delivered, inc_local, tr_priced, n_missing, tr_med = cur.fetchone()
        shipped, delivered = int(shipped), int(delivered)
        if shipped == 0:
            continue
        cogs_pct, _ = _cogs_pct(cur, sc, where, wargs, sample)
        rate = fx.get(sc["cur"], 1.0); vat = be.VAT.get(sc["country"], be.VAT_RO)
        inc_ron_v = float(inc_local) * rate                       # încasări RON, cu TVA
        inc = inc_ron_v / (1 + vat)                               # ex-TVA destinație
        cogs = (cogs_pct or 0) * inc_ron_v
        tr_v = float(tr_priced) + int(n_missing) * float(tr_med)  # transport RON, cu TVA (imputat)
        transport = tr_v / (1 + be.VAT_RO)
        mkt = float(marketing.get(key, 0.0))
        agency = _agency_cost(key, inc_ron_v, tr_v, months)
        subs = _brand_fixed(key, fx, months) + cfg.CONSUMABILE_PER_PARCEL * shipped
        contrib = inc - cogs - transport - mkt - agency - subs
        if mkt == 0 and key not in cfg.AGENCY_NONE:
            nomkt.append(key)
        cogs_disp = f"{cogs:,.0f}" if cogs_pct is not None else "n/a"
        print(f"  {key:<12} | {delivered:>12,} | {inc:>12,.0f} | {cogs_disp:>12} | {transport:>12,.0f} | "
              f"{mkt:>12,.0f} | {agency:>12,.0f} | {subs:>12,.0f} | {contrib:>12,.0f} | {100*contrib/inc if inc else 0:>11.0f}%")
        for k, v in (("inc", inc), ("cogs", cogs), ("tr", transport), ("mkt", mkt), ("ag", agency), ("subs", subs), ("contrib", contrib)):
            tot[k] += v
    print("  " + "-" * 128)
    print(f"  {'TOTAL':<12} | {'':>12} | {tot['inc']:>12,.0f} | {tot['cogs']:>12,.0f} | {tot['tr']:>12,.0f} | "
          f"{tot['mkt']:>12,.0f} | {tot['ag']:>12,.0f} | {tot['subs']:>12,.0f} | {tot['contrib']:>12,.0f} | "
          f"{100*tot['contrib']/tot['inc'] if tot['inc'] else 0:>11.0f}%")
    opex = cfg.OPEX_SHARED_MONTHLY * months
    ebitda = tot["contrib"] - opex
    print(f"  {'− OPEX comun':<12} | {'(salarii/chirii/SaaS, nivel grup)':>40} {opex:>14,.0f}")
    print(f"  {'= EBITDA':<12} | {ebitda:>14,.0f}  ({100*ebitda/tot['inc'] if tot['inc'] else 0:.0f}% din încasări)")
    if nomkt:
        print(f"\n  ⚠️ Marketing=0 (cont nemapat în metrics / fără override) la: {', '.join(nomkt)}")
        print(f"     → completează MARKETING_OVERRIDE în profit_config.py sau mapează conturile (brand_*_ad_accounts).")
    if auto_mkt:
        print(f"  ℹ️ Marketing auto din metrics: {', '.join(f'{k}={v:,.0f}' for k,v in auto_mkt.items())}")


def sku_pnl(cur, key, days, frm, to, fx, limit):
    cfg = be.STORES[key]; awb = f"%{cfg['awb']}%"
    where, wargs = _period(days, frm, to)
    cm = be._shopify_cost_map(cfg["shop"])
    if not cm:
        print(f"  COGS indisponibil pt {key} (token Shopify mort?) — nu pot calcula profit/SKU."); return
    rate = fx.get(cfg["cur"], 1.0); vat = be.VAT.get(cfg["country"], be.VAT_RO)
    # livrate: alocă transportul coletului pe linii după cota de venit
    cur.execute(f"""SELECT line_items, total_price, transport_cost FROM orders o JOIN stores s ON s.uid=o.store_uid
                    WHERE s.name ILIKE %s AND line_items IS NOT NULL AND total_price>0
                    AND aggregated_status = ANY(%s) AND {where}""",
                [awb, list(be.DELIVERED)] + wargs)
    agg = {}  # sku -> [units, rev_local, cogs_local, tr_alloc_ron, title]
    for li, price, tcost in cur.fetchall():
        price = float(price or 0); tcost = float(tcost or 0)
        lines = [(((it.get("inventory_item") or {}).get("sku") or "").strip(),
                  float(it.get("quantity") or 0), float(it.get("price") or 0),
                  ((it.get("inventory_item") or {}).get("title_1") or "")) for it in (li or [])]
        line_rev = sum(q * p for _, q, p, _ in lines) or 1
        for sku, q, p, title in lines:
            if not sku:
                continue
            a = agg.setdefault(sku, [0.0, 0.0, 0.0, 0.0, title])
            a[0] += q; a[1] += q * p
            a[2] += cm.get(sku, 0) * q
            a[3] += tcost * (q * p / line_rev)   # transport alocat (RON)
    rows = []
    for sku, (units, rev_l, cogs_l, tr_ron, title) in agg.items():
        inc = rev_l * rate / (1 + vat)
        cogs = cogs_l * rate
        tr = tr_ron / (1 + be.VAT_RO)
        contrib = inc - cogs - tr
        rows.append((sku, title[:26], units, inc, cogs, tr, contrib, 100 * contrib / inc if inc else 0,
                     "✓" if sku in cm else "✗"))
    rows.sort(key=lambda r: r[6], reverse=True)
    print(f"\n  P&L per SKU — {key} ({len(rows)} SKU livrate). Top + bottom după contribuție (RON, ex-TVA):")
    print(f"  {'SKU':<18} {'Produs':<27} {'Buc':>6} {'Incasari':>10} {'COGS':>9} {'Transp':>8} {'Contrib':>10} {'%Marja':>7} cost")
    def show(r):
        print(f"  {r[0][:18]:<18} {r[1]:<27} {r[2]:>6.0f} {r[3]:>10,.0f} {r[4]:>9,.0f} {r[5]:>8,.0f} {r[6]:>10,.0f} {r[7]:>6.0f}% {r[8]:>4}")
    for r in rows[:limit]:
        show(r)
    if len(rows) > limit:
        print(f"  … {len(rows)-limit} la mijloc …")
        for r in rows[-min(8, max(0, len(rows)-limit)):]:
            show(r)


def main():
    ap = argparse.ArgumentParser(description="P&L real per brand / per SKU din AWB + Shopify.")
    ap.add_argument("--by", choices=["brand", "sku"], default="brand")
    ap.add_argument("--store", help="pt --by sku (un brand); pt --by brand: implicit toate")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--from", dest="frm"); ap.add_argument("--to")
    ap.add_argument("--sample", type=int, default=4000, help="comenzi pt COGS% (--by brand)")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--marketing", default="", help='cheltuieli mkt: "esteban=95000,belasil=46000"')
    a = ap.parse_args()
    mkt = {}
    for part in [p for p in a.marketing.split(",") if "=" in p]:
        k, v = part.split("=", 1); mkt[k.strip().lower()] = float(v)
    cn = be.awb_conn(); cur = cn.cursor(); fx = be._fx_map()
    per = (f"{a.frm}→{a.to or 'azi'}") if a.frm else f"ultimele {a.days}z"
    print(f"\n=== Profitabilitate ({per}) — RON. Marketing auto din metrics (unde-i mapat) + agenție + abonamente + OPEX → EBITDA ===")
    if a.by == "brand":
        keys = [a.store.lower()] if a.store else list(be.STORES)
        gtt, auto_gtt = fetch_marketing(a.days, a.frm, a.to)        # Google+TikTok din metrics
        mlive, auto_m = fetch_meta(a.days, a.frm, a.to, fx)        # Meta HIBRID (live token + metrics)
        marketing = {}
        for k in be.STORES:
            base = gtt.get(k, 0.0) + mlive.get(k, 0.0)
            marketing[k] = float(cfg.MARKETING_OVERRIDE.get(k, base))
        marketing.update(mkt)   # --marketing din CLI bate tot
        auto = {k: auto_gtt.get(k, 0.0) + auto_m.get(k, 0.0) for k in set(auto_gtt) | set(auto_m)}
        brand_pnl(cur, [k for k in keys if k in be.STORES], a.days, a.frm, a.to, fx, a.sample, marketing, auto)
    else:
        if not a.store or a.store.lower() not in be.STORES:
            sys.exit(f"--by sku cere --store dintre: {', '.join(be.STORES)}")
        sku_pnl(cur, a.store.lower(), a.days, a.frm, a.to, fx, a.limit)
    cn.close()


if __name__ == "__main__":
    main()
