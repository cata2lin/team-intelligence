"""
wms_marketing.py — per-SKU marketing din WMS (token-independent), pt profit_by_sku.

Flux: wms_ad_spend (raw USD/campanie) -> grup (wms_nomen + wms_nomen_extra: ACCOUNT exact, apoi
CAMPAIGN_KEYWORD substring) -> USD->RON (fx_rates) -> spend per grup/zi -> alocat pe SKU pe COMENZI
(grup->SKU din wms_product_group + wms_product_group_extra). WMS = primar; SKU/grup fără WMS -> cache.

Expune wms_group_spend_ron(pf_conn, metrics_cur, lo, hi) -> {group: ron}.
"""
from collections import defaultdict


def _load_fx(metrics_cur):
    metrics_cur.execute("SELECT \"rateDate\", rate FROM fx_rates WHERE \"fromCurrency\"='USD' AND \"toCurrency\"='RON' ORDER BY \"rateDate\"")
    return [(str(d), float(r)) for d, r in metrics_cur.fetchall()]


def _usd_ron(fx, date):
    best = fx[0][1] if fx else 4.5
    for d, r in fx:
        if d <= date:
            best = r
        else:
            break
    return best


def _load_nomen(pf_conn):
    acc = defaultdict(list); key = defaultdict(list)
    for plat, grp, mt, pat in pf_conn.execute(
        "SELECT platform,product_group,map_type,pattern FROM wms_nomen "
        "UNION ALL SELECT platform,product_group,map_type,pattern FROM wms_nomen_extra"):
        if mt == "ACCOUNT":
            acc[plat].append(((pat or "").strip().lower(), grp))
        elif mt == "CAMPAIGN_KEYWORD":
            key[plat].append(((pat or "").strip().upper(), grp))
    return acc, key


def _group_of(acc, key, plat, account, campaign):
    a = (account or "").strip().lower(); c = (campaign or "").upper()
    # CAMPAIGN_KEYWORD prioritar (per-produs), apoi ACCOUNT (fallback brand)
    for p, g in key[plat]:
        if p and p in c:
            return g
    for p, g in acc[plat]:
        if a == p:
            return g
    return None


EXCLUDE_GROUPS = {"test"}   # „fără teste" — grupul Test nu primește marketing


def wms_group_spend_ron(pf_conn, metrics_cur, lo, hi):
    """{group: spend_ron} din WMS pe [lo, hi] (incl.). Grupul Test e exclus („fără teste")."""
    fx = _load_fx(metrics_cur)
    acc, key = _load_nomen(pf_conn)
    out = defaultdict(float)
    for src, date, account, campaign, spend in pf_conn.execute(
        "SELECT source,date,account,campaign,spend_usd FROM wms_ad_spend WHERE date>=? AND date<=?", (lo, hi)):
        g = _group_of(acc, key, src, account, campaign)
        if g and g.strip().lower() not in EXCLUDE_GROUPS:
            out[g] += (spend or 0) * _usd_ron(fx, date)
    return dict(out)


def wms_sku_to_group(pf_conn):
    """{sku_upper: group}. SHEET-ul (wms_product_group) e AUTORITATIV; extra DOAR umple golurile
    (SKU-uri neclasificate de sheet), ca să nu suprascrie clasificarea ta cu branduri partajate."""
    m = {}
    for sku, grp in pf_conn.execute("SELECT sku, grp FROM wms_product_group_extra"):  # întâi extra
        if sku:
            m[sku.strip().upper()] = grp
    for sku, grp in pf_conn.execute("SELECT sku, grp FROM wms_product_group"):          # sheet SUPRASCRIE (câștigă)
        if sku and (grp or "").strip():
            m[sku.strip().upper()] = grp
    return m


# grup-BRAND (adăugat de mine, nu-i în Product Group sheet) -> prefix magazin. Restul = grup-TIP (sheet).
PREFIX_GROUP = {"NUB": "Nubra", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL", "BON": "Bonhaus RO",
                "MAG": "Magdeal", "ROSSI": "Rossi"}


def wms_sku_marketing(pf_conn, metrics_cur, lo, hi):
    """{sku_upper: marketing_ron} per-SKU din WMS. Grup-BRAND -> SKU vândute în magazinul lui (prefix), pe qty;
    grup-TIP -> SKU din Product Group, pe qty total. Un SKU partajat primește din fiecare grup (cont) care-l rulează."""
    from collections import defaultdict
    group_spend = wms_group_spend_ron(pf_conn, metrics_cur, lo, hi)
    month = lo[:7]
    # qty per (prefix, sku) și total, pe luna ferestrei
    qps = defaultdict(float); qtot = defaultdict(float)
    for prefix, sku, qty in pf_conn.execute(
        "SELECT prefix, sku, SUM(qty) FROM profit_order_lines WHERE month=? AND sku IS NOT NULL AND sku<>'' GROUP BY prefix, sku", (month,)):
        s = (sku or "").strip().upper()
        qps[((prefix or "").strip(), s)] += (qty or 0); qtot[s] += (qty or 0)
    brand_groups = set(PREFIX_GROUP.values())
    # group -> [(sku, weight)]
    members = defaultdict(list)
    for grp in group_spend:
        pfx = next((p for p, g in PREFIX_GROUP.items() if g == grp), None)
        if pfx:  # grup-BRAND: SKU vândute în prefix, weight = qty în prefix
            for (p, sku), q in qps.items():
                if p == pfx and q > 0:
                    members[grp].append((sku, q))
    # grup-TIP din sheet (toate care NU-s brand)
    sheet = defaultdict(list)
    for sku, grp in pf_conn.execute("SELECT sku, grp FROM wms_product_group"):
        if sku and (grp or "").strip():
            sheet[grp].append((sku or "").strip().upper())
    for grp in group_spend:
        if grp in brand_groups:
            continue
        for sku in sheet.get(grp, []):
            if qtot.get(sku, 0) > 0:
                members[grp].append((sku, qtot[sku]))
    out = defaultdict(float)
    for grp, S in group_spend.items():
        mm = members.get(grp, [])
        tw = sum(w for _, w in mm)
        if tw <= 0:
            continue
        for sku, w in mm:
            out[sku] += S * w / tw
    return dict(out)


if __name__ == "__main__":
    import os, re, sqlite3, psycopg2, sys
    def cl(d):
        d = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", d)
        return re.sub(r"[?&]+(&|$)", r"\1", d).rstrip("?&")
    lo, hi = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else ("2026-06-19", "2026-06-22")
    pf = sqlite3.connect("/root/Scripturi/data/profitability.db")
    mc = psycopg2.connect(cl(os.environ["DATABASE_URL_METRICS"])).cursor()
    g = wms_group_spend_ron(pf, mc, lo, hi)
    print("WMS group spend RON %s..%s: total=%.0f, %d grupuri" % (lo, hi, sum(g.values()), len(g)))
    sku_mk = wms_sku_marketing(pf, mc, lo, hi)
    print("\nPER-SKU: total alocat=%.0f RON pe %d SKU (reconciliere cu grupul: %.1f%%)" % (
        sum(sku_mk.values()), len(sku_mk), 100 * sum(sku_mk.values()) / sum(g.values()) if g else 0))
    # Nubra: cât s-a împrăștiat
    nub = [(s, v) for s, v in sku_mk.items() if v > 0]
    nub_skus = [r[0] for r in pf.execute("SELECT DISTINCT sku FROM profit_order_lines WHERE prefix='NUB'")]
    nub_in = [(s, v) for s, v in sku_mk.items() if s in set((x or '').upper() for x in nub_skus)]
    print("  Nubra (grup %.0f RON) -> împrăștiat pe %d SKU; top 5:" % (g.get("Nubra", 0), len(nub_in)))
    for s, v in sorted(nub_in, key=lambda x: -x[1])[:5]:
        print("     %-16s %.0f RON" % (s, v))
