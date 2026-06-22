# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30", "paramiko>=3.0"]
# ///
"""
cs_proactive_delays.py — coletele BLOCATE prea mult în tranzit (sau cu status curier problematic)
care încă n-au ajuns. CS contactează clientul PROACTIV, înainte să întrebe el -> previne tichete
WISMO + previne refuzuri (clientul nervos refuză). Cu mesaj gata de trimis per limbă. NU scrie nimic.

  uv run cs_proactive_delays.py --stuck-days 6
  uv run cs_proactive_delays.py --brand Grandia --draft
"""
import os, sys, json, subprocess, shlex, urllib.parse, argparse, datetime
import pg8000.dbapi

VPS = "root@84.46.242.181"

def _vps_run(remote_cmd):
    """Run a command on the profit VPS over SSH (paramiko, password from KB/env).
    Zero-touch: PROFIT_SSH_HOST/USER/PASS are read from env, else the team KB.
    Returns a CompletedProcess-like object (.stdout/.stderr/.returncode)."""
    import os as _os, sys as _sys, types as _types, subprocess as _sp
    def _sec(k):
        v = _os.environ.get(k)
        if v:
            return v
        kb = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                           "..", "..", "..", "core", "scripts", "kb.py")
        try:
            return _sp.run(["uv", "run", kb, "secret-get", k],
                           capture_output=True, text=True, timeout=30).stdout.strip()
        except Exception:
            return ""
    host = _sec("PROFIT_SSH_HOST") or "84.46.242.181"
    user = _sec("PROFIT_SSH_USER") or "root"
    pwd = _sec("PROFIT_SSH_PASS")
    if not pwd:
        _sys.exit("Lipsa PROFIT_SSH_PASS (KB/env). Ruleaza: kb.py secret-set PROFIT_SSH_PASS ...")
    import paramiko
    cl = paramiko.SSHClient()
    cl.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cl.connect(host, username=user, password=pwd, timeout=30)
    _i, _o, _e = cl.exec_command(remote_cmd, timeout=180)
    out = _o.read().decode(); err = _e.read().decode()
    rc = _o.channel.recv_exit_status()
    cl.close()
    return _types.SimpleNamespace(stdout=out, stderr=err, returncode=rc)
HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = {"GEN": ("Gento", "ro"), "EST": ("Esteban", "ro"), "GT": ("George Talent", "ro"), "NUB": ("Nubra", "ro"),
          "GRAN": ("Grandia", "ro"), "BELA": ("Belasil", "ro"), "OFER": ("Ofertele Zilei", "ro"), "MAG": ("Magdeal", "ro"),
          "RED": ("Reduceri bune", "ro"), "CARP": ("Carpetto", "ro"), "BON": ("Bonhaus RO", "ro"), "CZ": ("Bonhaus CZ", "cz"),
          "PL": ("Bonhaus PL", "pl"), "BONBG": ("Bonhaus BG", "bg")}
MSG = {
    "ro": "Bună {n}! Suntem de la {b}. Coletul tău {o} (AWB {a}) e încă pe drum și durează puțin mai mult decât de obicei. Suntem pe fază, ajunge curând — te ținem la curent. Dacă vrei orice, suntem aici! 📦",
    "cz": "Dobrý den {n}! Tady {b}. Vaše zásilka {o} (AWB {a}) je stále na cestě a trvá to o něco déle. Sledujeme to a brzy dorazí. Dáme vědět! 📦",
    "pl": "Cześć {n}! Tu {b}. Twoja paczka {o} (AWB {a}) jest w drodze i trwa to trochę dłużej niż zwykle. Pilnujemy tego, niedługo dotrze — damy znać! 📦",
    "bg": "Здравейте {n}! Това е {b}. Пратката {o} (AWB {a}) още пътува и отнема малко повече време. Следим я и скоро ще пристигне — ще ви държим в течение! 📦",
}


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", default=""); ap.add_argument("--brands", default="", help="mai multe magazine, separate prin virgulă")
    ap.add_argument("--stuck-days", type=int, default=6, dest="sd")
    ap.add_argument("--from", default="", dest="dfrom", help="YYYY-MM-DD"); ap.add_argument("--to", default="", dest="dto", help="YYYY-MM-DD")
    ap.add_argument("--limit", type=int, default=50); ap.add_argument("--draft", action="store_true")
    a = ap.parse_args()
    wanted = [x.strip() for x in (a.brands or a.brand).split(",") if x.strip()]
    prefixes = [p for p, (b, _l) in PREFIX.items() if any(w.lower() in b.lower() for w in wanted)]
    if a.dfrom or a.dto:
        lo = a.dfrom or "2025-01-01"; hi = a.dto or datetime.date.today().isoformat()
    else:
        lo = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
        hi = (datetime.date.today() - datetime.timedelta(days=a.sd)).isoformat()
    pf = ("AND prefix IN (" + ",".join(repr(p) for p in prefixes) + ")") if prefixes else ""
    py = ("import sqlite3,json;c=sqlite3.connect('data/profitability.db');lo=" + repr(lo) + ";hi=" + repr(hi) + ";"
          "print(json.dumps([dict(zip(['o','p','rev','awb','ck','cs','cr'],r)) for r in c.execute("
          "\"SELECT order_name,prefix,revenue,awb,courier_key,courier_status,created_at FROM profit_orders "
          "WHERE status_category='In curs de livrare' AND substr(created_at,1,10) BETWEEN ? AND ? " + pf + " LIMIT 3000\",(lo,hi))]))")
    out = _vps_run("cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py)).stdout.strip()
    try:
        stuck = json.loads(out.splitlines()[-1])
    except Exception:
        print("Nu am putut citi datele."); return
    url = secret("DATABASE_URL_METRICS"); u = urllib.parse.urlparse(url)
    conn = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                               password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                               port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    cur = conn.cursor(); info = {}
    nn = [s["o"] for s in stuck]
    for i in range(0, len(nn), 800):
        ch = nn[i:i + 800]; ph = ",".join(["%s"] * len(ch))
        cur.execute('SELECT name,"shippingName",COALESCE("shippingPhone",phone),"shippingCity" FROM orders WHERE name IN (' + ph + ')', ch)
        for r in cur.fetchall():
            info[r[0]] = {"name": r[1], "phone": r[2], "city": r[3]}
    conn.close()
    today = datetime.date.today()
    rows = []
    for s in stuck:
        c = info.get(s["o"], {})
        try:
            age = (today - datetime.date.fromisoformat(s["cr"][:10])).days
        except Exception:
            age = a.sd
        brand, lang = PREFIX.get(s["p"], (s["p"], "ro"))
        rows.append({"o": s["o"], "brand": brand, "lang": lang, "age": age, "awb": s["awb"], "ck": s["ck"],
                     "cs": s["cs"], "name": c.get("name"), "phone": c.get("phone"), "city": c.get("city")})
    rows.sort(key=lambda x: -x["age"])
    print("=== COLETE BLOCATE >%d zile (de contactat proactiv)%s ===" % (a.sd, (" | " + a.brand) if a.brand else ""))
    print("Colete în tranzit prea mult: %d\n" % len(rows))
    print("%-13s %-12s %4s %-14s %-16s %-12s" % ("comandă", "brand", "zile", "curier_status", "client", "telefon"))
    print("-" * 80)
    for x in rows[:a.limit]:
        print("%-13s %-12s %4d %-14s %-16s %-12s" % (x["o"], x["brand"][:12], x["age"],
              (x["cs"] or "—")[:14], (x["name"] or "—")[:16], (x["phone"] or "—")[:12]))
        if a.draft:
            nm = (x["name"] or "").split()[0] if x["name"] else ""
            print("   → " + MSG.get(x["lang"], MSG["ro"]).format(n=nm, b=x["brand"], o=x["o"], a=x["awb"] or "-"))


if __name__ == "__main__":
    main()
