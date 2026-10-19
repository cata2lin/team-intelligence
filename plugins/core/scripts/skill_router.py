"""UserPromptSubmit hook: pune skill-urile echipei în fața agentului ÎNAINTE să improvizeze.

DE CE: catalogul din CLAUDE.team.md arăta 104 skill-uri, Second Brain are ~500 — jumătate nu erau
instalate local deloc, iar `find_skills` (semantic, pe descrieri în engleză) rata exact întrebările
de zi cu zi în română („ce comandă are telefonul 07…" nu scotea cs-360, „câte bucăți am vândut"
nu scotea product-sales). Agenții improvizau query-uri raw fiindcă nu vedeau skill-ul.

Ce face, la fiecare prompt:
  1. scor LEXICAL (BM25, fără diacritice, rădăcini de 6 litere) pe nume + descriere, din catalogul
     cache-uit local — merge offline și prinde vocabularul românesc;
  2. scor SEMANTIC din Second Brain (/api/skills/route), cu timeout scurt — dacă pică, rămâne lexicalul;
  3. le contopește (reciprocal-rank fusion) și injectează top 5 în context, cu CUM se folosește
     fiecare: instalat → Skill tool; doar în Second Brain → calea locală a SKILL.md-ului (cache) +
     skill_info/run_skill dacă are acțiuni.
  4. dacă nimic nu se potrivește: spune agentului să rezolve singur și să propună salvarea ca skill,
     și notează „golul" în KB (events, type=skill_gap) prin bufferul hook-ului de log.

  uv run --no-project skill_router.py            # hook (citește JSON pe stdin)
  uv run --no-project skill_router.py --sync     # reîmprospătează catalogul + corpurile SKILL.md
  uv run --no-project skill_router.py --sync-if-stale   # (SessionStart) sync detașat doar dacă e vechi
  uv run --no-project skill_router.py --test "câte bucăți am vândut din HA-0002"

Doar stdlib (pornire instantă). Orice eroare → exit 0 fără output: hook-ul nu blochează niciodată un prompt.
"""
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request
from collections import Counter
from pathlib import Path

HOME = Path.home()
CACHE = Path(os.environ.get("ARONA_SKILL_CACHE") or HOME / ".cache" / "arona-skills")
CATALOG = CACHE / "catalog.json"
BODIES = CACHE / "skills"
SYNC_EVERY_S = 6 * 3600
SEMANTIC_TIMEOUT_S = 2.0
TOP_N = 6

STOP = set(
    "si sau de la pe in cu din ce care un o a al ale ai lui le sa se nu ma mi am are e este fi fa "
    "vreau cat cate cati cum unde cand pt pentru tot toate mai doar ca daca imi iti ne va vor "
    "the and for to of an on with use when or by is it this that from as be are any all via "
    "skill skills despre scrie spune explica arata zi-mi da-mi poti te rog acum azi".split()
)
# descrierile sunt pe jumătate în engleză, cererile vin în română: punte minimă RO → EN (rădăcini de 6)
SYNONYMS = {
    "reclam": ["ads"], "campan": ["campai"], "vandut": ["sales"], "vanzar": ["sales"], "vinde": ["sales"],
    "comand": ["order", "comenz"], "comenz": ["comand", "order"], "venit": ["intrat"], "intrat": ["venit"], "telefo": ["phone"], "client": ["custom"], "stocul": ["stoc", "stock"],
    "stoc": ["stock"], "pret": ["price"], "pretur": ["price"], "factur": ["invoic"], "livrar": ["delive"],
    "livrat": ["delive"], "refuz": ["refusa"], "retur": ["return"], "profit": ["pnl"], "colete": ["parcel"],
    "colet": ["parcel"], "produs": ["produc"], "magazi": ["store", "shop"], "tichet": ["ticket"],
    "raspun": ["reply"], "poze": ["image"], "imagin": ["image"], "cheltu": ["spend"], "buget": ["budget"],
    "portof": ["portfo"], "scade": ["mutare", "stoc"], "scadem": ["mutare", "stoc"], "muta": ["mutare"],
    "mutat": ["mutare"], "recept": ["recepti"], "neplec": ["neplecate"], "deadst": ["lichid", "stoc"],
}
# Înlocuite de Order Hub din 6-oct-2026 (shared/CS.md §4): routerul nu mai trimite agenți spre ele
DEPRECATE = {
    "gigi:cs-actions", "mcp:arona-fulfillment/xc_order_cancel", "mcp:arona-fulfillment/xc_awb_make",
    "mcp:arona-fulfillment/xc_awb_void", "mcp:arona-fulfillment/xc_awb_regen", "mcp:arona-fulfillment/xc_inv_make",
}
TRIVIAL = re.compile(r"^(da|nu|ok|okay|yes|no|go|continua|continue|mersi|multumesc|super|perfect|bine)\b[.! ]*$", re.I)


def toks(text):
    t = unicodedata.normalize("NFKD", (text or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return [w[:6] for w in re.findall(r"[a-z0-9]+", t) if len(w) > 1 and w not in STOP]


def frontmatter(path):
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:6000]
    except Exception:
        return None, None
    m = re.match(r"^---\s*\n(.*?)\n---", head, re.S)
    if not m:
        return None, None
    fm = m.group(1)
    name = re.search(r"^name:\s*(.+)$", fm, re.M)
    desc = re.search(r"^description:\s*(.+?)(?=^\S[\w-]*:|\Z)", fm, re.M | re.S)
    clean = lambda s: re.sub(r"\s+", " ", s).strip().strip("\"'>|").strip()
    return (clean(name.group(1)) if name else None), (clean(desc.group(1)) if desc else "")


def local_skills():
    """Skill-urile instalate pe mașina asta (invocabile direct cu Skill tool)."""
    out = {}
    roots = [(p, True) for p in (HOME / ".claude" / "plugins" / "marketplaces").glob("*/plugins/*/skills/*/SKILL.md")]
    roots += [(p, False) for p in (HOME / ".claude" / "skills").glob("*/SKILL.md")]
    for p, namespaced in roots:
        name, desc = frontmatter(p)
        slug = p.parent.name
        plugin = p.parent.parent.parent.name if namespaced else None
        key = f"{plugin}:{slug}" if plugin else (name or slug)
        if name and ":" in name and not plugin:
            key = name
        out[key] = {"name": key, "description": desc or "", "installed": True, "path": str(p)}
    return out


# ---------------------------------------------------------------- Second Brain (opțional)
def sb_conf():
    conf = {}
    f = HOME / ".claude" / "second-brain.env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                conf[k.strip()] = v.strip()
    base = os.environ.get("SB_SERVER_URL") or conf.get("SECOND_BRAIN_URL")
    user = os.environ.get("SB_USERNAME") or conf.get("SECOND_BRAIN_USER")
    pw = os.environ.get("SB_PASSWORD") or conf.get("SECOND_BRAIN_PASS")
    return (base.rstrip("/") if base else None), user, pw


def http(method, url, body=None, token=None, timeout=10.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def sb_token(base, user, pw, fresh=False, timeout=5.0):
    tf = CACHE / ".jwt"
    if not fresh and tf.exists() and time.time() - tf.stat().st_mtime < 6 * 3600:
        return tf.read_text().strip()
    res = http("POST", f"{base}/api/auth/login", {"username": user, "password": pw}, timeout=timeout) or {}
    tok = res.get("access_token") or res.get("token")
    if tok:
        CACHE.mkdir(parents=True, exist_ok=True)
        tf.write_text(tok)
        try:
            os.chmod(tf, 0o600)
        except Exception:
            pass
    return tok


def sb_call(method, path, body=None, timeout=10.0):
    base, user, pw = sb_conf()
    if not (base and user and pw):
        return None
    tok = sb_token(base, user, pw, timeout=timeout)
    try:
        return http(method, base + path, body, tok, timeout)
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
        tok = sb_token(base, user, pw, fresh=True, timeout=timeout)
        return http(method, base + path, body, tok, timeout)


def mcp_tools():
    """Tool-urile serverelor MCP stdio din ~/.claude.json care rulează un mcp_server.py al nostru
    (arona-*), citite cu ast din sursă — fără să pornim serverele."""
    import ast
    out = {}
    try:
        conf = json.loads((HOME / ".claude.json").read_text(encoding="utf-8"))
    except Exception:
        return out
    for server, spec in (conf.get("mcpServers") or {}).items():
        src = next((a for a in spec.get("args") or [] if str(a).endswith("mcp_server.py")), None)
        if not src or not os.path.isfile(src):
            continue
        try:
            tree = ast.parse(Path(src).read_text(encoding="utf-8"))
        except Exception:
            continue
        for fn in tree.body:
            if not isinstance(fn, ast.FunctionDef):
                continue
            if not any("tool" in ast.unparse(d) for d in fn.decorator_list):
                continue
            doc = re.sub(r"\s+", " ", ast.get_docstring(fn) or "").strip()
            key = f"mcp:{server}/{fn.name}"
            out[key] = {"name": key, "description": doc[:400], "installed": True,
                        "mcp_tool": f"mcp__{server}__{fn.name}"}
    return out


def sync():
    """Catalog = Second Brain (sursa canonică) ∪ skill-urile instalate local. Corpurile SKILL.md din
    Second Brain se descarcă incremental (după updated_at) ca agentul să le poată CITI ca fișier."""
    CACHE.mkdir(parents=True, exist_ok=True)
    lock = CACHE / ".sync.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 600:
        return
    lock.write_text(str(os.getpid()))
    try:
        cat = local_skills()
        cat.update(mcp_tools())
        old = {}
        if CATALOG.exists():
            try:
                old = {s["name"]: s for s in json.loads(CATALOG.read_text(encoding="utf-8"))}
            except Exception:
                old = {}
        try:
            remote = sb_call("GET", "/api/skills", timeout=30) or []
        except Exception:
            remote = []
        for s in remote:
            name = s.get("name")
            if not name or str(s.get("enabled", "True")) == "False":
                continue
            plugin, _, slug = name.partition(":")
            body_path = BODIES / (plugin or "_") / (slug or plugin) / "SKILL.md"
            prev = old.get(name) or {}
            if not body_path.exists() or prev.get("updated_at") != s.get("updated_at"):
                try:
                    full = sb_call("GET", f"/api/skills/{s['id']}", timeout=20) or {}
                    if full.get("body"):
                        body_path.parent.mkdir(parents=True, exist_ok=True)
                        body_path.write_text(full["body"], encoding="utf-8")
                except Exception:
                    pass
            if name in cat:
                cat[name]["in_sb"] = True
                cat[name]["updated_at"] = s.get("updated_at")
                if len(s.get("description") or "") > len(cat[name]["description"]):
                    cat[name]["description"] = s["description"]
                continue
            cat[name] = {
                "name": name,
                "description": s.get("description") or "",
                "installed": False,
                "in_sb": True,
                "path": str(body_path) if body_path.exists() else None,
                "updated_at": s.get("updated_at"),
            }
        tmp = CATALOG.with_suffix(".tmp")
        tmp.write_text(json.dumps(list(cat.values()), ensure_ascii=False), encoding="utf-8")
        tmp.replace(CATALOG)
    finally:
        try:
            lock.unlink()
        except Exception:
            pass


def spawn_sync():
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--sync"], **kw)
    except Exception:
        pass


def load_catalog():
    if CATALOG.exists():
        try:
            cat = json.loads(CATALOG.read_text(encoding="utf-8"))
            if time.time() - CATALOG.stat().st_mtime > SYNC_EVERY_S:
                spawn_sync()
            return cat
        except Exception:
            pass
    spawn_sync()
    return list(local_skills().values()) + list(mcp_tools().values())


# ---------------------------------------------------------------- scorare
def lexical(query, cat):
    q = toks(query)
    q += [syn for w in q for syn in SYNONYMS.get(w, [])]
    if not q:
        return []
    docs, slugs = [], []
    for s in cat:
        slug = s["name"].split(":")[-1].replace("-", " ")
        slugs.append(set(toks(slug)))
        # numele contează de 3 ori: „product-sales" trebuie să bată o descriere care doar pomenește vânzări
        docs.append(toks(f"{slug} {slug} {slug} {s['description']}"))
    n = len(docs)
    avg = sum(map(len, docs)) / max(n, 1)
    df = Counter(w for d in docs for w in set(d))
    out = []
    for s, d, sl in zip(cat, docs, slugs):
        if s["name"] in DEPRECATE or re.match(r"^\W*(⛔|DEPRECAT|COMASAT)", s.get("description") or "", re.I):
            continue  # skill comasat/șters — routerul nu trimite agenți spre el
        tf = Counter(d)
        sc, hits = 0.0, 0
        for w in set(q):
            if w in tf:
                idf = math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                sc += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(d) / avg))
                hits += 1
                if w in sl and len(w) > 2:
                    sc += 1.5 * idf  # termenul e chiar în numele skill-ului („tiktok" → tiktok-ads)
        # un singur cuvânt comun („scrie", „toamnă") nu e potrivire — cere 2 termeni sau numele skill-ului
        if sc > 0 and (hits >= 2 or {w for w in sl if len(w) > 2} & set(q)):
            out.append((sc, s["name"]))
    out.sort(reverse=True)
    return out[:15]


def semantic(query):
    try:
        res = sb_call("POST", "/api/skills/route", {"query": query[:1000], "threshold": 0.3}, timeout=SEMANTIC_TIMEOUT_S) or []
    except Exception:
        return []
    out = []
    for r in res if isinstance(res, list) else []:
        name = r.get("name") or r.get("skill")
        sc = r.get("score") or r.get("similarity") or 0
        if name:
            out.append((float(sc), name))
    return out[:15]


def rank(query, cat):
    lex, sem = lexical(query, cat), semantic(query)
    top_lex = lex[0][0] if lex else 0
    fused = Counter()
    keep = set()
    for i, (sc, n) in enumerate(lex):
        fused[n] += 1 / (60 + i)
        if top_lex >= 4.0 and sc >= 0.45 * top_lex:
            keep.add(n)
    sem = [(sc, n) for sc, n in sem if n not in DEPRECATE]
    for i, (sc, n) in enumerate(sem):
        fused[n] += 0.25 / (60 + i)  # semanticul e mai slab pe română (măsurat oct-2026) — cântărește mai puțin
        if sc >= 0.5:
            keep.add(n)
    by_name = {s["name"]: s for s in cat}
    return [by_name.get(n, {"name": n, "description": "", "installed": False})
            for n, _ in fused.most_common() if n in keep][:TOP_N]


def how_to_use(s):
    if s.get("mcp_tool"):
        return f"tool MCP → cheamă `{s['mcp_tool']}` (încarcă-l cu ToolSearch dacă e amânat)"
    if s.get("installed"):
        return "instalat → invocă-l cu Skill tool"
    if s.get("path"):
        return f"doar în Second Brain → citește {s['path']}; dacă are acțiuni: skill_info + run_skill"
    return "doar în Second Brain → second-brain skill_info / run_skill"


CONTINUARE = re.compile(r"^(si|și|dar|pai|păi|ok|bine|nu|da|deci|atunci|acum|mai|hai|vezi|zi|spune)\b", re.I)


def log_gap(prompt, session_id):
    """Golul ajunge în KB (events) prin același buffer pe care Stop hook-ul îl golește.
    Doar cereri de lucru: o replică scurtă sau de continuare („păi de unde să știu", „și 152?")
    nu e un skill lipsă — pe 19-oct 42 din 56 de goluri erau zgomot de felul ăsta."""
    words = re.findall(r"\w+", prompt)
    if len(words) < 6 or CONTINUARE.match(prompt.strip()):
        return
    stamp = CACHE / f".gap_{re.sub(r'[^A-Za-z0-9-]', '', session_id or 'nosession')[:64]}"
    try:
        if stamp.exists() and time.time() - stamp.stat().st_mtime < 1200:
            return
        CACHE.mkdir(parents=True, exist_ok=True)
        stamp.write_text("")
    except Exception:
        pass
    summary = re.sub(r"\d{6,}", "#", re.sub(r"\s+", " ", prompt))[:200]
    rec = {"ts": time.time(), "type": "skill_gap", "action": "no_match", "name": "skill-router", "summary": summary}
    try:
        buf = os.path.join(tempfile.gettempdir(), f"claude_kb_{session_id or 'nosession'}.jsonl")
        with open(buf, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def context_for(prompt, session_id=None):
    p = prompt.strip()
    if len(p) < 15 or p.startswith("/") or TRIVIAL.match(p) or p.startswith("<"):
        return None
    hits = rank(p, load_catalog())
    if not hits:
        log_gap(p, session_id)
        return ("[skill-router] Niciun skill al echipei nu se potrivește clar cu cererea. Rezolv-o tu "
                "(verifică întâi shared/HARTA.md / shared/CS.md). Dacă soluția e reutilizabilă, la final "
                "propune utilizatorului s-o salvezi ca skill (second-brain-skill).")
    lines = ["[skill-router] Skill-uri ale echipei potrivite pentru cererea asta — folosește-le ÎNAINTE "
             "să improvizezi query-uri/scripturi (ignoră-le dacă nu se potrivesc de fapt):"]
    for s in hits:
        desc = re.sub(r"\s+", " ", s.get("description") or "")
        desc = desc[:160] + ("…" if len(desc) > 160 else "")
        lines.append(f"- {s['name']} — {desc} [{how_to_use(s)}]")
    lines.append("Dacă niciunul nu acoperă cererea, rezolv-o tu; dacă soluția e reutilizabilă, propune "
                 "la final s-o salvezi ca skill (second-brain-skill).")
    return "\n".join(lines)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if "--sync" in sys.argv:
        sync()
        return
    if "--sync-if-stale" in sys.argv:  # SessionStart: niciodată blocant, sync-ul rulează detașat
        if not CATALOG.exists() or time.time() - CATALOG.stat().st_mtime > SYNC_EVERY_S:
            spawn_sync()
        return
    if "--test" in sys.argv:
        q = " ".join(sys.argv[sys.argv.index("--test") + 1:])
        print(context_for(q) or "(nimic — prompt trivial)")
        return
    try:
        data = json.load(sys.stdin)
        ctx = context_for(data.get("prompt") or "", data.get("session_id"))
    except Exception:
        return
    if ctx:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}},
                         ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
