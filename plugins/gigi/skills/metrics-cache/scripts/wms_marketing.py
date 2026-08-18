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
            key[plat].append((_norm(pat), grp))
    for plat in key:   # cel mai LUNG keyword (cel mai specific) câștigă — „COVORAȘ MAGIC" bate „MAGDEAL"
        key[plat].sort(key=lambda x: -len(x[0]))
    return acc, key


_DIAC = str.maketrans("ĂÂÎȘŞȚŢăâîșşțţ", "AAISSTTAAISSTT")
def _norm(s):
    """upper + FĂRĂ diacritice (Nomenclatorul are „LAVETA ABRAZIVĂ", campania „LAVETA ABRAZIVA")."""
    return (s or "").translate(_DIAC).upper().strip()


def _group_of(acc, key, plat, account, campaign, ad_name=""):
    a = (account or "").strip().lower()
    c = _norm((campaign or "") + " " + (ad_name or ""))   # campanie + AD NAME (FB), insensibil la diacritice
    # CAMPAIGN_KEYWORD prioritar (per-produs, cel mai lung/specific), apoi ACCOUNT (fallback brand)
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
    for src, date, account, campaign, ad, spend in pf_conn.execute(
        "SELECT source,date,account,campaign,ad_name,spend_usd FROM wms_ad_spend WHERE date>=? AND date<=?", (lo, hi)):
        g = _group_of(acc, key, src, account, campaign, ad)
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


# grup-BRAND (single-categorie, nu-s în Product Group sheet) -> prefix magazin. Restul = grup-TIP (sheet).
# Magdeal NU e aici (Esteban 3/Reflexino = per-produs via keyword). {HA}/{HAA} în campanii = tag AGENȚIE.
PREFIX_GROUP = {"NUB": "Nubra", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL", "BON": "Bonhaus RO", "ROSSI": "Rossi"}

# Grup special „DAY:<PREFIX>" = campanie MULTI-PRODUS (catalog / „ALL PRODUCT" / cont devenit deals):
# spend-ul fiecărei ZILE se împarte pe produsele care au avut comenzi ÎN ZIUA ACEEA, în magazinul <PREFIX>.
# De ce zilnic și nu lunar: magazinele deals rotesc produsele des, iar o medie lunară pune spend pe
# produse care nici nu rulau în ziua aia. Greutatea = nr. de COMENZI (o comandă = 1 achiziție), ca
# în restul motorului. NB: `profit_order_lines` n-are dată (doar lună) ⇒ sursa zilnică e `profit_orders`.
# `DAY:AUTO` = același lucru, dar magazinul se deduce din CONT (harta `wms_account_store`, seed-uită
# o dată din maparea autoritativă). Necesar fiindcă ACEEAȘI campanie catalog („PRODUCT LAUNCH ALL
# PRODUCT") rulează pe 4 conturi = 4 magazine, iar un keyword duce la un singur grup.
DAY_PREFIX = "DAY:"
DAY_AUTO = "DAY:AUTO"


def _account_store_map(pf_conn):
    """{(platform, cont_lower, campanie): prefix} — harta noastră în DB, FĂRĂ dependență de sheet.
    Cheia include CAMPANIA fiindcă există conturi PARTAJATE între magazine (ex. 'ROSSI Nails Romania'
    = GT + Magdeal + Reduceri + Covoria): o hartă doar per cont ar atribui tot contul magazinului
    dominant. Rândul cu campanie='' e fallback și e scris DOAR pentru conturile dedicate."""
    try:
        return {(p, a, c): pfx for p, a, c, pfx in pf_conn.execute(
            "SELECT platform, account, campaign, prefix FROM wms_account_store")}
    except Exception:
        return {}


def _daily_order_weights(pf_conn, lo, hi):
    """{(zi, prefix): {SKU: nr_comenzi}} — produsele cu comenzi în ziua respectivă, per magazin."""
    from collections import defaultdict
    w = defaultdict(lambda: defaultdict(float))
    for d, prefix, skus in pf_conn.execute(
            "SELECT substr(created_at,1,10), prefix, skus FROM profit_orders "
            "WHERE substr(created_at,1,10)>=? AND substr(created_at,1,10)<=?", (lo, hi)):
        if not skus:
            continue
        for s in {x.strip().upper() for x in str(skus).split(",") if x.strip()}:
            w[(d, (prefix or "").strip())][s] += 1
    return w
import re as _re
_SKU_IN_CAMP = _re.compile(r"HA-\d{3,5}")   # cod SKU HA-#### în numele campaniei (ex. „...ROATA ABDOMINALĂ HA-0420-")


def wms_sku_marketing(pf_conn, metrics_cur, lo, hi):
    """{sku_upper: marketing_ron} per-SKU din WMS. PRIORITATE: (0) cod SKU EXACT în campanie (HA-####, validat
    că e SKU vândut) → direct pe SKU; altfel (1) keyword campanie → grup, (2) cont → grup-brand → alocat pe
    COMENZI (qty). Grupul Test exclus. Grup-brand → SKU din magazin (prefix); grup-tip → SKU din Product Group."""
    from collections import defaultdict
    fx = _load_fx(metrics_cur)
    acc, key = _load_nomen(pf_conn)
    month = lo[:7]
    qps = defaultdict(float); qtot = defaultdict(float)
    for prefix, sku, qty in pf_conn.execute(
        "SELECT prefix, sku, SUM(qty) FROM profit_order_lines WHERE month=? AND sku IS NOT NULL AND sku<>'' GROUP BY prefix, sku", (month,)):
        s = (sku or "").strip().upper()
        qps[((prefix or "").strip(), s)] += (qty or 0); qtot[s] += (qty or 0)
    ha_skus = set(s.strip().upper() for (s,) in pf_conn.execute("SELECT DISTINCT sku FROM profit_order_lines WHERE sku LIKE 'HA-%'"))
    dayw = _daily_order_weights(pf_conn, lo, hi)
    acct_store = _account_store_map(pf_conn)
    out = defaultdict(float); group_spend = defaultdict(float); unspread = defaultdict(float)
    for src, date, account, campaign, ad, spend in pf_conn.execute(
        "SELECT source,date,account,campaign,ad_name,spend_usd FROM wms_ad_spend WHERE date>=? AND date<=?", (lo, hi)):
        ron = (spend or 0) * _usd_ron(fx, date)
        if ron <= 0:
            continue
        exact = next((m for m in _SKU_IN_CAMP.findall(((campaign or "") + " " + (ad or "")).upper()) if m in ha_skus), None)
        if exact:                                       # (0) cod SKU exact în campanie/ad → direct pe SKU
            out[exact] += ron
            continue
        g = _group_of(acc, key, src, account, campaign, ad)
        if not g or g.strip().lower() == "test":
            continue
        if g.startswith(DAY_PREFIX):                    # multi-produs → împarte pe comenzile ZILEI
            if g.strip().upper() == DAY_AUTO:           # magazinul vine din CONT+CAMPANIE
                a_l = (account or "").strip().lower()
                pfx = (acct_store.get((src, a_l, (campaign or "").strip()))
                       or acct_store.get((src, a_l, "")) or "")
                if not pfx:                             # cont partajat/necunoscut → NU ghicim
                    unspread["DAY:AUTO(nerezolvat: %s)" % (account or "?")] += ron
                    continue
            else:
                pfx = g[len(DAY_PREFIX):].strip()
            wts = dict(dayw.get((date, pfx)) or {})
            if not wts:                                 # ziua n-are comenzi în magazin → cade pe luna
                wts = {s: q for (p, s), q in qps.items() if p == pfx and q > 0}
            tw = sum(wts.values())
            if tw <= 0:                                 # nici lunar nimic → NU pierdem tăcut
                unspread[g] += ron
                continue
            for sku, w in wts.items():
                out[sku] += ron * w / tw
            continue
        group_spend[g] += ron                           # (1) keyword / (2) cont → grup
    # alocare grup → SKU pe comenzi
    brand_groups = set(PREFIX_GROUP.values())
    members = defaultdict(list)
    for grp in group_spend:
        pfx = next((p for p, g in PREFIX_GROUP.items() if g == grp), None)
        if pfx:  # grup-BRAND: SKU vândute în prefix, weight = qty în prefix
            for (p, sku), q in qps.items():
                if p == pfx and q > 0:
                    members[grp].append((sku, q))
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
    for grp, S in group_spend.items():
        mm = members.get(grp, [])
        tw = sum(w for _, w in mm)
        if tw <= 0:
            continue
        for sku, w in mm:
            out[sku] += S * w / tw
    if unspread:
        import sys as _sys
        _sys.stderr.write("[wms_marketing] ⚠ %.0f RON NEîmprăștiați (zile fără comenzi în magazin): %s\n"
                          % (sum(unspread.values()), ", ".join(sorted(unspread))))
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
