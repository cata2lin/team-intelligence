# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_intl.py — uneltele CS pentru piețele INTERNAȚIONALE (BG, HU, CZ, SK, PL, GR, MD). DOAR CITIRE.

  uv run cs_intl.py tichete [--piata BG] [--toate] [--pagini 20] [--json]
      Tichetele OPEN din Richpanel pe piață, cu ultimul mesaj de la CLIENT (= așteaptă răspuns).
      Magazinul vine din richpanel_schema.resolve_store (pagina FB din prefixul id-ului, cutia de
      e-mail, prefixul comenzii, domeniul din text); piața = magazinul, iar unde magazinul lipsește
      sau e multi-piață (Duppo EU), din LIMBA în care scrie clientul (marcat „din limbă").
      Nu afișează text de client: doar nr. tichet, canal, magazin, vârstă, categorie, escaladare.
  uv run cs_intl.py tracking --awb FAN30411342[,8xxxxxxxxxx,...]
      FAN Courier Moldova (FAN + cifre) din pagina publică app.fancourier.md; restul (DPD/Sameday/
      Econt/Packeta) prin awb-track. Pentru FAN MD decide „Starea actuală", nu ultimul eveniment.
  uv run cs_intl.py politica [--piata CZ]
      Politica de retur / livrare / contact pe piață, citită de pe paginile publice ale magazinelor
      (verificat 19-oct-2026) + autoritatea de protecția consumatorului.

Nu scrie NIMIC în Richpanel (fără draft, tag, notă, status). Secrete: RICHPANEL_MCP_TOKEN (env sau KB).
"""
import argparse, collections, html, json, os, re, subprocess, sys, time, urllib.request
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "..", "..", "core", "scripts")
sys.path.insert(0, SCRIPTS)
import richpanel_schema as rps  # noqa: E402  (schema veche+nouă Richpanel, o singură sursă)

KB = os.path.join(SCRIPTS, "kb.py")
AWBTRACK = os.path.join(HERE, "..", "awb-track", "awb_track.py")
MCP_URL = "https://mcp.richpanel.com/mcp"

PIETE = ("BG", "HU", "CZ", "SK", "PL", "GR", "MD", "HR")

# magazin (numele din richpanel_schema / cs_auto_draft) → piață. Duppo BG = „Duppo EU", magazin
# MULTI-PIAȚĂ din 22-sep-2026 (BG+CZ+HU+SK pe același Shopify) → piața o dă limba clientului.
STORE_MARKET = {
    "Duppo BG": "BG", "Bonhaus BG": "BG", "Nocturna BG": "BG",
    "Bonhaus CZ": "CZ", "Duppo Czechia": "CZ",
    "Bonhaus SK": "SK", "Duppo SK": "SK",
    "Bonhaus HU": "HU", "Duppo HU": "HU", "Duppo Hungary": "HU",
    "Bonhaus PL": "PL", "Nocturna PL": "PL",
    "Duppo Moldova": "MD", "Bonhaus HR": "HR",
}
MULTI_PIATA = {"Duppo BG"}
LANG_MARKET = {"bg": "BG", "hu": "HU", "cz": "CZ", "sk": "SK", "pl": "PL", "el": "GR", "ru": "MD", "hr": "HR"}

# ---------- limbă (variantă ușoară a detect_lang din cs_auto_draft) ----------
_CYR = re.compile(r"[Ѐ-ӿ]")
_RU_ONLY = re.compile(r"[ыэё]")
_GREEK = re.compile(r"[Ͱ-Ͽ]")
_SRC = "ąáäćčďęéěíłĺľńňóôöőřŕśšťúůüűýźżžăâîșşțţ"
_DST = "aaaccdeeeilllnnoooorrsstuuuuyzzzaaisstt"
_DEACC = str.maketrans(_SRC, _DST)
_WORDS = {
    "cz": r"\b(?:dekuji|dekujeme|zbozi|nebo|jsem|jste|kdy|neni|muzete|chci|vraceni|zasilk[auy]|dobry den|objednal jsem)\b",
    "sk": r"\b(?:dakujem|dakujeme|tovar|alebo|som|kedy|mozete|chcem|vratenie|zasielk[auy]|objednal som)\b",
    "pl": r"\b(?:dziekuje|prosze|zamowieni[ea]|przesylk[ai]|paczk[ai]|kiedy|gdzie|jestem|czy|witam|dzien dobry|pozdrawiam)\b",
    "hu": r"\b(?:koszonom|kerem|rendeles\w*|rendeltem|csomag\w*|szallitas|termek|mikor|hogy|nem|mar|csak)\b",
    "hr": r"\b(?:hvala|molim|narudzb\w+|posiljk\w+|gdje|kada|zelim|dostava|dobar dan)\b",
    "ro": r"\b(?:buna|multumesc|comanda|coletul|va rog|pentru|este|sunt|cand|unde|vreau|livrare|primit|produsul)\b",
}


def lang_of(text):
    t = text or ""
    if _GREEK.search(t):
        return "el"
    if _CYR.search(t):
        return "ru" if _RU_ONLY.search(t.lower()) else "bg"
    low = t.lower()
    if any(c in low for c in "łąężśźń"):
        return "pl"
    if any(c in low for c in "řůě"):
        return "cz"
    if any(c in low for c in "ľĺŕôä"):
        return "sk"
    if any(c in low for c in "őű"):
        return "hu"
    if any(c in low for c in "ăâîșțşţ"):
        return "ro"
    d = low.translate(_DEACC)
    best = max(((len(re.findall(p, d)), k) for k, p in _WORDS.items()), default=(0, None))
    return best[1] if best[0] >= 2 else None


# ---------- escaladare juridică, pe limbi (din garda botului, cs_auto_draft ESCAL) ----------
ESCAL = re.compile(
    r"anpc|protectia consumator|avocat|instanta|\bкзп\b|за защита на потребител|жалба|адвокат|прокуратура|"
    r"заведа дело|ще ви съдя|fogyasztovedelem|bekelteto testulet|ugyved|birosag|beperel|feljelentes|"
    r"\bna soi\b|soi\.sk|\bna coi\b|\bdtest\b|obchodn\w* inspekc|ochrana spotrebitel|advokat|zalob|"
    r"\buokik\b|rzecznik\w* konsument|inspekcja handlowa|adwokat|pozew|do sadu|"
    r"protectia consumatorului|consumator\.gov\.md|chargeback|consumer protection|legal action", re.I)

CATS = [  # categorie grosieră, multi-limbă (fără diacritice)
    ("retur_refund", r"\bretur|refund|zwrot|vraceni|vratenie|odstoup|visszakuld|visszaterit|връщан|възстанов"),
    ("anulare", r"anul|cancel|anuluj|zrus|storno|lemond|отказ|анулир"),
    ("lipsa_defect", r"lipse|spart|defect|damaged|broken|uszkodz|poskoz|serult|hianyz|повред|счупен|липсва|само един|samo edin"),
    ("wismo", r"unde e|nu a ajuns|awb|tracking|gdzie|przesylk|kde je|zasilk|zasielk|hol van|csomag|кога|къде|пратк|колет"),
    ("presale", r"\bpret|\bcena\b|price|skladem|na stanie|keszlet|цена|налич"),
]


def categorie(text, channel):
    d = (text or "").lower().translate(_DEACC)
    for cat, p in CATS:
        if re.search(p, d):
            return cat
    return "comentariu" if channel in ("facebook_feed_comment", "instagram_comment") else "altele"


# ---------- Richpanel (JSON-RPC, doar list_conversations) ----------
def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k],
                                               capture_output=True, text=True).stdout.strip()


class Richpanel:
    def __init__(self, token):
        if not token:
            raise SystemExit("lipsește RICHPANEL_MCP_TOKEN (env sau KB)")
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                               "clientInfo": {"name": "cs-intl", "version": "1"}}})

    def _post(self, p):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        ln = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(ln[-1][5:]) if ln else json.loads(body)

    def list_open(self, page):
        r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "list_conversations",
            "arguments": {"status": "OPEN", "page": page, "per_page": 50, "sortKey": "updatedAt", "order": "desc"}}})
        rps.check(r, "list_conversations")
        txt = r["result"]["content"][0]["text"]
        try:
            d = json.loads(txt)
        except Exception:
            d = {"_text": txt}
        return d


def _age_h(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return round((datetime.now(timezone.utc) - d).total_seconds() / 3600, 1)
    except Exception:
        return None


def piata_tichet(t):
    text = " ".join(str(t.get(k) or "") for k in ("subject", "first_message"))
    store = rps.resolve_store(t)
    lang = lang_of(text)
    if store in STORE_MARKET and store not in MULTI_PIATA:
        return STORE_MARKET[store], store, "magazin", lang
    if store in MULTI_PIATA:
        return LANG_MARKET.get(lang, STORE_MARKET[store]), store, ("magazin+limbă" if lang in LANG_MARKET else "magazin"), lang
    if store:  # magazin românesc: piața RO, chiar dacă scrie în altă limbă
        return "RO", store, "magazin", lang
    if lang in LANG_MARKET:
        return LANG_MARKET[lang], "?", "din limbă", lang
    return "?", "?", "necunoscut", lang


def cmd_tichete(a):
    rp = Richpanel(secret("RICHPANEL_MCP_TOKEN"))
    seen, rows, pagini = set(), [], 0
    for page in range(1, a.pagini + 1):
        d = rp.list_open(page)
        batch = rps.tickets(d)
        pagini = page
        if not batch:
            break
        for t in batch:
            cid = t.get("id") or t.get("conversation_no")
            if cid in seen:
                continue
            seen.add(cid)
            piata, store, cum, lang = piata_tichet(t)
            text = " ".join(str(t.get(k) or "") for k in ("subject", "first_message"))
            rows.append({
                "nr": t.get("conversation_no"), "piata": piata, "magazin": store, "cum": cum, "limba": lang or "-",
                "canal": t.get("channel"), "ultimul": rps.sender(t) or "?",
                "varsta_h": _age_h(t.get("updated_at") or t.get("created_at")),
                "categorie": categorie(text, t.get("channel")),
                "escaladare": bool(ESCAL.search(text.lower().translate(_DEACC))),
            })
        if isinstance(d, dict) and d.get("has_more") is False:
            break
        time.sleep(0.2)
    intl = [r for r in rows if r["piata"] not in ("RO", "?")]
    if not a.toate:
        intl = [r for r in intl if r["ultimul"] == "customer"]
    if a.piata:
        intl = [r for r in intl if r["piata"] == a.piata.upper()]
    intl.sort(key=lambda r: (not r["escaladare"], -(r["varsta_h"] or 0)))
    if a.json:
        print(json.dumps({"scanate": len(rows), "pagini": pagini, "tichete": intl}, ensure_ascii=False, indent=1))
        return
    nec = sum(1 for r in rows if r["piata"] == "?")
    print("CS INTERNAȚIONAL — tichete OPEN %s | scanate %d (pagini %d), nerezolvate ca piață %d"
          % ("(toate)" if a.toate else "cu ultimul mesaj de la CLIENT", len(rows), pagini, nec))
    cnt = collections.Counter(r["piata"] for r in intl)
    print("Pe piață: " + (" · ".join("%s=%d" % kv for kv in cnt.most_common()) or "nimic"))
    esc = sum(r["escaladare"] for r in intl)
    if esc:
        print("⚠ ESCALADĂRI (autoritate/juridic): %d — răspuns uman, NU macro" % esc)
    print("%-8s %-4s %-14s %-13s %-5s %-21s %-8s %-12s %s" % ("tichet", "piat", "magazin", "cum", "limba", "canal", "vârsta h", "categorie", "esc"))
    for r in intl[: a.max]:
        print("%-8s %-4s %-14s %-13s %-5s %-21s %-8s %-12s %s" % (
            r["nr"] or "?", r["piata"], str(r["magazin"])[:14], r["cum"], r["limba"], str(r["canal"])[:21],
            r["varsta_h"] if r["varsta_h"] is not None else "?", r["categorie"], "DA" if r["escaladare"] else ""))
    if len(intl) > a.max:
        print("… încă %d (folosește --max)" % (len(intl) - a.max))
    print("\n„din limbă” = magazinul n-a putut fi aflat (e-mail fără cutie în schema nouă); piața e ghicită din limba clientului.")


# ---------- tracking ----------
FAN_MD_URL = "https://app.fancourier.md/fan/Main?tracking=true&awbno=%s"
_FAN_RE = re.compile(r"^FAN\d{6,}$", re.I)
_FAN_ESEC = re.compile(r"refuz|nu raspunde|nu mai doreste|returnat|retur|livrare esuata|nelivrat", re.I)


def fan_md(awb):
    req = urllib.request.Request(FAN_MD_URL % awb, headers={"User-Agent": "Mozilla/5.0 (arona-cs-intl)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        s = r.read().decode("utf-8", "replace")
    s = re.sub(r"<script.*?</script>|<style.*?</style>", "", s, flags=re.S)
    lines = [l.strip() for l in html.unescape(re.sub(r"<[^>]+>", "\n", s)).splitlines() if l.strip()]
    stare = None
    if "Starea actuală:" in lines:
        i = lines.index("Starea actuală:")
        stare = lines[i + 1] if i + 1 < len(lines) else None
    ist, zi = [], None
    if "Istoric expediții" in lines:
        for l in lines[lines.index("Istoric expediții") + 1:]:
            if l.startswith("www.") or l.startswith("Awb") or _FAN_RE.match(l):
                continue
            if re.match(r"^\d{2}-\d{2}-\d{4}$", l):
                zi = l
            elif re.match(r"^\d{2}:\d{2}$", l):
                ist.append([zi, l, None])
            elif ist and ist[-1][2] is None:
                ist[-1][2] = l
    ist = [e for e in ist if e[2]]
    esec = any(_FAN_ESEC.search(e[2]) for e in ist)
    nota = None
    if not stare and not ist:
        nota = "pagină goală: AWB necunoscut sau citire eșuată (NU e un status)"
    elif stare and stare.lower().startswith("livrat") and esec:
        nota = "CAPCANĂ: „livrat” după o livrare eșuată = colet scanat înapoi în hub, NU livrat clientului"
    return {"awb": awb, "curier": "FAN Courier Moldova", "stare_actuala": stare, "istoric": ist[-8:],
            "semn_esec": esec, "nota": nota, "link": FAN_MD_URL % awb}


def cmd_tracking(a):
    awbs = [x.strip() for x in a.awb.replace(";", ",").split(",") if x.strip()]
    fan = [x for x in awbs if _FAN_RE.match(x)]
    rest = [x for x in awbs if x not in fan]
    out = []
    for x in fan:
        try:
            out.append(fan_md(x.upper()))
        except Exception as e:
            out.append({"awb": x, "curier": "FAN Courier Moldova", "eroare": str(e)[:200], "link": FAN_MD_URL % x})
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for o in out:
            print("── %s · %s" % (o["awb"], o["curier"]))
            if o.get("eroare"):
                print("   EROARE la citire: %s" % o["eroare"])
            else:
                print("   Starea actuală: %s" % (o["stare_actuala"] or "—"))
                for z, h, ev in o["istoric"]:
                    print("   %s %s  %s" % (z or "", h, ev))
                if o["nota"]:
                    print("   ⚠ %s" % o["nota"])
            print("   %s" % o["link"])
    if rest:
        args = ["uv", "run", AWBTRACK]
        for x in rest:
            args += ["--awb", x]
        if a.json:
            args.append("--json")
        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run(args, capture_output=True, text=True, env=env, timeout=280)
        sys.stdout.write(r.stdout)
        if r.returncode != 0:
            sys.stderr.write(r.stderr[-1500:])
            raise SystemExit(r.returncode)


# ---------- politica de retur ----------
POLITICI_JSON = os.path.join(HERE, "references", "politici.json")


def cmd_politica(a):
    d = json.load(open(POLITICI_JSON, encoding="utf-8"))
    piete = [a.piata.upper()] if a.piata else list(d["piete"])
    out = {p: d["piete"].get(p) or {"eroare": "piață necunoscută; ai: %s" % ", ".join(d["piete"])} for p in piete}
    if a.json:
        print(json.dumps({"verificat": d["verificat"], "comun": d["comun"], "piete": out}, ensure_ascii=False, indent=1))
        return
    print("Politici verificate pe %s (paginile publice ale magazinelor). Comun: %s\n" % (d["verificat"], d["comun"]))
    for p, v in out.items():
        print("══ %s" % p)
        for k, val in v.items():
            if isinstance(val, list):
                print("  %s:" % k)
                for x in val:
                    print("    - %s" % (json.dumps(x, ensure_ascii=False) if isinstance(x, dict) else x))
            else:
                print("  %s: %s" % (k, val))
        print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tichete")
    t.add_argument("--piata", default="", help="BG|HU|CZ|SK|PL|GR|MD|HR (gol = toate)")
    t.add_argument("--toate", action="store_true", help="și cele unde ultimul mesaj e al nostru")
    t.add_argument("--pagini", type=int, default=20, help="pagini de 50 scanate (implicit 20 = 1000 tichete)")
    t.add_argument("--max", type=int, default=60)
    t.add_argument("--json", action="store_true")
    k = sub.add_parser("tracking")
    k.add_argument("--awb", required=True)
    k.add_argument("--json", action="store_true")
    p = sub.add_parser("politica")
    p.add_argument("--piata", default="")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args()
    {"tichete": cmd_tichete, "tracking": cmd_tracking, "politica": cmd_politica}[a.cmd](a)


if __name__ == "__main__":
    main()
