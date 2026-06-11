# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
cod_confirmation.py — PREVENȚIA refuzului: coada de CONFIRMARE pre-livrare a comenzilor COD
riscante (neexpediate încă, status Netrimisa), prioritizate după risc: client care a mai REFUZAT
înainte sau comandă de VALOARE MARE. Confirmi telefonic/SMS înainte să cheltui transportul ->
tai refuzul la sursă (cealaltă jumătate a levierului de ~272k/lună). NU scrie nimic.

  uv run cod_confirmation.py --days 5
  uv run cod_confirmation.py --brand Grandia --min-value 200 --draft
"""
import os, sys, json, subprocess, shlex, urllib.parse, argparse, datetime
import pg8000.dbapi

VPS = "root@84.46.242.181"
HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = {"GEN": ("Gento", "ro"), "EST": ("Esteban", "ro"), "GT": ("George Talent", "ro"), "NUB": ("Nubra", "ro"),
          "GRAN": ("Grandia", "ro"), "BELA": ("Belasil", "ro"), "OFER": ("Ofertele Zilei", "ro"), "MAG": ("Magdeal", "ro"),
          "RED": ("Reduceri bune", "ro"), "CARP": ("Carpetto", "ro"), "BON": ("Bonhaus RO", "ro"), "CZ": ("Bonhaus CZ", "cz"),
          "PL": ("Bonhaus PL", "pl"), "BONBG": ("Bonhaus BG", "bg")}
MSG = {
    "ro": "Bună {n}! Suntem de la {b}. Ți-am pregătit comanda {o} ({v} lei, plată ramburs). Confirmi că o vrei și că adresa/telefonul sunt corecte? Răspunde DA și o trimitem azi. 📦",
    "cz": "Dobrý den {n}! Tady {b}. Připravili jsme objednávku {o} ({v}, dobírka). Potvrďte prosím, že ji chcete a že adresa je správná. Odpovězte ANO a dnes ji odešleme. 📦",
    "pl": "Cześć {n}! Tu {b}. Przygotowaliśmy zamówienie {o} ({v}, za pobraniem). Potwierdź, że je chcesz i że adres jest poprawny. Odpowiedz TAK, a wyślemy dziś. 📦",
    "bg": "Здравейте {n}! Това е {b}. Подготвихме поръчка {o} ({v}, наложен платеж). Потвърдете, че я искате и адресът е верен. Отговорете ДА и я изпращаме днес. 📦",
}


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def ssh(py):
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS,
                          "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py)],
                         capture_output=True, text=True, timeout=90).stdout.strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return None


def mconn():
    url = secret("DATABASE_URL_METRICS"); u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                               password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                               port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default=""); ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--min-value", type=float, default=150, dest="minv"); ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--draft", action="store_true")
    a = ap.parse_args()
    prefix = ""
    for p, (b, _l) in PREFIX.items():
        if a.brand and a.brand.lower() in b.lower():
            prefix = p; break
    nlo = (datetime.date.today() - datetime.timedelta(days=a.days)).isoformat()
    rlo = (datetime.date.today() - datetime.timedelta(days=90)).isoformat()
    pf = ("AND prefix=" + repr(prefix)) if prefix else ""
    # neexpediate (Netrimisa) recente + order_names refuzate (90z) pentru detectarea refuznicilor
    py = ("import sqlite3,json;c=sqlite3.connect('data/profitability.db');"
          "nl=" + repr(nlo) + ";rl=" + repr(rlo) + ";"
          "net=[dict(zip(['o','p','rev'],r)) for r in c.execute(\"SELECT order_name,prefix,revenue FROM profit_orders "
          "WHERE status_category='Netrimisa' AND substr(created_at,1,10)>=? " + pf + "\",(nl,))];"
          "refz=[r[0] for r in c.execute(\"SELECT order_name FROM profit_orders WHERE status_category='Refuzata' AND substr(created_at,1,10)>=?\",(rl,))];"
          "print(json.dumps({'net':net,'refz':refz}))")
    d = ssh(py)
    if not d:
        print("Nu am putut citi comenzile (SSH/DB)."); return
    net, refz = d["net"], d["refz"]
    conn = mconn(); cur = conn.cursor()
    risky_phones = set()
    for i in range(0, len(refz), 800):
        ch = refz[i:i + 800]; ph = ",".join(["%s"] * len(ch))
        cur.execute('SELECT DISTINCT COALESCE("shippingPhone",phone) FROM orders WHERE name IN (' + ph + ')', ch)
        for r in cur.fetchall():
            if r[0]:
                risky_phones.add("".join(x for x in r[0] if x.isdigit())[-9:])
    info = {}
    nn = [o["o"] for o in net]
    for i in range(0, len(nn), 800):
        ch = nn[i:i + 800]; ph = ",".join(["%s"] * len(ch))
        cur.execute('SELECT name,"shippingName",COALESCE("shippingPhone",phone),"shippingCity","totalPrice" FROM orders WHERE name IN (' + ph + ')', ch)
        for r in cur.fetchall():
            info[r[0]] = {"name": r[1], "phone": r[2], "city": r[3], "total": float(r[4] or 0)}
    conn.close()
    rows = []
    for o in net:
        c = info.get(o["o"], {}); val = c.get("total") or (o["rev"] or 0)
        phn = "".join(x for x in (c.get("phone") or "") if x.isdigit())[-9:]
        repeat = phn in risky_phones and phn
        if not (repeat or val >= a.minv):
            continue
        brand, lang = PREFIX.get(o["p"], (o["p"], "ro"))
        rows.append({"o": o["o"], "brand": brand, "lang": lang, "val": val, "name": c.get("name"),
                     "phone": c.get("phone"), "city": c.get("city"), "risk": "REFUZAT ÎNAINTE" if repeat else "VALOARE MARE"})
    rows.sort(key=lambda x: (x["risk"] != "REFUZAT ÎNAINTE", -x["val"]))
    print("=== CONFIRMARE PRE-LIVRARE COD (risc) — neexpediate ultimele %d zile%s ===" % (a.days, (" | " + a.brand) if a.brand else ""))
    print("De confirmat înainte de expediere: %d (refuznici: %d)\n" % (len(rows), sum(1 for x in rows if x["risk"] == "REFUZAT ÎNAINTE")))
    print("%-13s %-12s %8s  %-14s %-18s %-12s" % ("comandă", "brand", "valoare", "RISC", "client", "telefon"))
    print("-" * 86)
    for x in rows[:a.limit]:
        print("%-13s %-12s %8s  %-14s %-18s %-12s" % (x["o"], x["brand"][:12], "{:,.0f}".format(x["val"]),
              x["risk"], (x["name"] or "—")[:18], (x["phone"] or "—")[:12]))
        if a.draft:
            nm = (x["name"] or "").split()[0] if x["name"] else ""
            print("   → " + MSG.get(x["lang"], MSG["ro"]).format(n=nm, b=x["brand"], o=x["o"], v="{:,.0f}".format(x["val"])))


if __name__ == "__main__":
    main()
