# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30", "paramiko>=3.0"]
# ///
"""
cs_customer_360.py — vedere 360 pe un client pentru Customer Service.
Lipești telefon / email / nume -> toate comenzile lui (din toate magazinele), LTV,
livrate vs refuzate, retururi (RMA Grandia), și flag automat "REFUZNIC SERIAL"
(de pus pe card, nu COD) ca să nu mai pierdem bani. NU scrie nimic.

  uv run cs_customer_360.py --phone 0748620192
  uv run cs_customer_360.py --email client@gmail.com
"""
import os, sys, json, subprocess, shlex, urllib.parse, argparse
import pg8000.dbapi

# Windows (mașinile CS): consola e cp1252 → forțez UTF-8 DIN PRIMA ca să NU crape pe diacritice (ț/ș/ă/î/â).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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


def ssh_status(order_names):
    if not order_names:
        return {}
    lst = json.dumps(order_names)
    py = ("import sqlite3,json,sys;ns=json.loads(sys.argv[1]);c=sqlite3.connect('data/profitability.db');"
          "q='SELECT order_name,status_category FROM profit_orders WHERE order_name IN (%s)'%(','.join('?'*len(ns)));"
          "print(json.dumps({r[0]:r[1] for r in c.execute(q,ns)}))")
    cmd = "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py) + " " + shlex.quote(lst)
    out = _vps_run(cmd).stdout.strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone"); ap.add_argument("--email"); ap.add_argument("--name")
    a = ap.parse_args()
    if not any([a.phone, a.email, a.name]):
        print("Dă --phone / --email / --name."); return
    conn = mconn(); cur = conn.cursor()
    cur.execute("SELECT id,name FROM brands"); brands = {r[0]: r[1] for r in cur.fetchall()}
    if a.phone:
        q = "".join(ch for ch in a.phone if ch.isdigit())[-9:]
        cur.execute('SELECT name,"brandId","totalPrice","financialStatus","shopifyCreatedAt","shippingName" '
                    'FROM orders WHERE phone LIKE %s OR "shippingPhone" LIKE %s ORDER BY "shopifyCreatedAt" DESC', ("%" + q, "%" + q))
    elif a.email:
        cur.execute('SELECT name,"brandId","totalPrice","financialStatus","shopifyCreatedAt","shippingName" '
                    'FROM orders WHERE lower(email)=lower(%s) ORDER BY "shopifyCreatedAt" DESC', (a.email,))
    else:
        cur.execute('SELECT name,"brandId","totalPrice","financialStatus","shopifyCreatedAt","shippingName" '
                    'FROM orders WHERE lower("shippingName")=lower(%s) ORDER BY "shopifyCreatedAt" DESC LIMIT 50', (a.name,))
    orders = [{"o": r[0], "brand": brands.get(r[1], "?"), "total": float(r[2] or 0),
               "fin": r[3], "date": str(r[4])[:10], "cust": r[5]} for r in cur.fetchall()]
    conn.close()
    if not orders:
        print("Niciun client găsit."); return
    deliv = ssh_status([o["o"] for o in orders])
    cust = next((o["cust"] for o in orders if o["cust"]), "(necunoscut)")
    livr = sum(1 for o in orders if deliv.get(o["o"]) == "Livrata")
    refz = sum(1 for o in orders if deliv.get(o["o"]) == "Refuzata")
    closed = sum(1 for o in orders if deliv.get(o["o"]) in ("Livrata", "Refuzata", "Anulata"))
    ltv = sum(o["total"] for o in orders if deliv.get(o["o"]) == "Livrata")
    refrate = refz / (livr + refz) * 100 if (livr + refz) else 0
    print("=== CLIENT 360: %s ===" % cust)
    print("  Comenzi total: %d | livrate: %d | refuzate: %d | rată refuz: %.0f%%" % (len(orders), livr, refz, refrate))
    print("  LTV (din livrate): %s lei | branduri: %s" % ("{:,.0f}".format(ltv), ", ".join(sorted({o["brand"] for o in orders}))))
    if refz >= 2 or (refrate >= 50 and (livr + refz) >= 2):
        print("  🚩 REFUZNIC SERIAL — recomandare: ofertă DOAR cu plata cardului (nu COD).")
    print("\n  comenzi:")
    for o in orders[:25]:
        print("   %-13s %-13s %8s lei | %s | livrabilitate: %s" % (
            o["o"], o["brand"][:13], "{:,.0f}".format(o["total"]), o["date"], deliv.get(o["o"], "?")))


if __name__ == "__main__":
    main()
