# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
cs_order_status.py — "Unde e coletul meu?" (WISMO) rezolvat instant pentru Customer Service.
Lipești order / telefon / email / AWB -> statusul complet într-un loc: comandă, plată,
fulfillment, livrabilitate + tracking AWB LIVE (prin skill-ul awb-track) + un răspuns gata
de trimis în română. NU scrie nimic.

  uv run cs_order_status.py --order EST179388
  uv run cs_order_status.py --awb 81298289998
  uv run cs_order_status.py --phone 0748620192
  uv run cs_order_status.py --email client@gmail.com --reply
"""
import os, sys, json, subprocess, shlex, urllib.parse, argparse
import pg8000.dbapi

VPS = "root@84.46.242.181"
HERE = os.path.dirname(os.path.abspath(__file__))


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def mconn():
    url = secret("DATABASE_URL_METRICS"); u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                               password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                               port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def resolve_orders(a):
    """-> list of order_name. From --order/--awb direct; from --phone/--email via metrics."""
    if a.order:
        return [a.order.strip()]
    if a.awb:
        py = ("import sqlite3,json;c=sqlite3.connect('data/profitability.db');"
              "print(json.dumps([r[0] for r in c.execute(\"SELECT order_name FROM profit_orders WHERE awb=?\",(%r,))]))" % a.awb.strip())
        out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS,
                              "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py)],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        try:
            return json.loads(out.splitlines()[-1])
        except Exception:
            return []
    # phone / email -> metrics
    conn = mconn(); cur = conn.cursor()
    if a.phone:
        q = "".join(ch for ch in a.phone if ch.isdigit())[-9:]
        cur.execute('SELECT name FROM orders WHERE phone LIKE %s OR "shippingPhone" LIKE %s ORDER BY "shopifyCreatedAt" DESC LIMIT 10', ("%" + q, "%" + q))
    else:
        cur.execute('SELECT name FROM orders WHERE lower(email)=lower(%s) ORDER BY "shopifyCreatedAt" DESC LIMIT 10', (a.email,))
    names = [r[0] for r in cur.fetchall()]
    conn.close()
    return names


def ssh_status(order_names):
    if not order_names:
        return {}
    lst = json.dumps(order_names)
    py = ("import sqlite3,json,sys;ns=json.loads(sys.argv[1]);c=sqlite3.connect('data/profitability.db');"
          "q='SELECT order_name,prefix,revenue,currency,status_category,courier_status,courier_key,awb,payment_status,fulfillment_status,skus,created_at FROM profit_orders WHERE order_name IN (%s)'%(','.join('?'*len(ns)));"
          "print(json.dumps([dict(zip(['o','p','rev','cur','sc','cs','ck','awb','pay','ful','sk','cr'],r)) for r in c.execute(q,ns)]))")
    cmd = "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py) + " " + shlex.quote(lst)
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS, cmd],
                         capture_output=True, text=True, timeout=70).stdout.strip()
    try:
        return {r["o"]: r for r in json.loads(out.splitlines()[-1])}
    except Exception:
        return {}


def metrics_info(order_names):
    if not order_names:
        return {}
    conn = mconn(); cur = conn.cursor()
    ph = ",".join(["%s"] * len(order_names))
    cur.execute('SELECT name,"shippingName","shippingCity","totalPrice","financialStatus","fulfillmentStatus","shopifyCreatedAt" FROM orders WHERE name IN (' + ph + ')', order_names)
    out = {r[0]: {"name": r[1], "city": r[2], "total": float(r[3] or 0), "fin": r[4], "ful": r[5], "date": str(r[6])[:10]} for r in cur.fetchall()}
    conn.close()
    return out


def live_awb(awb):
    if not awb:
        return None
    try:
        p = subprocess.run(["uv", "run", os.path.join(HERE, "..", "awb-track", "awb_track.py"), "--awb", awb, "--json"],
                           capture_output=True, text=True, timeout=40)
        for line in reversed(p.stdout.splitlines()):
            if line.strip().startswith("["):
                d = json.loads(line)
                return d[0] if d else None
    except Exception:
        return None
    return None


REPLY = {
    "Livrata": "Bună! Comanda ta {o} a fost livrată ({date}). Sperăm că ești mulțumit! Dacă e ceva, suntem aici. 🙌",
    "In curs de livrare": "Bună! Comanda ta {o} e în drum cu {ck} (AWB {awb}), status: {cs}. Ar trebui să ajungă în 1-2 zile. 📦",
    "Netrimisa": "Bună! Comanda ta {o} e în procesare și pleacă spre tine în curând. Te anunțăm cu AWB-ul. 🙂",
    "Refuzata": "Bună! Comanda ta {o} s-a întors pentru că nu a putut fi livrată. Vrei să ți-o retrimitem? Sau plătești cu cardul și primești -10%. Răspunde DA. 🙌",
    "Anulata": "Bună! Comanda ta {o} a fost anulată. Dacă a fost o greșeală, o reluăm imediat — spune-ne.",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--order"); ap.add_argument("--awb"); ap.add_argument("--phone"); ap.add_argument("--email")
    ap.add_argument("--reply", action="store_true")
    a = ap.parse_args()
    if not any([a.order, a.awb, a.phone, a.email]):
        print("Dă --order / --awb / --phone / --email."); return
    names = resolve_orders(a)
    if not names:
        print("Nicio comandă găsită."); return
    st = ssh_status(names); mi = metrics_info(names)
    for o in names:
        s = st.get(o, {}); m = mi.get(o, {})
        if not s and not m:
            print("• %s — negăsit în profitabilitate." % o); continue
        sc = s.get("sc") or "?"
        print("\n• Comandă %s | client %s (%s) | %s lei | %s" % (
            o, m.get("name") or "—", m.get("city") or "—", "{:,.0f}".format(m.get("total") or s.get("rev") or 0), m.get("date") or s.get("cr", "")[:10]))
        print("   plată: %s | fulfillment: %s | livrabilitate: %s" % (s.get("pay") or m.get("fin") or "?", s.get("ful") or m.get("ful") or "?", sc))
        if s.get("awb"):
            la = live_awb(s["awb"])
            live = (" → LIVE: %s (%s)" % (la.get("status"), la.get("detail", ""))) if la else ""
            print("   AWB %s (%s), status: %s%s" % (s["awb"], s.get("ck") or "?", s.get("cs") or "—", live))
        if a.reply:
            tmpl = REPLY.get(sc, "Bună! Verificăm statusul comenzii {o} și revenim imediat.")
            print("   RĂSPUNS: " + tmpl.format(o=o, date=m.get("date") or "-", ck=s.get("ck") or "curier", awb=s.get("awb") or "-", cs=s.get("cs") or "în tranzit"))


if __name__ == "__main__":
    main()
