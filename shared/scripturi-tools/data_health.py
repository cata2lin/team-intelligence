"""
data_health.py — watchdog de PROSPEȚIME A DATELOR pentru pipeline-urile ARONA.

De ce: eșecurile sunt TĂCUTE. Tokenul Meta a murit pe 2026-06-19 (91% din spend, orb),
descoperit după 3 zile și reparat după 11. Cronul TikTok s-a oprit complet — 0 rulări,
11 zile, nimeni n-a aflat. `sync_runs` a eșuat de ~7.400 ori în 2 zile în tăcere.

Verificăm IEȘIREA (datele produse), NU dacă „a rulat jobul": logul TikTok se scria în timp
ce sync-ul eșua, deci vechimea unui log nu dovedește nimic. Fiecare check compară cât de
veche e cea mai recentă DATĂ dintr-un tabel cu un SLA în ore, derivat din cron-ul care-l
alimentează.

Complementar cu `check_token_expiry.py` (care păzește EXPIRAREA tokenurilor, cron 8:00):
aici ne uităm la conturile care atârnă de un token deja mort — modul în care a picat efectiv.

Rulează pe VPS. Trimite email DOAR când e ceva roșu (`--always` forțează), pe același drum
dovedit ca check_token_expiry: Gmail API cu service account care impersonează Workspace.

  .venv/bin/python data_health.py                      # doar raport în consolă
  .venv/bin/python data_health.py --email X --key ...  # + email dacă e roșu
"""
import os, re, sys, argparse, sqlite3
from datetime import datetime, timezone

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PF_DB = "/root/Scripturi/data/profitability.db"
OK, WARN, CRIT = "OK", "WARN", "CRIT"
ICON = {OK: "🟢", WARN: "🟡", CRIT: "🔴"}


def _send_email(to, subject, body, key, sender):
    """Identic cu check_token_expiry.py — SA cu delegare domain-wide, scope gmail.modify."""
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
    """DSN curățat de parametrii pe care psycopg2 nu-i înghite (ca în check_token_expiry)."""
    dsn = os.environ.get(var)
    if not dsn:
        return None
    dsn = re.sub(r"([?&])(schema|channel_binding|pgbouncer|connection_limit)=[^&]*", r"\1", dsn)
    return re.sub(r"[?&]+(&|$)", r"\1", dsn).rstrip("?&")


def _age_h(ts):
    """Vechime în ore a unui timestamp/dată (naiv sau aware, date sau str)."""
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts[:19])
    if not isinstance(ts, datetime):
        ts = datetime(ts.year, ts.month, ts.day)
    if ts.tzinfo:
        ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
    return (datetime.utcnow() - ts).total_seconds() / 3600


def _judge(rows, key, ts, sla_h, note=""):
    """O linie de raport: verde sub SLA, roșu peste. `ts` None = nu există deloc date."""
    if ts is None:
        rows.append((CRIT, key, "fără date deloc" + (" · " + note if note else "")))
        return
    age = _age_h(ts)
    label = str(ts)[:16]
    det = "%s · %.0fh (SLA %dh)" % (label, age, sla_h)
    rows.append((CRIT if age > sla_h else OK, key, det + (" · " + note if note else "")))


# ---------------------------------------------------------------- checks

def check_metrics(rows, ctx):
    """Warehouse: spend per platformă (brand + per-SKU), P&L pe brand, curs valutar, sync_runs."""
    dsn = _dsn("DATABASE_URL_METRICS")
    if not dsn:
        rows.append((CRIT, "metrics", "lipsește DATABASE_URL_METRICS")); return
    import psycopg2
    cx = psycopg2.connect(dsn); cur = cx.cursor()

    # SLA 36h: build_cache rulează 5:30 + la fiecare 3h între 9 și 21.
    # Reținem prospețimea cache-ului per platformă — WMS (redundant) e critic DOAR dacă și cache-ul e stale.
    for tbl, pref in (("cache.daily_ad_spend_ron", "spend"), ("cache.product_ad_spend", "spend_sku")):
        cur.execute("SELECT platform, MAX(date) FROM %s GROUP BY platform ORDER BY 1" % tbl)
        got = cur.fetchall()
        if not got:
            rows.append((CRIT, pref, "tabel gol"))
        for plat, mx in got:
            _judge(rows, "%s.%s" % (pref, plat), mx, 36)
            if tbl == "cache.daily_ad_spend_ron":
                ctx.setdefault("cache_fresh", {})[plat] = (_age_h(mx) is not None and _age_h(mx) <= 36)

    cur.execute("SELECT MAX(month) FROM cache.brand_pnl_monthly")
    mx = cur.fetchone()[0]
    cur_month = datetime.utcnow().strftime("%Y-%m")
    rows.append((OK if str(mx) >= cur_month else CRIT, "brand_pnl",
                 "ultima lună %s (curentă %s)" % (mx, cur_month)))

    # P&L GOL dar cu marketing = exact avaria din 2026-07: engine-ul n-a mai sincronizat
    # comenzile, iar cache-ul s-a recalculat la fiecare 3h raportând fidel −1,5M pierdere.
    # Luna e „prezentă", deci checkul de mai sus trece — abia raportul de aici o prinde.
    cur.execute("""SELECT COALESCE(SUM(delivered_orders),0), COALESCE(SUM(revenue_exvat),0),
                          COALESCE(SUM(marketing),0)
                   FROM cache.brand_pnl_monthly WHERE month = %s""", (cur_month,))
    dlv, rev, mkt = cur.fetchone()
    if mkt > 0 and (rev == 0 or dlv == 0):
        rows.append((CRIT, "brand_pnl.gol",
                     "luna curentă: %d livrate / %.0f venit dar %.0f marketing → P&L FALS" % (dlv, rev, mkt)))
    else:
        rows.append((OK, "brand_pnl.gol", "%d livrate, %.0f venit, %.0f marketing" % (dlv, rev, mkt)))

    cur.execute('SELECT MAX("rateDate") FROM fx_rates')
    _judge(rows, "fx_rates", cur.fetchone()[0], 96, "curs BNR")

    # Conturile care atârnă de un token MORT — modul real în care a picat Meta pe 06-19.
    # Filtrăm pe lastSyncAt recent: ~85 de conturi vechi/non-ARONA stau permanent pe tokenul
    # „Sabina" (mort, fără acces) și ar face checkul roșu pe veci. Contează doar cele VII.
    cur.execute("""SELECT COUNT(*) FROM meta_ad_accounts a
                   JOIN meta_access_tokens t ON t.id = a."tokenId"
                   WHERE a."isActive" = true
                     AND a."lastSyncAt" > NOW() - INTERVAL '45 days'
                     AND (t."isActive" = false OR (t."expiresAt" IS NOT NULL AND t."expiresAt" < NOW()))""")
    dead = cur.fetchone()[0]
    rows.append((CRIT if dead else OK, "meta_accounts",
                 "%d conturi care sincronizau atârnă de token mort" % dead if dead
                 else "toate conturile vii au token valid"))

    cur.execute('SELECT COUNT(*) FROM tiktok_access_tokens WHERE "isActive"=true AND "needsReauth"=true')
    tt = cur.fetchone()[0]
    rows.append((CRIT if tt else OK, "tiktok_token",
                 "%d tokenuri cer re-autorizare" % tt if tt else "ok"))

    # sync_runs: o sursă care rulează dar eșuează 100% e mai rea decât una care nu rulează
    # status e enum (SyncRunStatus) → cast la text, altfel ILIKE crapă
    cur.execute("""SELECT source,
                          COUNT(*) FILTER (WHERE status::text ILIKE '%%fail%%') f,
                          COUNT(*) FILTER (WHERE status::text NOT ILIKE '%%fail%%') s
                   FROM sync_runs WHERE "createdAt" > NOW() - INTERVAL '24 hours'
                   GROUP BY source ORDER BY 1""")
    got = cur.fetchall()
    if not got:
        rows.append((WARN, "sync_runs", "nicio rulare în 24h"))
    for src, f, s in got:
        st = CRIT if (f and not s) else (WARN if f > s else OK)
        rows.append((st, "sync_runs.%s" % src.lower(), "%d ok / %d eșuate în 24h" % (s, f)))
    cx.close()


def check_awbprint(rows, ctx):
    """Sursa de adevăr livrare: dacă sync-ul ei stă, tot ce ține de livrat/transport e vechi."""
    dsn = _dsn("DATABASE_URL_AWBPRINT")
    if not dsn:
        rows.append((CRIT, "awbprint", "lipsește DATABASE_URL_AWBPRINT")); return
    import psycopg2
    cx = psycopg2.connect(dsn); cur = cx.cursor()
    cur.execute("SELECT MAX(synced_at), MAX(frisbo_created_at) FROM orders")
    synced, created = cur.fetchone()
    _judge(rows, "awbprint.sync", synced, 8, "heartbeat sync")
    _judge(rows, "awbprint.orders", created, 24, "cea mai nouă comandă")
    cx.close()


def check_profitdb(rows, ctx):
    """Motorul de profit + calea de marketing token-independentă (WMS)."""
    if not os.path.exists(PF_DB):
        rows.append((CRIT, "profitability.db", "fișierul lipsește")); return
    cx = sqlite3.connect(PF_DB); cx.execute("PRAGMA busy_timeout=8000;")
    cur = cx.cursor()
    cur_month = datetime.utcnow().strftime("%Y-%m")

    # WMS = sursă REDUNDANTĂ de marketing per-SKU: din 2026-06 profit_by_sku cade automat pe
    # cache când WMS lipsește pe o platformă (per zi). Deci WMS stale NU e critic cât timp cache-ul
    # acoperă platforma respectivă — altfel te-ar suna zilnic degeaba (WMS TikTok e mort de o lună,
    # dar cache-ul TikTok e proaspăt din tiktok_warehouse_sync). Critic DOAR dacă pică AMBELE.
    cache_fresh = ctx.get("cache_fresh", {})
    for src, plat, sla, note in (("fb", "meta", 36, "WMS Facebook"), ("tt", "tiktok", 48, "WMS TikTok")):
        cur.execute("SELECT MAX(date) FROM wms_ad_spend WHERE source=?", (src,))
        mx = cur.fetchone()[0]
        age = _age_h(mx)
        stale = age is None or age > sla
        covered = cache_fresh.get(plat, False)
        if not stale:
            rows.append((OK, "wms.%s" % src, "%s · %.0fh · %s" % (str(mx)[:16], age or 0, note)))
        elif covered:
            rows.append((WARN, "wms.%s" % src, "%s vechi %.0fh — dar cache %s e proaspăt, fallback acoperă (%s)"
                         % (str(mx)[:16], age or 0, plat, note)))
        else:
            rows.append((CRIT, "wms.%s" % src, "%s vechi %.0fh ȘI cache %s stale → marketing per-SKU descoperit (%s)"
                         % (str(mx)[:16], age or 0, plat, note)))

    cur.execute("SELECT MAX(created_at) FROM profit_orders")
    _judge(rows, "profit_orders", cur.fetchone()[0], 30)

    for tbl, key in (("profit_order_lines", "profit_lines"), ("profit_marketing_override", "marketing_ovr")):
        cur.execute("SELECT MAX(month) FROM %s" % tbl)
        mx = cur.fetchone()[0]
        rows.append((OK if mx and mx >= cur_month else CRIT, key,
                     "ultima lună %s (curentă %s)" % (mx, cur_month)))

    # maparea WMS: dacă suplimentul e gol, marketingul per-SKU cade tăcut pe cache
    for tbl in ("wms_nomen", "wms_nomen_extra", "wms_product_group", "wms_product_group_extra"):
        try:
            n = cur.execute("SELECT COUNT(*) FROM %s" % tbl).fetchone()[0]
        except sqlite3.Error:
            n = 0
        rows.append((CRIT if not n else OK, "mapare.%s" % tbl, "%d reguli" % n))
    cx.close()


def check_heartbeats(rows, ctx):
    """Dead-man-switch: cronuri care pinguie pe SUCCES (heartbeat.py). Overdue = n-a rulat/a picat.
    Prinde exact golul pe care prospețimea datelor nu-l acoperă („cronul nu s-a executat deloc")."""
    if not os.path.exists(PF_DB):
        return
    cx = sqlite3.connect(PF_DB); cx.execute("PRAGMA busy_timeout=8000;")
    try:
        hb = cx.execute("SELECT name, last_ping, expected_interval_min, note FROM cron_heartbeat").fetchall()
    except sqlite3.Error:
        cx.close(); return
    cx.close()
    for name, last, interval, note in hb:
        if not interval:
            rows.append((WARN, "hb.%s" % name, "fără interval setat (nu pot judeca overdue)")); continue
        age = _age_h(last)
        # grace: 2× intervalul + 30 min buffer (toleranță la jitter de cron / rulare lentă)
        limit_h = (interval * 2 + 30) / 60.0
        det = "ultimul ping %s · %.1fh (interval %dmin)" % (str(last)[:16], age or 0, interval)
        rows.append((CRIT if (age is None or age > limit_h) else OK, "hb.%s" % name,
                     (det + (" · " + note if note else "")) if age is not None else "NICIUN ping vreodată"))


def main():
    ap = argparse.ArgumentParser(description="Watchdog de prospețime a datelor ARONA")
    ap.add_argument("--email", help="destinatar (email trimis DOAR dacă e ceva roșu)")
    ap.add_argument("--from", dest="sender", default="gheorghe.beschea@overheat.agency")
    ap.add_argument("--key", default="/root/Scripturi/google_credentials.json")
    ap.add_argument("--always", action="store_true", help="trimite mailul chiar dacă totul e verde")
    a = ap.parse_args()

    rows = []
    ctx = {}  # check_metrics populează cache_fresh înainte ca check_profitdb să-l citească
    for fn in (check_metrics, check_awbprint, check_profitdb, check_heartbeats):
        try:
            fn(rows, ctx)
        except Exception as e:
            rows.append((CRIT, fn.__name__, "checkul însuși a crăpat: %s: %s" % (type(e).__name__, str(e)[:120])))

    bad = [r for r in rows if r[0] == CRIT]
    warn = [r for r in rows if r[0] == WARN]
    good = [r for r in rows if r[0] == OK]

    out = ["DATA HEALTH ARONA — %s UTC" % datetime.utcnow().strftime("%Y-%m-%d %H:%M"), ""]
    for title, group in (("🔴 CRITIC", bad), ("🟡 ATENȚIE", warn), ("🟢 OK", good)):
        if not group:
            continue
        out.append("%s (%d)" % (title, len(group)))
        out += ["  %-22s %s" % (k, d) for _, k, d in group]
        out.append("")
    out.append("Verificăm DATELE produse, nu dacă a rulat jobul — un cron poate scrie în log "
               "și când sync-ul eșuează (așa am pierdut 11 zile de Meta în iunie).")
    report = "\n".join(out)
    print(report)

    if a.email and (bad or a.always):
        sev = "🔴 %d probleme" % len(bad) if bad else "🟢 totul ok"
        try:
            _send_email(a.email, "[data-health] ARONA — %s" % sev, report, a.key, a.sender)
            print("\n[email] trimis către %s" % a.email)
        except Exception as e:
            print("\n[email] EȘUAT: %s: %s" % (type(e).__name__, e))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
