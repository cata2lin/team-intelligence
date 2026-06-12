# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_quality_audit.py — AUDIT DE CALITATE pe tichetele CS (unde s-a răspuns prost).

Versiunea sistematică a raportului „răspuns prost" din documentația CS: în loc de 3 exemple,
scanează TOT istoricul Richpanel exportat și marchează automat tichetele problemă, pe semnale reale:
  • LENT  — primă reacție mare (first_response_time − created_at)
  • FRICȚIUNE — multe round-trip-uri (comment_count mare) = nu s-a rezolvat din prima
  • FRUSTRARE — clientul semnalează („al treilea email", „nu răspunde nimeni", „v-am tot scris")
  • ESCALADARE — amenințare ANPC / protecția consumatorului / dau în judecată
  • VECHI DESCHIS — status OPEN de prea mult timp
Grupează pe MAGAZIN și pe AGENT. Citește richpanel_tickets.db (din gigi:richpanel-export).

  uv run cs_quality_audit.py summary                  # tablou: per agent + per magazin + total probleme
  uv run cs_quality_audit.py frustrated               # tichete cu frustrare/escaladare (cele mai grave)
  uv run cs_quality_audit.py slow --hours 24          # primă reacție peste N ore
  uv run cs_quality_audit.py stale --days 7           # OPEN mai vechi de N zile
  uv run cs_quality_audit.py friction --min 6         # tichete cu peste N mesaje
  uv run cs_quality_audit.py frustrated --agent "Cristina" --json

Read-only. NU scrie/răspunde nimic.
"""
import os, re, json, sqlite3, argparse, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")

AGENTS = {
    "0964e420-84e7-457f-b0b5-57253b9a0dc8": "Alexandra (AnnaR)",
    "245b9936-837a-4c9b-8fad-fe2d179a4ddf": "Martina (CZ)",
    "76459f48-c911-4c69-871e-537e0ac645ac": "Irina/Daniela",
    "ecd1325c-8da5-409f-ad90-3405c062ff44": "Diana (Nocturna)",
    "20458195-be56-4eb0-a42d-e439ec9bc864": "Cristina (Raluca)",
    "6acebee5-9015-4e63-9646-ebfe32017be9": "Mariana (admin)",
}
# categorii de exclus (nu-s CS real)
NOISE_CAT = {"comentariu_social", "spam_automat", "recenzie_feedback"}

FRUST = re.compile(r"al\s*(doilea|treilea|patrulea|cincilea|\d+)[- ]?lea\s*(e?mail|mesaj|oar)|"
                   r"a\s*(doua|treia|patra|\d+)[- ]?a?\s*oar[ăa]|de\s*(atatea|cate|cat|nenum)\s*ori|"
                   r"nu\s*(imi\s*|mi\s*)?r[ăa]spunde\s*nimeni|nici\s*un\s*r[ăa]spuns|niciun\s*r[ăa]spuns|"
                   r"nu\s*am\s*primit\s*(niciun\s*)?r[ăa]spuns|nu\s*primesc\s*(niciun\s*)?r[ăa]spuns|"
                   r"v-?am\s*(tot\s*)?scris|nu\s*mi-?a\s*r[ăa]spuns|inca\s*o\s*data|încă\s*o\s*dat[ăa]|"
                   r"tot\s*nu|a\s*nu\s*[sș]tiu\s*cata", re.I)
ESCAL = re.compile(r"anpc|protec[tț]ia\s*consumator|dau\s*[iî]n\s*judecat|instan[tț]|avocat|"
                   r"poli[tț]i[ae]|reclama[tț]ie\s*oficial|terminal\s*de\s*plat|denun[tț]", re.I)


def parse_ts(v):
    if v is None or v == "":
        return None
    s = str(v)
    try:
        if s.isdigit():
            n = int(s)
            return datetime.datetime.fromtimestamp(n / (1000 if n > 1e12 else 1), datetime.timezone.utc)
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def load():
    con = sqlite3.connect(DB)
    cols = [r[1] for r in con.execute("PRAGMA table_info(tickets)")]
    sc = "resolved_store" if "resolved_store" in cols else "store"
    rows = con.execute(f"SELECT id,conversation_no,{sc},assignee_id,status,category,channel,comment_count,"
                       "created_at,COALESCE(first_message,'')||' '||COALESCE(subject,''),raw FROM tickets").fetchall()
    con.close()
    now = datetime.datetime.now(datetime.timezone.utc)
    out = []
    for tid, no, store, aid, status, cat, channel, cc, created, text, raw in rows:
        if cat in NOISE_CAT or (channel or "").find("comment") >= 0:
            continue
        try:
            d = json.loads(raw) if raw else {}
        except Exception:
            d = {}
        c_ts = parse_ts(created) or parse_ts(d.get("created_at"))
        fr_ts = parse_ts(d.get("first_response_time"))
        delay_h = (fr_ts - c_ts).total_seconds() / 3600 if (fr_ts and c_ts) else None
        age_d = (now - c_ts).total_seconds() / 86400 if c_ts else None
        txt = " ".join((text or "").split())
        out.append({"id": tid, "no": no, "store": store or "?", "agent": AGENTS.get(aid, "(neasignat)"),
                    "status": status, "cc": cc or 0, "delay_h": delay_h, "age_d": age_d,
                    "date": str(c_ts)[:10] if c_ts else "", "text": txt,
                    "frust": bool(FRUST.search(txt)), "escal": bool(ESCAL.search(txt))})
    return out


def flags(it, slow_h=24, stale_d=7, fric=6):
    f = []
    if it["escal"]:
        f.append("ESCALADARE")
    if it["frust"]:
        f.append("FRUSTRARE")
    if it["delay_h"] is not None and it["delay_h"] > slow_h:
        f.append("LENT")
    if (it["status"] or "").upper() == "OPEN" and it["age_d"] and it["age_d"] > stale_d:
        f.append("VECHI-DESCHIS")
    if it["cc"] >= fric:
        f.append("FRICTIUNE")
    return f


def summary(items):
    real = len(items)
    flagged = [it for it in items if flags(it)]
    print("═" * 74)
    print("  CS QUALITY AUDIT — %d tichete CS reale | %d cu probleme (%d%%)" % (real, len(flagged), 100 * len(flagged) // max(real, 1)))
    print("═" * 74)
    tot = {}
    for it in items:
        for fl in flags(it):
            tot[fl] = tot.get(fl, 0) + 1
    print("  Probleme: " + " | ".join("%s=%d" % (k, v) for k, v in sorted(tot.items(), key=lambda x: -x[1])))
    # per agent
    print("\n  PER AGENT: %-20s vol  lent%%  med_h  frustr  escal" % "agent")
    by = {}
    for it in items:
        a = by.setdefault(it["agent"], {"n": 0, "slow": 0, "delays": [], "fr": 0, "es": 0})
        a["n"] += 1
        if it["delay_h"] is not None:
            a["delays"].append(it["delay_h"])
            if it["delay_h"] > 24:
                a["slow"] += 1
        a["fr"] += it["frust"]; a["es"] += it["escal"]
    for ag, a in sorted(by.items(), key=lambda x: -x[1]["n"]):
        med = sorted(a["delays"])[len(a["delays"]) // 2] if a["delays"] else 0
        slowp = 100 * a["slow"] // len(a["delays"]) if a["delays"] else 0
        print("    %-22s %4d  %4d%%  %5.1f  %5d  %5d" % (ag[:22], a["n"], slowp, med, a["fr"], a["es"]))
    # per magazin (top probleme)
    print("\n  PER MAGAZIN (cele mai multe probleme):")
    bs = {}
    for it in flagged:
        bs[it["store"]] = bs.get(it["store"], 0) + 1
    for st, c in sorted(bs.items(), key=lambda x: -x[1])[:8]:
        print("    %-20s %d" % (st[:20], c))
    print("\n  → detalii: frustrated / slow / stale / friction  [--agent X] [--store Y]")


def listing(items, mode, args):
    if mode == "frustrated":
        sel = [it for it in items if it["frust"] or it["escal"]]
        title = "🚩 FRUSTRARE / ESCALADARE (cele mai grave)"
    elif mode == "slow":
        sel = [it for it in items if it["delay_h"] is not None and it["delay_h"] > args.hours]
        sel.sort(key=lambda x: -x["delay_h"]); title = "🐢 PRIMĂ REACȚIE > %dh" % args.hours
    elif mode == "stale":
        sel = [it for it in items if (it["status"] or "").upper() == "OPEN" and it["age_d"] and it["age_d"] > args.days]
        sel.sort(key=lambda x: -(x["age_d"] or 0)); title = "📭 OPEN > %d zile" % args.days
    else:  # friction
        sel = [it for it in items if it["cc"] >= args.min]
        sel.sort(key=lambda x: -x["cc"]); title = "🔁 FRICȚIUNE (≥%d mesaje)" % args.min
    if args.agent:
        sel = [it for it in sel if args.agent.lower() in it["agent"].lower()]
    if args.store:
        sel = [it for it in sel if it["store"].lower() == args.store.lower()]
    if args.json:
        print(json.dumps(sel, ensure_ascii=False, indent=2, default=str)); return
    print("═" * 74); print("  %s — %d tichete" % (title, len(sel))); print("═" * 74)
    for it in sel[:60]:
        extra = ""
        if it["delay_h"] is not None and mode == "slow":
            extra = " %.0fh" % it["delay_h"]
        elif mode == "stale":
            extra = " %.0fz" % (it["age_d"] or 0)
        elif mode == "friction":
            extra = " %dmsg" % it["cc"]
        tags = "+".join(flags(it))
        print("  #%-7s %-12s %-18s %-6s%s | %s" % (it["no"] or "?", it["store"][:12], it["agent"][:18],
              (it["status"] or "")[:6], extra, it["text"][:90]))
        if tags:
            print("            ⚑ %s" % tags)
    if len(sel) > 60:
        print("  … încă %d (--json pt toate)" % (len(sel) - 60))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["summary", "frustrated", "slow", "stale", "friction"])
    ap.add_argument("--hours", type=int, default=24); ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min", type=int, default=6); ap.add_argument("--agent"); ap.add_argument("--store")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(DB):
        print("Nu găsesc DB-ul:", DB, "— rulează întâi gigi:richpanel-export."); return
    items = load()
    if a.mode == "summary":
        summary(items)
    else:
        listing(items, a.mode, a)


if __name__ == "__main__":
    main()
