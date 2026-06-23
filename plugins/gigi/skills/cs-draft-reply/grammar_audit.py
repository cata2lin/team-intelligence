# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
grammar_audit.py — AUDIT de limbă ROMÂNĂ + GRAMATICĂ pe răspunsurile generate de cs_auto_draft
(toate tipurile: comentariu public, mesaj privat/DM, email, mesaj de escaladare/așteptare).

Citește drafturile din JSON-ul produs de `cs_auto_draft.py --json` (câmpurile `draft` + `private_msg`),
le trece printr-un corector RO strict (LLM) și raportează DOAR greșelile reale, cu corectura.

  uv run cs_auto_draft.py --limit 20 --json 2>/dev/null | grep @@JSON@@ | sed 's/^@@JSON@@//' > /tmp/cs_drafts.json
  uv run grammar_audit.py --file /tmp/cs_drafts.json

Verifică: acord, prepoziții (ex. „te-am scris” GREȘIT → „ți-am scris/v-am scris”), cazuri (dativ/acuzativ),
„decât”/„doar”, diacritice lipsă, „care” vs „pe care”, „v-a/va/vă”, virgule, registru (comentariu public = PLURAL politicos),
naturalețe (să nu sune robotic/AI). NU semnalează lucruri corecte.
"""
import os, re, json, argparse, subprocess, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")

def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()

AUDIT_SYS = """Ești CORECTOR profesionist MULTILINGV (română, cehă, poloneză, bulgară, engleză) pentru mesaje de Customer Service.
Primești o listă de texte (răspunsuri către clienți). Fiecare are: `lang` (limba așteptată) și `mesaj_client` (ce a scris EFECTIV clientul).
Pentru FIECARE text:
1. LIMBA: răspunsul trebuie să fie în ACEEAȘI limbă în care a scris clientul (`mesaj_client`). `lang` e o indicație; dacă `mesaj_client` e clar în altă limbă, primează limba din `mesaj_client`. Dacă răspunsul e în altă limbă decât a scris clientul (ex. răspuns în ROMÂNĂ la un mesaj în cehă/poloneză/bulgară/engleză) → eroare GRAVĂ, tip "limba_gresita".
2. GRAMATICA ÎN LIMBA TEXTULUI (acord, cazuri, prepoziții, diacritice/semne specifice, punctuație). Pentru ROMÂNĂ specific: „ți-am scris/v-am scris" NU „te-am scris"; comentariu public = POLITICOS, la PLURAL (dumneavoastră/vă), nu „tu".
Semnalează DOAR greșeli REALE, cu corectura — nu rescrie stilul corect, nu inventa.
Întoarce STRICT JSON: {"results":[{"id":<int>,"verdict":"OK"|"ISSUES","issues":[{"gresit":"<fragment>","corect":"<corectura>","tip":"limba_gresita|gramatica|acord|caz|prepozitie|registru|diacritice|punctuatie|naturalete"}]}]}.
Dacă textul e corect → verdict "OK", issues [].
"""

def llm_json(system, user):
    ok = secret("OPENAI_API_KEY")
    body = {"model": os.environ.get("AUDIT_MODEL", "gpt-4o"), "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    req = urllib.request.Request("https://api.openai.com/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + ok, "content-type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read())["choices"][0]["message"]["content"]


def kind_of(row):
    ch = (row.get("channel") or "").lower()
    if ch in ("facebook_feed_comment", "instagram_comment"):
        return "comentariu public"
    if ch == "email":
        return "email"
    return ch or "mesaj"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="/tmp/cs_drafts.json")
    a = ap.parse_args()
    rows = json.load(open(a.file, encoding="utf-8"))

    # construiește lista de texte (fiecare draft public + fiecare DM = text separat)
    texts = []
    for r in rows:
        k = kind_of(r)
        lg = r.get("language") or "ro"
        cm = r.get("cust_msg") or ""
        if (r.get("draft") or "").strip():
            texts.append({"no": r["no"], "lang": lg, "cust_msg": cm, "tip": k + (" (escaladare)" if r.get("escalate") else ""), "text": r["draft"].strip()})
        if (r.get("private_msg") or "").strip():
            texts.append({"no": r["no"], "lang": lg, "cust_msg": cm, "tip": "mesaj privat (DM)", "text": r["private_msg"].strip()})

    payload = "TEXTE DE VERIFICAT:\n" + "\n".join(
        "id=%d | lang=%s | tip=%s | tichet #%s\n  mesaj_client: %s\n  RASPUNS: %s\n" % (
            i, t["lang"], t["tip"], t["no"], (t["cust_msg"] or "(necunoscut)")[:200], t["text"]) for i, t in enumerate(texts))
    raw = llm_json(AUDIT_SYS, payload)
    try:
        res = json.loads(raw).get("results", [])
    except Exception:
        print("Răspuns ne-parsabil:", raw[:400]); return
    by_id = {r.get("id"): r for r in res}

    issues_total = 0
    clean = 0
    pat = {}
    print("═" * 90)
    print("  AUDIT ROMÂNĂ + GRAMATICĂ — %d texte (din %d tichete)" % (len(texts), len(rows)))
    print("═" * 90)
    for i, t in enumerate(texts):
        r = by_id.get(i) or {}
        iss = r.get("issues") or []
        if not iss:
            clean += 1
            continue
        issues_total += len(iss)
        print("\n#%s · %s" % (t["no"], t["tip"]))
        print("  text: %s" % " ".join(t["text"].split())[:120])
        for it in iss:
            tip = it.get("tip", "?"); pat[tip] = pat.get(tip, 0) + 1
            print("   ⚠️ [%s] „%s” → „%s”" % (tip, it.get("gresit", ""), it.get("corect", "")))

    print("\n" + "─" * 90)
    print("  REZUMAT: %d texte curate / %d total · %d greșeli" % (clean, len(texts), issues_total))
    if pat:
        print("  Tipare recurente: " + " · ".join("%s=%d" % (k, v) for k, v in sorted(pat.items(), key=lambda x: -x[1])))
    else:
        print("  ✅ Niciun text cu greșeli.")


if __name__ == "__main__":
    main()
