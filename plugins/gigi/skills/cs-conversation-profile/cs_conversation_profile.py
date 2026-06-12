# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_conversation_profile.py — PROFIL CLAR al unei conversații CS, cu toate datele LEGATE:
cine e clientul (istoric, VIP/refuznic) + comanda relevantă (produse, status livrare, AWB, curier)
+ ce vrea/problema + SENTIMENT în context + acțiunea recomandată. Un LLM citește transcriptul
COMPLET + identitatea unificată (din gigi:customer-identity) și produce un profil 360°.

Asta e „sentiment în CONTEXT": nu doar negativ/pozitiv pe text gol, ci legat de comanda reală,
statusul livrării și produsele clientului.

  uv run cs_conversation_profile.py --conv 265078
  uv run cs_conversation_profile.py --conv 265078 --json

LLM: ANTHROPIC_API_KEY (Claude) dacă există, altfel OPENAI_API_KEY. Model: env PROFILE_MODEL. Read-only.
"""
import os, json, subprocess, urllib.request, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
CI = os.path.join(HERE, "..", "customer-identity", "customer_identity.py")
MCP_URL = "https://mcp.richpanel.com/mcp"

SYS = """Ești analist senior Customer Service la ARONA. Primești TRANSCRIPTUL unei conversații + DATELE LEGATE ale clientului (comenzi, status livrare, AWB, curier, produse, tichete anterioare). Produci un PROFIL 360° clar și acționabil, fundamentat DOAR pe datele primite (nu inventa nimic; dacă lipsește, scrie „necunoscut").

Format (exact aceste secțiuni, concis):
👤 CLIENT: nume, contact, flag (VIP dacă LTV mare / REFUZNIC dacă a refuzat / client nou), nr comenzi + LTV.
📦 COMANDA RELEVANTĂ: nr comandă, produse, status livrare (Livrată/Netrimisă/Refuzată/În curs), AWB + curier dacă există.
❓ CE VREA / PROBLEMA: categoria (WISMO/retur/anulare/problemă produs/modificare/presale) + rezumat în 1-2 fraze.
😶 SENTIMENT: negativ/neutru/pozitiv + intensitate 0-3 (3=furie/ANPC) + DE CE (legat de context, nu doar cuvinte).
✅ ACȚIUNE RECOMANDATĂ: ce trebuie făcut concret (procedura ARONA: WISMO→link tracking; retur→formular+14 zile; produs spart parfum→retrimitere+cadou; etc.), cu prioritate.

Scrie clar, în română, fără preambul."""


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def mcp_conv(conv_no, token):
    h = {"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

    def post(p):
        req = urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode()
        except Exception as e:
            return {"_error": str(e)}
        ln = [l for l in body.splitlines() if l.startswith("data:")]
        try:
            return json.loads(ln[-1][5:]) if ln else json.loads(body)
        except Exception as e:
            return {"_error": str(e)}
    post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "profile", "version": "1"}}})
    r = post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "get_conversation", "arguments": {"conversation_number": conv_no, "mode": "audit", "max_messages": 40}}})
    if isinstance(r, dict) and r.get("_error"):
        return None
    try:
        return json.loads(r["result"]["content"][0]["text"])
    except Exception:
        return None


def llm(system, user, want_json=False):
    try:
        ak = secret("ANTHROPIC_API_KEY")
        if ak:
            body = {"model": os.environ.get("PROFILE_MODEL", "claude-3-5-sonnet-20241022"), "max_tokens": 1200,
                    "system": system, "messages": [{"role": "user", "content": user}]}
            req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                         headers={"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=90).read())["content"][0]["text"]
        ok = secret("OPENAI_API_KEY")
        if ok:
            body = {"model": os.environ.get("PROFILE_MODEL", "gpt-4o-mini"), "temperature": 0.2,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + ok, "content-type": "application/json"})
            return json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"]
    except Exception as e:
        raise SystemExit("Eroare LLM: %s" % e)
    raise SystemExit("Nicio cheie LLM (ANTHROPIC_API_KEY / OPENAI_API_KEY).")


def transcript(conv):
    msgs = conv.get("messages") or (conv.get("messages_page") or {}).get("messages") or []
    out = []
    for m in msgs[-20:]:
        if m.get("is_private"):
            continue
        t = " ".join((m.get("text") or "").split())
        if t:
            out.append("- %s%s" % ("[AI] " if m.get("is_ai") else "", t[:350]))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conv", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    tok = secret("RICHPANEL_MCP_TOKEN")
    conv = mcp_conv(a.conv, tok)
    if not conv:
        print("Nu am putut citi conversația #%s (eroare Richpanel)." % a.conv); return
    tk = conv.get("ticket") or {}
    tr = transcript(conv)

    # date LEGATE — identitate unificată + comenzi + status + produse
    cust = {}
    try:
        out = subprocess.run(["uv", "run", CI, "--conv", str(a.conv), "--json"], capture_output=True, text=True, timeout=130).stdout
        cust = json.loads(out[out.index("{"):]) if "{" in out else {}
    except Exception:
        cust = {}
    orders = cust.get("orders", [])
    od = "\n".join("  • %s (%s) | status=%s | curier=%s AWB=%s | produse: %s" % (
        o.get("o"), o.get("brand", o.get("store", "?")), o.get("deliv", "?"), o.get("courier", "?") or "?",
        o.get("awb", "") or "—", (o.get("skus") or "")[:50]) for o in orders[:8]) or "  (nicio comandă găsită — posibil prospect / pre-vânzare)"
    convos = cust.get("convos", [])
    flags = []
    livr = sum(1 for o in orders if o.get("deliv") == "Livrata")
    refz = sum(1 for o in orders if o.get("deliv") == "Refuzata")
    if refz >= 2:
        flags.append("REFUZNIC SERIAL (%d refuzuri)" % refz)
    ctx = ("Emailuri: %s | Telefoane: %s\nComenzi (%d): %d livrate, %d refuzate\n%s\nTichete anterioare: %d | Flaguri: %s\nCanal: %s" % (
        ", ".join(cust.get("emails", []) or ["—"]), ", ".join(cust.get("phones", []) or ["—"]),
        len(orders), livr, refz, od, len(convos), ", ".join(flags) or "—", tk.get("channel", "?")))

    user = "TRANSCRIPT CONVERSAȚIE (#%s):\n%s\n\nDATE LEGATE ALE CLIENTULUI:\n%s\n\nProdu profilul 360°." % (a.conv, tr or "(fără mesaje)", ctx)
    if a.json:
        user += "\n\nRăspunde DOAR JSON: {client, comanda, problema, categorie, sentiment, intensitate, actiune}."
    out = llm(SYS, user)
    print("═" * 74)
    print("  PROFIL CONVERSAȚIE #%s — %s" % (a.conv, tk.get("channel", "?")))
    print("═" * 74)
    print(out.strip())


if __name__ == "__main__":
    main()
