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
    rf = (datetime.date.today() - datetime.timedelta(days=75)).strftime("%Y-%m")
    # neexpediate (Netrimisa)+skus + refuzate 90z (refuznici) + PRODUSE-RISC calculate din istoric (refuz >30% si >1.6x media magazinului)
    py = ("import sqlite3,json,collections\n"
          "c=sqlite3.connect('data/profitability.db')\n"
          "net=[dict(zip(['o','p','rev','skus'],r)) for r in c.execute(\"SELECT order_name,prefix,revenue,skus FROM profit_orders WHERE status_category='Netrimisa' AND substr(created_at,1,10)>=%(nl)r %(pf)s\")]\n"
          "refz=[r[0] for r in c.execute(\"SELECT order_name FROM profit_orders WHERE status_category='Refuzata' AND substr(created_at,1,10)>=%(rl)r\")]\n"
          "pl=collections.Counter();pr=collections.Counter();prod=collections.defaultdict(lambda:[0,0])\n"
          "for pfx,st,sk in c.execute(\"SELECT prefix,status_category,skus FROM profit_orders WHERE status_category IN ('Livrata','Refuzata') AND substr(created_at,1,7)>=%(rf)r\"):\n"
          " (pl if st=='Livrata' else pr)[pfx]+=1\n"
          " if sk:\n"
          "  for s in set(x.strip() for x in sk.split(';')):\n"
          "   if s: prod[(pfx,s)][0 if st=='Livrata' else 1]+=1\n"
          "red=[]\n"
          "for (pfx,s),(liv,ref) in prod.items():\n"
          " tot=liv+ref\n"
          " if tot>=40 and ref/tot>0.30:\n"
          "  avg=pr[pfx]/(pl[pfx]+pr[pfx]) if (pl[pfx]+pr[pfx]) else 0\n"
          "  if avg and ref/tot>1.6*avg: red.append([pfx,s])\n"
          "print(json.dumps({'net':net,'refz':refz,'red':red}))") % {'nl': nlo, 'pf': pf, 'rl': rlo, 'rf': rf}
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
    red = set((x[0], x[1]) for x in (d.get("red") or []))
    RANK = {"REFUZAT ÎNAINTE": 0, "PRODUS RISC": 1, "IMPULS 50-100": 2, "VALOARE MARE": 3}
    rows = []
    for o in net:
        c = info.get(o["o"], {}); val = c.get("total") or (o["rev"] or 0)
        phn = "".join(x for x in (c.get("phone") or "") if x.isdigit())[-9:]
        repeat = bool(phn) and phn in risky_phones
        skus = o.get("skus") or ""
        red_hit = next((s.strip() for s in skus.split(";") if (o["p"], s.strip()) in red), None)
        impulse = skus and ";" not in skus and 50 <= val <= 100
        if not (repeat or red_hit or impulse or val >= a.minv):
            continue
        if repeat:
            risk, why = "REFUZAT ÎNAINTE", "a refuzat în ultimele 90 zile"
        elif red_hit:
            risk, why = "PRODUS RISC", "produs cu refuz mare (" + red_hit[:20] + ")"
        elif impulse:
            risk, why = "IMPULS 50-100", "1 produs, 50-100 lei (refuz ~22%)"
        else:
            risk, why = "VALOARE MARE", "valoare ≥ %.0f lei" % a.minv
        brand, lang = PREFIX.get(o["p"], (o["p"], "ro"))
        rows.append({"o": o["o"], "brand": brand, "lang": lang, "val": val, "name": c.get("name"),
                     "phone": c.get("phone"), "city": c.get("city"), "risk": risk, "why": why})
    rows.sort(key=lambda x: (RANK.get(x["risk"], 9), -x["val"]))
    nrisk = {k: sum(1 for x in rows if x["risk"] == k) for k in RANK}
    print("=== CONFIRMARE PRE-LIVRARE COD (risc) — neexpediate ultimele %d zile%s ===" % (a.days, (" | " + a.brand) if a.brand else ""))
    print("De confirmat: %d  |  refuznici: %d · produs-risc: %d · impuls 50-100: %d · valoare mare: %d\n" % (
        len(rows), nrisk["REFUZAT ÎNAINTE"], nrisk["PRODUS RISC"], nrisk["IMPULS 50-100"], nrisk["VALOARE MARE"]))
    print("%-13s %-12s %8s  %-15s %-30s %-12s" % ("comandă", "brand", "valoare", "RISC", "motiv", "telefon"))
    print("-" * 96)
    for x in rows[:a.limit]:
        print("%-13s %-12s %8s  %-15s %-30s %-12s" % (x["o"], x["brand"][:12], "{:,.0f}".format(x["val"]),
              x["risk"], x["why"][:30], (x["phone"] or "—")[:12]))
        if a.draft:
            nm = (x["name"] or "").split()[0] if x["name"] else ""
            print("   → " + MSG.get(x["lang"], MSG["ro"]).format(n=nm, b=x["brand"], o=x["o"], v="{:,.0f}".format(x["val"])))


if __name__ == "__main__":
    main()
