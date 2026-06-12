# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
customer_identity.py — IDENTITATE UNIFICATĂ a clientului, cross-platform:
Shopify (comenzi) <-> Richpanel (Email / Facebook / Instagram / Messenger).

Dai ORICE punct de pornire și primești UN singur profil:
  • cine e (nume, email-uri, telefoane, oraș, handle-uri sociale)
  • ce a cumpărat (toate comenzile din toate magazinele) + livrare/refuz/profit (din Scripturi)
  • toate tichetele lui pe TOATE canalele (din Richpanel) + flaguri (refuznic serial, LTV)

  uv run customer_identity.py --email client@gmail.com
  uv run customer_identity.py --phone 0760383019
  uv run customer_identity.py --order EST185476
  uv run customer_identity.py --conv 265226          # nr conversație Richpanel (extrage email/tel din text)
  uv run customer_identity.py --email x@y.ro --json   # ieșire JSON

PUNTEA, pe scurt:
  - Email → direct. Mesaje sociale → email/telefon e adesea ÎN textul conversației (CS îl cere) → regex.
  - Comentarii FB/IG la reclame (60%) → doar ID anonimizat + pagina → se poate atribui DOAR magazinul, nu individul (privacy Facebook).
  - Profilul de client Richpanel e deja un CDP (orderIds + magazin + LTV) → îl folosim ca punte, îmbogățit cu livrabilitate/profit din profit_orders.
NU scrie nimic nicăieri (read-only).
"""
import os, sys, re, json, subprocess, shlex, urllib.parse, urllib.request, argparse, datetime
import pg8000.dbapi

VPS = "root@84.46.242.181"
HERE = os.path.dirname(os.path.abspath(__file__))
MCP_URL = "https://mcp.richpanel.com/mcp"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?4?0)\s*7\d{2}[\s.\-]?\d{3}[\s.\-]?\d{3}")


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


def norm_phone(p):
    d = "".join(ch for ch in (p or "") if ch.isdigit())
    return d[-9:] if len(d) >= 9 else ""


def to_date(v):
    if not v:
        return ""
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        ms = int(v)
        return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
    return str(v)[:10]


# ───────────────────────── Richpanel MCP ─────────────────────────
class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": "customer-identity", "version": "1"}}})

    def _post(self, payload):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        lines = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(lines[-1][5:]) if lines else json.loads(body)

    def call(self, name, args):
        try:
            r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": name, "arguments": args}})
            txt = r["result"]["content"][0]["text"]
            try:
                return json.loads(txt)
            except Exception:
                return {"_text": txt}
        except Exception as e:
            return {"_error": str(e)}


# ───────────────────────── Shopify (metrics + profit_orders) ─────────────────────────
def mconn():
    url = secret("DATABASE_URL_METRICS"); u = urllib.parse.urlparse(url)
    return pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                                password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                                port=u.port or 5432, database=(u.path or "/").lstrip("/"))


def shopify_orders(cur, brands, emails, phones, order_names):
    """Toate comenzile pt setul de email-uri/telefoane/nr-comenzi. Dedup pe order name."""
    found = {}
    cols = 'name,"brandId","totalPrice","financialStatus","shopifyCreatedAt","shippingName",email,"shippingPhone","phone"'
    def add(rows):
        for r in rows:
            found[r[0]] = {"o": r[0], "brand": brands.get(r[1], "?"), "total": float(r[2] or 0),
                           "fin": r[3], "date": str(r[4])[:10], "cust": r[5], "email": r[6],
                           "phone": norm_phone(r[7] or r[8])}
    for e in emails:
        cur.execute('SELECT %s FROM orders WHERE lower(email)=lower(%%s)' % cols, (e,))
        add(cur.fetchall())
    for ph in phones:
        cur.execute('SELECT %s FROM orders WHERE phone LIKE %%s OR "shippingPhone" LIKE %%s' % cols,
                    ("%" + ph, "%" + ph))
        add(cur.fetchall())
    if order_names:
        ph = ",".join(["%s"] * len(order_names))
        cur.execute('SELECT %s FROM orders WHERE name IN (%s)' % (cols, ph), list(order_names))
        add(cur.fetchall())
    return list(found.values())


def ssh_profit(order_names):
    if not order_names:
        return {}
    lst = json.dumps(list(order_names))
    py = ("import sqlite3,json,sys;ns=json.loads(sys.argv[1]);c=sqlite3.connect('data/profitability.db');"
          "q='SELECT order_name,status_category,skus,revenue,currency,awb,courier_status,courier_key FROM profit_orders WHERE order_name IN (%s)'%(','.join('?'*len(ns)));"
          "print(json.dumps({r[0]:{'st':r[1],'skus':r[2],'rev':r[3],'cur':r[4],'awb':r[5],'cstat':r[6],'ck':r[7]} for r in c.execute(q,ns)}))")
    cmd = "cd /root/Scripturi && .venv/bin/python3 -c " + shlex.quote(py) + " " + shlex.quote(lst)
    out = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", VPS, cmd],
                         capture_output=True, text=True, timeout=80).stdout.strip()
    try:
        return json.loads(out.splitlines()[-1])
    except Exception:
        return {}


# ───────────────────────── Resolver ─────────────────────────
def resolve(a):
    emails, phones, order_names = set(), set(), set()
    if a.email:
        emails.add(a.email.strip().lower())
    if a.phone:
        p = norm_phone(a.phone)
        if p:
            phones.add(p)
    if a.order:
        order_names.add(a.order.strip().upper())

    tok = secret("RICHPANEL_MCP_TOKEN")
    mcp = MCP(tok) if tok else None

    # --conv: extrage email/telefon din conversație
    seed_conv = None
    if a.conv and mcp:
        conv = mcp.call("get_conversation", {"conversation_number": a.conv, "mode": "audit", "max_messages": 50})
        seed_conv = conv
        blob = json.dumps(conv, ensure_ascii=False)
        frm = (conv.get("ticket") or {}).get("from") or {} if isinstance(conv, dict) else {}
        if isinstance(frm.get("id"), str) and "@" in frm["id"]:
            emails.add(frm["id"].lower())
        for m in EMAIL_RE.findall(blob):
            if "richpanel" not in m and "sentry" not in m:
                emails.add(m.lower())
        for ph in PHONE_RE.findall(blob):
            p = norm_phone(ph)
            if p:
                phones.add(p)

    # Richpanel CDP (get_customer_by_email_or_phone) — punte + expand
    cdp = []
    if mcp:
        for e in list(emails):
            r = mcp.call("get_customer_by_email_or_phone", {"id": e, "type": "email"})
            cdp += (r.get("customers") or []) if isinstance(r, dict) else []
        for ph in list(phones):
            r = mcp.call("get_customer_by_email_or_phone", {"id": ph, "type": "phone"})
            cdp += (r.get("customers") or []) if isinstance(r, dict) else []
    # dedupe profile pe id
    cdp = list({c.get("id"): c for c in cdp if isinstance(c, dict)}.values())
    # expand seed-ul cu ce a găsit CDP (un tur)
    cdp_orderids = set()
    for c in cdp:
        for key in ("email", "billingEmail", "shippingEmail"):
            if c.get(key):
                emails.add(c[key].lower())
        for key in ("phone", "shippingPhone", "billingPhone"):
            p = norm_phone(c.get(key))
            if p:
                phones.add(p)
        for oid in (c.get("orderIds") or []):
            order_names.add(str(oid).lstrip("#").upper())
            cdp_orderids.add(str(oid).lstrip("#").upper())

    # Shopify orders
    conn = mconn(); cur = conn.cursor()
    cur.execute("SELECT id,name FROM brands"); brands = {r[0]: r[1] for r in cur.fetchall()}
    orders = shopify_orders(cur, brands, emails, phones, order_names)
    conn.close()
    # mai adună email/telefon din comenzi (pt conversații)
    for o in orders:
        if o.get("email"):
            emails.add(o["email"].lower())
        if o.get("phone"):
            phones.add(o["phone"])

    # livrabilitate/profit din profit_orders
    prof = ssh_profit({o["o"] for o in orders} | order_names)
    for o in orders:
        p = prof.get(o["o"]) or {}
        o["deliv"] = p.get("st", "?")
        o["skus"] = p.get("skus", "")
        o["cstat"] = p.get("cstat", "")
        o["awb"] = p.get("awb", "")
        o["courier"] = p.get("ck", "")

    # Conversații Richpanel cross-canal
    convos, seen = [], set()
    if mcp:
        for ident, typ in [(e, "email") for e in emails] + [(ph, "phone") for ph in phones]:
            r = mcp.call("search_conversations_by_customer", {"id": ident, "type": typ, "per_page": 50})
            for cv in (r.get("tickets") or r.get("conversations") or []) if isinstance(r, dict) else []:
                cid = cv.get("id") or cv.get("conversation_no")
                if cid in seen:
                    continue
                seen.add(cid)
                convos.append({"no": cv.get("conversation_no"), "channel": cv.get("channel"),
                               "subject": (cv.get("subject") or "")[:60], "status": cv.get("status"),
                               "created": to_date(cv.get("created_at"))})

    return {"emails": sorted(emails), "phones": sorted(phones), "cdp": cdp, "orders": orders,
            "convos": convos, "order_names": sorted(order_names), "seed_conv": seed_conv}


# ───────────────────────── Render ─────────────────────────
CH_LABEL = {"facebook_feed_comment": "FB comentariu", "facebook_message": "FB mesaj",
            "messenger": "Messenger", "instagram_comment": "IG comentariu",
            "instagram_message": "IG mesaj", "email": "Email", "email_from_widget": "Email widget"}


def render(R):
    cdp = R["cdp"]
    name = next((c.get("name") for c in cdp if c.get("name")), None)
    if not name:
        name = next((o["cust"] for o in R["orders"] if o.get("cust")), "(necunoscut)")
    city = next((c.get("city") for c in cdp if c.get("city")), "")
    social = {}
    for c in cdp:
        for net in ("facebook", "instagram", "twitter", "linkedin"):
            if c.get(net):
                social[net] = c[net]
    stores = sorted({s for c in cdp for s in (c.get("appClientIdList") or [])} |
                    {o["brand"] for o in R["orders"]})

    print("═" * 60)
    print("  IDENTITATE UNIFICATĂ: %s" % name)
    if city:
        print("  Oraș: %s" % city)
    print("═" * 60)
    print("  📧 Emailuri : %s" % (", ".join(R["emails"]) or "—"))
    print("  📱 Telefoane: %s" % (", ".join(R["phones"]) or "—"))
    if social:
        print("  🔗 Social   : %s" % ", ".join("%s=%s" % (k, v) for k, v in social.items()))
    print("  🏬 Magazine : %s" % (", ".join(stores) or "—"))

    # Comenzi
    o = R["orders"]
    livr = sum(1 for x in o if x["deliv"] == "Livrata")
    refz = sum(1 for x in o if x["deliv"] == "Refuzata")
    ltv = sum(x["total"] for x in o if x["deliv"] == "Livrata")
    refrate = refz / (livr + refz) * 100 if (livr + refz) else 0
    cdp_ltv = next((c.get("ltv") for c in cdp if c.get("ltv")), None)
    print("\n  🛒 COMENZI: %d total | %d livrate | %d refuzate | rată refuz %.0f%%" % (len(o), livr, refz, refrate))
    print("     LTV (livrate): %s lei%s" % ("{:,.0f}".format(ltv),
          ("  | LTV Richpanel: %.0f" % cdp_ltv) if cdp_ltv else ""))
    if refz >= 2 or (refrate >= 50 and (livr + refz) >= 2):
        print("     🚩 REFUZNIC SERIAL — ofertă DOAR cu plata cardului (nu COD).")
    for x in sorted(o, key=lambda z: z["date"], reverse=True)[:15]:
        prod = (x.get("skus") or "")[:32]
        print("     %-13s %-12s %7s lei | %s | %-12s | %s" % (
            x["o"], x["brand"][:12], "{:,.0f}".format(x["total"]), x["date"], x["deliv"], prod))

    # Tichete
    cv = R["convos"]
    print("\n  💬 TICHETE RICHPANEL: %d" % len(cv))
    bych = {}
    for c in cv:
        bych[c["channel"]] = bych.get(c["channel"], 0) + 1
    if bych:
        print("     pe canal: %s" % ", ".join("%s=%d" % (CH_LABEL.get(k, k), v) for k, v in sorted(bych.items(), key=lambda z: -z[1])))
    opn = [c for c in cv if (c.get("status") or "").upper() == "OPEN"]
    if opn:
        print("     ⚠️ %d DESCHISE acum" % len(opn))
    for c in sorted(cv, key=lambda z: z.get("created") or "", reverse=True)[:12]:
        print("     #%-7s %-14s %-7s %s | %s" % (
            c.get("no") or "?", CH_LABEL.get(c["channel"], c["channel"] or "?"),
            (c.get("status") or "")[:7], c.get("created") or "", c["subject"]))

    if not cdp and not o:
        print("\n  (Niciun profil/comandă găsit — posibil comentariu social fără email/telefon.)")


def main():
    ap = argparse.ArgumentParser(description="Identitate unificată client cross-platform (Shopify<->Richpanel).")
    ap.add_argument("--email"); ap.add_argument("--phone"); ap.add_argument("--order")
    ap.add_argument("--conv", help="nr conversație Richpanel (extrage email/telefon din text)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not any([a.email, a.phone, a.order, a.conv]):
        print("Dă --email / --phone / --order / --conv."); return
    R = resolve(a)
    if a.json:
        R.pop("seed_conv", None)
        print(json.dumps(R, ensure_ascii=False, indent=2, default=str))
    else:
        render(R)


if __name__ == "__main__":
    main()
