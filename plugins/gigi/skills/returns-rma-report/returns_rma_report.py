# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
returns_rma_report.py — Analiza retururilor & schimburilor Grandia (rma_requests).

Raspunde la: cate retururi sunt deschise, ce RMA-uri stau blocate la AWAITING_REFUND,
cat s-a rambursat luna asta, de ce returneaza oamenii, ce produse/SKU se returneaza cel
mai des.

Surse (DB Grandia, READ-ONLY, doar SELECT):
  rma_requests(status,type,reason,"refundAmount","paidAmount","orderName","orderId",
               "requestNumber","customerName","createdAt","sentToPaymentAt","paidAt",...)
  -> "Order"(id) -> "OrderLineItem"(orderId, sku, title, productId) -> "Product"(shopifyGid)

Moduri:
  uv run returns_rma_report.py                 # toate sectiunile (default)
  uv run returns_rma_report.py --pipeline      # RMA-uri deschise pe status + zile in status
  uv run returns_rma_report.py --reasons       # motive (count + RON rambursat), RETURN vs EXCHANGE
  uv run returns_rma_report.py --products      # top produse/SKU returnate
  uv run returns_rma_report.py --month 2026-06 # filtreaza pe luna (createdAt)
  uv run returns_rma_report.py --sla 7         # prag zile pt AWAITING_REFUND blocat (default 7)
  uv run returns_rma_report.py --limit 15
"""
import os, sys, subprocess, argparse, urllib.parse
import pg8000.dbapi

# statusuri "deschise" = RMA inca in lucru (nu inchis/anulat)
OPEN_STATUSES = ("NEW", "IN_PROGRESS", "DELIVERED", "AWAITING_REFUND")


def get_conn():
    url = os.environ.get("DATABASE_URL_GRANDIA")
    if not url:
        kb = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "..", "core", "scripts", "kb.py")
        url = subprocess.run(["uv", "run", kb, "secret-get", "DATABASE_URL_GRANDIA"],
                             capture_output=True, text=True).stdout.strip()
    if not url:
        sys.exit("EROARE: nu am putut obtine DATABASE_URL_GRANDIA (env sau KB).")
    u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(
        ssl_context=True,
        user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""),
        host=u.hostname, port=u.port or 5432,
        database=(u.path or "/").lstrip("/"))


def month_clause(month):
    """Returneaza (sql_fragment, params) ce filtreaza pe createdAt in luna data, sau ('', [])."""
    if not month:
        return "", []
    return ' AND "createdAt" >= %s::date AND "createdAt" < (%s::date + INTERVAL \'1 month\')', [month + "-01", month + "-01"]


def ron(n):
    return "{:,.0f}".format(float(n or 0))


# ----------------------------------------------------------------------------
def pipeline(cur, month, sla):
    mc, mp = month_clause(month)
    print("=== PIPELINE RMA DESCHISE %s===" % (("(" + month + ") ") if month else ""))
    # status breakdown + varsta
    cur.execute(
        'SELECT status::text, COUNT(*), '
        'COALESCE(SUM("refundAmount"),0), '
        'EXTRACT(DAY FROM now()-MIN("createdAt"))::int, '
        'EXTRACT(DAY FROM now()-MAX("createdAt"))::int, '
        'ROUND(AVG(EXTRACT(DAY FROM now()-"createdAt"))::numeric,1) '
        'FROM rma_requests WHERE status = ANY(%s)' + mc +
        ' GROUP BY status ORDER BY COUNT(*) DESC',
        [list(OPEN_STATUSES)] + mp)
    rows = cur.fetchall()
    if not rows:
        print("  Niciun RMA deschis.")
    else:
        print("  %-16s%6s%14s%9s%9s%9s" % ("status", "nr", "RON_refund", "max_zile", "min_zile", "med_zile"))
        tot_n = tot_r = 0
        for st, n, r, mx, mn, avg in rows:
            print("  %-16s%6d%14s%9s%9s%9s" % (st, n, ron(r), mx, mn, avg))
            tot_n += n; tot_r += float(r or 0)
        print("  %-16s%6d%14s" % ("TOTAL deschise", tot_n, ron(tot_r)))

    # AWAITING_REFUND blocate peste prag SLA — cele mai vechi
    print("\n--- AWAITING_REFUND blocate > %d zile (cele mai vechi) ---" % sla)
    cur.execute(
        'SELECT "requestNumber", "orderName", reason, "refundAmount", '
        'EXTRACT(DAY FROM now()-"createdAt")::int AS age, '
        '("sentToPaymentAt" IS NOT NULL) AS sent '
        'FROM rma_requests WHERE status=\'AWAITING_REFUND\'' + mc +
        ' ORDER BY "createdAt" ASC', mp)
    aw = cur.fetchall()
    stuck = [x for x in aw if (x[4] or 0) >= sla]
    if not aw:
        print("  Niciun RMA in AWAITING_REFUND.")
    else:
        flagged = stuck if stuck else aw
        tag = "BLOCAT" if stuck else "(niciun blocaj peste prag — afisez toate)"
        print("  %s — %d RMA, total de rambursat %s RON" % (tag, len(flagged), ron(sum(float(x[3] or 0) for x in flagged))))
        print("  %-10s%-13s%-18s%10s%6s%7s" % ("req", "comanda", "motiv", "RON", "zile", "trimis"))
        for req, oname, reason, ref, age, sent in flagged:
            print("  %-10s%-13s%-18s%10s%6s%7s" % (
                req or "-", oname or "-", (reason or "-")[:17], ron(ref), age, "DA" if sent else "nu"))


# ----------------------------------------------------------------------------
def reasons(cur, month, limit):
    mc, mp = month_clause(month)
    print("\n=== MOTIVE RETUR %s===" % (("(" + month + ") ") if month else ""))
    # RETURN vs EXCHANGE total
    cur.execute(
        'SELECT type::text, COUNT(*), COALESCE(SUM("refundAmount"),0) '
        'FROM rma_requests WHERE 1=1' + mc + ' GROUP BY type ORDER BY COUNT(*) DESC', mp)
    for t, n, r in cur.fetchall():
        print("  %-10s nr=%-4d  RON_rambursat=%s" % (t, n, ron(r)))
    print("  %-18s%8s%8s%14s" % ("motiv", "total", "din_care", "RON_refund"))
    print("  %-18s%8s%8s%14s" % ("", "nr", "RET/EXC", ""))
    cur.execute(
        'SELECT reason, COUNT(*), '
        'SUM(CASE WHEN type=\'RETURN\' THEN 1 ELSE 0 END), '
        'SUM(CASE WHEN type=\'EXCHANGE\' THEN 1 ELSE 0 END), '
        'COALESCE(SUM("refundAmount"),0) '
        'FROM rma_requests WHERE 1=1' + mc +
        ' GROUP BY reason ORDER BY COUNT(*) DESC LIMIT %s', mp + [limit])
    rows = cur.fetchall()
    if not rows:
        print("  (niciun RMA in interval)")
    for reason, n, ret, exc, ref in rows:
        print("  %-18s%8d%8s%14s" % ((reason or "-")[:17], n, "%d/%d" % (ret, exc), ron(ref)))


# ----------------------------------------------------------------------------
def products(cur, month, limit):
    mc, mp = month_clause(month)
    print("\n=== TOP PRODUSE / SKU RETURNATE %s===" % (("(" + month + ") ") if month else ""))
    print("  (nr = de cate ori SKU-ul apare pe comanda unui RMA; o comanda poate avea mai multe linii)")
    cur.execute(
        'SELECT COALESCE(oli.sku,\'(fara SKU)\') AS sku, '
        '       MAX(oli.title) AS title, '
        '       MAX(p."productType") AS pt, '
        '       COUNT(DISTINCT r.id) AS rma_count, '
        '       COALESCE(SUM(CASE WHEN r.type=\'RETURN\' THEN 1 ELSE 0 END),0) AS ret, '
        '       COALESCE(SUM(CASE WHEN r.type=\'EXCHANGE\' THEN 1 ELSE 0 END),0) AS exc '
        'FROM rma_requests r '
        'JOIN "OrderLineItem" oli ON oli."orderId" = r."orderId" '
        'LEFT JOIN "Product" p ON p."shopifyGid" = oli."productId" '
        'WHERE 1=1' + mc.replace('"createdAt"', 'r."createdAt"') +
        ' GROUP BY oli.sku ORDER BY rma_count DESC, ret DESC LIMIT %s', mp + [limit])
    rows = cur.fetchall()
    if not rows:
        print("  (niciun produs in interval)")
        return
    print("  %-16s%-40s%6s%9s" % ("SKU", "produs", "nr", "RET/EXC"))
    for sku, title, pt, cnt, ret, exc in rows:
        print("  %-16s%-40s%6d%9s" % (
            (sku or "-")[:15], (title or "-")[:39], cnt, "%d/%d" % (ret, exc)))


# ----------------------------------------------------------------------------
def refunds_summary(cur, month):
    mc, mp = month_clause(month)
    lbl = ("luna " + month) if month else "tot istoricul"
    cur.execute(
        'SELECT COUNT(*) FILTER (WHERE "paidAt" IS NOT NULL), '
        '       COALESCE(SUM("paidAmount") FILTER (WHERE "paidAt" IS NOT NULL),0), '
        '       COUNT(*) FILTER (WHERE status=\'AWAITING_REFUND\'), '
        '       COALESCE(SUM("refundAmount") FILTER (WHERE status=\'AWAITING_REFUND\'),0) '
        'FROM rma_requests WHERE 1=1' + mc, mp)
    paid_n, paid_sum, aw_n, aw_sum = cur.fetchone()
    print("\n=== RAMBURSARI (%s) ===" % lbl)
    print("  Rambursat (paidAt): nr=%d  total=%s RON" % (paid_n, ron(paid_sum)))
    print("  De rambursat (AWAITING_REFUND): nr=%d  total=%s RON" % (aw_n, ron(aw_sum)))


# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Analiza retururi/RMA Grandia (read-only).")
    ap.add_argument("--pipeline", action="store_true", help="RMA deschise pe status + blocaje AWAITING_REFUND")
    ap.add_argument("--reasons", action="store_true", help="motive de retur (count + RON), RETURN vs EXCHANGE")
    ap.add_argument("--products", action="store_true", help="top produse/SKU returnate")
    ap.add_argument("--month", help="filtru luna YYYY-MM (pe createdAt)")
    ap.add_argument("--sla", type=int, default=7, help="prag zile pt AWAITING_REFUND blocat (default 7)")
    ap.add_argument("--limit", type=int, default=15, help="nr randuri in reasons/products")
    a = ap.parse_args()

    if a.month:
        try:
            import datetime
            datetime.datetime.strptime(a.month, "%Y-%m")
        except ValueError:
            sys.exit("EROARE: --month trebuie format YYYY-MM (ex 2026-06).")

    show_all = not (a.pipeline or a.reasons or a.products)
    conn = get_conn()
    cur = conn.cursor()
    try:
        if show_all or a.pipeline:
            pipeline(cur, a.month, a.sla)
            refunds_summary(cur, a.month)
        if show_all or a.reasons:
            reasons(cur, a.month, a.limit)
        if show_all or a.products:
            products(cur, a.month, a.limit)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
