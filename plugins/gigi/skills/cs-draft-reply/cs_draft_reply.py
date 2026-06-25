# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_draft_reply.py — generează un DRAFT de răspuns la un tichet Richpanel, cu LLM,
fundamentat pe procedurile CS ARONA + datele REALE ale clientului (comenzi, livrare, AWB).

⚠️ DOAR DRAFT. Nu trimite NICIODATĂ mesaj live la client. Cu `--create-draft` salvează
draftul în Richpanel (agentul îl verifică/editează/trimite manual). Fără flag, doar îl afișează.
(Regula echipei: răspunsul rămâne manual; trimiterea live e dezactivată — vezi memoria.)

  uv run cs_draft_reply.py --conv 265761                 # afișează draftul propus
  uv run cs_draft_reply.py --conv 265761 --create-draft  # + salvează ca DRAFT în Richpanel (NU trimite)

LLM: folosește ANTHROPIC_API_KEY (Claude) dacă există în KB, altfel OPENAI_API_KEY. Model: env DRAFT_MODEL.
"""
import os, sys, json, subprocess, urllib.request, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
CI = os.path.join(HERE, "..", "customer-identity", "customer_identity.py")
MCP_URL = "https://mcp.richpanel.com/mcp"

PLAYBOOK = """Ești agent Customer Service la ARONA (magazine Shopify cu plată ramburs: Esteban, George Talent, Nubra, Gento — parfumuri; Grandia, Carpetto, Covoria — casă/mobilă; Bonhaus RO/CZ/PL/BG; Belasil; Reduceri bune, Ofertele Zilei, Magdeal etc.).
Scrie răspunsul către client respectând EXACT procedurile echipei:

- LIVRARE/WISMO („unde e coletul"): dacă ai AWB + curier în date, dă linkul de tracking CORECT după CURIER (folosește câmpul `curier`, NU presupune DPD): DPD → https://tracking.dpd.ro?shipmentNumber=<AWB> ; Sameday → https://www.sameday.ro/#awb=<AWB> ; Packeta/Zasilkovna → https://tracker.packeta.com/ro/?id=<AWB> ; Econt → https://www.econt.com/en/services/track-shipment/<AWB>. Dacă e întârziat, scuze + estimare. Dacă NU știi curierul sau lipsește AWB, NU inventa link — spune politicos că verifici și revii, sau cere numele+telefonul+nr comenzii.
- RETUR: trimite formularul https://bi.grandia.ro/returns?order=<nr>&email=<email> ; „Suma vă va fi returnată în maximum 14 zile de la ajungerea coletului." Dacă formularul dă eroare, cere IBAN + numele titularului. **Se returnează DOAR valoarea PRODUSELOR — transportul NU se returnează.** Returul îl plătește clientul.
- PRODUS SPART/DETERIORAT (parfum): NU oferi refund. Oferă RETRIMITERE GRATUITĂ: cere care parfum e afectat și anunță că trimiteți o nouă comandă cu cel inițial + un parfum cadou din partea voastră.
- PRODUS DEFECT/LIPSĂ PIESE (mobilă/casă): cere o poză; dacă produsul e pe stoc → retrimitere/schimb; dacă nu → retur + refund (formularul).
- ANULARE: confirmă anularea politicos.
- MODIFICARE adresă/telefon: confirmă că ai actualizat datele.
- PRE-VÂNZARE (preț/stoc/livrare): răspuns clar, încurajează plasarea comenzii.

REGULI:
- Răspunde în LIMBA clientului (română/cehă/poloneză/bulgară/engleză).
- Ton: politicos, cald, cu diacritice. Începe cu „Bună ziua" și încheie cu „Cu drag, Echipa <Magazin>".
- RĂSPUNDE LA ULTIMUL MESAJ + STAREA CURENTĂ a conversației. Citește TOT istoricul. **NU repeta un răspuns anterior și NU cere date pe care clientul LE-A TRIMIS DEJA** (AWB, IBAN, nr comandă, poză, adresă etc.).
- **Dacă clientul A TRIMIS DEJA AWB-ul (returul e EXPEDIAT):** returul e pe drum — **NU-i mai da adresa de retur, NU-i cere să trimită/reexpedieze produsul, NU recere AWB/IBAN**. Confirmă scurt că ai PRIMIT AWB-ul (și IBAN-ul, dacă l-a dat) și spune doar pasul următor: refundul se procesează după ce coletul ajunge în depozit, în maximum 14 zile. Atât.
- SUMA DE REFUND: folosește suma deja PRECIZATĂ de agent în conversație (dacă există). NU folosi „valoarea comenzii (cu transport)" ca sumă de refund — transportul NU se returnează. Dacă nu ești sigur de sumă, scrie general („suma aferentă produselor returnate") fără cifră, NU inventa.
- Folosește DOAR datele primite în context (comenzi, status, AWB). NU inventa numere de AWB, date de livrare sau prețuri.
- Fii concis (3-8 rânduri). Scrie DOAR textul răspunsului, fără explicații meta."""


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def mcp(name, args, token):
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
            return {"_error": "răspuns ne-parsabil: %s" % e}
    post({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "draft", "version": "1"}}})
    r = post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    if isinstance(r, dict) and r.get("_error"):
        return r
    try:
        txt = r["result"]["content"][0]["text"]
    except Exception:
        return {"_error": "răspuns neașteptat de la Richpanel"}
    try:
        return json.loads(txt)
    except Exception:
        return {"_text": txt}


def llm(system, user):
    try:
        ak = secret("ANTHROPIC_API_KEY")
        if ak:
            body = {"model": os.environ.get("DRAFT_MODEL", "claude-3-5-sonnet-20241022"), "max_tokens": 1000,
                    "system": system, "messages": [{"role": "user", "content": user}]}
            req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                         headers={"x-api-key": ak, "anthropic-version": "2023-06-01", "content-type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=90).read())
            return r["content"][0]["text"], "claude"
        ok = secret("OPENAI_API_KEY")
        if ok:
            body = {"model": os.environ.get("DRAFT_MODEL", "gpt-4o-mini"), "temperature": 0.3,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Authorization": "Bearer " + ok, "content-type": "application/json"})
            r = json.loads(urllib.request.urlopen(req, timeout=90).read())
            return r["choices"][0]["message"]["content"], "openai/gpt"
    except Exception as e:
        raise SystemExit("Eroare LLM: %s" % e)
    raise SystemExit("Nicio cheie LLM în KB (ANTHROPIC_API_KEY / OPENAI_API_KEY).")


def transcript(conv):
    msgs = conv.get("messages") or (conv.get("messages_page") or {}).get("messages") or []
    out = []
    for m in msgs[-15:]:
        if m.get("is_private"):
            continue
        t = " ".join((m.get("text") or "").split())
        if t:
            who = "[AI]" if m.get("is_ai") else ""
            out.append("- %s %s" % (who, t[:400]))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conv", required=True, help="nr conversație Richpanel")
    ap.add_argument("--create-draft", action="store_true", help="salvează draftul în Richpanel (NU trimite)")
    a = ap.parse_args()
    tok = secret("RICHPANEL_MCP_TOKEN")

    conv = mcp("get_conversation", {"conversation_number": a.conv, "mode": "audit", "max_messages": 30}, tok)
    tk = conv.get("ticket") or {}
    conv_id = tk.get("id")
    tr = transcript(conv)
    if not tr:
        print("Conversația nu are mesaje citibile."); return

    # date client (reuse customer-identity)
    cust = {}
    try:
        out = subprocess.run(["uv", "run", CI, "--conv", str(a.conv), "--json"], capture_output=True, text=True, timeout=120).stdout
        cust = json.loads(out[out.index("{"):]) if "{" in out else {}
    except Exception:
        cust = {}
    orders = cust.get("orders", [])
    COURIER = {"dpd-ro": "DPD", "dpd": "DPD", "sameday": "Sameday", "packeta": "Packeta", "econt": "Econt"}
    od = "\n".join("  comanda %s (%s): valoare comandă (cu transport)=%.0f lei, status=%s, curier=%s, AWB=%s, produse=%s" % (
        o.get("o"), o.get("brand", o.get("store", "?")), float(o.get("total") or 0), o.get("deliv", "?"),
        COURIER.get((o.get("courier") or "").lower(), o.get("courier") or "?"), o.get("awb", "") or "—", (o.get("skus") or "")[:40])
        for o in orders[:8]) or "  (nicio comandă găsită — posibil pre-vânzare / client nou)"
    store = orders[0]["brand"] if orders else (cust.get("emails", [""])[0].split("@")[-1].split(".")[0].title() if cust.get("emails") else "magazinul nostru")

    user = ("Magazin: %s\nCanal: %s\n\nCONVERSAȚIA (ultimele mesaje):\n%s\n\nDATELE CLIENTULUI:\n%s\n\n"
            "Scrie DOAR textul răspunsului-draft către client, în limba lui, respectând procedurile."
            % (store, tk.get("channel", "?"), tr, od))

    draft, engine = llm(PLAYBOOK, user)
    print("═" * 70)
    print("  DRAFT propus — conv #%s | %s | LLM: %s" % (a.conv, store, engine))
    print("═" * 70)
    print(draft.strip())
    print("═" * 70)

    if a.create_draft:
        if not conv_id:
            print("⚠️ Nu am conversation_id; nu pot salva draftul."); return
        res = mcp("create_draft", {"conversation_id": conv_id, "body": draft.strip()}, tok)
        ok = not (isinstance(res, dict) and res.get("_error"))
        print("\n✅ DRAFT salvat în Richpanel (NU trimis la client). Agentul îl verifică și trimite manual." if ok
              else "\n⚠️ create_draft a întors: %s" % res)
    else:
        print("\n(Doar afișat. Adaugă --create-draft ca să-l salvezi ca DRAFT în Richpanel — tot NU se trimite la client.)")


if __name__ == "__main__":
    main()
