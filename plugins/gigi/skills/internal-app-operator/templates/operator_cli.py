# /// script
# requires-python = ">=3.10"
# dependencies = ["pyjwt"]
# ///
"""
operator_cli.py — TEMPLATE de CLI operator pentru un app intern HTTP (FastAPI/Express).

Copiază-l în skill-ul tău, redenumește-l (<app>_cli.py) și adaptează DOAR:
  1. AUTH (funcția auth_header) — vezi cele 3 moduri de mai jos.
  2. BASE (URL-ul app-ului) și numele env-urilor.
  3. HIGH_RISK / _risk_note — clasificarea operațiunilor periculoase pt app-ul tău.
Restul (areas/endpoints/sig/call, dry-run implicit) rămâne la fel.

Manifestul `endpoints.json` se generează cu gen_manifest_fastapi.py (AST) și se livrează în skill.

AUTH (secretul vine din KB, env-first, NICIODATĂ printat):
  KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
  export <APP>_SECRET="$(uv run "$KB" secret-get <NUMELE_SECRETULUI>)"
"""
import argparse, datetime, json, os, re, sys, urllib.request, urllib.error, urllib.parse

# ── ADAPTEAZĂ ────────────────────────────────────────────────────────────────
BASE = os.environ.get("OPERATOR_BASE_URL", "https://app.intern.example").rstrip("/")
AUTH_MODE = os.environ.get("OPERATOR_AUTH", "jwt")   # "jwt" | "bearer" | "cookie"

def auth_header():
    """Întoarce headerele de auth. Alege UN mod și șterge-le pe celelalte."""
    if AUTH_MODE == "jwt":
        # App-ul verifică DOAR semnătura JWT (middleware jwt.decode(token, SECRET)) → emitem local.
        sk = os.environ.get("OPERATOR_JWT_SECRET") or _need_secret("OPERATOR_JWT_SECRET")
        import jwt
        who = os.environ.get("OPERATOR_USER", "claude-automation")
        tok = jwt.encode({"sub": who, "role": "admin", "permissions": {},
                          "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)},
                         sk, algorithm="HS256")
        return {"Authorization": f"Bearer {tok}"}
    if AUTH_MODE == "bearer":
        return {"Authorization": f"Bearer {os.environ.get('OPERATOR_TOKEN') or _need_secret('OPERATOR_TOKEN')}"}
    if AUTH_MODE == "cookie":
        return {"Cookie": os.environ.get("OPERATOR_COOKIE") or _need_secret("OPERATOR_COOKIE")}
    die(f"OPERATOR_AUTH necunoscut: {AUTH_MODE}")

def _need_secret(name):
    die(f'{name} lipsește. export {name}="$(uv run <kb.py> secret-get <cheia din KB>)"')

HIGH_RISK = re.compile(r"(delete|clear|cancel|send-to|push-to|download|remap|execute|reset|reject|void|storno)", re.I)

def _risk_note(path, method="POST"):
    if method == "DELETE": return "ȘTERGERE (ireversibilă)"
    p = path.lower()
    if "clear" in p: return "șterge date"
    if "send-to" in p: return "trimite în alt sistem (creează efect real)"
    if "push-to" in p: return "scrie în alt sistem (ex. Shopify)"
    if "download" in p: return "marchează ca descărcat → iese din coadă"
    if "cancel" in p or "reject" in p: return "anulează / respinge"
    return "efect greu reversibil"
# ─────────────────────────────────────────────────────────────────────────────

MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "endpoints.json")

def die(msg, code=1):
    print(f"✖ {msg}", file=sys.stderr); sys.exit(code)

def load_manifest():
    if not os.path.exists(MANIFEST): die(f"lipsește manifestul {MANIFEST} (rulează gen_manifest_fastapi.py)")
    return json.load(open(MANIFEST, encoding="utf-8"))

def http(method, path, body=None, query=None):
    url = BASE + path
    if query: url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", **auth_header()}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=180); raw = r.read().decode()
        try: return r.status, json.loads(raw)
        except Exception: return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:1500]

def find(eps, method, path):
    method = method.upper()
    for e in eps:
        if e["method"] == method and e["path"] == path: return e
    for e in eps:
        if e["method"] == method and re.match("^" + re.sub(r"\{[^}]+\}", r"[^/]+", e["path"]) + "$", path):
            return e
    return None

def cmd_areas(a, eps):
    from collections import Counter
    m = Counter(e["area"] for e in eps if e["kind"] == "mutation")
    r = Counter(e["area"] for e in eps if e["kind"] == "read")
    hr = Counter(e["area"] for e in eps if e.get("risk") == "high")
    for area in sorted(set(m) | set(r)):
        print(f"  {area:22} {m.get(area,0):>3} ✏️   {r.get(area,0):>3} 📖   {('🔴 '+str(hr[area])) if hr.get(area) else ''}")
    print(f"\n  TOTAL: {sum(m.values())} mutații ✏️  · {sum(r.values())} citiri 📖  · {sum(hr.values())} high-risk 🔴")

def cmd_endpoints(a, eps):
    sel = [e for e in eps if (not a.area or e["area"] == a.area) and (not a.mutations or e["kind"] == "mutation")]
    if a.area and not sel: die(f"arie necunoscută: {a.area}")
    cur = None
    for e in sorted(sel, key=lambda x: (x["area"], x["path"])):
        if e["area"] != cur: cur = e["area"]; print(f"\n{cur}")
        mark = "📖" if e["kind"] == "read" else ("🔴" if e.get("risk") == "high" else "✏️ ")
        print(f"  {mark} {e['method']:6} {e['path']:50} {e['fn']}")
    print(f"\n{len(sel)} endpointuri. Ce cere unul:  sig <METHOD> <path>")

def cmd_sig(a, eps):
    e = find(eps, a.method, a.path)
    if not e: die(f"necunoscut: {a.method} {a.path}")
    kind = "📖 citire" if e["kind"] == "read" else ("🔴 high-risk" if e.get("risk") == "high" else "✏️  mutație")
    print(f"\n{e['method']} {e['path']}   [{e['area']}.{e['fn']}]   {kind}\n")
    if e.get("path_params"): print("  path params: " + ", ".join(e["path_params"]))
    if e.get("body_fields"):
        print(f"  body JSON ({e.get('body_model')}):")
        for f in e["body_fields"]:
            print(f"     {f['name']}: {f['type']}" + (f" = {f['default']}" if f.get("default") else ""))
    if not e.get("body_fields") and not e.get("other_params"):
        print("  (fără model de body — trece --json '{...}' brut dacă e nevoie)")

def cmd_call(a, eps):
    e = find(eps, a.method, a.path)
    if not e: die(f"necunoscut: {a.method} {a.path}")
    body = json.loads(a.json) if a.json else None
    query = dict(q.split("=", 1) for q in a.query) if a.query else None
    method = e["method"]
    if e["kind"] == "mutation":
        if not a.apply:
            print(f"DRY-RUN — aș trimite:\n  {method} {BASE}{a.path}")
            if body is not None: print(f"  body: {json.dumps(body, ensure_ascii=False)}")
            if query: print(f"  query: {query}")
            print("\n  Adaugă --apply." + ("  (🔴 HIGH-RISK: cere și --confirm: " + _risk_note(a.path, method) + ")" if e.get("risk") == "high" else ""))
            return
        if e.get("risk") == "high" and not a.confirm:
            die(f"🔴 high-risk ({_risk_note(a.path, method)}). Adaugă --confirm pe lângă --apply.")
    status, resp = http(method, a.path, body, query)
    ok = 200 <= status < 300
    print(f"{'✅' if ok else '✖'} HTTP {status}")
    print(resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False, indent=2)[:6000])
    if not ok: sys.exit(1)

def main():
    eps = load_manifest()
    ap = argparse.ArgumentParser(description="Operator CLI pt un app intern (dry-run implicit).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("areas")
    g = sub.add_parser("endpoints"); g.add_argument("area", nargs="?"); g.add_argument("--mutations", action="store_true")
    g = sub.add_parser("sig"); g.add_argument("method"); g.add_argument("path")
    g = sub.add_parser("call"); g.add_argument("method"); g.add_argument("path")
    g.add_argument("--json"); g.add_argument("--query", action="append", default=[])
    g.add_argument("--apply", action="store_true"); g.add_argument("--confirm", action="store_true")
    a = ap.parse_args()
    {"areas": cmd_areas, "endpoints": cmd_endpoints, "sig": cmd_sig, "call": cmd_call}[a.cmd](a, eps)

if __name__ == "__main__":
    main()
