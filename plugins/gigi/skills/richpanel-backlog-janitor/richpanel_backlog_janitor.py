# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
richpanel_backlog_janitor.py — CURĂȚĂ backlog-ul Richpanel în siguranță.

Problema (date verificate prin query_analytics, per canal): `facebook_feed_comment`
e cel mai mare canal — ~11.071 comentarii noi / lună, backlog ~1.345 deschise.
O bună parte sunt comentarii NON-ACȚIONABILE (zgomot / testimoniale / neutru fără
întrebare) care zac OPEN și umflă backlog-ul. Iar comentariile WISMO ("unde e
coletul?") nu trebuie închise — trebuie SNOOZATE până la ETA-ul estimat, ca să se
redeschidă automat când chiar e timpul să fie acționate.

Acest skill, în DRY-RUN by default:
  • IDENTIFICĂ comentariile FB de AUTO-ÎNCHIS (zgomot/testimonial/neutru fără întrebare),
  • IDENTIFICĂ comentariile WISMO deschise de SNOOZAT până la ETA estimat,
  • NU închide NICIODATĂ un LEAD sau o RECLAMAȚIE (sunt acționabile — vânzări / reputație),
  • exclude `messenger` / `facebook_message` (suport real 1-la-1, NU se atinge).

Clasificarea comentariilor REFOLOSEȘTE logica din `gigi:cs-comment-intelligence`
(lead / reclamație / testimonial / întrebare / neutru / zgomot, reguli RO + CZ/PL/BG),
iar detectarea WISMO refolosește regulile din `gigi:richpanel-export` (livrare_wismo).

  uv run richpanel_backlog_janitor.py                      # DRY-RUN: ce AR închide + ce AR snooza (NU scrie nimic)
  uv run richpanel_backlog_janitor.py --type close         # doar candidații de auto-închidere
  uv run richpanel_backlog_janitor.py --type snooze        # doar candidații de snooze (WISMO)
  uv run richpanel_backlog_janitor.py --json               # ieșire JSON pt automatizări
  uv run richpanel_backlog_janitor.py --apply              # EXECUTĂ (close + snooze) — gated explicit
  uv run richpanel_backlog_janitor.py --type close --apply # execută DOAR auto-închiderile

SIGURANȚĂ (dură): NICIODATĂ send_message / niciun mesaj către client. Permise (doar cu
--apply): update_conversation_status (CLOSED) și snooze_conversation. DRY-RUN nu scrie nimic.
"""
import os, sys, re, json, time, sqlite3, argparse, subprocess, importlib.util, urllib.request, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.abspath(os.path.join(HERE, ".."))          # .../plugins/gigi/skills
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
MCP_URL = "https://mcp.richpanel.com/mcp"

# canale-comentariu (le procesăm) vs canale de suport real 1-la-1 (NU le atingem)
COMMENT_CHANNELS = {"facebook_feed_comment", "instagram_comment"}
SUPPORT_CHANNELS = {"messenger", "facebook_message", "instagram_message", "email", "email_from_widget", "aircall"}

# tipuri acționabile → NU se închid NICIODATĂ
KEEP_TYPES = {"lead", "reclamatie", "intrebare"}
# SIGUR de auto-închis (default): testimonial pozitiv + zgomot pur.
# `neutru` e o găleată-de-rezervă ÎN CARE cad și reclamații ratate de clasificator
# (ex. „Niște gunoaie", „Nu e bun de nimic", „Păcăleală"), de aceea NU e închis decât
# explicit cu --include-neutru, și chiar și-atunci doar după veto-ul de reclamație.
SAFE_CLOSEABLE = {"zgomot", "testimonial"}
RISKY_CLOSEABLE = {"neutru"}

# zile estimate până la ETA pe magazin/piață (snooze WISMO → auto-reopen la ETA)
ETA_DAYS_DEFAULT = 4
ETA_DAYS_BY_MARKET = {  # piețe externe = livrare mai lungă
    "Bonhaus CZ": 7, "Bonhaus PL": 7, "Bonhaus BG": 7,
}


# ── refolosim logica din skill-urile surori (import dinamic, fără să le rulăm main) ──
def _load(modname, skill_dir, filename):
    path = os.path.join(SKILLS, skill_dir, filename)
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _cmt = _load("cs_comment_intelligence", "cs-comment-intelligence", "cs_comment_intelligence.py")
    classify = _cmt.classify                     # (text, store) -> lead/reclamatie/testimonial/intrebare/neutru/zgomot
except Exception as e:                            # pragma: no cover
    print("Nu pot încărca cs-comment-intelligence:", e); sys.exit(1)


# Veto RO de reclamație — întărit. Clasificatorul din cs-comment-intelligence rulează
# regexul COMPLAINT pe text BRUT (nu lowercase / fără diacritice), așa că ratează reclamații
# evidente. Aici normalizăm (lowercase + scoatem diacriticele) și adăugăm vocabular RO real
# văzut în backlog, ca să NU închidem din greșeală un comentariu negativ pe o reclamă live.
_RO_DEACC = str.maketrans("ăâîșşțţ", "aaissttt"[0:7])
EXTRA_COMPLAINT_RO = re.compile(
    r"gunoi|gunoai|porc[aă]ri|mizeri|p[aă]c[aă]l|musama|jucari|jucări|nu (e|ii|i)\s*bun|"
    r"nu (e|este)\s*(ca|cum)|nu sunt ce|nu este ce|altceva dec[aâ]t|nu (se )?(potriv|merit)|"
    r"mincin|minte|minti|minci|ieftin|de proast|proast|de prost|porcar|"
    r"nulitate|nu merit|nu recomand|teap[aă]|tzeap|tzap|escroc|ho[tț]i|furt|"
    r"defect|stricat|rupt|spart|deteriorat|nu (mi-a|am) ajuns|nu am primit|"
    r"banii inapoi|despagubi|desp[aă]gubi|restituit|returnat|reclama[tț]i|"
    r"sifonat|[sș]ifonat|nu corespunde|microscopic|prea (mic|scump)|lipse[sș]te|lipsesc", re.I)


# Veto cross-limbă pentru „testimonial" care de fapt e NEGATIV (lauda negată: „nu sunt
# mulțumit", „nie zadowolona", „nezauważyłam super efektu", „calitate rea"). Clasificatorul
# soră pune `testimonial` și pe astea (regexul de PRAISE prinde „super"/„zadowol" în context
# negat), așa că le filtrăm explicit aici. Mai bine păstrăm un comentariu decât să-l închidem greșit.
NEG_SIGNAL = re.compile(
    r"\bnie\b|niezbyt|nezauwaz|\bnezau|\bnefunguj|\bne ?doporuc|nedoporucuj|nespokojen|"   # PL/CZ negații
    r"calitate (foarte )?rea|foarte rea|prost[ăa]? calitate|comentarii(le)? negativ|"        # RO
    r"\beu nu sunt\b|nu sunt mul[tț]umit|\bnu sunt\b\.?\s*$|"                                  # „Eu nu sunt." (laudă negată)
    r"nu (sunt|este|e|mai)\s+(mul[tț]umit|bun|grozav|recomand)|nu ma m|nu m-?am|"
    r"din pacate|din p[aă]cate|le.?am aruncat|aruncat|dezamag|nemul[tț]umit|"
    r"f[aă]r[aă]\s+(pomp|capac|accesori|instruc|garan[tț]|pies|cablu|incarcator|înc[aă]rc)|"   # lipsă accesoriu
    r"prea (subtire|sub[tț]ire|mic|mar|scump|gros)|nu (atat|at[aâ]t|așa|asa) de", re.I)


def looks_like_complaint(text, store):
    """Veto de siguranță: regulile CZ/PL/BG din cs-comment-intelligence + veto RO întărit + veto negativ cross-limbă."""
    t = text or ""
    if NEG_SIGNAL.search(t):                    # laudă-negată / semnal negativ, în orice limbă
        return True
    lang = _cmt.STORE_LANG.get(store)
    if lang:                                    # piețe externe → regexurile de reclamație ale skill-ului soră
        L = _cmt.LANG[lang]
        tt = _cmt._deacc_cz(t) if L.get("deacc") else t
        if L["complaint"].search(tt):
            return True
    norm = t.lower().translate(_RO_DEACC)        # RO: normalizat + vocabular extins
    return bool(_cmt.COMPLAINT.search(norm) or EXTRA_COMPLAINT_RO.search(norm))

try:
    _exp = _load("richpanel_export", "richpanel-export", "richpanel_export.py")
    rp_categorize = _exp.categorize              # (subject, first_message, channel) -> categorie richpanel-export
except Exception as e:                            # pragma: no cover
    print("Nu pot încărca richpanel-export:", e); sys.exit(1)


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


class MCP:
    """JSON-RPC direct la endpoint-ul MCP Richpanel (același pattern ca richpanel-export)."""
    def __init__(self, token):
        self.token = token
        self._init()

    def _post(self, payload):
        h = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        lines = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(lines[-1][5:]) if lines else json.loads(body)

    def _init(self):
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "arona-backlog-janitor", "version": "1.0"}}})

    def call(self, name, args, retries=3):
        for i in range(retries):
            try:
                res = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": name, "arguments": args}})
                if res.get("error"):
                    raise RuntimeError("JSON-RPC error: %s" % res["error"])
                txt = res.get("result", {}).get("content", [{}])[0].get("text", "")
                return json.loads(txt) if txt.startswith("{") else {}
            except Exception:
                if i == retries - 1:
                    raise
                time.sleep(3 * (i + 1))
                try:
                    self._init()
                except Exception:
                    pass


# ── resolved_store din DB-ul local (maparea pagină FB → magazin), după id ──
def load_store_map():
    """conversation id -> resolved_store (sau store), din snapshot-ul local read-only."""
    if not os.path.exists(DB):
        return {}
    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=30)
    cols = [r[1] for r in con.execute("PRAGMA table_info(tickets)")]
    sc = "resolved_store" if "resolved_store" in cols else "store"
    m = {}
    for tid, store in con.execute(f"SELECT id, {sc} FROM tickets WHERE channel LIKE '%comment%'"):
        if store:
            m[tid] = store
    con.close()
    return m


def text_of(t):
    return " ".join(((t.get("first_message") or "") + " " + (t.get("subject") or "")).split())


def is_wismo(t):
    return rp_categorize(t.get("subject"), t.get("first_message"), t.get("channel")) == "livrare_wismo"


def has_question(text):
    return "?" in (text or "")


# ── 1. fetch live OPEN conversations pe canalele-comentariu ──
def fetch_open_comments(mcp, store_map, max_pages=200, verbose=False):
    """Paginăm pe alias-ul MCP `facebook`/`instagram`. ATENȚIE: proxy-ul MCP filtrează canalele
    INTERN, deci o pagină poate veni cu mai puține rânduri decât per_page chiar dacă mai există
    pagini → ne bazăm DOAR pe `has_more`, cu o oprire de siguranță după pagini goale consecutive."""
    out = []
    seen = set()
    for ch in ("facebook", "instagram"):   # alias-urile MCP; le filtrăm strict pe canalul-comentariu
        page = 1
        empty_streak = 0
        while page <= max_pages:
            d = mcp.call("list_conversations", {"status": "OPEN", "channel": ch,
                                                "per_page": 50, "page": page,
                                                "sortKey": "createdAt", "order": "asc"})
            ts = d.get("tickets") or []
            empty_streak = empty_streak + 1 if not ts else 0
            for t in ts:
                cid = t.get("id")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                if t.get("channel") not in COMMENT_CHANNELS:   # exclude messenger/facebook_message (suport real)
                    continue
                store = store_map.get(cid) or "necunoscut"
                txt = text_of(t)
                out.append({
                    "id": cid, "no": t.get("conversation_no"), "store": store,
                    "channel": t.get("channel"), "status": t.get("status"),
                    "created_at": t.get("created_at"), "text": txt,
                    "type": classify(txt, store), "wismo": is_wismo(t),
                    "question": has_question(txt),
                })
            if not d.get("has_more") or empty_streak >= 3:
                break
            page += 1
            time.sleep(0.3)
    if verbose:
        print("  [debug] %d conversații-comentariu OPEN colectate" % len(out), file=sys.stderr)
    return out


# ── fallback: din snapshot-ul local (dacă MCP nu e disponibil / --offline) ──
def fetch_open_comments_local():
    con = sqlite3.connect("file:" + DB + "?mode=ro", uri=True, timeout=30)
    cols = [r[1] for r in con.execute("PRAGMA table_info(tickets)")]
    sc = "resolved_store" if "resolved_store" in cols else "store"
    rows = con.execute(
        f"SELECT id, conversation_no, {sc}, channel, status, created_at, subject, first_message "
        "FROM tickets WHERE status='OPEN' AND channel LIKE '%comment%'").fetchall()
    con.close()
    out = []
    for tid, no, store, ch, status, created, subject, fm in rows:
        if ch not in COMMENT_CHANNELS:
            continue
        t = {"subject": subject, "first_message": fm, "channel": ch}
        txt = text_of(t)
        store = store or "necunoscut"
        out.append({"id": tid, "no": no, "store": store, "channel": ch, "status": status,
                    "created_at": created, "text": txt, "type": classify(txt, store),
                    "wismo": is_wismo(t), "question": has_question(txt)})
    return out


# ── 2. decizii: close-candidates + snooze-candidates ──
def decide(items, include_neutru=False):
    closeable = set(SAFE_CLOSEABLE) | (RISKY_CLOSEABLE if include_neutru else set())
    closes, snoozes, kept = [], [], []
    for it in items:
        # WISMO deschis → SNOOZE (niciodată close), indiferent de tipul lingvistic
        if it["wismo"]:
            it = dict(it, action="snooze", reason="WISMO deschis → snooze până la ETA")
            snoozes.append(it); continue
        # acționabile → NU le atingem
        if it["type"] in KEEP_TYPES or it["question"]:
            it = dict(it, action="keep",
                      reason=("acționabil: %s" % it["type"]) if it["type"] in KEEP_TYPES else "are întrebare deschisă")
            kept.append(it); continue
        # candidat AUTO-CLOSE, DAR mereu cu veto de reclamație (chiar și pe testimonial)
        if it["type"] in closeable:
            if looks_like_complaint(it["text"], it["store"]):
                kept.append(dict(it, action="keep", type="reclamatie",
                                 reason="reclamație detectată la veto (al 2-lea pas) → nu închid"))
                continue
            it = dict(it, action="close", reason="non-acționabil (%s) fără întrebare" % it["type"])
            closes.append(it); continue
        # `neutru` fără --include-neutru → păstrat din precauție (poate ascunde o reclamație ratată)
        kept.append(dict(it, action="keep", reason="păstrat din precauție (%s, fără --include-neutru)" % it["type"]))
    return closes, snoozes, kept


def parse_ts(v):
    if not v:
        return None
    s = str(v)
    try:
        if s.isdigit():
            n = int(s)
            return datetime.datetime.fromtimestamp(n / (1000 if n > 1e12 else 1), datetime.timezone.utc)
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def eta_iso(it):
    """ETA estimat (ISO 8601 UTC) = created_at + ETA_DAYS, dar minim mâine față de acum."""
    base = parse_ts(it.get("created_at")) or datetime.datetime.now(datetime.timezone.utc)
    days = ETA_DAYS_BY_MARKET.get(it.get("store"), ETA_DAYS_DEFAULT)
    eta = base + datetime.timedelta(days=days)
    floor = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    if eta < floor:
        eta = floor
    return eta.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── output ──
def line(it):
    return "  #%-7s %-14s %-18s %-9s | %s" % (
        it.get("no") or "?", (it.get("store") or "")[:14], it["channel"][:18],
        it["type"][:9], (it["text"] or "")[:80])


def report(closes, snoozes, kept, total, applied, json_out, show_close=True, show_snooze=True):
    if json_out:
        payload = {"total_open_comments": total, "kept": len(kept), "applied": applied}
        if show_close:
            payload["auto_close"] = [{"id": c["id"], "no": c["no"], "store": c["store"], "type": c["type"],
                                      "channel": c["channel"], "text": c["text"], "reason": c["reason"]} for c in closes]
        if show_snooze:
            payload["snooze"] = [{"id": s["id"], "no": s["no"], "store": s["store"], "channel": s["channel"],
                                  "snooze_till": eta_iso(s), "text": s["text"], "reason": s["reason"]} for s in snoozes]
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str)); return

    mode = "APLICAT (am scris în Richpanel)" if applied else "DRY-RUN (nu am scris nimic)"
    print("═" * 86)
    print("  RICHPANEL BACKLOG JANITOR — %s" % mode)
    print("  %d comentarii FB/IG deschise scanate  |  %d de auto-închis  |  %d de snooze  |  %d păstrate (acționabile)"
          % (total, len(closes), len(snoozes), len(kept)))
    print("═" * 86)

    if show_close:
        print("\n  🧹 AUTO-CLOSE — %d comentarii non-acționabile (zgomot/testimonial/neutru, fără întrebare, fără veto de reclamație):" % len(closes))
        by = {}
        for c in closes:
            by[c["type"]] = by.get(c["type"], 0) + 1
        if by:
            print("     din care: " + " | ".join("%s=%d" % (k, v) for k, v in sorted(by.items(), key=lambda x: -x[1])))
        for c in closes[:60]:
            print(line(c))
        if len(closes) > 60:
            print("     … încă %d (folosește --json pt toate)" % (len(closes) - 60))

    if show_snooze:
        print("\n  💤 SNOOZE — %d comentarii WISMO deschise (auto-reopen la ETA estimat):" % len(snoozes))
        for s in snoozes[:60]:
            print("  →%s  " % eta_iso(s) + line(s).lstrip())
        if len(snoozes) > 60:
            print("     … încă %d" % (len(snoozes) - 60))

    print("\n  🔒 NU se ating: LEAD-uri, RECLAMAȚII și orice comentariu cu întrebare deschisă.")
    if not applied:
        print("  ▶ Nimic NU a fost scris. Rulează cu --apply ca să execuți (close + snooze).")


# ── apply (gated) ──
def apply_close(mcp, closes):
    ok, fail = 0, 0
    for c in closes:
        try:
            mcp.call("update_conversation_status", {"conversation_id": c["id"], "status": "CLOSED"})
            ok += 1
        except Exception as e:
            fail += 1
            print("    ✗ close #%s eșuat: %s" % (c.get("no"), e))
        time.sleep(0.2)
    print("  ✓ Închise: %d  (eșecuri: %d)" % (ok, fail))


def apply_snooze(mcp, snoozes):
    ok, fail = 0, 0
    for s in snoozes:
        try:
            mcp.call("snooze_conversation", {"conversation_id": s["id"], "snoozed_till": eta_iso(s)})
            ok += 1
        except Exception as e:
            fail += 1
            print("    ✗ snooze #%s eșuat: %s" % (s.get("no"), e))
        time.sleep(0.2)
    print("  ✓ Snoozate: %d  (eșecuri: %d)" % (ok, fail))


def main():
    ap = argparse.ArgumentParser(description="Curăță backlog-ul Richpanel: auto-close comentarii non-acționabile + snooze WISMO. DRY-RUN by default.")
    ap.add_argument("--type", choices=["close", "snooze"], help="doar un tip de operație (implicit: ambele)")
    ap.add_argument("--apply", action="store_true", help="EXECUTĂ efectiv (altfel DRY-RUN)")
    ap.add_argument("--include-neutru", dest="include_neutru", action="store_true",
                    help="include și comentariile `neutru` în auto-close (riscant: găleata-de-rezervă; tot trec prin veto de reclamație)")
    ap.add_argument("--offline", action="store_true", help="citește din snapshot-ul local în loc de MCP live")
    ap.add_argument("--verbose", action="store_true", help="loguri de paginare pe stderr")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    mcp = None
    if a.offline:
        if not os.path.exists(DB):
            print("Nu găsesc DB-ul local:", DB); sys.exit(1)
        items = fetch_open_comments_local()
    else:
        tok = secret("RICHPANEL_MCP_TOKEN")
        if not tok:
            print("Lipsește RICHPANEL_MCP_TOKEN în KB."); sys.exit(1)
        mcp = MCP(tok)
        store_map = load_store_map()
        items = fetch_open_comments(mcp, store_map, verbose=a.verbose)

    closes, snoozes, kept = decide(items, include_neutru=a.include_neutru)

    # filtrare pe --type pentru afișare ȘI pentru apply
    show_close = a.type in (None, "close")
    show_snooze = a.type in (None, "snooze")
    report(closes, snoozes, kept, len(items), a.apply, a.json,
           show_close=show_close, show_snooze=show_snooze)

    if a.apply:
        if a.offline or mcp is None:
            print("\n  ✗ --apply necesită MCP live (nu merge cu --offline)."); sys.exit(1)
        if not a.json:
            print("\n  ── APLIC ──")
        if show_close and closes:
            apply_close(mcp, closes)
        if show_snooze and snoozes:
            apply_snooze(mcp, snoozes)


if __name__ == "__main__":
    main()
