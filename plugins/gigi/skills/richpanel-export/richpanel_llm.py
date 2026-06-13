# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
richpanel_llm.py — reclasifică CATEGORIE + SENTIMENT pe tichetele OPEN cu un LLM (Gemini Flash),
mult mai precis decât regulile (prinde nuanțe: politețe ≠ pozitiv, „nu am primit X" greșit ≠ wismo).

Rulează DUPĂ categorize/sentiment pe reguli (în pipeline) și SUPRASCRIE doar pe OPEN — restul de
223k rămân pe reguli (gratis, pt analitică). Incremental: doar tichete noi/schimbate (col `llm_sig`),
deci re-rulat des = puține apeluri. Citește/scrie SQLite-ul local (rulează pe VPS, în pipeline).

  uv run richpanel_llm.py                 # toate OPEN ne-clasificate încă de LLM
  uv run richpanel_llm.py --recent 1      # doar cele schimbate recent
  uv run richpanel_llm.py --limit 50 --dry # arată ce-ar pune, fără scriere
Necesită GEMINI_API_KEY (sau GOOGLE_AI_API_KEY) în env/KB. Fără cheie → iese curat (skip).
"""
import os, re, json, sqlite3, subprocess, hashlib, time, urllib.request, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
URL = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"
BATCH = 12

# vocabularul de categorii (IDENTIC cu regulile, ca tagurile să fie consistente)
CATS = ["livrare_wismo", "problema_produs", "retur", "schimb_swap", "anulare", "modificare_comanda",
        "refuz_livrare", "plata_factura", "presale_intrebare", "comanda_noua", "recenzie_feedback",
        "comentariu_social", "salut_fara_continut", "spam_automat", "altele"]
NOISE_CAT = {"comentariu_social", "spam_automat", "salut_fara_continut", "formular_contact"}

SYSTEM = (
    "Ești un clasificator pt customer service e-commerce COD (România). Pt fiecare mesaj de client dat, "
    "întoarce categoria și sentimentul. Categorii permise (alege EXACT una):\n"
    "- livrare_wismo: întreabă UNDE e comanda / când ajunge / tracking (coletul NU a sosit încă).\n"
    "- problema_produs: a PRIMIT comanda dar e greșită/defectă/lipsă/alt produs decât a comandat.\n"
    "- retur: vrea să returneze / banii înapoi.\n"
    "- schimb_swap: vrea schimb (altă mărime/model/culoare).\n"
    "- anulare: vrea să anuleze comanda.\n"
    "- modificare_comanda: vrea să schimbe adresa/telefon/date pe comandă.\n"
    "- refuz_livrare: a refuzat / nu acceptă coletul.\n"
    "- plata_factura: factură / plată / card debitat.\n"
    "- presale_intrebare: întrebare ÎNAINTE de cumpărare (stoc/preț/disponibil).\n"
    "- comanda_noua: vrea să plaseze o comandă.\n"
    "- recenzie_feedback: lasă o părere/recenzie.\n"
    "- comentariu_social: comentariu la reclamă FB/IG.\n"
    "- salut_fara_continut: doar salut, fără cerere.\n"
    "- spam_automat: automat/spam/notificare.\n"
    "- altele: nimic din ce e mai sus.\n"
    "Sentiment: negativ / neutru / pozitiv. ATENTIE: politetea (multumesc, va rog frumos) NU inseamna "
    "pozitiv - un reclamant politicos e tot negativ. intensity 0-3 (3 = escaladare ANPC/juridic).\n"
    'Răspunde DOAR cu JSON: {"r":[{"i":<index>,"cat":"<categorie>","sent":"negativ|neutru|pozitiv","int":<0-3>}]}'
)


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


def gemini(key, system, user):
    body = {"system_instruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
    req = urllib.request.Request(URL % (MODEL, key), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read().decode())
    return d["candidates"][0]["content"]["parts"][0]["text"]


def classify(key, batch):
    """batch = [(idx, text)] → {idx: (cat, sent, intensity)}"""
    lines = []
    for i, txt in batch:
        lines.append("[%d] %s" % (i, " ".join((txt or "").split())[:600]))
    user = "Clasifică fiecare mesaj (după index):\n" + "\n".join(lines)
    out = {}
    try:
        txt = gemini(key, SYSTEM, user)
        data = json.loads(txt)
        for r in data.get("r", []):
            cat = r.get("cat") if r.get("cat") in CATS else "altele"
            sent = r.get("sent") if r.get("sent") in ("negativ", "neutru", "pozitiv") else "neutru"
            out[int(r["i"])] = (cat, sent, int(r.get("int", 0) or 0))
    except Exception as e:
        print("  ⚠ batch eșuat:", str(e)[:120])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, help="doar tichete updatate în ultimele N zile")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry", action="store_true", help="arată, nu scrie")
    a = ap.parse_args()
    if not os.path.exists(DB):
        print("Rulează pe VPS (lipsește %s)." % DB); return
    key = secret("GEMINI_API_KEY") or secret("GOOGLE_AI_API_KEY")
    if not key or len(key) < 10:
        print("Fără GEMINI_API_KEY → sar peste pasul LLM."); return

    con = sqlite3.connect(DB, timeout=120)
    con.execute("PRAGMA busy_timeout=120000")
    try:
        con.execute("ALTER TABLE tickets ADD COLUMN llm_sig TEXT")
    except Exception:
        pass
    where = "upper(status)='OPEN'"
    params = []
    if a.recent:
        where = "(upper(status)='OPEN' OR updated_at >= date('now', ?))"
        params = ["-%d days" % a.recent]
    rows = con.execute(
        "SELECT id, COALESCE(first_message,'')||' '||COALESCE(subject,''), category, channel, llm_sig "
        "FROM tickets WHERE " + where, params).fetchall()

    todo = []
    for tid, text, cat, channel, llm_sig in rows:
        if cat in NOISE_CAT or "comment" in (channel or ""):
            continue  # zgomotul rămâne pe reguli (nu irosim LLM)
        sig = hashlib.sha1((text or "").encode()).hexdigest()[:12]
        if sig != (llm_sig or ""):
            todo.append((tid, text, sig))
    if a.limit:
        todo = todo[:a.limit]
    print("→ %d tichete OPEN de reclasificat cu LLM (%s)" % (len(todo), MODEL))
    if not todo:
        con.close(); return

    changed = nproc = 0
    for b in range(0, len(todo), BATCH):
        chunk = todo[b:b + BATCH]
        res = classify(key, [(i, chunk[i][1]) for i in range(len(chunk))])
        for i, (tid, text, sig) in enumerate(chunk):
            if i not in res:
                continue
            cat, sent, inten = res[i]
            nproc += 1
            if a.dry:
                print("  %s → %s | %s(%d)" % (tid[:18], cat, sent, inten))
                continue
            con.execute("UPDATE tickets SET category=?, sentiment=?, sent_intensity=?, llm_sig=? WHERE id=?",
                        (cat, sent, str(inten), sig, tid))
            changed += 1
        if not a.dry:
            con.commit()
        print("  …%d/%d" % (min(b + BATCH, len(todo)), len(todo)), flush=True)
        time.sleep(0.3)
    con.commit(); con.close()
    print("\n════ LLM: %d tichete clasificate, %d scrise ════" % (nproc, changed))


if __name__ == "__main__":
    main()
