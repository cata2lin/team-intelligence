# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""Bulk-link Richpanel tickets -> Shopify customers + resolve store. Builds customer_identity + enriches tickets."""
import os, re, json, sqlite3, subprocess, urllib.parse, datetime
import pg8000.dbapi

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))  # .../Scripturi
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?4?0)\s*7\d{2}[\s.\-]?\d{3}[\s.\-]?\d{3}")

# Mapare PAGINĂ FB/IG -> MAGAZIN, rezolvată prin browser autentificat (graph.facebook.com/<id> -> handle real)
PAGE_OVERRIDE = {
    "426248277236834": "Esteban", "364899953373966": "Ofertele Zilei", "775068272350568": "Magdeal",
    "569610726226886": "Reduceri bune", "676105508924341": "George Talent", "568416516348894": "Belasil",
    "582569158278392": "Nubra", "700342149818211": "Bonhaus PL", "629666993566339": "Grandia",
    "434151126459295": "Bonhaus CZ", "651700798017858": "Casa Ofertelor", "582681401604162": "Ofertele Zilei",
    "1678573069021466": "Orasul Verde", "522811567592063": "Gento", "621560724373069": "Carpetto",
    "680369271815957": "Bonhaus BG", "421367954403103": "Apreciat", "1805415543098993": "Rossi Nails",
    "61586834387211": "Lab Noir",
}


# domeniu email `to` → MAGAZIN (DOAR domeniile specifice unui magazin). Pe astea, magazinul
# tichetului e cunoscut din inbox → alege comanda clientului DIN acel magazin.
STORE_DOMAIN = {
    "esteban.ro": "Esteban", "nubra.ro": "Nubra", "rossinails.ro": "Rossi Nails",
    "belasil.ro": "Belasil", "george-talent.ro": "George Talent", "carpetto.ro": "Carpetto",
    "grandia.ro": "Grandia", "apreciat.ro": "Apreciat", "casaofertelor.ro": "Casa Ofertelor",
    "bonhaus.bg": "Bonhaus BG", "bonhaus.cz": "Bonhaus CZ", "bonhaus.pl": "Bonhaus PL",
    "bonhaus.ro": "Bonhaus", "gento.customerdesk.io": "Gento",
}
# inboxuri PARTAJATE (primesc tichete pt mai multe magazine) — NU identifică un magazin.
SHARED_INBOX = {"nocturna.ro", "trynocturna.eu", "nocturna.bg", "nocturna.pl", "nocturna.customerdesk.io", "nocturna.hu"}


def skey(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def parse_dt(v):
    if not v:
        return None
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def ph9(p):
    d = "".join(c for c in (p or "") if c.isdigit())
    return d[-9:] if len(d) >= 9 else ""


def prefix(name):
    m = re.match(r"^([A-Za-z]+)", name or "")
    return m.group(1).upper() if m else ""


print("→ conectare metrics + index comenzi…")
u = urllib.parse.urlparse(secret("DATABASE_URL_METRICS"))
mc = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                          password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                          port=u.port or 5432, database=(u.path or "/").lstrip("/"))
cur = mc.cursor()
cur.execute("SELECT id,name FROM brands")
brand = {r[0]: r[1] for r in cur.fetchall()}
cur.execute('SELECT name,email,phone,"shippingPhone","shippingName","brandId","totalPrice","shopifyCreatedAt" FROM orders')
email_idx, phone_idx, prefix_store = {}, {}, {}
rows = cur.fetchall()
for name, email, phone, sphone, sname, bid, total, cat in rows:
    store = brand.get(bid, "?")
    rec = {"o": name, "store": store, "name": sname, "total": float(total or 0), "date": str(cat)[:10]}
    if email:
        email_idx.setdefault(email.lower(), []).append(rec)
    for p in (phone, sphone):
        k = ph9(p)
        if k:
            phone_idx.setdefault(k, []).append(rec)
    px = prefix(name)
    if px:
        prefix_store.setdefault(px, {}).setdefault(store, 0)
        prefix_store[px][store] += 1
mc.close()
prefix2store = {px: max(d, key=d.get) for px, d in prefix_store.items()}
print(f"  comenzi indexate: {len(rows)} | emailuri:{len(email_idx)} telefoane:{len(phone_idx)} | prefixe:{len(prefix2store)}")

# ── Richpanel tickets ──
con = sqlite3.connect(DB, timeout=60)
con.execute("PRAGMA busy_timeout=60000")
con.row_factory = sqlite3.Row
tk = con.execute("SELECT id,channel,first_message,subject,store,order_name,raw FROM tickets").fetchall()
if not tk:
    print("Niciun tichet în DB — rulează întâi pull."); raise SystemExit
print(f"→ {len(tk)} tichete")

# Pas 1: parse raw + rezolva contact/comenzi/store via email/telefon/order (in memorie)
def resolve_one(t):
    try:
        raw = json.loads(t["raw"]) if t["raw"] else {}
    except Exception:
        raw = {}
    frm = raw.get("from") or {}
    to = raw.get("to") or {}
    page = to.get("id") if isinstance(to, dict) else None
    to_email = to.get("email") if isinstance(to, dict) else None
    dom = to_email.split("@")[-1].lower() if to_email and "@" in to_email else None
    # magazinul TICHETULUI (din inbox): domeniu specific SAU pagină FB fixă. Inbox partajat → necunoscut.
    ticket_store = STORE_DOMAIN.get(dom) if dom else None
    if not ticket_store and page in PAGE_OVERRIDE:
        ticket_store = PAGE_OVERRIDE[page]
    tdate = parse_dt(raw.get("created_at"))
    femail = frm.get("email") or (frm.get("id") if isinstance(frm.get("id"), str) and "@" in (frm.get("id") or "") else None)
    emails, phones = set(), set()
    if femail:
        emails.add(femail.lower())
    blob = (t["first_message"] or "") + " " + (t["subject"] or "")
    for e in EMAIL_RE.findall(blob):
        if "richpanel" not in e and "judgeme" not in e and "shopify" not in e:
            emails.add(e.lower())
    for p in PHONE_RE.findall(blob):
        k = ph9(p)
        if k:
            phones.add(k)
    matched, method = [], None
    for e in emails:
        if e in email_idx:
            matched += email_idx[e]; method = "email"
    if not matched:
        for p in phones:
            if p in phone_idx:
                matched += phone_idx[p]; method = "phone"
    matched = list({r["o"]: r for r in matched}.values())
    # ALEGE comanda corectă: întâi cele din MAGAZINUL TICHETULUI, apoi cea mai apropiată de DATA tichetului.
    chosen = None
    if matched:
        pool = [r for r in matched if ticket_store and skey(r["store"]) == skey(ticket_store)]
        if not pool:
            pool = matched
        if tdate:
            chosen = min(pool, key=lambda r: abs((parse_dt(r["date"]) - tdate).days) if parse_dt(r["date"]) else 10 ** 6)
        else:
            chosen = pool[0]
    store = ticket_store or (chosen["store"] if chosen else None)
    if not store and t["order_name"]:
        store = prefix2store.get(prefix(t["order_name"])); method = method or ("order" if store else None)
    cust = (chosen.get("name") if chosen else None) or next((r["name"] for r in matched if r["name"]), None)
    return {"t": t, "page": page, "emails": emails, "phones": phones, "matched": matched,
            "chosen": chosen, "store": store, "method": method, "cust": cust}

parsed = [resolve_one(t) for t in tk]

# Pas 2: voteaza pagina->magazin din store-urile rezolvate SIGUR (email/telefon/order)
page_votes = {}
for r in parsed:
    if r["page"] and r["store"] and r["method"] in ("email", "phone", "order"):
        page_votes.setdefault(r["page"], {}).setdefault(r["store"], 0)
        page_votes[r["page"]][r["store"]] += 1
page2store = {p: max(d, key=d.get) for p, d in page_votes.items()}
page2store.update(PAGE_OVERRIDE)  # override-ul rezolvat prin browser câștigă
print(f"  pagini FB/IG mapate la magazin: {len(page2store)} ({len(PAGE_OVERRIDE)} override fix + restul din voturi)")

# normalizator nume magazin (pt subiecte gen "Chat pe Nocturna.ro")
STORE_NAMES = set(brand.values()) | set(PAGE_OVERRIDE.values()) | {"Nocturna", "Covoria", "Lab Noir"}
store_norm = {re.sub(r"[^a-z0-9]", "", s.lower()): s for s in STORE_NAMES if s and s != "?"}


def store_from_subject(subj):
    m = re.search(r"[Cc]hat pe\s+([A-Za-z0-9 .\-]{2,25})", subj or "") or re.search(r"\bpe\s+([A-Za-z0-9\-]{2,20})\.ro\b", subj or "")
    if not m:
        return None
    tok = re.sub(r"[^a-z0-9]", "", m.group(1).replace(".ro", "").lower())
    return store_norm.get(tok)

# Pas 3: propaga store pe tichetele fara store dar cu pagina cunoscuta + scrie DB
con.execute("DROP TABLE IF EXISTS customer_identity")
con.execute("""CREATE TABLE customer_identity (
  ticket_id TEXT PRIMARY KEY, contact_email TEXT, contact_phone TEXT,
  customer_name TEXT, resolved_store TEXT, order_names TEXT, order_count INT, link_method TEXT)""")
# adauga coloane pe tickets daca lipsesc
for col in ("resolved_store", "contact_email", "contact_phone", "match_order", "link_method"):
    try:
        con.execute(f"ALTER TABLE tickets ADD COLUMN {col} TEXT")
    except Exception:
        pass

stats = {"linked": 0, "store_resolved": 0, "by_method": {}}
for r in parsed:
    t, page, matched, store, method = r["t"], r["page"], r["matched"], r["store"], r["method"]
    # fallback store: pagina cunoscuta -> store existent in tabel
    if not store and page and page in page2store:
        store = page2store[page]; method = method or "page"
    if not store:
        ss = store_from_subject(t["subject"])
        if ss:
            store = ss; method = method or "subject"
    if not store and t["store"]:
        store = t["store"]
    if matched:
        stats["linked"] += 1
        stats["by_method"][method] = stats["by_method"].get(method, 0) + 1
    if store:
        stats["store_resolved"] += 1
    con.execute("INSERT OR REPLACE INTO customer_identity VALUES (?,?,?,?,?,?,?,?)",
                (t["id"], next(iter(r["emails"]), None), next(iter(r["phones"]), None), r["cust"], store,
                 ",".join(x["o"] for x in matched), len(matched), method))
    con.execute("UPDATE tickets SET resolved_store=?, contact_email=?, contact_phone=?, match_order=?, link_method=? WHERE id=?",
                (store, next(iter(r["emails"]), None), next(iter(r["phones"]), None),
                 (r["chosen"]["o"] if r.get("chosen") else None), method, t["id"]))
con.commit()

# ── stats ──
n = len(tk)
before_store = con.execute("SELECT COUNT(*) FROM tickets WHERE store IS NOT NULL AND store!='' AND store!='necunoscut'").fetchone()[0]
after_store = con.execute("SELECT COUNT(*) FROM tickets WHERE resolved_store IS NOT NULL AND resolved_store!=''").fetchone()[0]
print("\n════ REZULTAT ════")
print(f"  Tichete legate la un client (comandă): {stats['linked']}/{n} ({100*stats['linked']//max(n,1)}%)  metode={stats['by_method']}")
print(f"  Magazin rezolvat: {before_store} → {after_store}/{n} ({100*after_store//max(n,1)}%)")
print("  Top magazine (resolved):")
for st, c in con.execute("SELECT resolved_store,COUNT(*) FROM tickets WHERE resolved_store!='' GROUP BY 1 ORDER BY 2 DESC LIMIT 12"):
    print(f"    {st:18} {c}")
con.close()
