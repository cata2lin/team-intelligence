# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
cs_profile.py — PROFIL 360° al unei conversații, SCRIPTAT (zero LLM, gratis, instant).
Asamblează cei 5 piloni din date + reguli: client + comanda + categorie + sentiment + acțiune
(din playbook). La tichetele deja legate (match_order) nu cere nici MCP, nici LLM.

  uv run cs_profile.py --conv 265078
  uv run cs_profile.py --conv 265078 --json

Diferența față de cs-conversation-profile (LLM): aici „ce vrea" = mesajul real al clientului
verbatim + categoria, nu o parafrază. Restul (client/comandă/status/sentiment/acțiune) e identic.
Read-only.
"""
import os, re, json, sqlite3, subprocess, shlex, urllib.parse, argparse
import pg8000.dbapi

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
import sys as _sys
_sys.path.insert(0, os.path.join(HERE, "..", "richpanel-export"))
import rp_db  # sursă: SQLite local (pipeline) sau Postgres partajat (agent CS)

VPS = "root@84.46.242.181"

# tabel ACȚIUNE pe categorie — din playbook-ul validat (proceduri reale ARONA)
ACTION = {
    "livrare_wismo": "Verifică AWB → trimite link tracking DPD: https://tracking.dpd.ro?shipmentNumber={awb}. Dacă e blocat/fără mișcare, contactează curierul DIRECT (ca ARONA SRL), nu pasa clientul. Întârziat → scuze + estimare.",
    "retur": "NU procesa returul direct — întâi oferă ALTERNATIVĂ (schimb mărime / păstrare produs + CADOU gratis). Doar la insistență: formular retur + suma în 14 zile. Dacă avarierea/produsul greșit e VINA NOASTRĂ → acceptă (fără clauza stare-perfectă).",
    "anulare": "Neexpediată → întâi anulează AWB-ul în xConnector, ABIA APOI comanda în Shopify + confirmă (semnat Echipa {store}). Parțială (dublă/mărime) → anulează DOAR comanda greșită, confirmă explicit ce rămâne. Dacă a plecat coletul → îndrumă REFUZUL la livrare.",
    "problema_produs": "Cere POZĂ (gatekeeper). Parfum spart → comandă nouă gratuită + parfum cadou. Mobilă/casă defectă → poză → schimb/retrimitere gratuit (vina noastră) sau refund dacă nu e pe stoc.",
    "modificare_comanda": "Neexpediată → actualizează datele în Shopify, confirmă. Expediată → redirecționare prin curier.",
    "schimb_swap": "Schimb mărime/model → cere nume+telefon + atașează tabelul de măsurători; transport pe client (GRATIS la mai multe seturi). Produsul vechi se predă curierului la livrarea noului.",
    "presale_intrebare": "Răspuns rapid la preț/stoc/livrare (DPD 1-3 zile, ramburs) → încurajează plasarea comenzii.",
    "comanda_noua": "Ghidează plasarea comenzii / plaseaz-o tu dacă cere.",
    "refuz_livrare": "Win-back COD (vezi gigi:cs-refused-recovery) — re-confirmă + oferă re-livrare.",
    "plata_factura": "Verifică EFECTIV tranzacția/factura (nu repeta mecanic „plata a fost procesată) + comunică suma corect, o singură dată.",
    "recenzie_feedback": "Mulțumește; dacă e negativă, oferă o soluție.",
}
PREFIX = {"EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GEN": "Gento", "GRAN": "Grandia", "GRAND": "Grandia",
          "BELA": "Belasil", "MAG": "Magdeal", "OFER": "Ofertele Zilei", "RED": "Reduceri bune", "BON": "Bonhaus RO",
          "BONBG": "Bonhaus BG", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL", "CARP": "Carpetto", "COV": "Covoria",
          "APR": "Apreciat", "ROSSI": "Rossi Nails"}
SENT = {"negativ": "🔴 NEGATIV", "pozitiv": "🟢 POZITIV", "neutru": "· neutru"}


def norm_phone(p):
    d = "".join(c for c in (p or "") if c.isdigit())
    return d[-9:] if len(d) >= 9 else ""


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def mconn():
    u = urllib.parse.urlparse(secret("DATABASE_URL_METRICS"))
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def ssh_profit(names):
    if not names:
        return {}
    lst = json.dumps(list(names))
    py = ("import sqlite3,json,sys;ns=json.loads(sys.argv[1]);c=sqlite3.connect('data/profitability.db');"
          "q='SELECT order_name,status_category,skus,awb,courier_key,courier_status FROM profit_orders WHERE order_name IN (%s)'%(','.join('?'*len(ns)));"
          "print(json.dumps({r[0]:{'st':r[1],'skus':r[2],'awb':r[3],'ck':r[4],'cstat':r[5]} for r in c.execute(q,ns)}))")
    cmd = "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py) + " " + shlex.quote(lst)
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS, cmd],
                         capture_output=True, text=True, timeout=70).stdout.strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conv", required=True); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    c = rp_db.open(DB)
    cols = [r[1] for r in c.execute("PRAGMA table_info(tickets)")]
    g = lambda col: col if col in cols else "NULL"
    row = c.execute("SELECT %s,%s,%s,%s,%s,%s,%s,%s,channel,status,subject,first_message FROM tickets WHERE conversation_no=?" % (
        g("resolved_store"), g("category"), g("sentiment"), g("sent_intensity"), g("match_order"),
        g("contact_email"), g("contact_phone"), g("customer_name")), (a.conv,)).fetchone()
    c.close()
    if not row:
        print("Tichet #%s negăsit." % a.conv); return
    store, cat, sent, inten, morder, cemail, cphone, cname, channel, status, subject, fmsg = row

    # CLIENT 360 — comenzile lui (metrics) + status (profit_orders)
    orders = []
    conn = mconn(); cur = conn.cursor()
    cur.execute("SELECT id,name FROM brands"); brand = {r[0]: r[1] for r in cur.fetchall()}
    seen = {}
    if cemail:
        cur.execute('SELECT name,"brandId","totalPrice","shippingName" FROM orders WHERE lower(email)=lower(%s)', (cemail,))
        for r in cur.fetchall():
            seen[r[0]] = r
    if cphone:
        q = norm_phone(cphone)
        cur.execute('SELECT name,"brandId","totalPrice","shippingName" FROM orders WHERE phone LIKE %s OR "shippingPhone" LIKE %s', ("%" + q, "%" + q))
        for r in cur.fetchall():
            seen[r[0]] = r
    if morder and morder not in seen:
        cur.execute('SELECT name,"brandId","totalPrice","shippingName" FROM orders WHERE name=%s', (morder,))
        for r in cur.fetchall():
            seen[r[0]] = r
    conn.close()
    prof = ssh_profit(set(seen.keys()))
    for nm, r in seen.items():
        p = prof.get(nm, {})
        orders.append({"o": nm, "brand": brand.get(r[1], "?"), "total": float(r[2] or 0), "cust": r[3],
                       "st": p.get("st", "?"), "skus": p.get("skus", ""), "awb": p.get("awb", ""), "ck": p.get("ck", "")})

    livr = sum(1 for o in orders if o["st"] == "Livrata")
    refz = sum(1 for o in orders if o["st"] == "Refuzata")
    ltv = sum(o["total"] for o in orders if o["st"] == "Livrata")
    name = cname or next((o["cust"] for o in orders if o["cust"]), "(necunoscut)")
    flags = []
    if refz >= 2:
        flags.append("🚩 REFUZNIC (%d refuzuri)" % refz)
    rel = next((o for o in orders if o["o"] == morder), orders[0] if orders else None)
    pfx = re.match(r"^([A-Za-z]+)", morder or (rel["o"] if rel else "") or "")
    store = store or (PREFIX.get(pfx.group(1).upper(), "?") if pfx else "?")
    action = ACTION.get(cat, "—").format(awb=(rel["awb"] if rel else "") or "<AWB>", store=store)

    if a.json:
        print(json.dumps({"conv": a.conv, "client": {"name": name, "email": cemail, "phone": cphone, "orders": len(orders),
              "ltv": ltv, "refused": refz, "flags": flags}, "order": rel, "category": cat, "sentiment": sent,
              "intensity": inten, "request": " ".join((fmsg or subject or "").split())[:200], "action": action},
              ensure_ascii=False, indent=1, default=str)); return

    print("═" * 70)
    print("  PROFIL 360 (scriptat) — conv #%s | %s | %s" % (a.conv, store, channel or "?"))
    print("═" * 70)
    print("👤 CLIENT: %s | 📧 %s | 📱 %s" % (name, cemail or "—", cphone or "—"))
    print("   %d comenzi · LTV %s lei · livrate %d / refuzate %d  %s" % (len(orders), "{:,.0f}".format(ltv), livr, refz, " ".join(flags)))
    if rel:
        print("📦 COMANDĂ: %s (%s) | status: %s | curier %s AWB %s" % (rel["o"], rel["brand"], rel["st"], rel["ck"] or "?", rel["awb"] or "—"))
        print("   produse: %s" % (rel["skus"] or "—")[:60])
    else:
        print("📦 COMANDĂ: (niciuna legată — posibil prospect / pre-vânzare)")
    print("❓ CATEGORIE: %s" % (cat or "?"))
    print("   ce scrie clientul: „%s”" % " ".join((fmsg or subject or "").split())[:120])
    print("😶 SENTIMENT: %s%s" % (SENT.get(sent, sent or "?"), (" (intensitate %s)" % inten) if inten and inten not in ("0", "None") else ""))
    print("✅ ACȚIUNE (procedura ARONA): %s" % action)


if __name__ == "__main__":
    main()
