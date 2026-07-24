"""
reconcile_sources.py — reconciliere între cele 3 surse de adevăr ARONA, cu ISTORIC de drift.

HARTA spune negru pe alb: „cache-urile derivate MINT per-brand — la verdict pe BANI confirmă
la SURSĂ". Engine-ul sub-număra livratele de ~1,6× (Esteban 5.713 engine vs 10.079 AWBprint),
iar tokenul Meta mort a golit spend-ul din warehouse. Problema: nimeni nu vede CÂND o sursă
începe să mintă — se descoperă peste două luni, la un audit.

Comparăm două metrici, fiecare apple-to-apple, între surse INDEPENDENTE:
  1. COMENZI LIVRATE  — engine (cache.brand_pnl_monthly) vs AWBprint (COUNT delivered).
     Independentă de monedă și TVA. Exact metrica ce a picat 1,6×.
  2. MARKETING (RON)  — engine (brand_pnl.marketing, din sheet) vs warehouse
     (cache.daily_ad_spend_ron). Două căi independente care TREBUIE să coincidă;
     divergența = token mort, atribuire stricată, sau override neactualizat.

Salvăm fiecare rulare în `recon_history` (profitability.db) → drift-ul se vede în TIMP, nu
doar acum. Alertă (email, doar pe roșu) când |drift| depășește pragul pe o metrică cu volum
material. Complementar cu data_health (prospețime): ăsta compară VALORI, nu vechime.

  reconcile_sources.py                          # ultimele 3 luni, raport în consolă
  reconcile_sources.py --months 6 --threshold 12
  reconcile_sources.py --email X --key ...      # + email dacă e drift material
"""
import os, re, sys, argparse, sqlite3
from datetime import datetime, date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PF_DB = "/root/Scripturi/data/profitability.db"

# AWBprint pune tot runul de litere din order_number ca prefix (NUBRA/GRAND); engine folosește NUB/GRAN.
AWB_TO_ENGINE = {"NUBRA": "NUB", "GRAND": "GRAN"}
# volumul sub care ignorăm driftul procentual (zgomot pe branduri mici — ROSSI/COV/CARP)
MIN_ORDERS = 40
MIN_MKT_RON = 3000


def _send_email(to, subject, body, key, sender):
    """Identic cu data_health/check_token_expiry — SA cu delegare Workspace, scope gmail.modify."""
    import base64
    from email.mime.text import MIMEText
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_file(
        key, scopes=["https://www.googleapis.com/auth/gmail.modify"]).with_subject(sender)
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    msg = MIMEText(body, _charset="utf-8")
    msg["to"] = to; msg["from"] = sender; msg["subject"] = subject
    svc.users().messages().send(
        userId="me", body={"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}).execute()


def _dsn(var):
    dsn = os.environ.get(var)
    if not dsn:
        sys.exit("Lipsește %s" % var)
    dsn = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", dsn)
    return re.sub(r"[?&]+(&|$)", r"\1", dsn).rstrip("?&")


def _months(n):
    """Ultimele n luni ca [(YYYY-MM, YYYY-MM-01, next-YYYY-MM-01)], cea mai nouă prima."""
    out = []
    y, m = datetime.utcnow().year, datetime.utcnow().month
    for _ in range(n):
        lo = "%04d-%02d-01" % (y, m)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append(("%04d-%02d" % (y, m), lo, "%04d-%02d-01" % (ny, nm)))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def _drift(a, b):
    """Drift semnat al lui a față de b (referință). None dacă b e 0."""
    return None if not b else (a - b) / b * 100.0


def gather(months):
    """→ list de dict per (month, prefix, metric) cu engine_val, other_val, drift, sursa referinței."""
    import psycopg2
    mx = psycopg2.connect(_dsn("DATABASE_URL_METRICS")); mcur = mx.cursor()
    ax = psycopg2.connect(_dsn("DATABASE_URL_AWBPRINT")); acur = ax.cursor()
    rows = []

    for month, lo, hi in months:
        # ---- 1. COMENZI LIVRATE: engine vs AWBprint ----
        mcur.execute("SELECT prefix, brand_name, delivered_orders, marketing, brand_id "
                     "FROM cache.brand_pnl_monthly WHERE month=%s", (month,))
        eng = {r[0]: {"brand": r[1], "delivered": r[2] or 0, "marketing": float(r[3] or 0),
                      "brand_id": r[4]} for r in mcur.fetchall()}

        acur.execute("""SELECT UPPER((regexp_match(order_number,'^([A-Za-z]+)'))[1]) pfx, COUNT(*)
                        FROM orders WHERE frisbo_created_at>=%s AND frisbo_created_at<%s
                          AND aggregated_status='delivered' GROUP BY 1""", (lo, hi))
        awb = {}
        for pfx, n in acur.fetchall():
            if not pfx:
                continue
            awb[AWB_TO_ENGINE.get(pfx, pfx)] = awb.get(AWB_TO_ENGINE.get(pfx, pfx), 0) + n

        for pfx in sorted(set(eng) | set(awb)):
            e = eng.get(pfx, {}).get("delivered", 0)
            a = awb.get(pfx, 0)
            rows.append({"month": month, "prefix": pfx, "brand": eng.get(pfx, {}).get("brand", pfx),
                         "metric": "livrate", "engine": e, "other": a, "other_src": "awbprint",
                         "drift": _drift(e, a), "material": max(e, a) >= MIN_ORDERS})

        # ---- 2. MARKETING: engine (sheet) vs warehouse — pe brand_id (evită cardinalitatea prefix↔brand) ----
        mcur.execute("""SELECT brand_id, ROUND(SUM(spend_ron)) FROM cache.daily_ad_spend_ron
                        WHERE date>=%s AND date<%s GROUP BY brand_id""", (lo, hi))
        wh = {bid: float(v or 0) for bid, v in mcur.fetchall()}
        eng_by_bid = {}
        for pfx, d in eng.items():
            bid = d["brand_id"]
            if bid:
                eng_by_bid.setdefault(bid, {"brand": d["brand"], "mkt": 0.0})
                eng_by_bid[bid]["mkt"] += d["marketing"]
        for bid in sorted(b for b in (set(eng_by_bid) | set(wh)) if b):
            e = eng_by_bid.get(bid, {}).get("mkt", 0.0)
            w = wh.get(bid, 0.0)
            rows.append({"month": month, "prefix": bid[:8], "brand": eng_by_bid.get(bid, {}).get("brand", bid[:8]),
                         "metric": "marketing", "engine": round(e), "other": round(w), "other_src": "warehouse",
                         "drift": _drift(e, w), "material": max(e, w) >= MIN_MKT_RON})
    mx.close(); ax.close()
    return rows


def store_history(rows):
    """Snapshot în profitability.db + întoarce driftul ANTERIOR pe fiecare (month,prefix,metric)."""
    cx = sqlite3.connect(PF_DB); cx.execute("PRAGMA busy_timeout=8000;")
    cx.execute("""CREATE TABLE IF NOT EXISTS recon_history (
        snapshot_date TEXT, month TEXT, prefix TEXT, brand TEXT, metric TEXT,
        engine_val REAL, other_val REAL, other_src TEXT, drift_pct REAL)""")
    cx.execute("CREATE INDEX IF NOT EXISTS idx_recon ON recon_history(month, prefix, metric, snapshot_date)")
    prev = {}
    for r in rows:
        c = cx.execute("""SELECT drift_pct, snapshot_date FROM recon_history
                          WHERE month=? AND prefix=? AND metric=? ORDER BY snapshot_date DESC LIMIT 1""",
                       (r["month"], r["prefix"], r["metric"])).fetchone()
        if c:
            prev[(r["month"], r["prefix"], r["metric"])] = c
    today = date.today().isoformat()
    # o rulare pe zi per cheie (idempotent la re-rulări)
    cx.execute("DELETE FROM recon_history WHERE snapshot_date=?", (today,))
    cx.executemany("INSERT INTO recon_history VALUES (?,?,?,?,?,?,?,?,?)",
                   [(today, r["month"], r["prefix"], r["brand"], r["metric"],
                     r["engine"], r["other"], r["other_src"], r["drift"]) for r in rows])
    cx.commit(); cx.close()
    return prev


def main():
    ap = argparse.ArgumentParser(description="Reconciliere engine ↔ AWBprint ↔ warehouse cu istoric de drift")
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--threshold", type=float, default=10.0, help="prag |drift%%| pentru alertă (metrici materiale)")
    ap.add_argument("--email"); ap.add_argument("--from", dest="sender", default="gheorghe.beschea@overheat.agency")
    ap.add_argument("--key", default="/root/Scripturi/google_credentials.json")
    ap.add_argument("--no-store", action="store_true", help="nu scrie snapshot (doar raport)")
    a = ap.parse_args()

    rows = gather(_months(a.months))
    prev = {} if a.no_store else store_history(rows)

    bad = [r for r in rows if r["material"] and r["drift"] is not None and abs(r["drift"]) > a.threshold]
    bad.sort(key=lambda r: -abs(r["drift"]))

    # Emailăm DOAR divergențele NOI — o metrică ce tocmai a trecut din OK în drift. Cele istorice
    # (mai/iunie, luni închise) rămân peste prag la fiecare rulare; le-am re-trimite zilnic = spam.
    # „Nou" = peste prag ACUM, dar sub prag (sau inexistent-dar-cu-baseline) la snapshotul anterior.
    # Prima rulare pe o cheie = doar baseline (fără email), ca să nu explodeze la primul run.
    def is_new(r):
        pk = (r["month"], r["prefix"], r["metric"])
        if pk not in prev:
            return False                       # cheie nouă → baseline tăcut
        pdrift = prev[pk][0]
        return pdrift is None or abs(pdrift) <= a.threshold   # era OK, acum e drift → tranziție reală
    newly_bad = [r for r in bad if is_new(r)]

    out = ["RECONCILIERE SURSE ARONA — %s UTC (prag %.0f%%)" % (datetime.utcnow().strftime("%Y-%m-%d %H:%M"), a.threshold), ""]

    def fmt(r):
        pk = (r["month"], r["prefix"], r["metric"])
        arrow = ""
        if pk in prev and prev[pk][0] is not None and r["drift"] is not None:
            delta = abs(r["drift"]) - abs(prev[pk][0])
            arrow = "  (%+.0f pp vs %s)" % (delta, prev[pk][1][5:])  # crește/scade driftul față de ultima dată
        d = "n/a" if r["drift"] is None else "%+.1f%%" % r["drift"]
        return "  %-7s %-14s %-10s eng=%-9s %s=%-9s  drift %s%s" % (
            r["month"], r["brand"][:14], r["metric"], r["engine"], r["other_src"][:4], r["other"], d, arrow)

    if bad:
        nnew = " · %d NOI" % len(newly_bad) if newly_bad else " · toate cunoscute (fără alertă)"
        out.append("🔴 DRIFT PESTE PRAG (%d%s):" % (len(bad), nnew))
        for r in bad:
            out.append(fmt(r) + ("   ⬅ NOU" if r in newly_bad else ""))
        out.append("")
    else:
        out.append("🟢 Nicio metrică materială peste prag.")
        out.append("")

    # rezumat pe cea mai recentă lună, ca privire de ansamblu
    latest = rows[0]["month"] if rows else None
    out.append("Detaliu %s (toate brandurile materiale):" % latest)
    for r in [x for x in rows if x["month"] == latest and x["material"]]:
        out.append(fmt(r))
    out.append("")
    out.append("Reconciliem VALORI între surse independente: livrate = engine vs AWBprint (metrica ce a "
               "picat 1,6× în iunie), marketing = sheet vs warehouse (prinde tokenul Meta mort). Driftul e "
               "salvat zilnic → vezi CÂND începe divergența, nu peste două luni.")
    report = "\n".join(out)
    print(report)

    # Email DOAR pe divergențe noi (tranziții OK→drift), nu pe cele deja cunoscute — respectă „doar la erori".
    if a.email and newly_bad:
        try:
            _send_email(a.email, "[reconcile] ARONA — 🔴 %d divergență(e) NOUĂ între surse" % len(newly_bad),
                        report, a.key, a.sender)
            print("\n[email] trimis către %s (%d noi)" % (a.email, len(newly_bad)))
        except Exception as e:
            print("\n[email] EȘUAT: %s: %s" % (type(e).__name__, e))
    elif a.email:
        print("\n[email] niciun drift NOU → fără email (%d cunoscute ignorate)" % len(bad))
    return 1 if newly_bad else 0


if __name__ == "__main__":
    sys.exit(main())
