# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
rma_sla_watchdog.py — DETECTOR DE BLOCAJE pe pipeline-ul de retururi/schimburi Grandia.

Spre deosebire de returns-rma-report (care doar listează RMA-urile deschise pe status),
acest watchdog găsește RMA-urile care au DEPĂȘIT SLA pe FIECARE etapă a pipeline-ului +
coada de erori la generarea AWB (clienți blocați de eroarea de formular DPD), grupat ca
să poată fi reparat la sursă (localitatea care pică).

Etape & praguri de BREACH (toate calculate la now()):
  (1) NEW neaprobat            > 2 zile de la createdAt          → de aprobat / respins
  (2) IN_PROGRESS, AWB încă NEW > 5 zile de la approvedAt        → clientul N-A PREDAT coletul → chase
  (3) AWAITING_REFUND          > 3 zile de la sentToPayment/created → bani promiși, neplătiți
  (4) EXCHANGE DELIVERED fără courier_shipments(type='SWAP')      → produsul de schimb N-A plecat

PLUS coada de erori formular (rma_logs.action='awb_failed'): RMA-urile la care generarea AWB
a picat (cauză dominantă reală: DPD „Localitate nevalida"), cu agregat al cauzelor și top
localități care pică — semnalul durabil pt fix-ul de produs (mapare localitate → DPD).

Surse (DB Grandia, READ-ONLY, doar SELECT):
  rma_requests   (status, type, lanț timestamp create/approve/sent/paid/closed,
                  customerName/Phone/Email, refundAmount, requestNumber, orderName, orderId, pickupCity/County)
  rma_awbs       (requestId → rma_requests.id; status rămâne NEW dacă clientul n-a predat)
  courier_shipments (orderId → rma_requests.orderId; type='SWAP' = coletul de schimb plecat)
  rma_logs       (action='awb_failed', message = eroarea DPD)

Moduri:
  uv run rma_sla_watchdog.py                 # tot raportul (breach-uri + erori formular)
  uv run rma_sla_watchdog.py --breaches      # doar breach-urile de SLA pe etape
  uv run rma_sla_watchdog.py --errors        # doar coada de erori AWB + top localități
  uv run rma_sla_watchdog.py --new-sla 2 --awb-sla 5 --refund-sla 3   # praguri custom (zile)
  uv run rma_sla_watchdog.py --open-only     # erori AWB doar pt RMA-uri ÎNCĂ deschise
  uv run rma_sla_watchdog.py --json          # ieșire JSON (automatizare)

READ-ONLY total. Nu scrie nimic în Postgres/Shopify/altundeva. Doar Grandia are aceste tabele.
"""
import os, sys, json, subprocess, argparse, urllib.parse
import pg8000.dbapi

HERE = os.path.dirname(os.path.abspath(__file__))

# etichete prietenoase pt fiecare etapă de breach
STAGE_LABEL = {
    "new_unapproved": "NEW neaprobat (de triat)",
    "awb_not_handed": "IN_PROGRESS — AWB încă NEW (clientul n-a predat → chase)",
    "refund_stuck": "AWAITING_REFUND (bani promiși, neplătiți)",
    "exchange_no_swap": "EXCHANGE livrat fără colet SWAP (schimbul n-a plecat)",
}


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k],
                          capture_output=True, text=True).stdout.strip()


def get_conn():
    url = secret("DATABASE_URL_GRANDIA")
    if not url:
        sys.exit("EROARE: nu am putut obține DATABASE_URL_GRANDIA (env sau KB).")
    u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(
        ssl_context=True,
        user=urllib.parse.unquote(u.username or ""),
        password=urllib.parse.unquote(u.password or ""),
        host=u.hostname, port=u.port or 5432,
        database=(u.path or "/").lstrip("/"))


def ron(n):
    return "{:,.2f}".format(float(n or 0))


def contact(name, phone, email):
    c = (phone or email or "—")
    if name:
        return "%s · %s" % (name, c)
    return c


# ───────────────────────── colectare BREACH-uri ─────────────────────────
def collect_breaches(cur, new_sla, awb_sla, refund_sla):
    out = {}

    # (1) NEW neaprobat > new_sla zile
    cur.execute(
        'SELECT "requestNumber","orderName","customerName","customerPhone","customerEmail",'
        '       "refundAmount", type::text,'
        '       EXTRACT(DAY FROM now()-"createdAt")::int AS age '
        'FROM rma_requests '
        "WHERE status='NEW' AND \"approvedAt\" IS NULL "
        '  AND "createdAt" < now() - (%s || \' days\')::interval '
        'ORDER BY "createdAt" ASC', [new_sla])
    out["new_unapproved"] = [
        {"req": r[0], "order": r[1], "name": r[2], "phone": r[3], "email": r[4],
         "refund": float(r[5] or 0), "type": r[6], "age": r[7]}
        for r in cur.fetchall()]

    # (2) IN_PROGRESS cu rma_awbs.status încă NEW > awb_sla zile de la aprobare
    cur.execute(
        'SELECT r."requestNumber", r."orderName", r."customerName", r."customerPhone",'
        '       r."customerEmail", r."refundAmount", r.type::text,'
        '       EXTRACT(DAY FROM now()-r."approvedAt")::int AS age '
        'FROM rma_requests r '
        'JOIN rma_awbs a ON a."requestId" = r.id '
        "WHERE r.status='IN_PROGRESS' AND a.status='NEW' "
        '  AND r."approvedAt" IS NOT NULL '
        '  AND r."approvedAt" < now() - (%s || \' days\')::interval '
        'GROUP BY r.id, r."requestNumber", r."orderName", r."customerName", r."customerPhone",'
        '         r."customerEmail", r."refundAmount", r.type, r."approvedAt" '
        'ORDER BY r."approvedAt" ASC', [awb_sla])
    out["awb_not_handed"] = [
        {"req": r[0], "order": r[1], "name": r[2], "phone": r[3], "email": r[4],
         "refund": float(r[5] or 0), "type": r[6], "age": r[7]}
        for r in cur.fetchall()]

    # (3) AWAITING_REFUND > refund_sla zile (de la sentToPayment dacă există, altfel createdAt)
    cur.execute(
        'SELECT "requestNumber","orderName","customerName","customerPhone","customerEmail",'
        '       "refundAmount", type::text,'
        '       EXTRACT(DAY FROM now()-COALESCE("sentToPaymentAt","createdAt"))::int AS age,'
        '       ("sentToPaymentAt" IS NOT NULL) AS sent '
        'FROM rma_requests '
        "WHERE status='AWAITING_REFUND' "
        '  AND COALESCE("sentToPaymentAt","createdAt") < now() - (%s || \' days\')::interval '
        'ORDER BY COALESCE("sentToPaymentAt","createdAt") ASC', [refund_sla])
    out["refund_stuck"] = [
        {"req": r[0], "order": r[1], "name": r[2], "phone": r[3], "email": r[4],
         "refund": float(r[5] or 0), "type": r[6], "age": r[7], "sent": bool(r[8])}
        for r in cur.fetchall()]

    # (4) EXCHANGE DELIVERED fără courier_shipments type='SWAP'
    cur.execute(
        'SELECT r."requestNumber", r."orderName", r."customerName", r."customerPhone",'
        '       r."customerEmail", r."refundAmount",'
        '       EXTRACT(DAY FROM now()-COALESCE(r."deliveredAt",r."updatedAt"))::int AS age '
        'FROM rma_requests r '
        "WHERE r.type='EXCHANGE' AND r.status='DELIVERED' "
        '  AND NOT EXISTS (SELECT 1 FROM courier_shipments cs '
        "                  WHERE cs.\"orderId\" = r.\"orderId\" AND cs.type='SWAP') "
        'ORDER BY COALESCE(r."deliveredAt",r."updatedAt") ASC')
    out["exchange_no_swap"] = [
        {"req": r[0], "order": r[1], "name": r[2], "phone": r[3], "email": r[4],
         "refund": float(r[5] or 0), "type": "EXCHANGE", "age": r[6]}
        for r in cur.fetchall()]

    return out


# ───────────────────────── colectare ERORI FORMULAR (awb_failed) ─────────────────────────
def collect_errors(cur, open_only):
    # un rând per log de eroare, legat de request (pt status curent + localitatea care pică)
    cur.execute(
        'SELECT l."requestId",'
        "       regexp_replace(l.message, '^DPD shipment creation failed: ', '') AS raw,"
        "       split_part(regexp_replace(l.message, '^DPD shipment creation failed: ', ''), ' (', 1) AS cause,"
        '       r."requestNumber", r."pickupCity", r."pickupCounty",'
        '       r.status::text, r."customerName", r."customerPhone", r."customerEmail",'
        '       r."refundAmount", l."createdAt"::date '
        "FROM rma_logs l "
        'LEFT JOIN rma_requests r ON r.id = l."requestId" '
        "WHERE l.action='awb_failed' "
        'ORDER BY l."createdAt" DESC')
    rows = cur.fetchall()

    OPEN = {"NEW", "IN_PROGRESS", "DELIVERED", "AWAITING_REFUND"}
    logs = []
    for r in rows:
        is_open = (r[6] in OPEN)
        if open_only and not is_open:
            continue
        logs.append({
            "req": r[3], "cause": r[2], "raw": r[1],
            "city": r[4], "county": r[5], "status": r[6],
            "name": r[7], "phone": r[8], "email": r[9],
            "refund": float(r[10] or 0), "date": str(r[11]), "open": is_open})

    # agregat pe cauză
    by_cause = {}
    for l in logs:
        by_cause[l["cause"]] = by_cause.get(l["cause"], 0) + 1
    by_cause = sorted(by_cause.items(), key=lambda x: -x[1])

    # agregat pe localitate care pică (din pickupCity normalizat) — semnalul pt fixul de produs
    by_city = {}
    for l in logs:
        city = (l["city"] or "—").strip()
        key = city.lower()
        if key not in by_city:
            by_city[key] = {"city": city, "county": l["county"], "n": 0}
        by_city[key]["n"] += 1
    by_city = sorted(by_city.values(), key=lambda x: -x["n"])

    # RMA-uri distincte încă blocate (open) de eroare
    open_reqs = sorted({l["req"] for l in logs if l["open"] and l["req"]})

    return {"logs": logs, "by_cause": by_cause, "by_city": by_city, "open_reqs": open_reqs}


# ───────────────────────── render ─────────────────────────
def hr(ch="═", n=72):
    print(ch * n)


def render_breaches(B, slas):
    hr()
    print("  WATCHDOG RMA GRANDIA — BLOCAJE PE PIPELINE (breach SLA)")
    hr()
    total_n = sum(len(v) for v in B.values())
    total_ron = sum(x["refund"] for v in B.values() for x in v)
    print("  Total RMA-uri în breach: %d  |  bani implicați (refund): %s RON" % (total_n, ron(total_ron)))
    print("  Praguri: NEW>%dz · AWB-NEW>%dz · AWAITING_REFUND>%dz · EXCHANGE-fără-SWAP" % slas)
    if total_n == 0:
        print("\n  ✅ Niciun breach pe nicio etapă. Pipeline curat.")
        return

    order = ["new_unapproved", "awb_not_handed", "refund_stuck", "exchange_no_swap"]
    for stage in order:
        rows = B.get(stage, [])
        if not rows:
            continue
        sub = sum(x["refund"] for x in rows)
        print("\n" + "─" * 72)
        print("  ▸ %s" % STAGE_LABEL[stage])
        print("    %d RMA · %s RON · cel mai vechi: %d zile" % (
            len(rows), ron(sub), max(x["age"] for x in rows)))
        print("    %-10s %-12s %5s %-9s %10s  %s" % ("req", "comandă", "zile", "tip", "RON", "client/contact"))
        for x in rows:
            sent = ""
            if stage == "refund_stuck":
                sent = " [trimis la plată]" if x.get("sent") else " [NEtrimis]"
            print("    %-10s %-12s %5d %-9s %10s  %s%s" % (
                (x["req"] or "-"), (x["order"] or "-")[:12], x["age"], (x["type"] or "-")[:9],
                ron(x["refund"]), contact(x["name"], x["phone"], x["email"])[:34], sent))


def render_errors(E, open_only):
    hr()
    print("  ERORI FORMULAR AWB (rma_logs.action='awb_failed')%s" % (" — DOAR RMA deschise" if open_only else ""))
    hr()
    logs = E["logs"]
    if not logs:
        print("  Nicio eroare de generare AWB în acest filtru.")
        return
    distinct_reqs = len({l["req"] for l in logs if l["req"]})
    print("  %d erori AWB pe %d RMA-uri%s." % (
        len(logs), distinct_reqs,
        ("; %d RMA ÎNCĂ blocate (open): %s" % (len(E["open_reqs"]), ", ".join(E["open_reqs"]))
         if E["open_reqs"] else " — toate retried/rezolvate ulterior")))

    print("\n  ── Cauze (cine pică formularul DPD) ──")
    for cause, n in E["by_cause"]:
        print("    %3d × %s" % (n, cause))

    print("\n  ── Top localități care pică (semnal pt fix produs: mapare localitate→DPD) ──")
    print("    %-24s %-18s %5s" % ("localitate", "județ", "erori"))
    for c in E["by_city"][:15]:
        print("    %-24s %-18s %5d" % ((c["city"] or "—")[:24], (c["county"] or "—")[:18], c["n"]))

    print("\n  ── Detaliu erori (cel mai recent întâi) ──")
    print("    %-10s %-9s %-14s %-18s %s" % ("req", "status", "localitate", "cauză", "data"))
    for l in logs[:30]:
        print("    %-10s %-9s %-14s %-18s %s" % (
            (l["req"] or "-"), (l["status"] or "-")[:9], (l["city"] or "-")[:14],
            (l["cause"] or "-")[:18], l["date"]))


# ───────────────────────── main ─────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Watchdog SLA pe pipeline-ul RMA Grandia (read-only).")
    ap.add_argument("--breaches", action="store_true", help="doar breach-urile de SLA pe etape")
    ap.add_argument("--errors", action="store_true", help="doar coada de erori AWB + top localități")
    ap.add_argument("--new-sla", type=int, default=2, dest="new_sla", help="zile NEW neaprobat (default 2)")
    ap.add_argument("--awb-sla", type=int, default=5, dest="awb_sla", help="zile IN_PROGRESS cu AWB încă NEW (default 5)")
    ap.add_argument("--refund-sla", type=int, default=3, dest="refund_sla", help="zile AWAITING_REFUND (default 3)")
    ap.add_argument("--open-only", action="store_true", help="erori AWB doar pt RMA-uri încă deschise")
    ap.add_argument("--json", action="store_true", help="ieșire JSON")
    a = ap.parse_args()

    show_all = not (a.breaches or a.errors)
    conn = get_conn()
    cur = conn.cursor()
    try:
        B = collect_breaches(cur, a.new_sla, a.awb_sla, a.refund_sla) if (show_all or a.breaches) else None
        E = collect_errors(cur, a.open_only) if (show_all or a.errors) else None
    finally:
        conn.close()

    if a.json:
        payload = {}
        if B is not None:
            payload["breaches"] = B
            payload["breach_total"] = {"count": sum(len(v) for v in B.values()),
                                       "refund_ron": round(sum(x["refund"] for v in B.values() for x in v), 2)}
        if E is not None:
            payload["errors"] = E
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return

    if B is not None:
        render_breaches(B, (a.new_sla, a.awb_sla, a.refund_sla))
    if E is not None:
        print()
        render_errors(E, a.open_only)


if __name__ == "__main__":
    main()
