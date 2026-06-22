# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
product_quality_radar.py — ce PRODUSE generează refund-uri și retururi, CU MOTIV.

Semnal de CALITATE per produs, din DOUĂ circuite independente:
  (a) REFUND-uri Shopify  — metrics.orders (totalRefunded>0) x order_line_items (sku, quantity)
                            x brands(name). Toate brandurile. → nr comenzi cu refund + RON + buc.
  (b) RETURURI RMA Grandia — grandia.rma_request_items (sku, quantity) x rma_requests
                            (reason, reasonNote, refundAmount, type). MOTIVUL agregat
                            (de ce se returnează: poor_quality / defective / damaged /
                             not_as_described / wrong_product / missing_parts / other)
                            + RATA DE DEFECT per SKU (retururi / comenzi vândute, numitor
                            din AWBprint — istoric complet, NU metrics care e truncat la 19-apr
                            și supraestima defect-rate ~38%).
  Cross-validare: SKU-uri care apar în AMBELE circuite = problemă confirmată.

Output: top produse-problemă cu motiv + cifre + recomandare CS/calitate
(verifică descriere, scoate de pe COD, fix furnizor).

READ-ONLY (doar SELECT). Sume brute în RON, cum sunt în DB.

Moduri:
  uv run product_quality_radar.py                 # sumar: refund Shopify + retururi RMA + cross-validare
  uv run product_quality_radar.py --store Grandia # filtrează refund-urile Shopify pe un brand
  uv run product_quality_radar.py --reason poor_quality   # doar SKU-uri returnate cu un motiv
  uv run product_quality_radar.py --limit 25
  uv run product_quality_radar.py --json          # pt automatizare
"""
import os, sys, json, subprocess, argparse, urllib.parse
import pg8000.dbapi

HERE = os.path.dirname(os.path.abspath(__file__))

# motivele RMA (enum reason din rma_requests) → etichetă RO + dacă e semnal de CALITATE
REASON_LABEL = {
    "poor_quality":    ("calitate slabă",        True),
    "defective":       ("defect",                True),
    "damaged":         ("deteriorat la livrare", True),   # mai degrabă transport/ambalare
    "not_as_described":("nu corespunde descrierii", True),
    "wrong_product":   ("produs greșit trimis",   False),  # eroare de fulfillment, nu calitate
    "missing_parts":   ("piese lipsă",            True),
    "other":           ("altul",                  False),
}
# motive care indică o problemă REALĂ de produs/descriere (nu eroare logistică pură)
QUALITY_REASONS = {k for k, (_l, q) in REASON_LABEL.items() if q}


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k],
                          capture_output=True, text=True).stdout.strip()


def connect(url_key):
    url = secret(url_key)
    if not url:
        sys.exit("EROARE: nu am putut obține %s (env sau KB)." % url_key)
    u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(
        ssl_context=True,
        user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""),
        host=u.hostname, port=u.port or 5432,
        database=(u.path or "/").lstrip("/"))


def ron(n):
    return "{:,.0f}".format(float(n or 0))


def pct(n, d):
    return (100.0 * float(n or 0) / float(d)) if d else 0.0


# ---------------------------------------------------------------------------
# (a) REFUND-uri Shopify din metrics — toate brandurile (sau filtrate pe --store)
def shopify_refunds(store, limit):
    conn = connect("DATABASE_URL_METRICS")
    cur = conn.cursor()
    where = 'o."totalRefunded" > 0'
    params = []
    if store:
        where += " AND b.name ILIKE %s"
        params.append("%" + store + "%")
    # totalRefunded e o sumă pe COMANDĂ; dacă o comandă are mai multe linii cu același SKU
    # nu vrem s-o numărăm de 2 ori. Pre-agregăm buc/comandă pe (order, brand, sku), apoi
    # însumăm refund-ul O DATĂ per comandă (MAX peste rândul comenzii = valoarea unică).
    cur.execute(
        'SELECT brand, sku, MAX(title) AS title, '
        '       COUNT(*) AS orders, '
        '       SUM(qty) AS qty, '
        '       ROUND(SUM(refund))::numeric AS refund '
        'FROM ( '
        '  SELECT b.name AS brand, oli.sku AS sku, MAX(oli.title) AS title, '
        '         o.id AS oid, SUM(oli.quantity) AS qty, MAX(o."totalRefunded") AS refund '
        '  FROM orders o '
        '  JOIN order_line_items oli ON oli."orderId" = o.id '
        '  JOIN brands b ON b.id = o."brandId" '
        '  WHERE ' + where +
        '  GROUP BY b.name, oli.sku, o.id '
        ') per_order '
        'GROUP BY brand, sku ORDER BY orders DESC, refund DESC '
        'LIMIT %s', params + [limit])
    rows = [{"brand": r[0], "sku": r[1], "title": r[2], "orders": int(r[3]),
             "qty": int(r[4]), "refund_ron": float(r[5] or 0)} for r in cur.fetchall()]
    conn.close()
    return rows


# numitor pt rata de defect: câte comenzi a vândut fiecare SKU (Grandia).
# SURSĂ = AWBprint (DB AWB/Frisbo), NU metrics: metrics.orders pt Grandia e TRUNCAT (începe ~19-apr-2026),
# pe când AWBprint are istoricul complet (din nov-2025) → metrics sub-numără vânzările vechi și
# SUPRAESTIMEAZĂ defect-rate ~38% (poate flipa decizia „scot de pe COD"). SKU la inventory_item.sku.
def sold_denominator(brand, skus):
    if not skus:
        return {}
    conn = connect("DATABASE_URL_AWBPRINT")
    cur = conn.cursor()
    ph = ",".join(["%s"] * len(skus))
    cur.execute(
        "SELECT lower(it->'inventory_item'->>'sku') AS sku, COUNT(DISTINCT o.id), "
        "       COALESCE(SUM((it->>'quantity')::numeric),0) "
        "FROM orders o JOIN stores s ON s.uid = o.store_uid "
        "CROSS JOIN LATERAL jsonb_array_elements(o.line_items::jsonb) AS it "
        "WHERE s.name = 'grandia.ro' AND lower(it->'inventory_item'->>'sku') IN (" + ph + ") "
        "GROUP BY 1", [str(s).lower() for s in skus])
    by_lower = {}
    for sku, orders, qty in cur.fetchall():
        by_lower[sku] = {"sold_orders": int(orders), "sold_qty": int(qty or 0)}
    conn.close()
    # cheile = SKU-ul original (case-ul din RMA); potrivire case-insensitive cu AWBprint
    return {s: by_lower.get(str(s).lower(), {"sold_orders": 0, "sold_qty": 0}) for s in skus}


# ---------------------------------------------------------------------------
# (b) RETURURI RMA Grandia — per SKU, cu motivul agregat
def rma_returns(reason_filter, limit):
    conn = connect("DATABASE_URL_GRANDIA")
    cur = conn.cursor()
    rf = ""
    params = []
    if reason_filter:
        rf = " AND r.reason = %s"
        params.append(reason_filter)
    cur.execute(
        'SELECT i.sku, MAX(i.title), '
        '       COUNT(DISTINCT r.id) AS rma_count, '
        '       COALESCE(SUM(i.quantity),0) AS qty, '
        '       COALESCE(SUM(r."refundAmount"),0) AS refund '
        'FROM rma_request_items i '
        'JOIN rma_requests r ON r.id = i."requestId" '
        'WHERE i.sku IS NOT NULL' + rf +
        ' GROUP BY i.sku ORDER BY rma_count DESC, refund DESC LIMIT %s',
        params + [limit])
    base = cur.fetchall()
    skus = [b[0] for b in base]
    # breakdown motiv per SKU (toate motivele, indiferent de filtru — ca să vezi MIXUL)
    reasons_by_sku = {}
    if skus:
        ph = ",".join(["%s"] * len(skus))
        cur.execute(
            'SELECT i.sku, r.reason, COUNT(DISTINCT r.id) '
            'FROM rma_request_items i JOIN rma_requests r ON r.id=i."requestId" '
            'WHERE i.sku IN (' + ph + ') GROUP BY i.sku, r.reason', list(skus))
        for sku, reason, n in cur.fetchall():
            reasons_by_sku.setdefault(sku, {})[reason or "other"] = int(n)
        # o notă liberă reprezentativă (reasonNote) per SKU, dacă există
        cur.execute(
            'SELECT DISTINCT ON (i.sku) i.sku, r."reasonNote" '
            'FROM rma_request_items i JOIN rma_requests r ON r.id=i."requestId" '
            'WHERE i.sku IN (' + ph + ') AND r."reasonNote" IS NOT NULL '
            "AND btrim(r.\"reasonNote\") <> '' ORDER BY i.sku, r.\"createdAt\" DESC", list(skus))
        notes = {row[0]: row[1] for row in cur.fetchall()}
    else:
        notes = {}
    conn.close()
    rows = []
    for sku, title, cnt, qty, refund in base:
        rows.append({"sku": sku, "title": title, "rma_count": int(cnt),
                     "qty": int(qty), "refund_ron": float(refund or 0),
                     "reasons": reasons_by_sku.get(sku, {}), "note": notes.get(sku)})
    return rows


def top_reason(reasons):
    """(cod_motiv, nr) cel mai frecvent."""
    if not reasons:
        return (None, 0)
    k = max(reasons, key=lambda x: reasons[x])
    return (k, reasons[k])


def reason_str(reasons):
    parts = []
    for k, n in sorted(reasons.items(), key=lambda x: -x[1]):
        lbl = REASON_LABEL.get(k, (k, False))[0]
        parts.append("%s x%d" % (lbl, n))
    return ", ".join(parts)


def recommend(rma_row, defect_rate):
    """Recomandare CS/calitate în funcție de motivul dominant + rata de defect."""
    top, _ = top_reason(rma_row["reasons"])
    recs = []
    if top in ("not_as_described",):
        recs.append("verifică DESCRIEREA/foto (clientul așteaptă altceva)")
    if top in ("poor_quality", "defective"):
        recs.append("problemă de CALITATE — verifică lotul / schimbă furnizorul")
    if top == "damaged":
        recs.append("AMBALARE/transport — întărește ambalajul, verifică curier")
    if top == "missing_parts":
        recs.append("KIT incomplet — verifică ce pune furnizorul în cutie")
    if top == "wrong_product":
        recs.append("eroare de FULFILLMENT — verifică maparea SKU în depozit")
    if defect_rate is not None and defect_rate >= 15:
        recs.append("rată retur %.0f%% — candidat de SCOS de pe COD / depublicat" % defect_rate)
    return "; ".join(recs) or "monitorizează"


# ---------------------------------------------------------------------------
def run(a):
    refunds = shopify_refunds(a.store, a.limit if not a.reason else 200)
    rma = rma_returns(a.reason, a.limit)

    # numitor defect-rate pt SKU-urile RMA (Grandia)
    rma_skus = [r["sku"] for r in rma]
    denom = sold_denominator("Grandia", rma_skus)
    for r in rma:
        d = denom.get(r["sku"], {})
        so = d.get("sold_orders", 0)
        r["sold_orders"] = so
        r["defect_rate"] = (pct(r["rma_count"], so) if so else None)

    # cross-validare: SKU în AMBELE circuite
    refund_skus = {r["sku"] for r in refunds if r["sku"]}
    rma_sku_set = {r["sku"] for r in rma}
    both = sorted(refund_skus & rma_sku_set)

    if a.json:
        print(json.dumps({
            "shopify_refunds": refunds,
            "rma_returns": rma,
            "cross_validated_skus": both,
        }, ensure_ascii=False, default=str))
        return

    # ---- (a) REFUND-uri Shopify ----
    title = "REFUND-uri Shopify" + ((" — " + a.store) if a.store else " (toate brandurile)")
    print("=== %s ===" % title)
    print("(nr = comenzi cu refund care conțin SKU-ul; RON = totalRefunded pe acele comenzi)")
    print("%-16s %-11s %5s %5s %9s  %s" % ("SKU", "brand", "nr", "buc", "RON", "produs"))
    print("-" * 100)
    for r in refunds[:a.limit]:
        print("%-16s %-11s %5d %5d %9s  %s" % (
            (r["sku"] or "—")[:16], (r["brand"] or "—")[:11], r["orders"], r["qty"],
            ron(r["refund_ron"]), (r["title"] or "—")[:42]))

    # ---- (b) RETURURI RMA cu MOTIV ----
    print("\n=== RETURURI RMA Grandia — CU MOTIV + rată de defect ===")
    if a.reason:
        print("(filtrat pe motiv: %s — dar mixul de motive arată toate motivele SKU-ului)"
              % REASON_LABEL.get(a.reason, (a.reason,))[0])
    print("(rata = RMA-uri / comenzi vândute SKU; >=15%% = semnal puternic)")
    for r in rma[:a.limit]:
        dr = r["defect_rate"]
        dr_s = ("%.0f%% (%d/%d)" % (dr, r["rma_count"], r["sold_orders"])) if dr is not None else "n/a"
        print("\n• %-15s %s" % (r["sku"], (r["title"] or "—")[:60]))
        print("    retururi: %d  buc: %d  rambursat: %s RON  | rată defect: %s"
              % (r["rma_count"], r["qty"], ron(r["refund_ron"]), dr_s))
        print("    motive: %s" % (reason_str(r["reasons"]) or "—"))
        if r.get("note"):
            print("    notă client: \"%s\"" % str(r["note"])[:90])
        print("    → %s" % recommend(r, dr))

    # ---- Cross-validare ----
    print("\n=== CROSS-VALIDARE — SKU-uri în AMBELE circuite (refund Shopify + retur RMA) ===")
    print("(problemă CONFIRMATĂ din două surse independente)")
    if not both:
        print("  (niciun SKU comun în top-urile afișate — mărește --limit)")
    else:
        rmap = {r["sku"]: r for r in refunds}
        amap = {r["sku"]: r for r in rma}
        for sku in both:
            f = rmap.get(sku, {})
            m = amap.get(sku, {})
            tr, _ = top_reason(m.get("reasons", {}))
            trl = REASON_LABEL.get(tr, (tr or "?",))[0]
            dr = m.get("defect_rate")
            dr_s = ("%.0f%%" % dr) if dr is not None else "n/a"
            print("  • %-15s refund: %d cmd / %s RON | RMA: %d (%s) | rată %s | %s"
                  % (sku, f.get("orders", 0), ron(f.get("refund_ron", 0)),
                     m.get("rma_count", 0), trl, dr_s, (m.get("title") or "")[:30]))

    print("\nRecomandare generală: produsele cu motiv 'nu corespunde descrierii' → "
          "fix la pagina de produs (foto/specs); 'calitate slabă/defect' → fix furnizor / "
          "scoate de pe COD; rată retur mare → depublică sau doar plată card.")


def main():
    ap = argparse.ArgumentParser(description="Radar de calitate per produs: refund-uri Shopify + retururi RMA cu motiv (read-only).")
    ap.add_argument("--store", default="", help="filtrează refund-urile Shopify pe un brand (ex: Grandia, Esteban)")
    ap.add_argument("--reason", default="", help="filtrează retururile RMA pe un motiv (%s)" % "/".join(REASON_LABEL))
    ap.add_argument("--limit", type=int, default=15, help="nr rânduri per secțiune (default 15)")
    ap.add_argument("--json", action="store_true", help="output JSON pt automatizare")
    a = ap.parse_args()
    if a.reason and a.reason not in REASON_LABEL:
        sys.exit("EROARE: --reason trebuie să fie unul din: %s" % ", ".join(REASON_LABEL))
    run(a)


if __name__ == "__main__":
    main()
