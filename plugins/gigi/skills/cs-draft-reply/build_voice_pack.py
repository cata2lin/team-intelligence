# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
build_voice_pack.py — extrage replici REALE ale agenților CS (cei 6) din tichete REZOLVATE,
grupate pe categorie, ca să le folosim ca FEW-SHOT de VOCE în cs_auto_draft.py (imită tonul,
nu copiază datele). Scrie .voice_pack.json lângă cs_auto_draft.py.

Distinge corect (ca cs-procedures): agent REAL = author_is_workspace_agent ȘI nu is_ai;
exclude botul/operator/auto. Folosește prima replică a clientului pt categorie.

  uv run build_voice_pack.py                 # scanează ~120 tichete CLOSED, 3 exemple/categorie
  uv run build_voice_pack.py --per 4 --scan 200
"""
import os, re, json, argparse, urllib.request, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
OUT = os.path.join(HERE, ".voice_pack.json")
MCP_URL = "https://mcp.richpanel.com/mcp"

DEACC = str.maketrans("ăâîșşțţ", "aaissttt"[0:7])
def deacc(s): return (s or "").lower().translate(DEACC)
RULES = [
    ("recenzie_feedback", r"recomand|ce parere|review|feedback|multumesc pentru|miroase (foarte )?bine|super produs"),
    ("anulare", r"anulez|anulare|renunt la comanda|nu mai vreau comanda|cancel|storno"),
    ("modificare_comanda", r"adresa gresita|alta adresa|schimb (nr|numarul|adresa|telefonul)|modific(a|are)? (comanda|adresa|telefon)|wrong address|change.*address"),
    ("retur", r"\bretur|returnez|returna|banii inapoi|refund|vreau banii|\breturn\b|sa le returnez"),
    ("schimb_swap", r"schimb produs|alt model|alta marime|alta culoare|inlocui|exchange"),
    ("problema_produs", r"defect|stricat|nu functioneaza|nu aspira|lipseste|deteriorat|spart|teapa|nu corespunde|am primit (alt|gresit)|damaged|broken"),
    ("livrare_wismo", r"unde (e|este|imi)|cand ajunge|coletul|nu a ajuns|nu am primit|awb|curier|tracking|intarzi|where is my order|track.*order"),
    ("plata_factura", r"factura|am platit de doua|chitanta|invoice"),
    ("presale_intrebare", r"aveti (pe |in )?stoc|cat costa|ce pret|livrati in|cand revine|dimensiuni|mai aveti|disponibil|in stock"),
]
def categorize(blob):
    t = deacc(blob)
    for cat, pat in RULES:
        if re.search(pat, t):
            return cat
    return "altele"

# categoriile pe care vrem voce (excludem altele/spam)
WANT = ["livrare_wismo", "retur", "problema_produs", "anulare", "modificare_comanda", "presale_intrebare", "recenzie_feedback"]


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()

class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "voice", "version": "1"}}})
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
            try: return json.loads(txt)
            except Exception: return {"_text": txt}
        except Exception as e:
            return {"_error": str(e)}


def clean(t):
    t = re.sub(r"\s+", " ", (t or "")).strip()
    return t


def good_reply(t):
    """replică de agent utilă ca exemplu de voce: nu prea scurtă/lungă, nu link-only, nu auto."""
    if not (40 <= len(t) <= 600):
        return False
    low = deacc(t)
    if any(b in low for b in ("unsubscribe", "noreply", "do not reply", "automat", "ticket #", "out of office")):
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per", type=int, default=3, help="exemple/categorie")
    ap.add_argument("--scan", type=int, default=140, help="câte tichete CLOSED să descarce")
    a = ap.parse_args()
    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))

    pack = {c: [] for c in WANT}
    page, seen, got = 1, 0, 0
    while got < a.scan and all(len(pack[c]) >= a.per for c in WANT) is False:
        r = mcp.call("list_conversations", {"status": "closed", "page": page, "per_page": 50,
                                            "sortKey": "updatedAt", "order": "desc"})
        batch = (r.get("tickets") or r.get("conversations") or []) if isinstance(r, dict) else []
        if not batch:
            break
        for t in batch:
            if all(len(pack[c]) >= a.per for c in WANT):
                break
            no = t.get("conversation_no")
            cat = categorize((t.get("subject") or "") + " " + (t.get("first_message") or ""))
            if cat not in WANT or len(pack[cat]) >= a.per:
                continue
            got += 1
            cv = mcp.call("get_conversation", {"conversation_number": str(no), "mode": "audit", "max_messages": 30})
            msgs = (cv.get("messages_page") or {}).get("messages") or cv.get("messages") or []
            for m in msgs:
                if m.get("is_private") or m.get("is_ai"):
                    continue
                if not m.get("author_is_workspace_agent"):
                    continue
                txt = clean(m.get("text"))
                if good_reply(txt) and txt not in pack[cat]:
                    pack[cat].append(txt)
                    break  # o replică bună/tichet ajunge
        if not (isinstance(r, dict) and r.get("has_more")):
            break
        page += 1
        if page > 12:
            break

    pack = {c: v for c, v in pack.items() if v}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, indent=1)
    print("✅ voice_pack: " + ", ".join("%s=%d" % (c, len(v)) for c, v in pack.items()) + "  → " + OUT)


if __name__ == "__main__":
    main()
