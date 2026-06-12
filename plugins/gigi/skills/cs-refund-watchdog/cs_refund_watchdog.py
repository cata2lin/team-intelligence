# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
cs_refund_watchdog.py — REFUND-uri PROMISE dar NEEFECTUATE.
Cel mai scump bug de Customer Service: clientul a dat banii (sau i s-a promis returul),
banii nu s-au întors -> risc ANPC / chargeback / recenzie 1 stea.

Două surse, ambele READ-ONLY:

  FAZA 1 — metrics.orders (toate magazinele):
    Comandă plătită cu CARDUL ('shopify_payments' în paymentGatewayNames),
    ANULATĂ (cancelledAt not null), dar încă PLĂTITĂ (financialStatus='PAID')
    și totalRefunded=0  ->  bani luați pe card, comanda anulată, NIMIC returnat.

  FAZA 2 — grandia.rma_requests (RMA / retur Grandia):
    Cerere de retur cu refundAmount > 0 care a fost trimisă la plată
    (status AWAITING_REFUND, sau DELIVERED/IN_PROGRESS/NEW de mult) dar NU e plătită
    (paidAt is null, paidAmount=0, niciun rând în rma_payments)  ->  retur promis, bani datorați.

Output: cazuri cu sumă, vechime (zile), comandă, client, sursă (card / RMA), plus
total RON în risc per sursă și per magazin. Moduri: --json, --store, --min-age, --min-amount.

  uv run cs_refund_watchdog.py                 # tot (card + RMA)
  uv run cs_refund_watchdog.py --store EST     # doar un magazin (faza 1)
  uv run cs_refund_watchdog.py --phase card    # doar faza 1 (card)
  uv run cs_refund_watchdog.py --phase rma     # doar faza 2 (RMA Grandia)
  uv run cs_refund_watchdog.py --min-age 7     # doar cazuri mai vechi de 7 zile
  uv run cs_refund_watchdog.py --json          # pt automatizare
NU scrie nimic nicăieri.
"""
import os, sys, json, subprocess, urllib.parse, argparse, datetime
import pg8000.dbapi

HERE = os.path.dirname(os.path.abspath(__file__))

# prefix comandă -> nume magazin
PREFIX = {
    "EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GEN": "Gento",
    "GRAN": "Grandia", "GRAND": "Grandia", "BELA": "Belasil", "CARP": "Carpetto",
    "COV": "Covoria", "MAG": "Magdeal", "OFER": "Ofertele Zilei", "RED": "Reduceri bune",
    "BON": "Bonhaus RO", "BONBG": "Bonhaus BG", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL",
    "APR": "Apreciat", "ROSSI": "Rossi Nails",
}


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def pg(url_key):
    u = urllib.parse.urlparse(secret(url_key))
    return pg8000.dbapi.connect(
        ssl_context=True, user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""), host=u.hostname,
        port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def order_prefix(name):
    """EST138691 -> EST ; GRAND6994 -> GRAND. Litere de la început."""
    s = "".join(ch for ch in (name or "") if not ch.isdigit())
    return s.upper()


def store_of(name, brand_fallback=""):
    p = order_prefix(name)
    # potriviri mai lungi întâi (GRAND înainte de GRAN etc.)
    for k in sorted(PREFIX, key=len, reverse=True):
        if p == k or p.startswith(k):
            return PREFIX[k]
    return brand_fallback or p or "?"


def days_since(ts):
    if not ts:
        return None
    try:
        if isinstance(ts, str):
            ts = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        return (datetime.datetime.now(datetime.timezone.utc) - ts).days
    except Exception:
        return None


# ───────────────────────── FAZA 1: card (metrics.orders) ─────────────────────────
def phase_card(store_filter):
    """Comenzi plătite cu cardul, anulate, încă PAID, totalRefunded=0 -> bani nereturnați."""
    conn = pg("DATABASE_URL_METRICS")
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM brands")
    brands = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute(
        'SELECT name, "brandId", email, COALESCE("shippingPhone", phone), '
        '"totalPrice", "totalRefunded", "financialStatus", "cancelledAt", currency '
        'FROM orders '
        "WHERE \"cancelledAt\" IS NOT NULL "
        "  AND 'shopify_payments' = ANY(\"paymentGatewayNames\") "
        '  AND COALESCE("totalRefunded", 0) = 0 '
        "  AND \"financialStatus\" = 'PAID' "
        'ORDER BY "totalPrice" DESC')
    rows = []
    for name, bid, email, phone, total, refunded, fin, cancelled, cur_code in cur.fetchall():
        store = store_of(name, brands.get(bid, ""))
        rows.append({
            "source": "card", "order": name, "store": store,
            "amount": float(total or 0), "currency": cur_code or "RON",
            "age_days": days_since(cancelled), "when": str(cancelled)[:10],
            "fin": fin, "customer": "", "email": email or "", "phone": phone or "",
            "note": "card -> anulată dar încă PAID, 0 returnat",
        })
    conn.close()
    if store_filter:
        sf = store_filter.lower()
        rows = [r for r in rows if sf in r["store"].lower()]
    return rows


# ───────────────────────── FAZA 2: RMA (grandia) ─────────────────────────
# status RMA în ordinea în care contează pentru risc (promis dar neplătit)
RMA_RISK_STATUS = ("AWAITING_REFUND", "DELIVERED", "IN_PROGRESS", "NEW")


def phase_rma():
    """RMA cu refund datorat dar neplătit. AWAITING_REFUND = cel mai sever (trimis la plată, neplătit)."""
    conn = pg("DATABASE_URL_GRANDIA")
    cur = conn.cursor()
    cur.execute(
        'SELECT r."requestNumber", r."orderName", r.status::text, r."refundAmount", '
        '       r."paidAmount", r."customerName", r."customerPhone", r."customerEmail", '
        '       r."deliveredAt", r."sentToPaymentAt", r."approvedAt", r."createdAt", '
        '       COALESCE(SUM(p.amount), 0) AS pay_sum '
        'FROM rma_requests r '
        'LEFT JOIN rma_payments p ON p."requestId" = r.id '
        "WHERE r.type = 'RETURN' "
        "  AND r.status::text IN ('AWAITING_REFUND', 'DELIVERED', 'IN_PROGRESS', 'NEW') "
        '  AND COALESCE(r."refundAmount", 0) > 0 '
        '  AND r."paidAt" IS NULL '
        '  AND COALESCE(r."paidAmount", 0) = 0 '
        'GROUP BY r.id, r."requestNumber", r."orderName", r.status, r."refundAmount", '
        '         r."paidAmount", r."customerName", r."customerPhone", r."customerEmail", '
        '         r."deliveredAt", r."sentToPaymentAt", r."approvedAt", r."createdAt" '
        'HAVING COALESCE(SUM(p.amount), 0) = 0 '
        'ORDER BY r."refundAmount" DESC')
    rows = []
    for (rn, oname, status, refund, paid, cname, cphone, cemail,
         delivered, sent, approved, created, paysum) in cur.fetchall():
        # vechimea = de când e "promis": trimis la plată > livrat retur > aprobat > creat
        anchor = sent or delivered or approved or created
        rows.append({
            "source": "rma", "order": oname or "", "store": "Grandia",
            "amount": float(refund or 0), "currency": "RON",
            "age_days": days_since(anchor), "when": str(anchor)[:10],
            "fin": status, "rma": rn, "customer": cname or "",
            "email": cemail or "", "phone": cphone or "",
            "note": {
                "AWAITING_REFUND": "RMA trimis la plată, NEPLĂTIT (cel mai sever)",
                "DELIVERED": "retur primit în depozit, refund încă neplătit",
                "IN_PROGRESS": "retur în curs, refund datorat neplătit",
                "NEW": "cerere retur nouă, refund datorat neplătit",
            }.get(status, "refund datorat neplătit"),
        })
    conn.close()
    return rows


# ───────────────────────── Render ─────────────────────────
SEV = {"AWAITING_REFUND": 0, "PAID": 0, "DELIVERED": 1, "IN_PROGRESS": 2, "NEW": 3}


def fmt_money(v, cur="RON"):
    return "{:,.2f} {}".format(v, cur)


def render(card_rows, rma_rows, args):
    total_card = sum(r["amount"] for r in card_rows)
    total_rma = sum(r["amount"] for r in rma_rows)
    grand = total_card + total_rma
    n = len(card_rows) + len(rma_rows)

    print("=" * 74)
    print("  CS REFUND WATCHDOG — refund-uri PROMISE dar NEEFECTUATE")
    print("  (bani luați / retururi promise care NU s-au întors la client)")
    print("=" * 74)
    print("  Cazuri: %d   |   TOTAL ÎN RISC: %s" % (n, fmt_money(grand)))
    print("  Card (anulate, PAID, 0 returnat): %d = %s" % (len(card_rows), fmt_money(total_card)))
    print("  RMA Grandia (refund neplătit):    %d = %s" % (len(rma_rows), fmt_money(total_rma)))
    if grand == 0:
        print("\n  Niciun caz găsit. Curat. (verifică conexiunile dacă te aștepți la cazuri)")
        return
    print("  ! Risc ANPC / chargeback / recenzii negative — de rezolvat cu prioritate pe vechime.")

    # FAZA 1 — card, grupat pe magazin
    if card_rows:
        print("\n" + "-" * 74)
        print("  FAZA 1 — CARD: plătit pe card, anulat, încă PAID, 0 returnat")
        print("-" * 74)
        by_store = {}
        for r in card_rows:
            by_store.setdefault(r["store"], []).append(r)
        for store in sorted(by_store, key=lambda s: -sum(x["amount"] for x in by_store[s])):
            grp = sorted(by_store[store], key=lambda x: -(x["age_days"] or 0))
            st = sum(x["amount"] for x in grp)
            print("\n  %s — %d caz(uri), %s" % (store, len(grp), fmt_money(st)))
            print("    %-13s %9s %5s %-11s %-22s %-13s" %
                  ("comandă", "sumă", "zile", "anulat", "client/email", "telefon"))
            for x in grp:
                who = x["customer"] or x["email"] or "—"
                print("    %-13s %9s %5s %-11s %-22s %-13s" % (
                    x["order"], "{:,.0f}".format(x["amount"]),
                    (str(x["age_days"]) if x["age_days"] is not None else "?"),
                    x["when"], who[:22], (x["phone"] or "—")[:13]))

    # FAZA 2 — RMA Grandia, sortat pe severitate apoi vechime
    if rma_rows:
        print("\n" + "-" * 74)
        print("  FAZA 2 — RMA GRANDIA: refund datorat, neplătit")
        print("-" * 74)
        rma_sorted = sorted(rma_rows, key=lambda x: (SEV.get(x["fin"], 9), -(x["age_days"] or 0)))
        print("    %-9s %-13s %9s %5s %-11s %-20s %-13s %s" %
              ("RMA", "comandă", "sumă", "zile", "din", "client", "telefon", "status"))
        for x in rma_sorted:
            print("    %-9s %-13s %9s %5s %-11s %-20s %-13s %s" % (
                x.get("rma", "—"), x["order"] or "—", "{:,.0f}".format(x["amount"]),
                (str(x["age_days"]) if x["age_days"] is not None else "?"),
                x["when"], (x["customer"] or "—")[:20], (x["phone"] or "—")[:13],
                x["fin"]))
        aw = [x for x in rma_rows if x["fin"] == "AWAITING_REFUND"]
        if aw:
            print("\n    ! %d RMA în AWAITING_REFUND (trimise la plată, NEplătite) = %s — cel mai sever." % (
                len(aw), fmt_money(sum(x["amount"] for x in aw))))

    # top 5 cele mai vechi indiferent de sursă
    allr = [r for r in (card_rows + rma_rows) if r["age_days"] is not None]
    if allr:
        oldest = sorted(allr, key=lambda x: -x["age_days"])[:5]
        print("\n  Cele mai VECHI (de atacat primele):")
        for x in oldest:
            tag = "CARD" if x["source"] == "card" else "RMA " + x.get("rma", "")
            print("    %4d zile  %-13s %9s  %-10s  %s" % (
                x["age_days"], x["order"] or "—", "{:,.0f}".format(x["amount"]),
                x["store"], tag))


def main():
    ap = argparse.ArgumentParser(description="Refund-uri promise dar neefectuate (card anulat + RMA Grandia).")
    ap.add_argument("--store", default="", help="filtrează pe magazin (ex. EST, Esteban) — afectează faza card")
    ap.add_argument("--phase", choices=["card", "rma", "all"], default="all", help="ce surse rulezi")
    ap.add_argument("--min-age", type=int, default=0, dest="min_age", help="doar cazuri mai vechi de N zile")
    ap.add_argument("--min-amount", type=float, default=0, dest="min_amount", help="doar cazuri >= X")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    card_rows, rma_rows = [], []
    if a.phase in ("card", "all"):
        try:
            card_rows = phase_card(a.store)
        except Exception as e:
            print("WARN faza CARD a eșuat: %s" % e, file=sys.stderr)
    # faza RMA e doar Grandia; dacă --store cere alt magazin, o sărim
    rma_wanted = a.phase in ("rma", "all") and (not a.store or "gran" in a.store.lower())
    if rma_wanted:
        try:
            rma_rows = phase_rma()
        except Exception as e:
            print("WARN faza RMA a eșuat: %s" % e, file=sys.stderr)

    def keep(r):
        if a.min_age and (r["age_days"] is None or r["age_days"] < a.min_age):
            return False
        if a.min_amount and r["amount"] < a.min_amount:
            return False
        return True

    card_rows = [r for r in card_rows if keep(r)]
    rma_rows = [r for r in rma_rows if keep(r)]

    if a.json:
        out = {
            "total_at_risk": round(sum(r["amount"] for r in card_rows + rma_rows), 2),
            "card_total": round(sum(r["amount"] for r in card_rows), 2),
            "rma_total": round(sum(r["amount"] for r in rma_rows), 2),
            "count": len(card_rows) + len(rma_rows),
            "card": card_rows, "rma": rma_rows,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    else:
        render(card_rows, rma_rows, a)


if __name__ == "__main__":
    main()
