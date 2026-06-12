# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
richpanel_auto_triage.py — TRIAJ AUTOMAT pe conversațiile OPEN din Richpanel:
propune TAG magazin + categorie + PRIORITATE (VIP / escaladare ANPC). Azi 99,7% din
conversații n-au niciun tag → tagging-ul ajută enorm la rutare.

⚠️ DRY-RUN implicit (arată ce AR seta, nu scrie nimic). Cu --apply scrie în Richpanel
DOAR taguri + prioritate (operații interne). NICIODATĂ mesaj la client.

  uv run richpanel_auto_triage.py                 # DRY-RUN — ce ar tagui/prioritiza
  uv run richpanel_auto_triage.py --limit 100
  uv run richpanel_auto_triage.py --json
  uv run richpanel_auto_triage.py --apply         # scrie tag+prioritate (niciun mesaj la client)
"""
import os, re, json, subprocess, urllib.parse, urllib.request, argparse, collections
import pg8000.dbapi

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MCP_URL = "https://mcp.richpanel.com/mcp"

# pagină FB/IG -> magazin (din memoria fb-page-store-map)
PAGE_STORE = {
    "426248277236834": "Esteban", "364899953373966": "Ofertele Zilei", "775068272350568": "Magdeal",
    "569610726226886": "Reduceri bune", "676105508924341": "George Talent", "568416516348894": "Belasil",
    "582569158278392": "Nubra", "700342149818211": "Bonhaus PL", "629666993566339": "Grandia",
    "434151126459295": "Bonhaus CZ", "651700798017858": "Casa Ofertelor", "582681401604162": "Ofertele Zilei",
    "1678573069021466": "Orasul Verde", "522811567592063": "Gento", "621560724373069": "Carpetto",
    "680369271815957": "Bonhaus BG", "421367954403103": "Apreciat", "1805415543098993": "Rossi Nails",
}
ORDER_PFX = {"EST": "Esteban", "GT": "George Talent", "NUB": "Nubra", "GEN": "Gento", "GRAN": "Grandia",
             "GRAND": "Grandia", "BELA": "Belasil", "MAG": "Magdeal", "OFER": "Ofertele Zilei", "RED": "Reduceri bune",
             "BON": "Bonhaus RO", "BONBG": "Bonhaus BG", "CZ": "Bonhaus CZ", "PL": "Bonhaus PL", "CARP": "Carpetto",
             "COV": "Covoria", "APR": "Apreciat", "ROSSI": "Rossi Nails"}
ORDER_RE = re.compile(r"\b(EST|GT|NUB|GRAND|GRAN|MAG|OFER|RED|BONBG|BON|CZ|PL|BELA|GEN|CARP|COV|APR|ROSSI)[ -]?(\d{4,7})\b", re.I)
DEACC = str.maketrans("ăâîșşțţ", "aaissttt"[0:7])
CATS = [
    ("anulare", r"anulez|anulare|renunt la comanda|nu mai vreau|cancel|storno|anuluj"),
    ("retur", r"\bretur|returnez|banii inapoi|refund|\breturn\b|odstoupeni|zwrot"),
    ("problema_produs", r"defect|stricat|spart|lipseste|lipsa|deteriorat|gresit produs|alt produs|damaged|broken"),
    ("modificare_comanda", r"gresit (adresa|nr)|alta adresa|adresa gresita|modific|change.*address"),
    ("livrare_wismo", r"unde (e|este)|cand ajunge|coletul|nu a ajuns|nu am primit|awb|tracking|intarzi|where is my order"),
    ("plata_factura", r"factura|am platit|card.*(debitat|taxat)|invoice"),
    ("presale_intrebare", r"cat costa|ce pret|pe stoc|aveti|disponibil|cum comand|how much|in stock"),
]
ESCAL = re.compile(r"anpc|protectia consumator|dau in judecat|instanta|avocat|denunt", re.I)


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def deacc(s):
    return (s or "").lower().translate(DEACC)


def categorize(text):
    t = deacc(text)
    for cat, pat in CATS:
        if re.search(pat, t):
            return cat
    return "general"


class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "triage", "version": "1"}}})

    def _post(self, p):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        ln = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(ln[-1][5:]) if ln else json.loads(body)

    def call(self, name, args):
        try:
            r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
            txt = r["result"]["content"][0]["text"]
            try:
                return json.loads(txt)
            except Exception:
                return {"_text": txt}
        except Exception as e:
            return {"_error": str(e)}


def vip_set():
    """Telefoane/emailuri cu LTV livrat >=1000 RON (din metrics)."""
    try:
        u = urllib.parse.urlparse(secret("DATABASE_URL_METRICS"))
        c = pg8000.dbapi.connect(ssl_context=True, user=urllib.parse.unquote(u.username or ""), password=urllib.parse.unquote(u.password or ""),
                                 host=u.hostname, port=u.port or 5432, database=(u.path or "/").lstrip("/"))
        cur = c.cursor()
        cur.execute('SELECT lower(email), SUM("totalPrice") FROM orders GROUP BY 1 HAVING SUM("totalPrice")>=1000')
        vips = set(r[0] for r in cur.fetchall() if r[0])
        c.close()
        return vips
    except Exception:
        return set()


def triage(t, vips):
    frm = t.get("from") or {}
    to = t.get("to") or {}
    email = (frm.get("email") or (frm.get("id") if isinstance(frm.get("id"), str) and "@" in (frm.get("id") or "") else "") or "").lower()
    blob = (t.get("subject") or "") + " " + (t.get("first_message") or "")
    # magazin
    store = PAGE_STORE.get((to.get("id") if isinstance(to, dict) else None) or "")
    m = ORDER_RE.search(blob)
    if not store and m:
        store = ORDER_PFX.get(m.group(1).upper())
    if not store and "@" in email:
        dom = email.split("@")[-1].split(".")[0]
        store = next((s for p, s in ORDER_PFX.items() if dom in s.lower().replace(" ", "")), None)
    cat = categorize(blob)
    if ESCAL.search(blob):
        prio, why = "URGENT", "escaladare ANPC/juridic"
    elif email and email in vips:
        prio, why = "HIGH", "client VIP (LTV ≥1000)"
    elif cat in ("retur", "problema_produs", "anulare"):
        prio, why = "HIGH", cat
    else:
        prio, why = "NORMAL", cat
    return {"no": t.get("conversation_no"), "id": t.get("id"), "store": store or "necunoscut",
            "cat": cat, "prio": prio, "why": why, "subj": " ".join(blob.split())[:50]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=80); ap.add_argument("--apply", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))
    vips = vip_set()
    convs, page, seen = [], 1, set()
    while len(convs) < a.limit:
        r = mcp.call("list_conversations", {"status": "open", "page": page, "per_page": 50})
        batch = (r.get("conversations") or r.get("tickets") or r.get("results") or []) if isinstance(r, dict) else []
        if not batch:
            break
        for t in batch:
            cid = t.get("id") or t.get("conversation_no")
            if cid not in seen:
                seen.add(cid); convs.append(t)
        page += 1
        if page > 6:
            break
    rows = [triage(t, vips) for t in convs[:a.limit]]
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1, default=str)); return
    head = "APLICAT (scris în Richpanel)" if a.apply else "DRY-RUN (nu am scris nimic)"
    print("═" * 84)
    print("  RICHPANEL AUTO-TRIAGE — %s  |  %d conversații OPEN" % (head, len(rows)))
    print("  Distribuție prioritate: " + " · ".join("%s=%d" % (k, v) for k, v in collections.Counter(x["prio"] for x in rows).most_common()))
    print("═" * 84)
    pr_rank = {"URGENT": 0, "HIGH": 1, "NORMAL": 2}
    for x in sorted(rows, key=lambda z: pr_rank.get(z["prio"], 3)):
        print("  #%-7s %-9s %-14s %-16s %s" % (x["no"] or "?", x["prio"], x["store"][:14], x["cat"][:16], x["subj"]))
        if a.apply and x["id"]:
            tags = [t for t in ["magazin:" + x["store"], "cat:" + x["cat"]] if "necunoscut" not in t]
            mcp.call("add_tags_to_conversation", {"conversation_id": x["id"], "tags": tags})
            if x["prio"] in ("URGENT", "HIGH"):
                mcp.call("update_conversation", {"conversation_id": x["id"], "priority": x["prio"]})
    if not a.apply:
        print("\n  → rulează cu --apply ca să scrie tag+prioritate (niciun mesaj la client).")


if __name__ == "__main__":
    main()
