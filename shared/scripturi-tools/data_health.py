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
import os, re, sys, json, argparse, sqlite3
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

# Sursele care TREBUIE să ruleze programat, cu SLA-ul în minute. Toate patru sunt cronuri
# Inngest la 10 minute; SLA-ul e larg intenționat, ca o rulare ratată să nu dea alarmă, dar
# o oprire reală să iasă în aceeași zi. Adaugi o sursă programată nouă → o treci și aici,
# altfel moartea ei rămâne invizibilă (vezi comentariul din check_metrics).
SCHEDULED_SOURCES = {
    "SHOPIFY": 120,
    "FACEBOOK_ADS": 120,
    "GOOGLE_ADS": 120,
    "TIKTOK_ADS": 120,
}


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

    # sync_runs: o sursă care rulează dar eșuează 100% e mai rea decât una care nu rulează.
    # ⚠️ ȘI MAI RĂU: o sursă care TACE COMPLET. Varianta veche făcea GROUP BY source, deci
    # raporta doar sursele care AVEAU rânduri — o sursă oprită de tot dispărea pur și simplu
    # din raport, fără nicio linie roșie. Exact așa a stat TIKTOK_ADS nesincronizat din
    # 2026-05 până pe 2026-09-08: funcțiile Inngest nu erau înregistrate, nu rula nimic, și
    # fiindcă nu exista niciun rând, watchdog-ul n-avea ce să înroșească. Absența nu se
    # măsura. Acum sursele așteptate sunt declarate explicit și lipsa lor e CRIT.
    # status e enum (SyncRunStatus) → cast la text, altfel ILIKE crapă
    cur.execute("""SELECT source::text,
                          COUNT(*) FILTER (WHERE status::text ILIKE '%%fail%%') f,
                          COUNT(*) FILTER (WHERE status::text NOT ILIKE '%%fail%%') s,
                          MAX("startedAt") ultima
                   FROM sync_runs
                   WHERE "createdAt" > NOW() - INTERVAL '24 hours'
                   GROUP BY 1 ORDER BY 1""")
    got = {r[0]: r[1:] for r in cur.fetchall()}
    if not got:
        rows.append((CRIT, "sync_runs", "nicio rulare în 24h, pe nicio sursă"))
    for src in sorted(set(got) | set(SCHEDULED_SOURCES)):
        if src not in got:
            rows.append((CRIT, "sync_runs.%s" % src.lower(),
                         "TĂCERE TOTALĂ 24h — cron programat lipsă (funcție neînregistrată în Inngest?)"))
            continue
        f, s, ultima = got[src]
        st = CRIT if (f and not s) else (WARN if f > s else OK)
        det = "%d ok / %d eșuate în 24h" % (s, f)
        # ⚠️ "startedAt" e timestamp FĂRĂ fus, stocat în UTC, iar NOW() e timestamptz pe
        # Europe/Bucharest: comparate direct, fereastra se mută cu 3 ore și verificarea
        # iese fals-verde. De-aia se scade explicit din ora UTC.
        sla_min = SCHEDULED_SOURCES.get(src)
        if sla_min and ultima is not None:
            age_min = (datetime.utcnow() - ultima).total_seconds() / 60.0
            if age_min > sla_min:
                st = CRIT
                det += " · ultima acum %.0f min (SLA %d)" % (age_min, sla_min)
        rows.append((st, "sync_runs.%s" % src.lower(), det))
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
    # Cursul BNR din AWB Arona (îl folosesc P&L-ul din AWB, meta-ads și tiktok-ads): a stat pe 21-iul până pe
    # 19-oct fiindcă BNR a mutat fluxul XML, iar nimeni n-a văzut — consumatorii îl prelungesc tăcut.
    cur.execute("SELECT MAX(rate_date) FROM exchange_rates")
    _judge(rows, "awbprint.curs_bnr", cur.fetchone()[0], 96, "curs BNR din AWB Arona")
    cx.close()


# ⚠️ Sursa a fost /root/Scripturi/logs/xc_awb_events.jsonl, scris de xc_fulfill.sh. La cutover-ul pe
# Order Hub (20-aug) acel script a ajuns sa sara toata flota, deci logul a ramas ORFAN: producatorul
# a fost retras intentionat, verificatorul a ramas sa se uite la el si a raportat 18 zile la rand
# „niciun AWB — cronul e mort complet", in timp ce depozitul facea ~2.500 etichete pe zi.
# Acum masuram EFECTUL la sursa lui: etichetele din AWBprint. Vezi [[awb-log-orfan-dupa-cutover-oh]].
# Praguri pe FEREASTRA ORARĂ de până acum (vezi mai jos), calibrate pe 7 zile reale de trafic:
# Praguri CALIBRATE prin backtest pe 9 zile reale (nu ghicite). Cu 5/2 și fără condiția de consistență
# dădea 3 fals pozitive de weekend: Rossi și Covoria aveau 0 AWB la 09:15, dar 8 pe toată ziua — magazine
# de 8-18 AWB/zi, la care „0 dimineața" e variație normală, nu defect.
AWB_BASE_CRIT = 20   # doar magazine cu volum mare: la ele 0 la ora asta e imposibil să fie întâmplare
AWB_BASE_WARN = 5
AWB_MIN_DAYS  = 6    # ...ȘI să fi produs în ≥6 din ultimele 7 zile (consistență, nu doar medie)


def check_awb_output(rows, ctx):
    """AWB-uri PRODUSE per magazin — golul care a lăsat incidentul din 2026-08-18 să treacă neobservat.

    Ce s-a întâmplat: o comandă cu `address1` NULL arunca TypeError în mijlocul buclei de `fulfill` și
    omora restul rulării magazinului. Esteban, Apreciat, Reduceri Bune și Ofertele Zilei au făcut ÎMPREUNĂ
    414 AWB-uri într-o zi și **0** a doua zi. Watchdog-ul zicea totul verde: `awbprint.sync` și
    `awbprint.orders` măsoară COMENZI CARE INTRĂ, nu ETICHETE CARE IES. Logul agregat al cronului scria
    „AWB=15 · magazine=23", adică sănătos. Nimeni n-a aflat până n-a întrebat un om.

    CUM compar (important): NU ziua întreagă față de media zilnică — `data_health` rulează la 09:15, când
    ziua abia a început, deci orice prag pe total ar tăcea exact când e nevoie de el. Compar **aceeași
    fereastră orară**: AWB-urile de azi până la ora curentă vs media AWB-urilor din ultimele 7 zile
    până la ACEEAȘI oră. Măsurat pe date reale: până la 09:15 Esteban are mereu între 96 (duminica) și
    632 (marți) — un 0 la ora aia nu e „zi liniștită", e cron mort.
    """
    # SURSA: AWBprint.order_awbs, nu logul xc_awb_events.jsonl. Logul a ramas ORFAN la cutover-ul pe
    # Order Hub (20-21 aug): xc_fulfill.sh sare toate magazinele, deci nu mai scrie nimic, iar
    # alarma „niciun AWB in 8 zile" a sunat fals 44 de zile la rand cu depozitul facand ~3.000 AWB/zi.
    # order_awbs.created_at e ora LOCALA (Europe/Bucharest) naiva; shipment_created_at e 100% NULL.
    dsn = _dsn("DATABASE_URL_AWBPRINT")
    if not dsn:
        rows.append((WARN, "awb.output", "lipsește DATABASE_URL_AWBPRINT")); return

    from collections import defaultdict
    from datetime import timedelta
    import psycopg2

    win = defaultdict(lambda: defaultdict(int))   # magazin -> zi -> AWB până la ora curentă
    cx = psycopg2.connect(dsn); cur = cx.cursor()
    # ziua si ora le da BAZA (Europe/Bucharest), nu VPS-ul: ceasul VPS e pe alt fus (CET) si fereastra
    # orara ar aluneca o ora fata de created_at
    cur.execute("SELECT current_date, to_char(localtime, 'HH24:MI')")
    d0, hhmm = cur.fetchone()
    today = d0.isoformat()
    hist_days = [(d0 - timedelta(days=i)).isoformat() for i in range(1, 8)]
    cur.execute("""
        SELECT COALESCE(s.name, '?'), a.created_at::date::text, COUNT(*)
          FROM order_awbs a
          JOIN orders o ON o.id = a.order_id
          LEFT JOIN stores s ON s.uid = o.store_uid
         WHERE a.created_at >= current_date - 7
           AND a.created_at::time <= localtime
           AND COALESCE(a.is_return_label, false) = false
         GROUP BY 1, 2""")
    for store, day, n in cur.fetchall():
        win[store.split(".")[0]][day] += n
    cx.close()

    if not win:
        rows.append((CRIT, "awb.output", "niciun AWB în order_awbs în ultimele 8 zile — nici depozitul, nici sync-ul AWBprint nu produc"))
        return

    live = tot_today = 0
    for store, byday in sorted(win.items()):
        vals = [byday.get(d, 0) for d in hist_days]
        base = sum(vals) / 7.0
        nz = sum(1 for v in vals if v > 0)      # în câte din cele 7 zile a produs până la ora asta
        n = byday.get(today, 0)
        tot_today += n
        if n:
            live += 1
        if n == 0 and base >= AWB_BASE_CRIT and nz >= AWB_MIN_DAYS:
            rows.append((CRIT, "awb.%s" % store[:16],
                         "0 AWB azi până la %s, dar media ultimelor 7 zile la aceeași oră = %.0f "
                         "(a produs în %d/7 zile) → cronul acestui magazin nu mai produce" % (hhmm, base, nz)))
        elif n == 0 and base >= AWB_BASE_WARN and nz == 7:
            rows.append((WARN, "awb.%s" % store[:16],
                         "0 AWB azi până la %s (media 7 zile la aceeași oră = %.1f, a produs în toate cele "
                         "7 zile) — magazin mic, poate fi doar o dimineață liniștită" % (hhmm, base)))
        elif base >= 20 and n < base * 0.25:
            rows.append((WARN, "awb.%s" % store[:16],
                         "doar %d AWB azi până la %s vs media %.0f (sub 25%%)" % (n, hhmm, base)))
    rows.append((OK, "awb.output", "%d AWB azi până la %s, pe %d magazine active (din %d cu istoric)"
                 % (tot_today, hhmm, live, len(win))))


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

    # PE MAGAZIN, nu doar pe tot tabelul (22-oct-2026): Esteban a rămas fără comenzile din 13–21 oct (9.622 pierdute,
    # ~1,2 M lei), iar verificarea de mai sus a rămas verde fiindcă celelalte magazine țineau MAX(created_at)
    # proaspăt. Un magazin cu volum (≥ 50 de comenzi în ultimele 14 zile) a cărui ultimă comandă e mai veche de 36 h
    # = descărcarea lui s-a oprit. Plus: luna curentă comparată cu backup-ul de dinaintea ultimului sync
    # (`_bak_po_<lună>`): un prefix cu sub 90% din backup a fost rescris cu o listă incompletă.
    # Magazinele scoase din sincronizare (`active=0` în stores.csv: Belasil, vândut 20-oct) nu se mai descarcă, deci nu
    # se judecă. Un magazin activ fără comenzi noi poate fi și liniște reală (Orice Redus, 20–22 oct), de aceea
    # prospețimea pe prefix e AVERTISMENT; compararea cu backup-ul de mai jos rămâne critică.
    inactive = set()
    try:
        import csv as _csv
        with open("/root/Scripturi/stores.csv", newline="") as fh:
            inactive = {r.get("prefix", "").strip() for r in _csv.DictReader(fh) if str(r.get("active", "1")).strip() == "0"}
    except (OSError, ValueError):
        pass
    cur.execute("""SELECT prefix, MAX(created_at), COUNT(*) FROM profit_orders
                    WHERE created_at >= datetime('now', '-14 days') GROUP BY prefix HAVING COUNT(*) >= 50""")
    for pfx, mx, n in cur.fetchall():
        age = _age_h(mx)
        if pfx not in inactive and age is not None and age > 36:
            rows.append((WARN, "profit_orders.%s" % pfx,
                         "ultima comandă %s · %.0fh (SLA 36h) pe %d comenzi/14z → descărcarea s-a oprit sau magazinul e liniștit"
                         % (str(mx)[:16], age, n)))
    bak = "_bak_po_" + cur_month.replace("-", "")
    try:
        cur.execute("SELECT b.prefix, COUNT(*) FROM %s b WHERE b.month=? GROUP BY b.prefix" % bak, (cur_month,))
        in_bak = dict(cur.fetchall())
        cur.execute("SELECT prefix, COUNT(*) FROM profit_orders WHERE month=? GROUP BY prefix", (cur_month,))
        viu = dict(cur.fetchall())
        for pfx, n in in_bak.items():
            if n >= 100 and viu.get(pfx, 0) < n * 0.9:
                rows.append((CRIT, "profit_orders.%s" % pfx,
                             "luna %s: %d în %s, %d acum (−%.0f%%) → sync-ul a rescris magazinul cu o listă incompletă"
                             % (cur_month, n, bak, viu.get(pfx, 0), 100 - 100.0 * viu.get(pfx, 0) / n)))
    except sqlite3.Error:
        pass

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


OH_MIRROR_DB = "/root/Scripturi/data/oh_mirror.db"


def check_oh_mirror(rows, ctx):
    """Oglinda Order Hub din scanner (comenzi, linii, AWB-uri): dacă puntea OH → scanner stă, depozitul vede
    comenzi vechi și scannerul nu anunță nimic. Puntea rulează la 10 min + demon la 3 s."""
    if not os.path.exists(OH_MIRROR_DB):
        rows.append((CRIT, "oh_mirror", "lipsește %s" % OH_MIRROR_DB)); return
    cx = sqlite3.connect("file:%s?mode=ro" % OH_MIRROR_DB, uri=True, timeout=10)
    try:
        ts = cx.execute("SELECT MAX(updated_at) FROM oh_orders").fetchone()[0]
    finally:
        cx.close()
    # updated_at are fus orar (+03:00): îl citim întreg, altfel ora locală ar trece drept UTC (3h „mai proaspăt”)
    _judge(rows, "oh_mirror", datetime.fromisoformat(ts) if ts else None, 12, "punte Order Hub → scanner")


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


GADS_GUARD_STATE = "/root/Scripturi/data/gads_feed_guard.json"


def check_gads_feed(rows, ctx):
    """Feedul Google spune adevarul despre stoc? (gads_feed_guard.py)

    Sursa: incidentul 18-aug-2026. Excluderea unui produs din canalul Google in Shopify NU sterge
    oferta din Merchant Center - o lasa ORFANA, inghetata pe availability=IN_STOCK, si Google
    liciteaza la infinit pe marfa care nu exista. GD-MOB-1938930 a ars 3.579 lei in 40 de zile cu
    ZERO comenzi. Judecam REZULTATUL guard-ului, nu daca a rulat jobul.
    """
    if not os.path.exists(GADS_GUARD_STATE):
        rows.append((CRIT, "gads_feed", "starea lipseste - guard-ul nu a rulat niciodata"))
        return
    try:
        with open(GADS_GUARD_STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception as e:
        rows.append((CRIT, "gads_feed", "stare ilizibila: %s" % e))
        return

    age = _age_h(st.get("moment"))
    if age is None or age > 26:
        rows.append((CRIT, "gads_feed", "ultima rulare %s (%.0fh, SLA 26h)"
                     % (str(st.get("moment"))[:16], age or 0)))
        return

    if st.get("esecuri"):
        rows.append((CRIT, "gads_feed.surse", "%d surse indisponibile: %s"
                     % (len(st["esecuri"]), "; ".join(st["esecuri"])[:120])))

    mag = st.get("magazine", [])
    scos = sum(m.get("de_scos", 0) for m in mag)
    pus = sum(m.get("de_pus", 0) for m in mag)
    err = [e for m in mag for e in m.get("erori", [])]
    trunc = [m["magazin"] for m in mag if m.get("jurnal_trunchiat")]

    if err:
        rows.append((CRIT, "gads_feed.erori", "%d scrieri esuate: %s" % (len(err), "; ".join(err)[:120])))
    if trunc:
        rows.append((WARN, "gads_feed.jurnal", "jurnal trunchiat pe: %s" % ", ".join(trunc)))

    # Judecam ce a RAMAS nereparat, nu ce s-a detectat: dupa un apply reusit detectarile sunt
    # istorie, iar ramasele sunt zero. Altfel checkul e rosu exact fiindca reparatia a mers.
    ram_s = sum(m.get("ramase_scos", m.get("de_scos", 0)) for m in mag)
    ram_p = sum(m.get("ramase_pus", m.get("de_pus", 0)) for m in mag)
    if st.get("aplicat") and (ram_s or ram_p):
        rows.append((CRIT, "gads_feed",
                     "dupa apply au ramas %d de scos / %d de pus - reparatia nu prinde" % (ram_s, ram_p)))
    elif st.get("aplicat") and (scos or pus):
        rows.append((OK, "gads_feed", "%s - reparat: %d scoase / %d puse pe %d magazine"
                     % (str(st.get("moment"))[:16], scos, pus, len(mag))))
    elif scos or pus:
        rows.append((WARN, "gads_feed", "%d de scos / %d de pus (rulare dry-run)" % (scos, pus)))
    else:
        rows.append((OK, "gads_feed", "%s - feed sincron cu stocul pe %d magazine"
                     % (str(st.get("moment"))[:16], len(mag))))


def main():
    ap = argparse.ArgumentParser(description="Watchdog de prospețime a datelor ARONA")
    ap.add_argument("--email", help="destinatar (email trimis DOAR dacă e ceva roșu)")
    ap.add_argument("--from", dest="sender", default="gheorghe.beschea@overheat.agency")
    ap.add_argument("--key", default="/root/Scripturi/google_credentials.json")
    ap.add_argument("--always", action="store_true", help="trimite mailul chiar dacă totul e verde")
    a = ap.parse_args()

    rows = []
    ctx = {}  # check_metrics populează cache_fresh înainte ca check_profitdb să-l citească
    for fn in (check_metrics, check_awbprint, check_awb_output, check_profitdb,
               check_gads_feed, check_oh_mirror, check_heartbeats):
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
