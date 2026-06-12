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
import os, re, json, sqlite3, subprocess, urllib.parse, urllib.request, argparse, datetime
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
# AWB: mulți clienți dau numărul AWB crezând că e numărul comenzii. AWB-urile DPD au 11-15 cifre
# (telefoanele RO au 10) → îl rezolvăm prin profit_orders.awb → order_name. Doar match exact în awb_idx.
AWB_RE = re.compile(r"\b(\d{11,15})\b")
PROFIT_DB = os.path.join(REPO, "data", "profitability.db")
# „comanda pe numele X" / „numele meu e X" → 2-3 cuvinte cu majusculă (fallback, doar nume UNICE)
NAME_RE = re.compile(r"(?:pe\s+numele|numele\s+(?:meu\s+)?(?:este|e|de)?|comanda\s+(?:este\s+)?pe)\s*:?\s*"
                     r"([A-ZĂÂÎȘȚ][a-zăâîșț]+(?:\s+[A-ZĂÂÎȘȚ][a-zăâîșț]+){1,2})")
SOCIAL = ("facebook_message", "messenger", "instagram_message", "email_from_widget", "email")  # toate canalele de conversație (comentariile la reclame excluse — ~0 contact)
# pagină FB/IG → magazin (din memoria fb-page-store-map) — dezambiguizează numele după magazinul paginii
PAGE_STORE = {
    "426248277236834": "Esteban", "364899953373966": "Ofertele Zilei", "775068272350568": "Magdeal",
    "569610726226886": "Reduceri bune", "676105508924341": "George Talent", "568416516348894": "Belasil",
    "582569158278392": "Nubra", "700342149818211": "Bonhaus PL", "629666993566339": "Grandia",
    "434151126459295": "Bonhaus CZ", "651700798017858": "Casa Ofertelor", "582681401604162": "Ofertele Zilei",
    "1678573069021466": "Orasul Verde", "522811567592063": "Gento", "621560724373069": "Carpetto",
    "680369271815957": "Bonhaus BG", "421367954403103": "Apreciat", "1805415543098993": "Rossi Nails",
}
WINDOW_DAYS = 120  # comanda relevantă tichetului: în jurul datei tichetului (±N zile)


def parse_dt(v):
    if not v:
        return None
    try:
        return datetime.datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None
BAD_EMAIL = ("richpanel", "judgeme", "shopify", "sentry", "facebook", "no-reply", "noreply", "mailer")
# emailurile AGENȚILOR (apar în transcript ca expeditor — NU sunt clientul)
AGENT_EMAILS = {"annamariarugina982@gmail.com", "martina.klimcikova@seznam.cz", "staverdaniela1@gmail.com",
                "contact@nocturna.ro", "ralucadiaconu636@gmail.com", "contact@upstreamtradellc.com"}


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def ph9(p):
    d = "".join(c for c in (p or "") if c.isdigit())
    return d[-9:] if len(d) >= 9 else ""


_DEACC = str.maketrans("ăâîșşțţáéíóúàèČčŠšŽžĂÂÎȘŞȚŢ", "aaissttaeiouaeCcSsZzAAISSTT")


def name_key(s):
    """normalizează un nume pt matching: lowercase, fără diacritice, spații colapsate."""
    return " ".join((s or "").translate(_DEACC).lower().split())


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
    ap.add_argument("--fast", action="store_true", help="DOAR pasul rapid (nume FB, fără MCP) — ieftin, pt pipeline-ul des")
    a = ap.parse_args()

    # index metrics
    print("→ index comenzi metrics…")
    u = urllib.parse.urlparse(secret("DATABASE_URL_METRICS"))
    mc = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""),
                              password=urllib.parse.unquote(u.password or ""), host=u.hostname,
                              port=u.port or 5432, database=(u.path or "/").lstrip("/"))
    cur = mc.cursor()
    cur.execute("SELECT id,name FROM brands"); brand = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute('SELECT name,email,phone,"shippingPhone","brandId","shippingName","shopifyCreatedAt" FROM orders')
    email_idx, phone_idx, name_idx = {}, {}, {}
    by_person = {}  # name_key(shippingName) -> [ {order, store, date, rec, phones:set} ] — toate comenzile acelui nume
    for name, email, phone, sphone, bid, sname, odate in cur.fetchall():
        store = brand.get(bid, "?")
        rec = (name, store, sname)
        if name:
            name_idx.setdefault(name.upper(), rec)
        if email:
            email_idx.setdefault(email.lower(), rec)
        phs = set()
        for p in (phone, sphone):
            k = ph9(p)
            if k:
                phone_idx.setdefault(k, rec); phs.add(k)
        if sname and len(sname.split()) >= 2:
            by_person.setdefault(name_key(sname), []).append(
                {"order": name, "store": store, "date": parse_dt(odate), "rec": rec, "phones": phs})
    mc.close()
    # nume „unice" = aparțin unui singur client (un singur telefon distinct peste toate comenzile)
    person_idx = {nk: lst[0]["rec"] for nk, lst in by_person.items()
                  if len(set().union(*[c["phones"] for c in lst])) <= 1}
    print(f"  comenzi:{len(name_idx)} emailuri:{len(email_idx)} telefoane:{len(phone_idx)} nume:{len(by_person)} (unice:{len(person_idx)})")

    def pick_by_name(cname, store, tdate):
        """alege comanda pt un nume FB dat magazinul (paginii) + data tichetului.
        nume unic → direct; nume multiplu → filtrează pe magazin + cea mai apropiată dată în fereastră."""
        nk = name_key(cname)
        cands = by_person.get(nk)
        if not cands:
            return None, None
        pool = [c for c in cands if c["store"] == store] if store else cands[:]
        if store and not pool:
            return None, None  # magazin cunoscut dar niciun nume pe el → nu lega cross-magazin greșit
        if len({c["order"] for c in pool}) == 1:
            return pool[0]["rec"], ("deep_fbname_page" if store else "deep_fbname")
        # mai multe comenzi → omonime? (mai multe telefoane = mai mulți oameni cu același nume)
        multi_person = len(set().union(*[c["phones"] for c in pool])) > 1
        if multi_person and not store:
            return None, None  # nume comun + mai mulți oameni + fără magazinul paginii → prea riscant
        if tdate is None:
            return None, None
        best, bestd = None, None
        for c in pool:  # cea mai apropiată comandă de data tichetului, în fereastră
            if not c["date"]:
                continue
            dd = abs((c["date"] - tdate).days)
            if dd <= WINDOW_DAYS and (bestd is None or dd < bestd):
                bestd, best = dd, c
        return (best["rec"], "deep_fbname_date") if best is not None else (None, None)

    # index AWB → order (din profit_orders, local pe VPS). AWB-ul = „numărul de comandă" pe care-l dau mulți clienți.
    awb_idx = {}
    if os.path.exists(PROFIT_DB):
        pc = sqlite3.connect("file:" + PROFIT_DB + "?mode=ro", uri=True, timeout=30)
        for awb, oname in pc.execute("SELECT awb,order_name FROM profit_orders WHERE awb IS NOT NULL AND awb!=''"):
            aw = str(awb).strip()
            if aw.isdigit() and oname and oname.upper() in name_idx:
                awb_idx.setdefault(aw, name_idx[oname.upper()])
        pc.close()
    print(f"  awb-uri: {len(awb_idx)}" + ("" if awb_idx else "  (fără profitability.db local — sar peste AWB)"))

    # candidați nelegați — cu nume FB (customer_name), data tichetului (created_at) și pagina (raw.to.id)
    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=30)
    base = "(match_order IS NULL OR match_order='')"
    if a.all:
        where, params = base, ()
    else:
        where, params = "channel IN (%s) AND %s" % (",".join("?" * len(SOCIAL)), base), SOCIAL
    try:
        rows = con.execute("SELECT id,conversation_no,customer_name,created_at,resolved_store,"
                           "json_extract(raw,'$.to.id') FROM tickets WHERE " + where, params).fetchall()
    except sqlite3.OperationalError:  # json1 indisponibil → fără pagină, doar resolved_store
        rows = [(r[0], r[1], r[2], r[3], r[4], None) for r in
                con.execute("SELECT id,conversation_no,customer_name,created_at,resolved_store FROM tickets WHERE " + where, params).fetchall()]
    con.close()

    def store_of(rstore, page):
        return rstore or PAGE_STORE.get(str(page) if page else "")

    # ── PAS RAPID (fără MCP, gratis): leagă pe numele FB + magazinul paginii + data tichetului ──
    found, linked = [], set()
    for tid, no, cname, created, rstore, page in rows:
        if not cname or len(str(cname).split()) < 2:
            continue
        rec, method = pick_by_name(cname, store_of(rstore, page), parse_dt(created))
        if rec:
            found.append((tid, None, None, rec[0], rec[1], method)); linked.add(tid)
    print(f"  PAS RAPID (nume FB, fără MCP): {len(found)} legate din {len(rows)} nelegate")

    # ── PAS MCP: pe ce a rămas, citește CORPUL conversației (order# / AWB / email / telefon / nume) ──
    todo = [] if a.fast else [(tid, no, store_of(rstore, page), parse_dt(created), cname)
            for (tid, no, cname, created, rstore, page) in rows if tid not in linked]
    if a.limit:
        todo = todo[:a.limit]
    print(("  (--fast: sar peste MCP)" if a.fast else f"→ {len(todo)} thread-uri pt extragere profundă MCP (workers={a.workers})"))

    mcp = MCP(secret("RICHPANEL_MCP_TOKEN")) if todo else None

    def work(row):
        tid, no, store, tdate, cname = row
        blob = mcp.conv_text(no)
        if not blob:
            return None
        rec, method, em, phn = None, None, None, None
        # 1) NUMĂR DE COMANDĂ în text = cel mai direct
        for pfx, dig in ORDER_RE.findall(blob):
            on = (pfx + dig).upper()
            if on in name_idx:
                rec, method = name_idx[on], "deep_order"; break
        emails, phones = set(), set()
        for e in EMAIL_RE.findall(blob):
            el = e.lower()
            if el not in AGENT_EMAILS and not any(b in el for b in BAD_EMAIL):
                emails.add(el)
        for p in PHONE_RE.findall(blob):
            k = ph9(p)
            if k:
                phones.add(k)
        if not rec:  # 2) email
            for e in emails:
                if e in email_idx:
                    rec, method, em = email_idx[e], "deep_email", e; break
        if not rec:  # 3) telefon
            for p in phones:
                if p in phone_idx:
                    rec, method, phn = phone_idx[p], "deep_phone", p; break
        if not rec:  # 4) AWB (clientul dă AWB-ul crezând că e nr. comenzii)
            for awb in AWB_RE.findall(blob):
                if awb in awb_idx:
                    rec, method = awb_idx[awb], "deep_awb"; break
        if not rec:  # 5) nume din corp SAU numele FB — dezambiguizat pe magazin(pagină)+dată
            for nm in list(NAME_RE.findall(blob)) + ([cname] if cname else []):
                rr, mm = pick_by_name(nm, store, tdate)
                if rr:
                    rec, method = rr, mm; break
        if not rec:
            return None
        return (tid, em or next(iter(emails), None), phn or next(iter(phones), None), rec[0], rec[1], method)

    done = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(work, r) for r in todo]
        for f in as_completed(futs):
            done += 1
            r = f.result()
            if r:
                found.append(r)
            if done % 200 == 0:
                print(f"  …{done}/{len(todo)} | legate nou (MCP): {len(found)}")

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
    n = len(rows)
    print(f"\n════ {len(found)}/{n} thread-uri NOU legate la un client ({100*len(found)//max(n,1)}%) ════")
    bym = {}
    for r in found:
        bym[r[5]] = bym.get(r[5], 0) + 1
    print("  metode:", dict(sorted(bym.items(), key=lambda x: -x[1])))
    con.close()


if __name__ == "__main__":
    main()
