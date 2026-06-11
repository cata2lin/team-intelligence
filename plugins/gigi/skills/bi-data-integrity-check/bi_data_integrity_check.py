# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
bi_data_integrity_check.py — Auditor de sanatate / integritate a datelor pentru
warehouse-ul BI `metrics`.

Raspunde la: "datele din BI sunt corecte?", "ce sync-uri sunt vechi/stale?",
"de ce e spend-ul GT zero?", "ce branduri n-au tracking mapat?", "TikTok mai
sincronizeaza?". NU scrie nimic in baze (doar SELECT).

Per brand x sursa {meta, google, tiktok, shopify}:
  - cont de reclame mapat & activ?  (brand_*_ad_accounts.isActive)
  - MAX(data insight) + lag in zile fata de prag (--threshold-days)
  - share de randuri cu conversionRate=0 desi orders>0 (randuri sparte)
  - convertedSessions=0 desi orders>0
La nivel global:
  - MAX(date) per tabel de insight
  - branduri cu VENIT dar 0 conturi de reclame mapate (spend citeste 0)

Iese un tabel RAG (RED/AMBER/GREEN) per brand x sursa + lista "top issues" cu
fix-ul concret.

Folosire:
  uv run bi_data_integrity_check.py audit                 # tabel RAG complet
  uv run bi_data_integrity_check.py audit --threshold-days 3
  uv run bi_data_integrity_check.py issues               # doar top issues (RED/AMBER)
  uv run bi_data_integrity_check.py mapping              # ce branduri n-au conturi mapate dar au venit
  uv run bi_data_integrity_check.py freshness            # MAX(date) per tabel + lag
  uv run bi_data_integrity_check.py brand esteban        # focus pe un singur brand

Flag-uri:
  --threshold-days N   prag lag pana la AMBER (default 2). >2x prag => RED.
  --window-days N      fereastra pt analiza venit / randuri sparte (default 30).
  --min-revenue N      prag venit (RON) ca un brand sa conteze pt "missing mapping" (default 1000).
"""
import sys
import os
import subprocess
import argparse
import urllib.parse
from datetime import date, datetime

import pg8000.dbapi

# Sursele de reclame: cheia tabelelor de mapare + cheia de join in insights.
AD_SOURCES = {
    "meta":   {"map": "brand_meta_ad_accounts",  "acc_col": "adAccountId",
               "ins": "meta_ad_insights_daily",   "ins_acc": "adAccountId"},
    "google": {"map": "brand_google_ads_accounts", "acc_col": "customerAccountId",
               "ins": "google_ads_insights_daily", "ins_acc": "customerAccountId"},
    "tiktok": {"map": "brand_tiktok_ad_accounts", "acc_col": "adAccountId",
               "ins": "tiktok_ad_insights_daily",  "ins_acc": "adAccountId"},
}
ALL_SOURCES = ["meta", "google", "tiktok", "shopify"]


def get_conn():
    url = os.environ.get("DATABASE_URL_METRICS")
    if not url:
        kb = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "..", "core", "scripts", "kb.py")
        url = subprocess.run(["uv", "run", kb, "secret-get", "DATABASE_URL_METRICS"],
                             capture_output=True, text=True).stdout.strip()
    u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(
        ssl_context=True,
        user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""),
        host=u.hostname, port=u.port or 5432,
        database=(u.path or "/").lstrip("/"))


def _d(v):
    """normalize date/datetime -> date or None"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return None


def lag_days(d):
    if d is None:
        return None
    return (date.today() - d).days


# ---------- collectors ----------

def load_brands(cur, only_slug=None):
    sql = 'SELECT id, slug, name FROM brands WHERE "isActive"'
    params = []
    if only_slug:
        sql += " AND slug = %s"
        params.append(only_slug)
    sql += " ORDER BY slug"
    cur.execute(sql, params)
    return [{"id": r[0], "slug": r[1], "name": r[2]} for r in cur.fetchall()]


def load_revenue(cur, window_days):
    cur.execute(
        'SELECT "brandId", ROUND(COALESCE(SUM("totalPrice"),0))::int, COUNT(*) '
        'FROM orders WHERE "shopifyCreatedAt" >= (CURRENT_DATE - %s * INTERVAL \'1 day\') '
        'AND "deletedAt" IS NULL GROUP BY "brandId"', (window_days,))
    return {r[0]: {"revenue": int(r[1] or 0), "orders": int(r[2] or 0)} for r in cur.fetchall()}


def load_ad_mapping(cur):
    """Per (brandId, source): {mapped, active, accounts:[...]} + last insight date."""
    out = {}
    for src, c in AD_SOURCES.items():
        # mapped accounts
        cur.execute(
            'SELECT "brandId", "%s", "isActive" FROM "%s"' % (c["acc_col"], c["map"]))
        for brand_id, acc, is_active in cur.fetchall():
            out.setdefault((brand_id, src), {"accounts": [], "active_accounts": []})
            out[(brand_id, src)]["accounts"].append(acc)
            if is_active:
                out[(brand_id, src)]["active_accounts"].append(acc)
        # last insight date per brand (join through mapping)
        cur.execute(
            'SELECT m."brandId", MAX(i.date) FROM "%s" m '
            'JOIN "%s" i ON i."%s" = m."%s" '
            'GROUP BY m."brandId"' % (c["map"], c["ins"], c["ins_acc"], c["acc_col"]))
        for brand_id, last in cur.fetchall():
            out.setdefault((brand_id, src), {"accounts": [], "active_accounts": []})
            out[(brand_id, src)]["last"] = _d(last)
    return out


def load_shopify_analytics(cur, window_days):
    """Per brandId: last date + broken-row counts in window."""
    cur.execute(
        'SELECT "brandId", MAX(date), '
        ' COUNT(*) FILTER (WHERE orders>0) AS rows_orders, '
        ' COUNT(*) FILTER (WHERE orders>0 AND ("conversionRate" IS NULL OR "conversionRate"=0)) AS cr_zero, '
        ' COUNT(*) FILTER (WHERE orders>0 AND ("convertedSessions" IS NULL OR "convertedSessions"=0)) AS cs_zero '
        'FROM shopify_analytics_daily '
        'WHERE date >= (CURRENT_DATE - %s * INTERVAL \'1 day\') '
        'GROUP BY "brandId"', (window_days,))
    out = {}
    for brand_id, last, rows_orders, cr_zero, cs_zero in cur.fetchall():
        out[brand_id] = {
            "last": _d(last),
            "rows_orders": int(rows_orders or 0),
            "cr_zero": int(cr_zero or 0),
            "cs_zero": int(cs_zero or 0),
        }
    return out


def load_table_freshness(cur):
    out = {}
    for label, tbl in (("meta", "meta_ad_insights_daily"),
                       ("google", "google_ads_insights_daily"),
                       ("tiktok", "tiktok_ad_insights_daily"),
                       ("shopify", "shopify_analytics_daily")):
        cur.execute('SELECT MAX(date), COUNT(*) FROM "%s"' % tbl)
        r = cur.fetchone()
        out[label] = {"last": _d(r[0]), "rows": int(r[1] or 0)}
    return out


# ---------- RAG engine ----------

def assess_cell(src, brand, rev, mapping, shop, threshold):
    """
    Return (rag, lag, detail, fix) for one brand x source.
    rag in {RED, AMBER, GREEN, N/A}
    """
    has_rev = rev["revenue"] > 0

    if src == "shopify":
        sh = shop.get(brand["id"])
        if not sh or sh["last"] is None:
            if has_rev:
                return ("RED", None, "0 randuri Shopify analytics desi exista venit",
                        "Verifica sync-ul SHOPIFY analytics pt acest brand")
            return ("N/A", None, "fara date / fara venit", "")
        lag = lag_days(sh["last"])
        # broken rows: conversionRate=0 desi orders>0
        bad = sh["cr_zero"]
        rows = sh["rows_orders"]
        bad_share = (bad / rows) if rows else 0
        detail = "ultim %s (lag %dz)" % (sh["last"], lag)
        fix = ""
        rag = "GREEN"
        if lag is not None and lag > 2 * threshold:
            rag = "RED"
            fix = "Sync Shopify analytics blocat — reporneste cron-ul de analytics"
        elif lag is not None and lag > threshold:
            rag = "AMBER"
            fix = "Sync Shopify analytics in urma — verifica urmatoarea rulare"
        if bad_share >= 0.5 and rows >= 5:
            detail += " | conversionRate=0 in %d/%d randuri cu orders>0" % (bad, rows)
            if rag == "GREEN":
                rag = "AMBER"
            fix = (fix + " ; ").lstrip(" ;") + "Re-pull analytics: conversionRate nepopulat (sessions/conversions lipsesc la sync)"
        return (rag, lag, detail, fix)

    # ad sources
    m = mapping.get((brand["id"], src), {})
    active = m.get("active_accounts", [])
    accounts = m.get("accounts", [])
    last = m.get("last")
    lag = lag_days(last)

    # No active mapping
    if not active:
        if accounts:
            detail = "%d cont(uri) mapate dar INACTIVE" % len(accounts)
            fix = 'Activeaza contul in %s ("isActive"=true) — altfel spend citeste 0' % AD_SOURCES[src]["map"]
            return ("RED" if has_rev else "AMBER", lag, detail, fix)
        # no account at all
        if has_rev:
            return ("RED", None, "fara cont mapat (spend citeste 0 desi exista venit)",
                    'Mapeaza ad account-ul in %s' % AD_SOURCES[src]["map"])
        return ("N/A", None, "fara cont mapat / fara venit",
                'Mapeaza ad account-ul daca brandul ruleaza %s' % src)

    # Has active mapping -> judge freshness of insights
    if last is None:
        return ("RED", None, "cont mapat dar 0 randuri de insight",
                "Cont mapat dar sync-ul nu aduce date — verifica token/permisiuni API %s" % src)
    detail = "cont activ, ultim insight %s (lag %dz)" % (last, lag)
    if lag > 2 * threshold:
        return ("RED", lag, detail,
                "Sync %s STALE (>%dz) — repara conectorul / reautentifica" % (src, 2 * threshold))
    if lag > threshold:
        return ("AMBER", lag, detail,
                "Sync %s in urma (>%dz) — monitorizeaza urmatoarea rulare" % (src, threshold))
    return ("GREEN", lag, detail, "")


def build_report(cur, threshold, window_days, min_revenue, only_slug=None):
    brands = load_brands(cur, only_slug)
    rev = load_revenue(cur, window_days)
    mapping = load_ad_mapping(cur)
    shop = load_shopify_analytics(cur, window_days)
    freshness = load_table_freshness(cur)

    report = []
    for b in brands:
        brev = rev.get(b["id"], {"revenue": 0, "orders": 0})
        cells = {}
        for src in ALL_SOURCES:
            rag, lag, detail, fix = assess_cell(src, b, brev, mapping, shop, threshold)
            cells[src] = {"rag": rag, "lag": lag, "detail": detail, "fix": fix}
        report.append({"slug": b["slug"], "name": b["name"], "id": b["id"],
                       "revenue": brev["revenue"], "orders": brev["orders"], "cells": cells})
    # sort by revenue desc (most important brands first)
    report.sort(key=lambda r: -r["revenue"])
    return report, freshness, min_revenue


# ---------- rendering ----------

RAG_GLYPH = {"RED": "🔴", "AMBER": "🟠", "GREEN": "🟢", "N/A": "·"}
RAG_TXT = {"RED": "RED ", "AMBER": "AMBR", "GREEN": "GRN ", "N/A": " -  "}


def cell_str(c, glyph):
    g = RAG_GLYPH[c["rag"]] if glyph else RAG_TXT[c["rag"]]
    return g


def print_audit(report, freshness, threshold, window_days, glyph=True):
    print("=" * 78)
    print("BI DATA INTEGRITY — audit warehouse `metrics`  (azi %s)" % date.today())
    print("prag lag AMBER=%dz, RED>%dz | fereastra venit/randuri=%dz" % (threshold, 2 * threshold, window_days))
    print("=" * 78)
    print("Freshness tabele de insight (MAX date):")
    for src, f in freshness.items():
        lag = lag_days(f["last"])
        mark = ""
        if lag is not None and lag > 2 * threshold:
            mark = "  <-- STALE"
        elif lag is not None and lag > threshold:
            mark = "  <-- in urma"
        print("  %-9s %s (lag %sz, %s randuri)%s" % (
            src, f["last"], lag if lag is not None else "?", f["rows"], mark))
    print("-" * 78)
    print("RAG per brand x sursa  (%s):" % ("🔴 RED  🟠 AMBER  🟢 GREEN  · N/A" if glyph else "RED/AMBR/GRN/-"))
    hdr = "%-16s %9s  %-5s %-5s %-5s %-5s" % ("brand", "venit", "meta", "ggl", "tik", "shop")
    print(hdr)
    print("-" * 78)
    for r in report:
        line = "%-16s %9s  %-5s %-5s %-5s %-5s" % (
            r["slug"][:16], "{:,}".format(r["revenue"]),
            cell_str(r["cells"]["meta"], glyph),
            cell_str(r["cells"]["google"], glyph),
            cell_str(r["cells"]["tiktok"], glyph),
            cell_str(r["cells"]["shopify"], glyph))
        print(line)
    print("-" * 78)
    print_issues_block(report, window_days)


def collect_issues(report):
    issues = []
    for r in report:
        for src in ALL_SOURCES:
            c = r["cells"][src]
            if c["rag"] in ("RED", "AMBER"):
                issues.append({"slug": r["slug"], "revenue": r["revenue"], "src": src,
                               "rag": c["rag"], "detail": c["detail"], "fix": c["fix"]})
    # RED before AMBER, then by revenue desc
    order = {"RED": 0, "AMBER": 1}
    issues.sort(key=lambda i: (order[i["rag"]], -i["revenue"]))
    return issues


def print_issues_block(report, window_days):
    issues = collect_issues(report)
    reds = [i for i in issues if i["rag"] == "RED"]
    print("TOP ISSUES — %d RED, %d AMBER:" % (len(reds), len(issues) - len(reds)))
    if not issues:
        print("  (niciuna — toate sursele GREEN/N-A)")
        return
    for i in issues[:30]:
        g = RAG_GLYPH[i["rag"]]
        print("  %s %-14s %-7s venit %9s | %s" % (
            g, i["slug"], i["src"], "{:,}".format(i["revenue"]), i["detail"]))
        if i["fix"]:
            print("       FIX: %s" % i["fix"])


def print_mapping(report, min_revenue):
    print("BRANDURI CU VENIT DAR FARA CONT DE RECLAME MAPAT (spend citeste 0):")
    print("(prag venit >= %s RON, fereastra configurata)" % "{:,}".format(min_revenue))
    print("-" * 70)
    any_found = False
    for r in report:
        if r["revenue"] < min_revenue:
            continue
        missing = []
        for src in ("meta", "google", "tiktok"):
            c = r["cells"][src]
            if c["rag"] in ("RED",) and ("fara cont" in c["detail"] or "INACTIVE" in c["detail"]):
                missing.append(src)
        if missing:
            any_found = True
            print("  %-16s venit %9s | LIPSA: %s" % (
                r["slug"], "{:,}".format(r["revenue"]), ", ".join(missing)))
    if not any_found:
        print("  (niciun brand cu venit semnificativ si mapare lipsa)")


def print_freshness(freshness, threshold):
    print("FRESHNESS tabele de insight (warehouse metrics):")
    print("-" * 60)
    for src, f in freshness.items():
        lag = lag_days(f["last"])
        status = "GREEN"
        if lag is not None and lag > 2 * threshold:
            status = "RED (STALE)"
        elif lag is not None and lag > threshold:
            status = "AMBER (in urma)"
        print("  %-9s ultim %s | lag %sz | %s randuri | %s" % (
            src, f["last"], lag if lag is not None else "?", f["rows"], status))


def print_brand(report, slug):
    r = next((x for x in report if x["slug"] == slug), None)
    if not r:
        print("Brand '%s' negasit (sau inactiv)." % slug)
        return
    print("=" * 60)
    print("Brand: %s (%s) — venit fereastra %s RON, %d comenzi" % (
        r["name"], r["slug"], "{:,}".format(r["revenue"]), r["orders"]))
    print("=" * 60)
    for src in ALL_SOURCES:
        c = r["cells"][src]
        print("  %s %-7s : %s" % (RAG_GLYPH[c["rag"]], src, c["detail"]))
        if c["fix"]:
            print("           FIX: %s" % c["fix"])


def main():
    ap = argparse.ArgumentParser(description="BI data integrity / brand health auditor (metrics warehouse)")
    ap.add_argument("mode", nargs="?", default="audit",
                    choices=["audit", "issues", "mapping", "freshness", "brand"])
    ap.add_argument("query", nargs="?", default="", help="slug de brand pt mode 'brand'")
    ap.add_argument("--threshold-days", type=int, default=2,
                    help="prag lag pana la AMBER (RED = peste 2x). default 2")
    ap.add_argument("--window-days", type=int, default=30,
                    help="fereastra pt venit / randuri sparte. default 30")
    ap.add_argument("--min-revenue", type=int, default=1000,
                    help="prag venit (RON) pt 'missing mapping'. default 1000")
    ap.add_argument("--no-glyph", action="store_true", help="text RAG in loc de emoji")
    a = ap.parse_args()

    conn = get_conn()
    cur = conn.cursor()
    try:
        only = a.query if a.mode == "brand" and a.query else None
        report, freshness, min_rev = build_report(
            cur, a.threshold_days, a.window_days, a.min_revenue, only_slug=None)

        if a.mode == "audit":
            print_audit(report, freshness, a.threshold_days, a.window_days, glyph=not a.no_glyph)
        elif a.mode == "issues":
            print("=== BI integrity — TOP ISSUES (prag %dz) ===" % a.threshold_days)
            print_issues_block(report, a.window_days)
        elif a.mode == "mapping":
            print_mapping(report, a.min_revenue)
        elif a.mode == "freshness":
            print_freshness(freshness, a.threshold_days)
        elif a.mode == "brand":
            if not a.query:
                print("Specifica un slug: uv run bi_data_integrity_check.py brand esteban")
            else:
                print_brand(report, a.query)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
