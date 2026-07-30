#!/usr/bin/env python3
"""Sincronizează COADA DE ADRESE CS într-o SQLite persistentă pe VPS — sursa de lucru pt găsit reguli noi de
auto-corecție. Adresele pe care cron-ul le lasă la CS (hold `bad-address`/`awb-esec-repetat`, needs-geocoder)
sunt scoase din event-log + AWBprint și UPSERT-ate în `cs_queue.db`. Cele care ulterior au primit AWB → `resolved`.

Rulare (VPS, are DATABASE_URL_AWBPRINT în env): `python3 cs_queue_sync.py [--days N]`. Idempotent (upsert).
Citire: `python3 cs_queue_sync.py --list [--shop EST] [--limit 50]` sau direct din SQLite. NU scrie nimic în afară de DB.
"""
import sqlite3, json, os, re, sys, argparse
from datetime import date, timedelta, datetime, timezone

DB = os.environ.get("CS_QUEUE_DB", "/root/Scripturi/data/cs_queue.db")
EVENT_LOG = os.environ.get("XC_EVENT_LOG", "/root/Scripturi/logs/xc_awb_events.jsonl")
SCHEMA = """CREATE TABLE IF NOT EXISTS cs_queue (
  order_number TEXT PRIMARY KEY, shop TEXT, address1 TEXT, address2 TEXT, city TEXT, province TEXT, zip TEXT,
  reason TEXT, first_seen TEXT, last_seen TEXT, status TEXT DEFAULT 'open', observatie TEXT, notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_cs_status ON cs_queue(status);
CREATE INDEX IF NOT EXISTS idx_cs_shop ON cs_queue(shop);"""
# status: 'open' = de lucrat (potențial corectabilă) · 'resolved' = a primit AWB · 'uncorrectable' = marcată
# manual ca IREPARABILĂ (junk/fără stradă/intl) → NU mai apare în --list (lucrăm DOAR pe 'open').


def _con():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.executescript(SCHEMA)
    cols = {r[1] for r in c.execute("PRAGMA table_info(cs_queue)")}  # migrare pt DB vechi
    if "notes" not in cols:
        c.execute("ALTER TABLE cs_queue ADD COLUMN notes TEXT")
    if "observatie" not in cols:
        c.execute("ALTER TABLE cs_queue ADD COLUMN observatie TEXT")
    return c


def _pg(url):
    import pg8000
    from urllib.parse import urlparse, unquote
    u = urlparse(url)
    c = pg8000.connect(user=unquote(u.username or ""), password=unquote(u.password or ""),
                       host=u.hostname, port=u.port or 5432, database=(u.path or "").lstrip("/"), ssl_context=True)
    c.autocommit = True
    return c


def _shop(onum):
    m = re.match(r"[A-Z]+", onum or "")
    return m.group(0) if m else ""


# Lungimea codului poștal pe țară (cifre): RO=6, BG=4, CZ/PL=5. Magazinele intl după prefix.
_ZIP_LEN = {"RO": 6, "BG": 4, "CZ": 5, "PL": 5}


def _country(shop):
    if shop == "BONBG":
        return "BG"
    if shop in ("CZ", "PL"):
        return shop
    return "RO"  # restul magazinelor = România


def _zip_ok(zp, shop):
    d = re.sub(r"\D", "", zp or "")  # doar cifrele (CZ „123 45", PL „12-345")
    return len(d) == _ZIP_LEN.get(_country(shop), 6)


def _diagnose(ad, reason, shop=""):
    """OBSERVAȚIE auto: ce pare în neregulă cu adresa (hint de lucru pt CS / găsit reguli).
    Se reîmprospătează la fiecare sync (reflectă adresa curentă). Distinctă de `notes` (adnotarea manuală).
    Validarea zip e pe ȚARĂ (RO 6 cifre, BG 4, CZ/PL 5) — nu marca intl ca „zip nevalid"."""
    a1 = (ad.get("address1") or "").strip()
    a2 = (ad.get("address2") or "").strip()
    zp = (ad.get("zip") or "").strip()
    city = (ad.get("city") or "").strip()
    prov = (ad.get("province") or "").strip()
    h = []
    if not a1 and not a2:
        h.append("adresă goală")
    elif not re.search(r"\d", a1 + " " + a2):
        h.append("fără număr")
    if not zp:
        h.append("fără cod poștal")
    elif not _zip_ok(zp, shop):
        h.append("zip nevalid (%s)" % _country(shop))
    if not city:
        h.append("fără localitate")
    if not prov and _country(shop) == "RO":  # județ contează doar la RO; intl nu-l cere
        h.append("fără județ")
    if city and re.fullmatch(r"\d+", city):
        h.append("localitate = cod poștal")
    if reason == "awb-esec-repetat":
        h.append("AWB eșuat repetat")
    return "; ".join(h) if h else reason


def sync(days):
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    made, held = set(), {}
    for line in open(EVENT_LOG, encoding="utf-8"):
        try:
            e = json.loads(line)
        except Exception:
            continue
        o, ts = e.get("order"), e.get("ts", "")
        if not o or ts < cutoff:
            continue
        if e.get("kind") in ("awb", "release-awb") and e.get("result") == "ok":
            made.add(o)
        if e.get("kind") == "hold" and e.get("reason") in ("bad-address", "awb-esec-repetat"):
            held[o] = e.get("reason")
    open_cs = {o: r for o, r in held.items() if o not in made}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con = _con(); cur = con.cursor()
    cur.execute("SELECT order_number FROM cs_queue WHERE status='open'")
    open_now = [o for (o,) in cur.fetchall()]
    # AWBprint: (a) adrese pt NOILE holds; (b) status AWB REAL pt TOATE cele open — ca să marcăm resolved
    # inclusiv comenzile rezolvate pe alte căi (CS manual / Shopify Flow / mai vechi decât fereastra event-log),
    # pe care detecția pe event-log le rata (→ coada raporta fals sute de „open" care de fapt aveau AWB).
    addr = {}; awb_real = set()
    try:
        aw = _pg(os.environ["DATABASE_URL_AWBPRINT"]); c = aw.cursor()
        if open_cs:
            c.execute("SELECT order_number, shipping_address FROM orders WHERE order_number = ANY(%s)", (list(open_cs),))
            for onum, sa in c.fetchall():
                addr[onum] = sa if isinstance(sa, dict) else json.loads(sa)
        if open_now:
            c.execute("SELECT order_number FROM orders WHERE order_number = ANY(%s) "
                      "AND (COALESCE(tracking_number,'')<>'' OR COALESCE(awb_count,0)>0)", (open_now,))
            awb_real = {r[0] for r in c.fetchall()}
    except Exception as ex:
        print("AWBprint indisponibil: %s" % ex, file=sys.stderr)
    # RESOLVED = are AWB în event-log SAU AWB REAL în AWBprint
    for o in open_now:
        if o in made or o in awb_real:
            cur.execute("UPDATE cs_queue SET status='resolved', last_seen=? WHERE order_number=?", (now, o))
    ins = upd = 0
    for o, reason in open_cs.items():
        ad = addr.get(o) or {}
        obs = _diagnose(ad, reason, _shop(o))
        row = (o, _shop(o), ad.get("address1"), ad.get("address2"), ad.get("city"), ad.get("province"),
               (ad.get("zip") or "").strip(), reason)
        cur.execute("SELECT status FROM cs_queue WHERE order_number=?", (o,))
        ex = cur.fetchone()
        if ex:
            # PĂSTREAZĂ 'uncorrectable' (marcat manual) ȘI 'resolved' (are AWB — nu-l readuce la 'open', altfel
            # bucla asta suprascria rezoluția din pasul AWBprint de mai sus). `observatie` auto = reîmprospătată.
            newst = ex[0] if ex[0] in ("uncorrectable", "resolved") else "open"
            cur.execute("UPDATE cs_queue SET shop=?,address1=?,address2=?,city=?,province=?,zip=?,reason=?,"
                        "observatie=?,last_seen=?,status=? WHERE order_number=?",
                        row[1:] + (obs, now, newst, o)); upd += 1
        else:
            cur.execute("INSERT INTO cs_queue(order_number,shop,address1,address2,city,province,zip,reason,"
                        "observatie,first_seen,last_seen,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,'open')",
                        row + (obs, now, now)); ins += 1
    con.commit()
    cur.execute("SELECT COUNT(*) FROM cs_queue WHERE status='open'")
    print("cs_queue: +%d noi · %d actualizate · %d deschise total (fereastră %d zile)" % (ins, upd, cur.fetchone()[0], days))
    con.close()


def lst(shop, limit):
    con = _con(); cur = con.cursor()
    q = ("SELECT order_number,address1,address2,city,province,zip,reason,observatie,notes "
         "FROM cs_queue WHERE status='open'")
    p = []
    if shop:
        q += " AND shop=?"; p.append(shop.upper())
    q += " ORDER BY first_seen DESC LIMIT ?"; p.append(limit)
    cur.execute(q, p)
    rows = cur.fetchall()
    print("=== COADĂ CS (%d deschise%s) ===" % (len(rows), ", shop=" + shop if shop else ""))
    for onum, a1, a2, city, prov, zp, reason, obs, notes in rows:
        print("  %-11s a1=%s" % (onum, a1 or "-"))
        print("             a2=%-28s loc=%-16s jud=%-10s zip=%s" %
              ((a2 or "-")[:28], (city or "-")[:16], (prov or "-")[:10], zp or "-"))
        print("             observație: %s  [%s]" % (obs or "-", reason))
        if notes:
            print("             notă: %s" % notes)
    con.close()


def mark(orders, status, note):
    con = _con(); cur = con.cursor(); n = 0
    for o in orders:
        cur.execute("UPDATE cs_queue SET status=?, notes=COALESCE(?,notes), last_seen=? WHERE order_number=?",
                    (status, note, datetime.now(timezone.utc).isoformat(timespec="seconds"), o.strip()))
        n += cur.rowcount
    con.commit(); con.close()
    print("marcate %s: %d comenzi" % (status, n))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--shop", default="")
    ap.add_argument("--limit", type=int, default=80)
    ap.add_argument("--mark", default="", help="listă order# separate prin virgulă, de marcat")
    ap.add_argument("--status", default="uncorrectable", help="uncorrectable | open | resolved")
    ap.add_argument("--note", default=None, help="adnotare (de ce necorectabilă / răspunsul corect)")
    a = ap.parse_args()
    if a.mark:
        mark([x for x in a.mark.split(",") if x.strip()], a.status, a.note)
    elif a.list:
        lst(a.shop, a.limit)
    else:
        sync(a.days)
