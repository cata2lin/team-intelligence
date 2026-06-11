# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
cs_refused_recovery.py — coada de RECUPERARE a comenzilor refuzate la livrare (COD).
Cele ~9.000 colete refuzate/lună au costat deja reclamă + transport; CS le poate recupera.
Scoate comenzile refuzate recente (cu telefon/email client, prioritizate pe valoare) și
redactează mesajul de win-back (relivrare / plată card) per piață. NU scrie nimic.

  uv run cs_refused_recovery.py --days 14
  uv run cs_refused_recovery.py --brand Esteban --days 14 --min-value 100
  uv run cs_refused_recovery.py --days 7 --draft        # + mesaj gata de trimis per comandă
"""
import os, sys, json, subprocess, shlex, urllib.parse, argparse, datetime
import pg8000.dbapi

VPS = "root@84.46.242.181"
PREFIX_BRAND = {"GEN": ("Gento", "ro"), "EST": ("Esteban", "ro"), "GT": ("George Talent", "ro"),
                "NUB": ("Nubra", "ro"), "GRAN": ("Grandia", "ro"), "BELA": ("Belasil", "ro"),
                "OFER": ("Ofertele Zilei", "ro"), "MAG": ("Magdeal", "ro"), "RED": ("Reduceri bune", "ro"),
                "CARP": ("Carpetto", "ro"), "COV": ("Covoria", "ro"), "BON": ("Bonhaus RO", "ro"),
                "CZ": ("Bonhaus CZ", "cz"), "PL": ("Bonhaus PL", "pl"), "BONBG": ("Bonhaus BG", "bg"),
                "NOC": ("Nocturna", "ro"), "LUX": ("Nocturna Lux", "ro"), "APR": ("Apreciat", "ro"),
                "ROSSI": ("Rossi Nails", "ro")}


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def ssh_refused(days, prefix):
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    months = sorted({(datetime.date.today() - datetime.timedelta(days=d)).strftime("%Y-%m") for d in (0, days, days + 31)})
    pf = "AND prefix='%s'" % prefix if prefix else ""
    py = ("import sqlite3,json;c=sqlite3.connect('data/profitability.db');"
          "rows=[dict(zip(['o','p','rev','cur','cs','ck','sk'],r)) for r in c.execute("
          "\"SELECT order_name,prefix,revenue,currency,courier_status,courier_key,skus FROM profit_orders "
          "WHERE status_category='Refuzata' AND substr(created_at,1,10)>='%s' %s\")];"
          "print(json.dumps(rows))") % (cutoff, pf)
    py = py.replace("%s", "%s")  # keep simple
    cmd = "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py)
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS, cmd],
                         capture_output=True, text=True, timeout=90).stdout.strip()
    try:
        return json.loads(out.splitlines()[-1]) if out else []
    except Exception:
        return []


def metrics_contacts(order_names):
    if not order_names:
        return {}
    url = secret("DATABASE_URL_METRICS"); u = urllib.parse.urlparse(url)
    conn = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    cur = conn.cursor()
    out = {}
    CH = 800
    for i in range(0, len(order_names), CH):
        chunk = order_names[i:i + CH]
        ph = ",".join(["%s"] * len(chunk))
        cur.execute('SELECT name, COALESCE("shippingName",email), COALESCE("shippingPhone",phone), '
                    '"shippingCity","shippingCountry","totalPrice" FROM orders WHERE name IN (' + ph + ')', chunk)
        for r in cur.fetchall():
            out[r[0]] = {"name": r[1], "phone": r[2], "city": r[3], "country": r[4], "total": float(r[5] or 0)}
    conn.close()
    return out


MSG = {
    "ro": "Bună {n}! Suntem de la {b}. Coletul tău ({o}) s-a întors pentru că nu a putut fi livrat. Vrei să ți-l retrimitem? Sau plătește acum cu cardul și primești -10% + livrare gratuită. Răspunde DA și ne ocupăm. 🙌",
    "bg": "Здравейте {n}! Това е {b}. Вашата пратка ({o}) се върна, защото не можа да бъде доставена. Искате ли да я изпратим отново? Платете с карта сега и получете -10% + безплатна доставка. Отговорете ДА и ще се погрижим. 🙌",
    "cz": "Dobrý den {n}! Tady {b}. Vaše zásilka ({o}) se vrátila, protože nemohla být doručena. Chcete ji poslat znovu? Zaplaťte kartou nyní a získáte -10 % + dopravu zdarma. Odpovězte ANO a postaráme se o to. 🙌",
    "pl": "Cześć {n}! Tu {b}. Twoja paczka ({o}) wróciła, bo nie udało się jej dostarczyć. Chcesz, żebyśmy wysłali ją ponownie? Zapłać teraz kartą i otrzymaj -10% + darmową dostawę. Odpowiedz TAK, a my się tym zajmiemy. 🙌",
    "en": "Hi {n}! This is {b}. Your parcel ({o}) came back undelivered. Want us to resend it? Pay by card now and get -10% + free shipping. Reply YES and we'll handle it. 🙌",
}


def msg(lang, cust, order, brand):
    nm = (cust or "").split()[0] if cust else ""
    return MSG.get(lang, MSG["en"]).format(n=nm, b=brand, o=order)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default="")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--min-value", type=float, default=0, dest="minv")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    prefix = ""
    for p, (b, _l) in PREFIX_BRAND.items():
        if a.brand and a.brand.lower() in b.lower():
            prefix = p; break
    refused = ssh_refused(a.days, prefix)
    contacts = metrics_contacts([r["o"] for r in refused])
    rows = []
    for r in refused:
        c = contacts.get(r["o"], {})
        val = c.get("total") or (r["rev"] or 0)
        if val < a.minv:
            continue
        brand, lang = PREFIX_BRAND.get(r["p"], (r["p"], "ro"))
        rows.append({"order": r["o"], "brand": brand, "lang": lang, "value": val,
                     "name": c.get("name"), "phone": c.get("phone"), "city": c.get("city"),
                     "country": c.get("country"), "courier_status": r["cs"]})
    rows.sort(key=lambda x: -x["value"])
    if a.json:
        print(json.dumps(rows, ensure_ascii=False)); return
    tot = sum(x["value"] for x in rows)
    print("=== RECUPERARE REFUZATE — ultimele %d zile%s ===" % (a.days, (" | " + a.brand) if a.brand else ""))
    print("Comenzi refuzate de recuperat: %d | valoare totală la risc: %s lei\n" % (len(rows), "{:,.0f}".format(tot)))
    print("%-13s %-13s %9s  %-20s %-13s %-10s" % ("comandă", "brand", "valoare", "client", "telefon", "oraș"))
    print("-" * 86)
    for x in rows[:a.limit]:
        print("%-13s %-13s %9s  %-20s %-13s %-10s" % (
            x["order"], x["brand"][:13], "{:,.0f}".format(x["value"]),
            (x["name"] or "—")[:20], (x["phone"] or "—")[:13], (x["city"] or "—")[:10]))
        if a.draft:
            print("   → " + msg(x["lang"], x["name"], x["order"], x["brand"]))


if __name__ == "__main__":
    main()
