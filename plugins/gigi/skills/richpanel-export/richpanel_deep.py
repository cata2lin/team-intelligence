# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
richpanel_deep.py — extragere PROFUNDĂ pt mesajele sociale (Messenger / FB msg / IG DM).

richpanel_link.py leagă pe email/telefon din `first_message`. Dar pe mesajele sociale
contactul e adesea ADÂNC în conversație (CS-ul îl cere după câteva mesaje). Acest pas
citește CORPUL fiecărui thread social nelegat (get_conversation), extrage email/telefon
cu regex și leagă la Shopify. Rulează după `richpanel_link.py`.

  uv run richpanel_deep.py                 # toate thread-urile sociale nelegate
  uv run richpanel_deep.py --limit 200     # doar primele N (test)
  uv run richpanel_deep.py --workers 6
"""
import os, re, json, sqlite3, subprocess, urllib.parse, urllib.request, argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import pg8000.dbapi

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MCP_URL = "https://mcp.richpanel.com/mcp"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?4?0)\s*7\d{2}[\s.\-]?\d{3}[\s.\-]?\d{3}")
ORDER_RE = re.compile(r"\b(EST|GT|NUB|GRAND|GRAN|MAG|OFER|RED|BONBG|BON|CZ|PL|BELA|GEN|CARP|COV|APR|ROSSI|LUX)[ -]?(\d{4,7})\b", re.I)
# „comanda pe numele X" / „numele meu e X" → 2-3 cuvinte cu majusculă (fallback, doar nume UNICE)
NAME_RE = re.compile(r"(?:pe\s+numele|numele\s+(?:meu\s+)?(?:este|e|de)?|comanda\s+(?:este\s+)?pe)\s*:?\s*"
                     r"([A-ZĂÂÎȘȚ][a-zăâîșț]+(?:\s+[A-ZĂÂÎȘȚ][a-zăâîșț]+){1,2})")
SOCIAL = ("facebook_message", "messenger", "instagram_message", "email_from_widget", "email")  # toate canalele de conversație (comentariile la reclame excluse — ~0 contact)
BAD_EMAIL = ("richpanel", "judgeme", "shopify", "sentry", "facebook", "no-reply", "noreply", "mailer")
# emailurile AGENȚILOR (apar în transcript ca expeditor — NU sunt clientul)
AGENT_EMAILS = {"annamariarugina982@gmail.com", "martina.klimcikova@seznam.cz", "staverdaniela1@gmail.com",
                "contact@nocturna.ro", "ralucadiaconu636@gmail.com", "contact@upstreamtradellc.com"}


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def ph9(p):
    d = "".join(c for c in (p or "") if c.isdigit())
    return d[-9:] if len(d) >= 9 else ""


class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "deep", "version": "1"}}})

    def _post(self, payload):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        lines = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(lines[-1][5:]) if lines else json.loads(body)

    def conv_text(self, conv_no):
        for attempt in range(2):
            try:
                r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "get_conversation", "arguments": {"conversation_number": conv_no, "mode": "audit", "max_messages": 50}}})
                return r["result"]["content"][0]["text"]
            except Exception:
                if attempt:
                    return ""
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int); ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--all", action="store_true", help="TOATE tichetele nelegate (nu doar conversațiile) — job lung")
    a = ap.parse_args()

    # index metrics
    print("→ index comenzi metrics…")
    u = urllib.parse.urlparse(secret("DATABASE_URL_METRICS"))
    mc = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                              password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                              port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    cur = mc.cursor()
    cur.execute("SELECT id,name FROM brands"); brand = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute('SELECT name,email,phone,"shippingPhone","brandId","shippingName" FROM orders')
    email_idx, phone_idx, name_idx = {}, {}, {}
    person_rec, person_ph = {}, {}  # shippingName.lower() -> rec + set telefoane (pt dezambiguizare omonime)
    for name, email, phone, sphone, bid, sname in cur.fetchall():
        rec = (name, brand.get(bid, "?"), sname)
        if name:
            name_idx.setdefault(name.upper(), rec)
        if email:
            email_idx.setdefault(email.lower(), rec)
        ph_any = None
        for p in (phone, sphone):
            k = ph9(p)
            if k:
                phone_idx.setdefault(k, rec); ph_any = k
        if sname and len(sname.split()) >= 2:
            nl = " ".join(sname.lower().split())
            person_rec[nl] = rec
            person_ph.setdefault(nl, set())
            if ph_any:
                person_ph[nl].add(ph_any)
    # DOAR nume care aparțin unui SINGUR client (un singur telefon) — evită omonimele
    person_idx = {n: r for n, r in person_rec.items() if len(person_ph.get(n, set())) == 1}
    mc.close()
    print(f"  comenzi:{len(name_idx)} emailuri:{len(email_idx)} telefoane:{len(phone_idx)} nume-unice:{len(person_idx)}")

    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=30)
    if a.all:
        todo = con.execute("SELECT id,conversation_no FROM tickets WHERE (match_order IS NULL OR match_order='')").fetchall()
    else:
        ph = ",".join("?" * len(SOCIAL))
        q = (f"SELECT id,conversation_no FROM tickets WHERE channel IN ({ph}) "
             "AND (match_order IS NULL OR match_order='')")
        todo = con.execute(q, SOCIAL).fetchall()
    con.close()
    if a.limit:
        todo = todo[:a.limit]
    print(f"→ {len(todo)} thread-uri sociale nelegate de procesat (workers={a.workers})")

    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))

    def work(row):
        tid, no = row
        blob = mcp.conv_text(no)
        if not blob:
            return None
        rec, method, em, phn = None, None, None, None
        # 1) NUMĂR DE COMANDĂ în text = cel mai direct link (dă și magazinul din prefix)
        for pfx, dig in ORDER_RE.findall(blob):
            on = (pfx + dig).upper()
            if on in name_idx:
                rec, method = name_idx[on], "deep_order"; break
        # 2) email, 3) telefon
        emails, phones = set(), set()
        for e in EMAIL_RE.findall(blob):
            el = e.lower()
            if el not in AGENT_EMAILS and not any(b in el for b in BAD_EMAIL):
                emails.add(el)
        for p in PHONE_RE.findall(blob):
            k = ph9(p)
            if k:
                phones.add(k)
        if not rec:
            for e in emails:
                if e in email_idx:
                    rec, method, em = email_idx[e], "deep_email", e; break
        if not rec:
            for p in phones:
                if p in phone_idx:
                    rec, method, phn = phone_idx[p], "deep_phone", p; break
        # 4) FALLBACK nume (doar dacă nu am găsit nimic altceva; doar nume UNICE)
        if not rec:
            for nm in NAME_RE.findall(blob):
                nl = " ".join(nm.lower().split())
                if nl in person_idx:
                    rec, method = person_idx[nl], "deep_name"; break
        if not rec:
            return None
        return (tid, em or next(iter(emails), None), phn or next(iter(phones), None), rec[0], rec[1], method)

    found = []
    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, r) for r in todo]
        for f in as_completed(futs):
            done += 1
            r = f.result()
            if r:
                found.append(r)
            if done % 200 == 0:
                print(f"  …{done}/{len(todo)} | legate nou: {len(found)}")

    # scrie in DB
    con = sqlite3.connect(DB, timeout=60)
    con.execute("PRAGMA busy_timeout=60000")
    for tid, em, phn, oname, store, method in found:
        con.execute("UPDATE tickets SET contact_email=COALESCE(contact_email,?), contact_phone=COALESCE(contact_phone,?), "
                    "match_order=?, resolved_store=COALESCE(NULLIF(resolved_store,''),?), link_method=? WHERE id=?",
                    (em, phn, oname, store, method, tid))
        con.execute("UPDATE customer_identity SET contact_email=COALESCE(contact_email,?), contact_phone=COALESCE(contact_phone,?), "
                    "customer_name=COALESCE(customer_name,?), resolved_store=COALESCE(NULLIF(resolved_store,''),?), "
                    "order_names=?, order_count=MAX(order_count,1), link_method=? WHERE ticket_id=?",
                    (em, phn, oname, store, oname, method, tid))
    con.commit()
    n = len(todo)
    print(f"\n════ {len(found)}/{n} thread-uri sociale NOU legate la un client ({100*len(found)//max(n,1)}%) ════")
    bym = {}
    for r in found:
        bym[r[5]] = bym.get(r[5], 0) + 1
    print("  metode:", bym)
    con.close()


if __name__ == "__main__":
    main()
