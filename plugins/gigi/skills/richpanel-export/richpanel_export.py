# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
richpanel_export.py — export istoric al tichetelor Richpanel în SQLite local, prin
endpoint-ul MCP (JSON-RPC direct cu RICHPANEL_MCP_TOKEN din KB — API-ul oficial e blocat
pe cont). Fază 1 = sumarele conversațiilor (subiect, prim mesaj, canal, agent, client,
timpi) + CATEGORISIRE pe reguli + magazin + nr comandă. Resumabil (merge pe zile).

  uv run richpanel_export.py pull --from 2026-06-09 --to 2026-06-11     # export interval
  uv run richpanel_export.py pull --from 2026-05-12 --to 2026-06-11 --quiet   # backfill (fundal)
  uv run richpanel_export.py stats                                      # ce avem în DB
  uv run richpanel_export.py categorize                                 # re-ruleaza regulile
"""
import os, sys, json, re, time, sqlite3, argparse, subprocess, urllib.request, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
DB_DEFAULT = "/Users/gheorghebeschea/Downloads/Scripturi/data/richpanel_tickets.db"
MCP_URL = "https://mcp.richpanel.com/mcp"

STORE_BY_EMAIL = {
    "esteban.ro": "Esteban", "george-talent.ro": "George Talent", "nubra.ro": "Nubra",
    "grandia.ro": "Grandia", "magdeal.ro": "Magdeal", "nocturna.ro": "Nocturna",
    "ofertelezilei.ro": "Ofertele Zilei", "reduceri-bune.ro": "Reduceri bune", "reduceribune.ro": "Reduceri bune",
    "bonhaus.ro": "Bonhaus RO", "bonhaus.cz": "Bonhaus CZ", "bonhaus.pl": "Bonhaus PL", "bonhaus.bg": "Bonhaus BG",
    "belasil.ro": "Belasil", "gento.ro": "Gento", "carpetto.ro": "Carpetto", "covoria.ro": "Covoria",
    "rossinails.ro": "Rossi Nails", "apreciat.ro": "Apreciat", "labnoir.ro": "Lab Noir",
}
ORDER_PFX = {"EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GRAND": "Grandia", "GRAN": "Grandia",
             "MAG": "Magdeal", "OFER": "Ofertele Zilei", "RED": "Reduceri bune", "BON": "Bonhaus RO",
             "CZ": "Bonhaus CZ", "PL": "Bonhaus PL", "BONBG": "Bonhaus BG", "BELA": "Belasil",
             "GEN": "Gento", "CARP": "Carpetto", "COV": "Covoria", "NOC": "Nocturna", "APR": "Apreciat",
             "ROSSI": "Rossi Nails", "LUX": "Nocturna Lux"}
ORDER_RE = re.compile(r"\b(EST|GT|NUB|GRAND|GRAN|MAG|OFER|RED|BONBG|BON|CZ|PL|BELA|GEN|CARP|COV|NOC|APR|ROSSI|LUX)[ -]?(\d{4,7})\b")

RULES = [  # (categorie, regex pe subiect+prim_mesaj, lowercase, diacritice scoase). Ordinea contează (primul match câștigă).
    ("spam_automat", r"left a \d star review|left the following|judge\.?me|chargeflow|out of office|automat[ae] reply|do[- ]?not[- ]?reply|weekly tiktok|performance report|newsletter|unsubscribe|password reset|verify your email|ordine.*confermato"),
    ("recenzie_feedback", r"ce parere ai|parerea ta|recenzi|review|feedback|multumesc pentru|sunt multumit"),
    ("retur", r"\bretur|returnez|returna|trimit inapoi|banii inapoi|ramburs(?!.*plata)|refund|vreau banii|\breturn\b|odstoupeni|\bzwrot|\breso\b"),
    ("schimb_swap", r"schimb produs|schimb cu alt|alt model|alta marime|alta culoare|inlocui|exchange|wymiana"),
    ("anulare", r"anulez|anulare|anulati|renunt la comanda|nu mai vreau comanda|cancel|anuluj|zrusit|storno|cancellare"),
    ("modificare_comanda", r"gresit (nr|numarul|adresa)|schimb (nr|numarul|adresa|telefonul)|modific (comanda|adresa|telefonul)|alta adresa|adresa gresita|actualizez|edit my order|change (my|the) order|change.*address|modify.*order|wrong address|update.*address|zmiana zamowienia"),
    ("livrare_wismo", r"unde (e|este|imi)|cand ajunge|coletul|nu a ajuns|nu am primit( inca)?|awb|curier|tracking|livrarea mea|status.*comand|comanda mea.*(ajun|liv)|intarzi|where is my order|how long will it take|when will.*(arrive|receive|get)|track.*(order|my)|delivery status|haven'?t received|aktualizace zasilky|kde je (moje|ma)|kdy dorazi|gdzie (jest|moja)|kiedy.*(dotrze|przesylka)|przesylka|dov.* il mio ordine|quando arriva|spedizione"),
    ("problema_produs", r"defect|stricat|nu functioneaza|lipseste|lipsesc|lipsa (din|produs)|gresit produs|alt produs decat|incomplet|deteriorat|spart|fara (pompita|capac|accesori)|damaged|broken|missing (part|piece)|poskozen|uszkodzon"),
    ("refuz_livrare", r"refuz|nu primesc coletul|nu accept coletul"),
    ("plata_factura", r"factura|plata nu|am platit de doua|card.*(debitat|taxat)|chitanta|bon fiscal|\binvoice|faktur"),
    ("presale_intrebare", r"aveti (pe stoc|in stoc)|este pe stoc|cat costa|ce pret|livrati in|cand revine|dimensiuni|este original|mai aveti|se potriveste|disponibil|how much|what.*price|in stock|do you have|available|jaka cena|na sklad"),
    ("comanda_noua", r"vreau sa comand|as dori sa comand|plasez o comanda|cum comand|doresc sa cumpar|i want to order|how (do i|to) order"),
    ("formular_contact", r"cererea dvs\.? de contact|contact form|chat with us|start a conversation|how can we help|shared files?$"),
]
DEACC = str.maketrans("ăâîșşțţ", "aaissttt"[0:7])


def deacc(s):
    return (s or "").lower().translate(DEACC)


def categorize(subject, first_message, channel):
    txt = deacc((subject or "") + " " + (first_message or ""))
    for cat, pat in RULES:
        if re.search(pat, txt):
            return cat
    if channel in ("facebook_feed_comment", "instagram_comment"):
        return "comentariu_social"
    return "altele"


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


class MCP:
    def __init__(self, token):
        self.token = token
        self._init()

    def _post(self, payload):
        h = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        lines = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(lines[-1][5:]) if lines else json.loads(body)

    def _init(self):
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "arona-export", "version": "1.0"}}})

    def call(self, name, args, retries=3):
        for i in range(retries):
            try:
                res = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": name, "arguments": args}})
                txt = res.get("result", {}).get("content", [{}])[0].get("text", "")
                return json.loads(txt) if txt.startswith("{") else {}
            except Exception:
                if i == retries - 1:
                    raise
                time.sleep(3 * (i + 1))
                try:
                    self._init()
                except Exception:
                    pass


def ensure_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS tickets(
        id TEXT PRIMARY KEY, conversation_no INTEGER, subject TEXT, status TEXT, priority TEXT,
        assignee_id TEXT, channel TEXT, from_email TEXT, to_email TEXT,
        customer_id TEXT, customer_name TEXT, customer_email TEXT, tags TEXT,
        first_message TEXT, comment_count INTEGER, created_at TEXT, updated_at TEXT,
        store TEXT, order_name TEXT, category TEXT, raw TEXT)""")
    db.execute("CREATE INDEX IF NOT EXISTS ix_t_created ON tickets(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS ix_t_cat ON tickets(category)")
    db.execute("CREATE TABLE IF NOT EXISTS pull_log(day TEXT PRIMARY KEY, n INTEGER, done_at TEXT)")
    return db


def derive(t):
    to_email = (t.get("to") or {}).get("email") or ""
    store = ""
    for dom, s in STORE_BY_EMAIL.items():
        if dom in to_email:
            store = s; break
    m = ORDER_RE.search((t.get("subject") or "") + " " + (t.get("first_message") or ""))
    order = (m.group(1) + m.group(2)) if m else ""
    if not store and m:
        store = ORDER_PFX.get(m.group(1), "")
    cat = categorize(t.get("subject"), t.get("first_message"), t.get("channel"))
    return store, order, cat


def clean(s):
    """Scoate surrogate-urile rupte (emoji din Facebook) care strică UTF-8."""
    return s.encode("utf-8", "replace").decode("utf-8") if isinstance(s, str) else s


def upsert(db, tickets):
    for t in tickets:
        store, order, cat = derive(t)
        cust = t.get("customer") or {}
        db.execute("""INSERT OR REPLACE INTO tickets
            (id, conversation_no, subject, status, priority, assignee_id, channel, from_email, to_email,
             customer_id, customer_name, customer_email, tags, first_message, comment_count, created_at,
             updated_at, store, order_name, category, raw)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            str(t.get("id")), t.get("conversation_no"), clean(t.get("subject")), t.get("status"), t.get("priority"),
            t.get("assignee_id"), t.get("channel"),
            clean((t.get("from") or {}).get("email") or (t.get("from") or {}).get("id") or ""),
            clean((t.get("to") or {}).get("email") or ""),
            cust.get("id"), clean(cust.get("name")), clean(cust.get("email")),
            json.dumps(t.get("tags") or []), clean(t.get("first_message")), t.get("comment_count"),
            t.get("created_at"), t.get("updated_at"), store, order, cat,
            clean(json.dumps(t, ensure_ascii=False))))
    db.commit()


def pull(db, mcp, dfrom, dto, quiet=False):
    day = datetime.date.fromisoformat(dfrom)
    end = datetime.date.fromisoformat(dto)
    while day <= end:
        ds = day.isoformat()
        de = (day + datetime.timedelta(days=1)).isoformat()  # endDate este EXCLUSIV
        if db.execute("SELECT 1 FROM pull_log WHERE day=?", (ds,)).fetchone():
            day += datetime.timedelta(days=1); continue
        total = 0
        for status in ("open", "closed"):
            page = 1
            while True:
                d = mcp.call("list_conversations", {"status": status, "startDate": ds, "endDate": de,
                                                    "per_page": 50, "page": page})
                ts = d.get("tickets") or []
                upsert(db, ts); total += len(ts)
                if len(ts) < 50 or page >= 200:
                    break
                page += 1
                time.sleep(0.4)
        db.execute("INSERT OR REPLACE INTO pull_log VALUES (?,?,?)", (ds, total, datetime.datetime.now().isoformat()))
        db.commit()
        if not quiet:
            print("  %s: %d tichete" % (ds, total), flush=True)
        day += datetime.timedelta(days=1)


def stats(db):
    n, lo, hi = db.execute("SELECT COUNT(*),MIN(substr(created_at,1,10)),MAX(substr(created_at,1,10)) FROM tickets").fetchone()
    print("=== Richpanel export: %d tichete | %s -> %s ===\n" % (n, lo, hi))
    print("-- categorii --")
    for c, k in db.execute("SELECT category,COUNT(*) FROM tickets GROUP BY 1 ORDER BY 2 DESC"):
        print("  %-22s %6d" % (c, k))
    print("-- canale --")
    for c, k in db.execute("SELECT channel,COUNT(*) FROM tickets GROUP BY 1 ORDER BY 2 DESC LIMIT 8"):
        print("  %-22s %6d" % (c, k))
    print("-- magazine --")
    for c, k in db.execute("SELECT COALESCE(NULLIF(store,''),'(necunoscut)'),COUNT(*) FROM tickets GROUP BY 1 ORDER BY 2 DESC LIMIT 12"):
        print("  %-22s %6d" % (c, k))
    print("-- cu nr comanda detectat --")
    print("  %d / %d" % (db.execute("SELECT COUNT(*) FROM tickets WHERE order_name!=''").fetchone()[0], n))


def recategorize(db):
    rows = db.execute("SELECT id,subject,first_message,channel FROM tickets").fetchall()
    for tid, subj, fm, ch in rows:
        db.execute("UPDATE tickets SET category=? WHERE id=?", (categorize(subj, fm, ch), tid))
    db.commit()
    print("recategorisite: %d" % len(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["pull", "stats", "categorize"])
    ap.add_argument("--from", dest="dfrom"); ap.add_argument("--to", dest="dto")
    ap.add_argument("--db", default=DB_DEFAULT); ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    db = ensure_db(a.db)
    if a.mode == "stats":
        stats(db); return
    if a.mode == "categorize":
        recategorize(db); return
    tok = secret("RICHPANEL_MCP_TOKEN")
    if not tok:
        print("Lipsește RICHPANEL_MCP_TOKEN în KB."); sys.exit(1)
    mcp = MCP(tok)
    pull(db, mcp, a.dfrom, a.dto or a.dfrom, quiet=a.quiet)
    stats(db)


if __name__ == "__main__":
    main()
