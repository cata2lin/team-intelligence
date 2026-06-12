# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_procedures.py — ÎNVAȚĂ procedurile CS ARONA direct din tichetele reale (descriptiv, nu presupus).
Pentru o categorie, eșantionează tichete REZOLVATE BINE, citește replicile AGENȚILOR REALI
(exclude bot/operator/auto-fill) și extrage cu LLM: pașii procedurii de-facto, replicile-șablon,
și CÂND/DE CE refuză/descurajează echipa (ex. retur — ARONA nu-l încurajează, igiena desigilată se refuză).

Folosește-l ca să documentezi procedurile + ca STANDARD față de care identifici greșeli (nu impune reguli din afară).

  uv run cs_procedures.py --category retur
  uv run cs_procedures.py --category all --out playbook.md
  uv run cs_procedures.py --category problema_produs --limit 20

LLM: ANTHROPIC_API_KEY (Claude) dacă există, altfel OPENAI_API_KEY. Model: env PROC_MODEL. Read-only.
"""
import os, json, sqlite3, subprocess, urllib.request, argparse, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MCP_URL = "https://mcp.richpanel.com/mcp"
AGENTS = {"20458195-be56-4eb0-a42d-e439ec9bc864": "Cristina", "ecd1325c-8da5-409f-ad90-3405c062ff44": "Diana",
          "76459f48-c911-4c69-871e-537e0ac645ac": "Irina", "245b9936-837a-4c9b-8fad-fe2d179a4ddf": "Martina",
          "0964e420-84e7-457f-b0b5-57253b9a0dc8": "Alexandra", "6acebee5-9015-4e63-9646-ebfe32017be9": "Mariana"}
CATS = ["livrare_wismo", "retur", "anulare", "problema_produs", "modificare_comanda", "schimb_swap",
        "presale_intrebare", "plata_factura", "refuz_livrare"]
SKIP = ("formularul de contact", "Veți fi anunțați", "Oferă echipei", "Simțiți-vă liber")


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def mcp(token):
    h = {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def post(p):
        req = urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        ln = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(ln[-1][5:]) if ln else json.loads(body)
    post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "proc", "version": "1"}}})
    return post


def conv_text(post, no):
    try:
        r = post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_conversation", "arguments": {"conversation_number": no, "mode": "audit", "max_messages": 16}}})
        d = json.loads(r["result"]["content"][0]["text"])
    except Exception:
        return None
    msgs = d.get("messages") or (d.get("messages_page") or {}).get("messages") or []
    lines = []
    for m in msgs:
        if m.get("is_private"):
            continue
        t = " ".join((m.get("text") or "").split())
        if not t or any(s in t for s in SKIP) or t.startswith(("firstName", "phone:")):
            continue
        a = m.get("author")
        if m.get("is_ai") or (a and a.get("name") == "operator"):
            continue  # bot/automat
        if a is None:
            lines.append("CLIENT: " + t[:280])
        elif a.get("id") in AGENTS:
            lines.append("AGENT(" + AGENTS[a["id"]] + "): " + t[:280])
    return "\n".join(lines) if any(l.startswith("AGENT") for l in lines) else None


def llm(system, user):
    ak = secret("ANTHROPIC_API_KEY")
    if ak:
        body = {"model": os.environ.get("PROC_MODEL", "claude-3-5-sonnet-20241022"), "max_tokens": 1600,
                "system": system, "messages": [{"role": "user", "content": user}]}
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=120).read())["content"][0]["text"]
    ok = secret("OPENAI_API_KEY")
    if ok:
        body = {"model": os.environ.get("PROC_MODEL", "gpt-4o-mini"), "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": "Bearer " + ok, "content-type": "application/json"})
        return json.loads(urllib.request.urlopen(req, timeout=120).read())["choices"][0]["message"]["content"]
    raise SystemExit("Nicio cheie LLM (ANTHROPIC_API_KEY / OPENAI_API_KEY).")


SYS = """Ești analist CS la ARONA (magazine COD: parfumuri Esteban/GT/Nubra/Gento + casă Grandia/Carpetto + Bonhaus RO/CZ/PL/BG). Primești transcripturi REALE dintr-o categorie de tichete (doar CLIENT + AGENȚI REALI; bot-ul e exclus). Documentează DESCRIPTIV cum tratează ECHIPA de fapt — NU cum crezi tu că ar trebui.
IMPORTANT: ARONA e COD și NU încurajează returul (retururile costă). La igienă personală / parfumuri DESIGILATE returul se REFUZĂ — dacă agenții refuză, asta E procedura, noteaz-o ca atare. Nu judeca, doar documentează ce fac agenții.
Pentru categoria dată, scrie în română, concis:
### Procedura de-facto (pași)
### Replici-șablon reale (citate/parafrazate, cu link-uri dacă apar)
### Politica de refuz / descurajare (CÂND și CUM refuză sau oferă alternativă în loc de refund)
### Edge case-uri / excepții"""


def learn(post, cat, con, limit):
    rows = con.execute("SELECT conversation_no FROM tickets WHERE category=? AND status='CLOSED' AND assignee_id IS NOT NULL "
                       "AND comment_count BETWEEN 2 AND 7 AND channel='email' ORDER BY RANDOM() LIMIT ?", (cat, limit * 2)).fetchall()
    corpus, used = [], 0
    for (no,) in rows:
        if used >= limit:
            break
        t = conv_text(post, no)
        if t:
            corpus.append("--- #%s ---\n%s" % (no, t)); used += 1
        time.sleep(0.35)
    if not corpus:
        return None, 0
    out = llm(SYS, "CATEGORIA: %s\n\nTRANSCRIPTURI REALE:\n%s\n\nDocumentează procedura de-facto." % (cat, "\n\n".join(corpus)[:24000]))
    return out, used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category", required=True, help="o categorie sau 'all'")
    ap.add_argument("--limit", type=int, default=15); ap.add_argument("--out")
    a = ap.parse_args()
    if not os.path.exists(DB):
        print("Nu găsesc DB-ul:", DB); return
    post = mcp(secret("RICHPANEL_MCP_TOKEN"))
    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=30)
    cats = CATS if a.category == "all" else [a.category]
    parts = ["# Playbook CS ARONA — proceduri învățate din tichete reale\n"]
    for cat in cats:
        print("→ învăț '%s'…" % cat, flush=True)
        proc, n = learn(post, cat, con, a.limit)
        if proc:
            block = "\n## %s  _(din %d tichete)_\n%s\n" % (cat.upper(), n, proc.strip())
            parts.append(block)
            if a.category != "all":
                print(block)
    con.close()
    full = "\n".join(parts)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(full)
        print("\n✅ Playbook scris în", a.out)
    elif a.category == "all":
        print(full)


if __name__ == "__main__":
    main()
