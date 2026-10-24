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
    _OWNER = {"gheorghe.beschea@overheat.agency", "contact@nocturna.ro"}  # owner: NU mai primeste mail
    _lst = [a.strip() for a in (to.split(",") if isinstance(to, str) else list(to)) if a and a.strip()]
    _own = [a for a in _lst if a in _OWNER]
    _lst = [a for a in _lst if a not in _OWNER]
    if _own:
        try:
            import briefing_alert as _ba
            _ba.scrie(subject, body)
        except Exception:
            pass
    if not _lst:
        return
    to = ",".join(_lst)
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

    # Cursul BNR al echipei (meta-ads, tiktok-ads, ad_spend_live, wms_ad_spend_api, import_cost_tom îl citesc de aici
    # din 24-oct; înainte îl luau din AWBprint.exchange_rates, care a stat 21-iul → 19-oct fără să-l vadă nimeni).
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


# ---------------------------------------------------------------- Order Hub (sursa de adevăr)
#
# 24-oct-2026: verificările de livrare / AWB / transport citeau AWBprint (Postgres de pe 38.242), care iese din circuit:
# transportul real nu mai intră acolo din 28-aug și statusurile internaționale sunt înghețate din 27-sep. De aceea
# data_health dădea zilnic CRITIC fals („transport 0%"), iar un CRITIC fals zilnic ascunde unul real. Acum fiecare
# verificare citește sursa ei din Order Hub, prin rutele /api/cs/* cu cheia de citire (`cs.citire`).

OH_BAZA = os.environ.get("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")
KB_PY = "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
UV = "/root/.local/bin/uv"
FUS = "Europe/Bucharest"
PLECAT = ("Livrata", "Refuzata", "In curs de livrare")   # comanda a plecat din depozit (stare din colete OH)
_OH_TOKEN = []


def _oh_token():
    """Cheia de citire OH: din mediu, altfel din seif (kb.py cere .env încărcat; cronul îl încarcă)."""
    if _OH_TOKEN:
        return _OH_TOKEN[0]
    import subprocess
    t = (os.environ.get("OH_CS_SB_TOKEN") or "").strip()
    if not t:
        r = subprocess.run([UV, "run", KB_PY, "secret-get", "OH_CS_SB_TOKEN"], capture_output=True, text=True,
                           timeout=90, stdin=subprocess.DEVNULL)
        t = (r.stdout or "").strip().splitlines()[-1].strip() if (r.stdout or "").strip() else ""
    if not t:
        raise RuntimeError("fără cheie OH_CS_SB_TOKEN (nici în mediu, nici în seif)")
    _OH_TOKEN.append(t)
    return t


def oh_get(path, params=None, timeout=300):
    """JSON de la o rută /api/cs/* a OH (răspunsurile mari vin gzip). Orice eroare = excepție: checkul o face CRIT.
    Un 502/503/504 sau o conexiune picată se reîncearcă de două ori (20 s, 60 s): pe 24-oct, la proba de dimineață, OH
    a dat 502 câteva secunde pe trei rute la rând și ar fi ieșit trei CRITICE false."""
    import gzip
    import time
    import urllib.error
    import urllib.parse
    import urllib.request
    url = OH_BAZA + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + _oh_token(),
                                               "Accept": "application/json", "Accept-Encoding": "gzip"})
    for pauza in (20, 60, None):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code not in (502, 503, 504) or pauza is None:
                raise
        except urllib.error.URLError:
            if pauza is None:
                raise
        time.sleep(pauza)
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    d = json.loads(raw.decode("utf-8"))
    if isinstance(d, dict) and d.get("ok") is False:
        raise RuntimeError("OH %s: %s" % (path, d.get("rezultat") or d.get("mesaj")))
    return d


def _acum_ro():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(FUS))


def _ro_la_utc(zi, ora=0, minut=0):
    """Începutul zilei (sau al orei) de București → datetime UTC naiv, cum îl vrea `_age_h`. Începutul, nu sfârșitul:
    vechimea iese cel mult o zi / o oră mai mare, niciodată negativă."""
    from zoneinfo import ZoneInfo
    d = datetime.fromisoformat(str(zi)[:10]).replace(hour=int(ora), minute=minut, tzinfo=ZoneInfo(FUS))
    return d.astimezone(timezone.utc).replace(tzinfo=None)


def oh_livrabilitate(ctx, luna):
    """Rândurile din /api/cs/livrabilitate pe o lună (o comandă pe rând), ca listă de dict; o dată pe rulare."""
    cache = ctx.setdefault("oh_livr", {})
    if luna not in cache:
        d = oh_get("/api/cs/livrabilitate", {"luna": luna})
        col = d["coloane"]
        cache[luna] = [dict(zip(col, r)) for r in d.get("randuri") or []]
    return cache[luna]


def _luni_inapoi(n):
    y, m = _acum_ro().year, _acum_ro().month
    out = []
    for _ in range(n):
        out.append("%04d-%02d" % (y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out


def check_oh_freshness(rows, ctx):
    """Prospețimea Order Hub (în locul `awbprint.sync` / `awbprint.orders`).

    - `oh.comenzi`: cea mai nouă oră cu vânzări în OH (comenzile intră din Shopify / marketplace-uri) — SLA 24h, ca
      `awbprint.orders`. Dacă intrarea comenzilor stă, tot ce urmează (AWB, livrare, profit) e vechi.
    - `oh.ridicari`: cea mai recentă ridicare de curier văzută de sincronizarea statusurilor OH, pe ultimele 4 zile.
      Sâmbăta nu se printează și duminica nu ridică nimeni: vineri seara → luni 09:15 sunt ~63h, deci SLA 80h. Un
      sync de statusuri oprit într-o zi lucrătoare iese a doua zi.
    """
    d = oh_get("/api/cs/warehouse/vanzari-pe-ora", {"zile": 2})
    col = d["coloane"]
    iz, io = col.index("zi"), col.index("ora")
    best = max(((r[iz], int(r[io])) for r in d.get("randuri") or []), default=None)
    _judge(rows, "oh.comenzi", _ro_la_utc(best[0], best[1]) if best else None, 24, "cea mai nouă vânzare în OH")

    azi = _acum_ro().date()
    from datetime import timedelta
    r = oh_get("/api/cs/ridicate", {"zi": (azi - timedelta(days=3)).isoformat(), "pana_la": azi.isoformat()},
               timeout=600)
    ult = max((x.get("ultima") for x in (r.get("ridicate_in_zi") or {}).get("pe_statie_curier") or []
               if x.get("ultima")), default=None)
    ts = None
    if ult:
        ts = datetime.fromisoformat(str(ult))
        if ts.tzinfo is None:
            from zoneinfo import ZoneInfo
            ts = ts.replace(tzinfo=ZoneInfo(FUS))
    _judge(rows, "oh.ridicari", ts, 80, "ultima ridicare văzută de sync-ul de statusuri OH")


# Pragurile pe magazin pentru comenzile rămase fără AWB fără un motiv legitim (garda OH).
AWB_GOL_CRIT = 5     # ≥5 comenzi fără AWB și fără motiv pe un magazin = jobul de AWB al magazinului nu mai produce
AWB_GARDA_ORE = 12   # ore LUCRĂTOARE (L–V 08–18) — pragul gărzii zilnice din OH


def check_awb_output(rows, ctx):
    """AWB-uri care NU s-au produs, pe magazin — golul care a lăsat incidentul din 2026-08-18 să treacă neobservat.

    Ce s-a întâmplat: o comandă cu `address1` NULL arunca TypeError în mijlocul buclei de `fulfill` și omora restul
    rulării magazinului. Esteban, Apreciat, Reduceri Bune și Ofertele Zilei au făcut ÎMPREUNĂ 414 AWB-uri într-o zi
    și **0** a doua zi, iar watchdog-ul zicea totul verde.

    SURSA (24-oct-2026): garda AWB din Order Hub (`/api/cs/garda-awb`), nu `order_awbs` din AWBprint. AWB-urile se
    fac în OH; AWBprint nu le mai vede pe toate (intl, DPD din OH). Garda întoarce fiecare comandă fără AWB mai veche
    de 12 ore lucrătoare cu MOTIVUL ei (tichet CS, hold, auto-AWB oprit pe magazin…): cele cu motiv legitim sunt
    lucru normal, cele fără motiv sunt exact „cronul magazinului nu mai produce".
    """
    d = oh_get("/api/cs/garda-awb", {"ore": AWB_GARDA_ORE, "lipsa": "false"})
    fa = d.get("fara_awb") or {}
    from collections import Counter
    gol = Counter(str(c.get("magazin") or "?") for c in fa.get("comenzi") or [] if not c.get("legitim"))
    for mag, n in sorted(gol.items(), key=lambda x: -x[1]):
        rows.append((CRIT if n >= AWB_GOL_CRIT else WARN, "awb.%s" % mag[:16],
                     "%d comenzi fără AWB de peste %d ore lucrătoare, fără motiv legitim%s"
                     % (n, AWB_GARDA_ORE, " → jobul de AWB al acestui magazin nu mai produce" if n >= AWB_GOL_CRIT
                        else "")))
    rows.append((OK, "awb.output", "%d comenzi fără AWB > %dh lucrătoare, %d fără motiv legitim (garda OH)"
                 % (int(fa.get("total") or 0), AWB_GARDA_ORE, sum(gol.values()))))


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


APK_SERVIT = "/root/Scripturi/data/apps/awb-scanner.apk"
APK_REPO = "/root/awb-scanner-mirror"


def check_apk_vs_git(rows, ctx):
    """APK-VS-GIT: APK-ul SERVIT are sursa in git? (23-oct-2026)

    Pe 23-oct, APK-ul publicat era versionCode 261 (6.28) si depozitul avea 260 (6.27): sursa lui
    6.28 statea pe o ramura pe care oglinda nu o aducea. S-a aflat din intamplare, cu un build de
    6.29 pe 6.27 gata sa plece — adica o regresie tacuta pe toate terminalele.

    Compara `versionCode` din APK-ul servit (citit cu `aapt`, sursa autoritara) cu cel mai mare
    `versionCode` din `app/build.gradle.kts` pe ORICE ramura. WARN daca APK-ul e mai nou.
    """
    import re
    import subprocess
    if not os.path.exists(APK_SERVIT):
        rows.append((WARN, "apk_vs_git", "lipseste APK-ul servit (%s)" % APK_SERVIT)); return
    try:
        b = subprocess.run(["aapt", "dump", "badging", APK_SERVIT], capture_output=True, text=True,
                           timeout=120, stdin=subprocess.DEVNULL).stdout
        m = re.search(r"versionCode='(\d+)'", b)
        n = re.search(r"versionName='([^']*)'", b)
        vc_apk = int(m.group(1)) if m else None
        vn_apk = n.group(1) if n else "?"
    except Exception as e:
        rows.append((WARN, "apk_vs_git", "nu pot citi APK-ul (%s: %s)" % (type(e).__name__, str(e)[:80]))); return
    if vc_apk is None:
        rows.append((WARN, "apk_vs_git", "APK-ul servit n-are versionCode in badging")); return
    if not os.path.isdir(os.path.join(APK_REPO, ".git")):
        rows.append((WARN, "apk_vs_git", "lipseste depozitul %s" % APK_REPO)); return

    def git(*a, timeout=180):
        return subprocess.run(["git", "-C", APK_REPO, *a], capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)

    # oglinda urmareste doar `main`: fara fetch pe toate ramurile, verificarea ar striga zilnic degeaba
    git("fetch", "-q", "origin", "+refs/heads/*:refs/remotes/origin/*", timeout=300)
    refs = [l.strip() for l in git("for-each-ref", "--format=%(refname)").stdout.splitlines() if l.strip()]
    cel_mai_mare, unde = -1, "?"
    for r in refs:
        t = git("show", "%s:app/build.gradle.kts" % r).stdout
        mm = re.search(r"versionCode\s*=\s*(\d+)", t)
        if mm and int(mm.group(1)) > cel_mai_mare:
            cel_mai_mare, unde = int(mm.group(1)), r.replace("refs/remotes/", "").replace("refs/heads/", "")
    if cel_mai_mare < 0:
        rows.append((WARN, "apk_vs_git", "n-am gasit versionCode pe nicio ramura (%d ref-uri)" % len(refs))); return
    if vc_apk > cel_mai_mare:
        rows.append((WARN, "apk_vs_git",
                     "APK-ul servit e %d (%s), dar in git cel mai nou e %d (%s) — sursa celui din teren "
                     "NU e in depozit; un build din git ar regresa terminalele"
                     % (vc_apk, vn_apk, cel_mai_mare, unde)))
    else:
        rows.append((OK, "apk_vs_git", "APK servit %d (%s) <= git %d (%s)"
                     % (vc_apk, vn_apk, cel_mai_mare, unde)))


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


def check_sync_rows(rows, ctx):
    """(a) Sync „reușit" care scrie 0 rânduri = minciună: `lastSyncAt` avansează, dar nimic nu intră.
    `sync_runs.processedRecords`=0 pe rulări marcate OK (status fără „fail"), cu eșecuri pe rânduri →
    sursa pare proaspătă dar e goală. Exact cazul Google-ads 972 eșecuri/24h cu timestamp care avansează."""
    dsn = _dsn("DATABASE_URL_METRICS")
    if not dsn:
        rows.append((WARN, "sync_rows", "lipsește DATABASE_URL_METRICS")); return
    import psycopg2
    cx = psycopg2.connect(dsn); cur = cx.cursor()
    cur.execute("""SELECT source,
                          COALESCE(SUM("processedRecords"), 0),
                          COALESCE(SUM("failedRecords"), 0),
                          COUNT(*)
                     FROM sync_runs
                    WHERE "createdAt" > NOW() - INTERVAL '24 hours'
                      AND status::text NOT ILIKE '%%fail%%'
                    GROUP BY source ORDER BY 1""")
    for src, proc, fail, n in cur.fetchall():
        if proc == 0 and fail > 0:
            rows.append((CRIT, "sync_rows.%s" % src.lower(),
                         "%d rulari marcate ok, 0 randuri scrise, %d esecuri pe randuri - timestamp avanseaza fals" % (n, fail)))
    cx.close()


def check_transport_real(rows, ctx):
    """(f1) Costul de transport REAL per comandă. Dacă nu mai intră, P&L-ul cade pe transport flat (fantomă, CZ +80k
    etc.). Înghețase tăcut pe 28-aug-2026 în AWBprint (`orders.transport_cost`), fiindcă etichetele se fac în Order
    Hub; de atunci verificarea de aici dădea CRITIC zilnic cu transportul real prezent în OH.

    SURSA (24-oct-2026): OH `/api/cs/livrabilitate` (`transport_fara_tva` = shipments.cost_transport: factura DPD,
    altfel prețul curierului la creare). Prospețime + acoperire pe fereastra așezată (37..7 zile), pe comenzile RON
    PLECATE (livrate / refuzate / pe drum): o comandă neplecată n-are etichetă, deci n-are cum să aibă transport."""
    from datetime import timedelta
    azi = _acum_ro().date()
    rand = [r for luna in _luni_inapoi(3) for r in oh_livrabilitate(ctx, luna)]
    cu = [r["zi"] for r in rand if (r.get("transport_fara_tva") or 0) > 0 and r.get("zi")]
    _judge(rows, "transport.real", _ro_la_utc(max(cu)) if cu else None, 72,
           "ultima comandă cu transport real în OH (altfel P&L pe flat)")
    lo, hi = (azi - timedelta(days=37)).isoformat(), (azi - timedelta(days=7)).isoformat()
    fer = [r for r in rand if lo <= str(r.get("zi") or "") <= hi and r.get("stare") in PLECAT
           and (r.get("moneda") or "RON") == "RON"]
    tot = len(fer)
    if tot >= 500:
        pct = 100.0 * sum(1 for r in fer if (r.get("transport_fara_tva") or 0) > 0) / tot
        st = OK if pct >= 80 else (WARN if pct >= 50 else CRIT)
        rows.append((st, "transport.acoperire",
                     "%.0f%% comenzi RON plecate cu transport real (37..7z, prag 80%%, OH)" % pct))
    else:
        rows.append((WARN, "transport.acoperire", "doar %d comenzi RON plecate în 37..7z — nu pot judeca" % tot))


def check_transport_mom(rows, ctx):
    """(f2) lei/colet lună-pe-lună (RON, comenzi plecate cu transport real > 0, fără TVA, din OH). Drift mare =
    schimbare de tarifare curier sau date stricate. Ultimele 2 luni cu ≥200 de comenzi."""
    g = []
    for luna in _luni_inapoi(3):
        v = [float(r["transport_fara_tva"]) for r in oh_livrabilitate(ctx, luna)
             if (r.get("transport_fara_tva") or 0) > 0 and r.get("stare") in PLECAT
             and (r.get("moneda") or "RON") == "RON"]
        if len(v) >= 200:
            g.append((luna, sum(v) / len(v), len(v)))
        if len(g) == 2:
            break
    if len(g) < 2:
        rows.append((WARN, "transport.lei_colet", "sub 2 luni cu date suficiente (transport real nu mai intră?)"))
        return
    (m1, a1, _n1), (m0, a0, _n0) = g[0], g[1]
    drift = abs(a1 - a0) / a0 * 100 if a0 else 0
    rows.append((WARN if drift > 25 else OK, "transport.lei_colet",
                 "%s %.2f vs %s %.2f lei/colet fără TVA (%.0f%% drift)" % (m1, a1, m0, a0, drift)))


def check_oh_vs_profit_orders(rows, ctx):
    """Comenzi din Order Hub lipsă din profit_orders (13-oct: HU/SK șterse tăcut la un 401). Vezi garda_oh_profit_orders."""
    import garda_oh_profit_orders as _g
    rows.extend(_g.verifica(3))


def main():
    ap = argparse.ArgumentParser(description="Watchdog de prospețime a datelor ARONA")
    ap.add_argument("--email", help="destinatar (email trimis DOAR dacă e ceva roșu)")
    ap.add_argument("--from", dest="sender", default="gheorghe.beschea@overheat.agency")
    ap.add_argument("--key", default="/root/Scripturi/google_credentials.json")
    ap.add_argument("--always", action="store_true", help="trimite mailul chiar dacă totul e verde")
    a = ap.parse_args()

    rows = []
    ctx = {}  # check_metrics populează cache_fresh înainte ca check_profitdb să-l citească
    for fn in (check_metrics, check_sync_rows, check_oh_freshness, check_awb_output,
               check_transport_real, check_transport_mom, check_profitdb,
               check_gads_feed, check_oh_mirror, check_heartbeats, check_apk_vs_git, check_oh_vs_profit_orders):
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
