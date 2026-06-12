# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_comment_intelligence.py — INTELIGENȚĂ pe comentariile la reclame FB/IG.

Cele ~12.000 comentarii la reclame nu sunt zgomot: ascund LEAD-uri de cumpărare
(„cum comand?", „pret?", „vreau și eu") = vânzări pierdute, RECLAMAȚII PUBLICE pe
reclame live (scad CTR / cresc CPA + reputație) și TESTIMONIALE (social proof de refolosit).
Skill-ul le clasifică, le grupează PE MAGAZIN (din maparea pagină→magazin) și scoate
liste acționabile. Citește din DB-ul exportat de `gigi:richpanel-export` (după `richpanel_link.py`).

  uv run cs_comment_intelligence.py summary                       # tablou per magazin
  uv run cs_comment_intelligence.py leads --store Esteban         # intenții de cumpărare (lead-uri)
  uv run cs_comment_intelligence.py leads --open                  # doar cele încă deschise
  uv run cs_comment_intelligence.py complaints --store Grandia    # reclamații publice de moderat
  uv run cs_comment_intelligence.py praise --store Nubra          # testimoniale de refolosit
  uv run cs_comment_intelligence.py leads --store GT --json

Read-only. NU scrie/răspunde nimic (răspunsul rămâne manual / draft).
"""
import os, re, sqlite3, argparse, json

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")

# ── reguli de clasificare (fără diacritice, tunate pe exemple reale) ──
COMPLAINT = re.compile(r"nu\s*recomand|teap[ăa]|tzeap|escroc|prostea|proast|prosti|prost\b|ruginit|"
                       r"jale|nasol|naspa|groaznic|incult|minciun|inseal|in[sș]el|slab(a|ă)?\b|nu\s*(mai\s*)?(funct|merg)|"
                       r"ru[sș]ine|dezamag|oroare|ho[tț]i|bataie de joc|bătaie de joc|nu am primit|nu mi-a|"
                       r"reclama[tț]i|de proast|penibil|aiurea|furt|nu se vede|nu arata ca|altceva decat|"
                       r"oribil|oribil|nu\s*(mai\s*)?cump[ăa]r|prea\s*scump|ave[tț]i\s*grij|nu\s*e(ste)?\s*ceea|"
                       r"nu[- ]?i\s*recomand|dezgust|catastrof|tzap[aă]|de\s*c[aă]cat", re.I)
BUY = re.compile(r"cum\s*(pot|fac|se)?\s*comand|comand[ăa]?\s*\?|cum\s*cump|unde\s*(pot|comand|gasesc|cump)|"
                 r"pre[tț]\b|pretu|cat\s*cost|cât\s*cost|vreau\s*(si|și|sa|să)?\s*(eu|io|comand)|a[sș]\s*(dori|vrea)|"
                 r"doresc|m[ăa]\s*interes|ave[tț]i\b|se\s*mai\s*(poate|gase|găse)|mai\s*ave[tț]i|link\b|disponibil|"
                 r"in\s*stoc|pe\s*stoc|cum\s*platesc|livra[tț]i", re.I)
PRAISE = re.compile(r"recomand\b|recomand cu|super\b|excelent|mul[tț]umesc|f(oarte)?\s*bun|perfect|"
                    r"calitate|frumos|frumoas|mul[tț]umit|de\s*top\b|minunat|genial|ador\b|imi place|îmi place|"
                    r"foarte mul[tț]umit|nota 10|deosebit", re.I)
NOISE = re.compile(r"avertisment de la facebook|известие|încălcare gravă|incalcare grava|shared file", re.I)


def classify(text):
    t = text or ""
    if NOISE.search(t):
        return "zgomot"
    if COMPLAINT.search(t):
        return "reclamatie"
    if BUY.search(t):
        return "lead"
    if PRAISE.search(t):
        return "testimonial"
    if "?" in t:
        return "intrebare"
    return "neutru"


LABEL = {"lead": "🟢 lead", "reclamatie": "🔴 reclamație", "testimonial": "⭐ testimonial",
         "intrebare": "❓ întrebare", "neutru": "· neutru", "zgomot": "zgomot"}


def load():
    con = sqlite3.connect(DB)
    has_rs = any(r[1] == "resolved_store" for r in con.execute("PRAGMA table_info(tickets)"))
    sc = "resolved_store" if has_rs else "store"
    rows = con.execute(f"SELECT id,conversation_no,{sc},status,created_at,COALESCE(first_message,'')||' '||COALESCE(subject,'') "
                       "FROM tickets WHERE channel LIKE '%comment%'").fetchall()
    con.close()
    out = []
    for tid, no, store, status, created, text in rows:
        out.append({"id": tid, "no": no, "store": store or "necunoscut", "status": status,
                    "date": str(created or "")[:10] if not str(created or "").isdigit() else created,
                    "text": " ".join((text or "").split()), "type": classify(text)})
    return out


def summary(items):
    stores = {}
    for it in items:
        s = stores.setdefault(it["store"], {"lead": 0, "reclamatie": 0, "testimonial": 0, "intrebare": 0, "neutru": 0, "zgomot": 0, "tot": 0})
        s[it["type"]] += 1; s["tot"] += 1
    print("═" * 78)
    print("  COMMENT INTELLIGENCE — %d comentarii la reclame FB/IG" % len(items))
    print("═" * 78)
    print("  %-16s %6s %7s %7s %7s %7s" % ("MAGAZIN", "total", "🟢lead", "🔴recl", "⭐testi", "❓intr"))
    for st, s in sorted(stores.items(), key=lambda x: -x[1]["tot"]):
        if s["tot"] < 5:
            continue
        print("  %-16s %6d %7d %7d %7d %7d" % (st[:16], s["tot"], s["lead"], s["reclamatie"], s["testimonial"], s["intrebare"]))
    tl = sum(s["lead"] for s in stores.values())
    tr = sum(s["reclamatie"] for s in stores.values())
    tt = sum(s["testimonial"] for s in stores.values())
    op_lead = sum(1 for it in items if it["type"] == "lead" and (it["status"] or "").upper() == "OPEN")
    print("─" * 78)
    print("  💰 %d LEAD-uri (intenție de cumpărare) — vânzări de recuperat (%d încă DESCHISE)" % (tl, op_lead))
    print("  🔴 %d RECLAMAȚII publice pe reclame live — moderare/reputație + CPA" % tr)
    print("  ⭐ %d TESTIMONIALE — social proof de refolosit" % tt)
    print("\n  → detalii: leads / complaints / praise  [--store X] [--open]")


def listing(items, typ, store, only_open, as_json):
    sel = [it for it in items if it["type"] == typ
           and (not store or it["store"].lower() == store.lower())
           and (not only_open or (it["status"] or "").upper() == "OPEN")]
    sel.sort(key=lambda x: str(x["date"]), reverse=True)
    if as_json:
        print(json.dumps(sel, ensure_ascii=False, indent=2, default=str)); return
    head = {"lead": "🟢 LEAD-URI (intenție cumpărare)", "reclamatie": "🔴 RECLAMAȚII publice",
            "testimonial": "⭐ TESTIMONIALE"}[typ]
    print("═" * 78)
    print("  %s — %d%s%s" % (head, len(sel), (" | magazin: " + store) if store else "", " | doar deschise" if only_open else ""))
    print("═" * 78)
    for it in sel[:80]:
        st = (it["status"] or "")[:6]
        print("  [%-12s %-6s %s] %s" % (it["store"][:12], st, str(it["date"])[:10], it["text"][:120]))
    if len(sel) > 80:
        print("  … încă %d (folosește --json pt toate)" % (len(sel) - 80))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["summary", "leads", "complaints", "praise"])
    ap.add_argument("--store"); ap.add_argument("--open", action="store_true"); ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(DB):
        print("Nu găsesc DB-ul:", DB, "\nRulează întâi gigi:richpanel-export (pull + richpanel_link.py)."); return
    items = load()
    if a.mode == "summary":
        summary(items)
    else:
        typ = {"leads": "lead", "complaints": "reclamatie", "praise": "testimonial"}[a.mode]
        listing(items, typ, a.store, a.open, a.json)


if __name__ == "__main__":
    main()
