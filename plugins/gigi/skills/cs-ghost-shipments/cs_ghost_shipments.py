# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
cs_ghost_shipments.py — COLETE FANTOMĂ: eticheta a fost printată (AWB emis) dar curierul
nu l-a scanat NICIODATĂ la ridicare -> clientul a primit mail Shopify că s-a expediat, dar
coletul n-a plecat din depozit. Cele mai furioase tichete WISMO ("scrie expediat de X zile,
unde e?!"). Acoperă gaura PRE-PICKUP pe care cs-proactive-delays (doar in-tranzit) o ratează.

Două semnale (DOAR profitability.db / profit_orders, citit prin SSH):
  (1) FANTOMĂ: shopify_delivery_status='LABEL_PRINTED' AND status_category='Netrimisa'
      AND created_at mai vechi de N zile (default 3). AWB-ul există (DPD) cu courier_status
      'Shipment data received' = înregistrat dar nescanat la ridicare. Coletul stă în depozit.
  (2) FĂRĂ TRACKING: status_category='Lipsa awb' = marcat expediat/FULFILLED fără AWB deloc.
      Clientul are mail de expediere dar n-are ce urmări.

Contact (nume/telefon/oraș) din metrics.orders. Sortare: vechime desc, apoi valoare desc.
Read-only total — nu scrie nimic în Postgres / Shopify / Richpanel.

  uv run cs_ghost_shipments.py                 # fantome >3 zile, toate magazinele
  uv run cs_ghost_shipments.py --days 5        # prag vechime 5 zile
  uv run cs_ghost_shipments.py --store Esteban # un singur magazin
  uv run cs_ghost_shipments.py --json          # pt automatizare
"""
import os, sys, json, subprocess, shlex, urllib.parse, argparse, datetime
import pg8000.dbapi

VPS = "root@84.46.242.181"
HERE = os.path.dirname(os.path.abspath(__file__))

# prefix din order_name -> nume magazin afișat
PREFIX = {
    "EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GEN": "Gento",
    "GRAN": "Grandia", "GRAND": "Grandia", "BELA": "Belasil", "CARP": "Carpetto",
    "COV": "Covoria", "MAG": "Magdeal", "OFER": "Ofertele Zilei", "RED": "Reduceri bune",
    "BON": "Bonhaus RO", "BONBG": "Bonhaus BG", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL",
    "APR": "Apreciat", "ROSSI": "Rossi Nails",
}

# acțiune sugerată per semnal + vechime
def suggested_action(flag, age):
    if flag == "lipsa_awb":
        return "Generează AWB ACUM (marcat expediat fără tracking) + mesaj proactiv"
    if age >= 7:
        return "URGENT: verifică depozit, re-expediere/refund + scuze proactive"
    if age >= 5:
        return "Verifică depozit + re-expediere; mesaj proactiv clientului"
    return "Verifică depozit (eticheta nescanata la curier) + mesaj proactiv"


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def fetch_profit(cut, prefixes):
    """Citește cele două semnale din profit_orders (SSH). cut = data prag YYYY-MM-DD (inclusiv)."""
    pf = ""
    if prefixes:
        pf = " AND prefix IN (" + ",".join(repr(p) for p in prefixes) + ")"
    py = (
        "import sqlite3,json;c=sqlite3.connect('data/profitability.db');cut=" + repr(cut) + ";"
        "cols=['order_name','prefix','created_at','revenue','currency','awb','courier_status','status_category','shopify_delivery_status'];"
        "q1=\"SELECT \"+','.join(cols)+\" FROM profit_orders WHERE shopify_delivery_status='LABEL_PRINTED' "
        "AND status_category='Netrimisa' AND substr(created_at,1,10) <= ?" + pf + " LIMIT 5000\";"
        "q2=\"SELECT \"+','.join(cols)+\" FROM profit_orders WHERE status_category='Lipsa awb'" + pf + " LIMIT 5000\";"
        "g=[dict(zip(cols,r)) for r in c.execute(q1,(cut,))];"
        "n=[dict(zip(cols,r)) for r in c.execute(q2)];"
        "print(json.dumps({'ghost':g,'noawb':n}))"
    )
    cmd = "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py)
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS, cmd],
                         capture_output=True, text=True, timeout=120).stdout.strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {"ghost": [], "noawb": []}


def fetch_contacts(order_names):
    """Nume/telefon/oraș din metrics.orders pentru lista de comenzi."""
    info = {}
    if not order_names:
        return info
    url = secret("DATABASE_URL_METRICS"); u = urllib.parse.urlparse(url)
    conn = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    cur = conn.cursor()
    nn = list(order_names)
    for i in range(0, len(nn), 800):
        ch = nn[i:i + 800]; ph = ",".join(["%s"] * len(ch))
        cur.execute('SELECT name,"shippingName",COALESCE("shippingPhone",phone),"shippingCity" '
                    'FROM orders WHERE name IN (' + ph + ')', ch)
        for r in cur.fetchall():
            info[r[0]] = {"name": r[1], "phone": r[2], "city": r[3]}
    conn.close()
    return info


def age_days(created_at, today):
    try:
        return (today - datetime.date.fromisoformat(str(created_at)[:10])).days
    except Exception:
        return 0


def build_rows(raw, today):
    rows = []
    for s in raw.get("ghost", []):
        rows.append({
            "flag": "fantoma", "o": s["order_name"], "prefix": s["prefix"],
            "brand": PREFIX.get(s["prefix"], s["prefix"]), "age": age_days(s["created_at"], today),
            "rev": float(s.get("revenue") or 0), "cur": s.get("currency") or "RON",
            "awb": s.get("awb") or "", "cs": s.get("courier_status") or "",
            "status": "Etichetă printată, NESCANATĂ de curier",
        })
    for s in raw.get("noawb", []):
        rows.append({
            "flag": "lipsa_awb", "o": s["order_name"], "prefix": s["prefix"],
            "brand": PREFIX.get(s["prefix"], s["prefix"]), "age": age_days(s["created_at"], today),
            "rev": float(s.get("revenue") or 0), "cur": s.get("currency") or "RON",
            "awb": s.get("awb") or "", "cs": s.get("courier_status") or "",
            "status": "Marcat expediat FĂRĂ AWB (fără tracking)",
        })
    return rows


def render(rows, days, store_filter, per_store):
    today = datetime.date.today()
    ghost = [r for r in rows if r["flag"] == "fantoma"]
    noawb = [r for r in rows if r["flag"] == "lipsa_awb"]
    val_total = sum(r["rev"] for r in rows)

    hdr = "=== COLETE FANTOMĂ — etichetă printată dar curierul nu a ridicat coletul ==="
    print(hdr)
    print("Prag vechime fantome: >%d zile%s" % (days, ("  |  magazin: " + store_filter) if store_filter else ""))
    print("FANTOME (expediat dar n-a plecat): %d  |  FĂRĂ TRACKING (lipsă AWB): %d  |  total: %d" % (
        len(ghost), len(noawb), len(rows)))
    print("Valoare blocată (revenue): %s lei\n" % "{:,.0f}".format(val_total))

    # Per magazin, fiecare sortat vechime desc apoi valoare desc
    by_store = {}
    for r in rows:
        by_store.setdefault(r["brand"], []).append(r)
    order = sorted(by_store.keys(),
                   key=lambda b: (-len(by_store[b]), -sum(x["rev"] for x in by_store[b])))

    for brand in order:
        lst = sorted(by_store[brand], key=lambda x: (-x["age"], -x["rev"]))
        sval = sum(x["rev"] for x in lst)
        print("── %s  (%d colete | %s lei blocați) %s" % (
            brand, len(lst), "{:,.0f}".format(sval), "─" * max(2, 40 - len(brand))))
        print("  %-13s %4s  %-34s %9s %-13s" % ("comandă", "zile", "status", "valoare", "AWB"))
        shown = lst[:per_store] if per_store > 0 else lst
        for x in shown:
            print("  %-13s %4d  %-34s %7s %-3s %-13s" % (
                x["o"], x["age"], x["status"][:34],
                "{:,.0f}".format(x["rev"]), x["cur"], (x["awb"] or "—")[:13]))
            print("     → %s" % suggested_action(x["flag"], x["age"]))
        if per_store > 0 and len(lst) > per_store:
            rest = lst[per_store:]
            print("  ... încă %d colete (%s lei) — vezi --json pt lista completă sau --per-store 0." % (
                len(rest), "{:,.0f}".format(sum(x["rev"] for x in rest))))
        print()

    # Sumar
    print("─" * 70)
    print("SUMAR: %d colete fantomă/fără-tracking, %s lei blocați." % (len(rows), "{:,.0f}".format(val_total)))
    if ghost:
        gval = sum(r["rev"] for r in ghost)
        oldest = max((r["age"] for r in ghost), default=0)
        print("  • %d fantome (etichetă nescanată), %s lei, cea mai veche de %d zile." % (
            len(ghost), "{:,.0f}".format(gval), oldest))
    if noawb:
        nval = sum(r["rev"] for r in noawb)
        print("  • %d marcate expediat FĂRĂ AWB, %s lei — clientul n-are ce urmări." % (
            len(noawb), "{:,.0f}".format(nval)))
    print("  Acțiune: verifică depozit / re-expediere / mesaj proactiv ÎNAINTE să-ți scrie clientul furios.")


def main():
    ap = argparse.ArgumentParser(description="Colete fantomă: etichetă printată dar curierul nu a ridicat coletul.")
    ap.add_argument("--days", type=int, default=3, help="prag vechime pt fantome (default 3)")
    ap.add_argument("--store", default="", help="filtrează un magazin (ex: Esteban, Grandia, Magdeal)")
    ap.add_argument("--per-store", type=int, default=20, dest="per_store",
                    help="câte colete afișezi per magazin în raport (0 = toate; default 20)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    today = datetime.date.today()
    cut = (today - datetime.timedelta(days=a.days)).isoformat()

    prefixes = []
    if a.store:
        prefixes = [p for p, b in PREFIX.items() if a.store.lower() in b.lower()]
        if not prefixes:
            print("Magazin necunoscut: %s. Magazine: %s" % (
                a.store, ", ".join(sorted(set(PREFIX.values())))))
            return

    raw = fetch_profit(cut, prefixes)
    rows = build_rows(raw, today)
    # contacte din metrics (telefon/oraș, util pt CS) — atașăm dar afișăm compact
    contacts = fetch_contacts({r["o"] for r in rows})
    for r in rows:
        c = contacts.get(r["o"], {})
        r["name"] = c.get("name"); r["phone"] = c.get("phone"); r["city"] = c.get("city")

    rows.sort(key=lambda x: (-x["age"], -x["rev"]))

    if a.json:
        for r in rows:
            r["action"] = suggested_action(r["flag"], r["age"])
        print(json.dumps({
            "days": a.days, "store": a.store or None, "cut": cut,
            "total": len(rows),
            "ghost": sum(1 for r in rows if r["flag"] == "fantoma"),
            "noawb": sum(1 for r in rows if r["flag"] == "lipsa_awb"),
            "value_blocked": round(sum(r["rev"] for r in rows), 2),
            "rows": rows,
        }, ensure_ascii=False, indent=2, default=str))
    else:
        render(rows, a.days, a.store, a.per_store)


if __name__ == "__main__":
    main()
