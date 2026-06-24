"""
wms_ad_spend_sync.py — trage spend-ul de reclame PER-CAMPANIE/AD din sheet-ul WMS
(„WMS Facebook 3" + „WMS Tiktok", SS 12L1KlG4...) intr-un tabel in profitability.db.

De ce: sheet-ul WMS pulleaza spend-ul direct din conectorul Facebook/TikTok (INDEPENDENT
de tokenul OAuth Meta care pica ~la 60 zile) -> sursa robusta pt marketing PER-SKU.
Tab-ul „WMS Facebook" ramane doar cu ZIUA CURENTA (refresh orar), deci stocam in DB
(INSERT OR REPLACE pe cheie) ca sa ACUMULAM istoricul chiar daca sheet-ul nu-l mai tine.

RAW (per source/date/account/campaign/ad). Atributia pe grup/SKU + USD->RON se fac la consum
(wms_marketing.py). Tot aici tragem si tabelele de mapare (Nomenclator FB/TT + Product Group)
+ construim suplimentul (reguli + SKU->grup pt conturile simple lipsa din Nomenclatorul sheet).
"""
import sys, sqlite3, socket
socket.setdefaulttimeout(120)
from datetime import datetime, date
from google.oauth2 import service_account
from googleapiclient.discovery import build

SS = "12L1KlG4EXxe6OAeZROEeDipy-72iuUzdaMoP_y-g5I0"
CRED = "/root/Scripturi/google_credentials.json"
PF_DB = "/root/Scripturi/data/profitability.db"
TAB_FB = "WMS Facebook 3"
TAB_TT = "WMS Tiktok"
TAB_NOMEN_FB = "Nomenclator"
TAB_NOMEN_TT = "Nomenclator Tiktok"
TAB_PRODUCT_GROUP = "Product Group"


def _num(s):
    s = (s or "").replace("$", "").replace("\xa0", "").strip()
    if "," in s and "." in s:
        s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _date_iso(s):
    s = (s or "").strip()
    if not s:
        return ""
    if "." in s and len(s) == 10:
        d, m, y = s.split(".")
        return "%s-%s-%s" % (y, m, d)
    if len(s) >= 10 and s[4] == "-":
        return s[:10]
    return ""


def _svc():
    cr = service_account.Credentials.from_service_account_file(
        CRED, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    return build("sheets", "v4", credentials=cr, cache_discovery=False).spreadsheets()


def _read(svc, tab):
    return svc.values().get(spreadsheetId=SS, range="'%s'" % tab).execute(num_retries=3).get("values", [])


def parse_fb(rows):
    out = []
    for r in rows:
        if not r or not r[0] or not r[0][0].isdigit():
            continue
        d = _date_iso(r[0])
        if not d:
            continue
        out.append(("fb", d, (r[1].strip() if len(r) > 1 else ""), (r[2].strip() if len(r) > 2 else ""),
                    (r[3].strip() if len(r) > 3 else ""), _num(r[4]) if len(r) > 4 else 0.0))
    return out


def parse_tt(rows):
    out = []
    for r in rows:
        if not r or not r[0] or not r[0][0].isdigit():
            continue
        d = _date_iso(r[0])
        if not d:
            continue
        out.append(("tt", d, (r[1].strip() if len(r) > 1 else ""), (r[2].strip() if len(r) > 2 else ""),
                    "", _num(r[3]) if len(r) > 3 else 0.0))
    return out


def upsert(recs):
    conn = sqlite3.connect(PF_DB); conn.execute("PRAGMA busy_timeout=8000;")
    conn.execute("""CREATE TABLE IF NOT EXISTS wms_ad_spend (
        source TEXT, date TEXT, account TEXT, campaign TEXT, ad_name TEXT,
        spend_usd REAL, fetched_at TEXT,
        PRIMARY KEY (source, date, account, campaign, ad_name))""")
    now = datetime.now().isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO wms_ad_spend (source,date,account,campaign,ad_name,spend_usd,fetched_at) "
        "VALUES (?,?,?,?,?,?,?)", [(s, d, a, c, ad, sp, now) for (s, d, a, c, ad, sp) in recs])
    conn.commit(); conn.close()


def pull_mappings(svc, conn):
    """Trage tabelele de mapare (Nomenclator FB/TT + Product Group) -> profitability.db (full replace)."""
    conn.execute("DROP TABLE IF EXISTS wms_nomen")
    conn.execute("CREATE TABLE wms_nomen (platform TEXT, product_group TEXT, map_type TEXT, pattern TEXT)")
    for plat, tab in (("fb", TAB_NOMEN_FB), ("tt", TAB_NOMEN_TT)):
        recs = []
        for r in _read(svc, tab)[1:]:
            if len(r) >= 3 and (r[0] or "").strip() and (r[2] or "").strip():
                recs.append((plat, r[0].strip(), (r[1] or "").strip().upper(), r[2].strip()))
        conn.executemany("INSERT INTO wms_nomen VALUES (?,?,?,?)", recs)
    conn.execute("DROP TABLE IF EXISTS wms_product_group")
    conn.execute("CREATE TABLE wms_product_group (sku TEXT, grp TEXT)")
    pg = []
    for r in _read(svc, TAB_PRODUCT_GROUP)[1:]:
        if len(r) >= 2 and (r[0] or "").strip():
            pg.append((r[0].strip(), (r[1] or "").strip()))
    conn.executemany("INSERT INTO wms_product_group VALUES (?,?)", pg)
    conn.commit()
    return (conn.execute("SELECT COUNT(*) FROM wms_nomen").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM wms_product_group").fetchone()[0])


def build_supplement(conn):
    """Supliment PERSISTENT (refresh la fiecare rulare): reguli cont->grup pt conturile simple lipsă din
    Nomenclatorul sheet (Nubra, Bonhaus CZ/RO/PL, Esteban 3, grandia.ro, Reflexino, Rossi, Nocturna...) +
    SKU->grup pt grupurile-BRAND care NU-s în Product Group (generat din comenzi, per prefix magazin)."""
    conn.execute("DROP TABLE IF EXISTS wms_nomen_extra")
    conn.execute("CREATE TABLE wms_nomen_extra (platform TEXT, product_group TEXT, map_type TEXT, pattern TEXT)")
    # NB: Esteban 2 -> Esteban e DEJA în Nomenclatorul sheet (account-level, tot contul). Esteban 3 + Reflexino
    # sunt MAGDEAL MULTI-PRODUS -> PER PRODUS via keyword (NU account-fallback), deci NU le punem aici; restul
    # neacoperit de keyword cade pe cache. „fără teste": grupul Test e exclus în wms_marketing.
    EXTRA = [
        ("fb", "Nubra", "ACCOUNT", "Nubra"),
        ("fb", "Bonhaus CZ", "ACCOUNT", "Bonhaus CZ"), ("fb", "Bonhaus RO", "ACCOUNT", "Bonhaus RO"),
        ("fb", "Bonhaus PL", "ACCOUNT", "Bonhaus PL"), ("fb", "Grandia", "ACCOUNT", "grandia.ro"),
        ("tt", "Nubra", "ACCOUNT", "Nubra"),
        ("tt", "Grandia", "ACCOUNT", "Grandia RO"), ("tt", "Rossi", "ACCOUNT", "ROSSI Nails Romania"),
        ("tt", "Pijamale", "ACCOUNT", "Nocturna Europa"), ("tt", "Pijamale", "ACCOUNT", "Nocturna.ro"),
        ("tt", "Bonhaus RO", "ACCOUNT", "Casa ofertelor"),
    ]
    conn.executemany("INSERT INTO wms_nomen_extra VALUES (?,?,?,?)", EXTRA)
    PREFIX_GROUP = {"NUB": "Nubra", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL", "BON": "Bonhaus RO",
                    "ROSSI": "Rossi"}
    conn.execute("DROP TABLE IF EXISTS wms_product_group_extra")
    conn.execute("CREATE TABLE wms_product_group_extra (sku TEXT, grp TEXT)")
    seen = set()
    for pfx, grp in PREFIX_GROUP.items():
        for (sku,) in conn.execute("SELECT DISTINCT sku FROM profit_order_lines WHERE prefix=? AND sku IS NOT NULL AND sku<>''", (pfx,)):
            s = (sku or "").strip()
            if s and (s, grp) not in seen:
                seen.add((s, grp)); conn.execute("INSERT INTO wms_product_group_extra VALUES (?,?)", (s, grp))
    conn.commit()
    return (conn.execute("SELECT COUNT(*) FROM wms_nomen_extra").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM wms_product_group_extra").fetchone()[0])


if __name__ == "__main__":
    svc = _svc()
    recs = []
    if "--tt-only" not in sys.argv:
        recs += parse_fb(_read(svc, TAB_FB))
    if "--fb-only" not in sys.argv:
        recs += parse_tt(_read(svc, TAB_TT))
    upsert(recs)
    _c = sqlite3.connect(PF_DB); _c.execute("PRAGMA busy_timeout=8000;")
    nn, npg = pull_mappings(svc, _c)
    ne, npge = build_supplement(_c); _c.close()
    print("upsert %d rânduri | mapări: wms_nomen=%d, wms_product_group=%d | supliment: nomen_extra=%d, pg_extra=%d"
          % (len(recs), nn, npg, ne, npge))
    conn = sqlite3.connect(PF_DB)
    for src in ("fb", "tt"):
        tot = conn.execute("SELECT COUNT(*), ROUND(SUM(spend_usd)) FROM wms_ad_spend WHERE source=?", (src,)).fetchone()
        print("  [%s] %d rânduri, %s USD total" % (src, tot[0], tot[1]))
    conn.close()
