# /// script
# requires-python = ">=3.10"
# dependencies = ["pyjwt"]
# ///
"""
scripts_cli.py — operează TOT dashboard-ul intern Scripturi (https://scripts.arona.ro) din terminal.

Al 5-lea app intern (după metrics-app / bi-grandia / tom / scentum). API FastAPI cu JWT.
NU stochează parolă: emite un token admin semnat cu secretul ECHIPEI din KB (JWT_SECRET_KEY),
exact secretul cu care serverul verifică semnătura. Toate cele 146 de mutații devin apelabile.

TOATE mutațiile sunt DRY-RUN implicit → scriu doar cu `--apply`.
Cele HIGH-RISK (DELETE, clear, cancel, send-to-tom, push-to-stores, download…) cer și `--confirm`.

AUTH (o dată pe sesiune — secretul nu se printează):
  KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
  export JWT_SECRET_KEY="$(uv run "$KB" secret-get JWT_SECRET_KEY)"

Comenzi:
  uv run scripts_cli.py areas                       # ariile funcționale + nr. de endpointuri
  uv run scripts_cli.py endpoints [area] [--mutations]
  uv run scripts_cli.py sig  <METHOD> <path>        # path-params + câmpurile de body (din manifest)
  uv run scripts_cli.py call <METHOD> <path> [--json '{...}'] [--query k=v] [--apply] [--confirm]
"""
import argparse, datetime, json, os, sys, urllib.request, urllib.error, urllib.parse

BASE = os.environ.get("SCRIPTS_BASE_URL", "https://scripts.arona.ro").rstrip("/")
MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "endpoints.json")
READ_METHODS = ("GET",)


def die(msg, code=1):
    print(f"✖ {msg}", file=sys.stderr)
    sys.exit(code)


def load_manifest():
    if not os.path.exists(MANIFEST):
        die(f"lipsește manifestul {MANIFEST}")
    return json.load(open(MANIFEST, encoding="utf-8"))


def token():
    sk = os.environ.get("JWT_SECRET_KEY")
    if not sk:
        die('JWT_SECRET_KEY lipsește. Rulează:\n'
            '  export JWT_SECRET_KEY="$(uv run <kb.py> secret-get JWT_SECRET_KEY)"')
    import jwt
    who = os.environ.get("SCRIPTS_USER", "claude-automation")
    return jwt.encode(
        {"sub": who, "role": "admin", "permissions": {},
         "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=15)},
        sk, algorithm="HS256")


def http(method, path, body=None, query=None):
    url = BASE + path
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token()}",
                                          "Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=180)
        raw = r.read().decode()
        try:
            return r.status, json.loads(raw)
        except Exception:
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:1500]


def find(eps, method, path):
    method = method.upper()
    # potrivire exacta, apoi pe sablon (path params {})
    for e in eps:
        if e["method"] == method and e["path"] == path:
            return e
    import re
    for e in eps:
        if e["method"] != method:
            continue
        rx = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", e["path"]) + "$"
        if re.match(rx, path):
            return e
    return None


def cmd_areas(a, eps):
    from collections import Counter
    m = Counter(e["area"] for e in eps if e["kind"] == "mutation")
    r = Counter(e["area"] for e in eps if e["kind"] == "read")
    hr = Counter(e["area"] for e in eps if e["risk"] == "high")
    for area in sorted(set(m) | set(r)):
        print(f"  {area:22} {m.get(area,0):>3} ✏️   {r.get(area,0):>3} 📖   "
              f"{('🔴 '+str(hr[area])) if hr.get(area) else ''}")
    print(f"\n  TOTAL: {sum(m.values())} mutații ✏️  · {sum(r.values())} citiri 📖  · "
          f"{sum(hr.values())} high-risk 🔴")
    print("  Detalii:  scripts_cli.py endpoints <area>")


def cmd_endpoints(a, eps):
    sel = [e for e in eps if (not a.area or e["area"] == a.area)
           and (not a.mutations or e["kind"] == "mutation")]
    if a.area and not sel:
        die(f"arie necunoscută: {a.area}. Vezi: scripts_cli.py areas")
    cur = None
    for e in sorted(sel, key=lambda x: (x["area"], x["path"])):
        if e["area"] != cur:
            cur = e["area"]
            print(f"\n{cur}")
        mark = "📖" if e["kind"] == "read" else ("🔴" if e["risk"] == "high" else "✏️ ")
        print(f"  {mark} {e['method']:6} {e['path']:50} {e['fn']}")
    print(f"\n{len(sel)} endpointuri. Ce cere unul:  scripts_cli.py sig <METHOD> <path>")


def cmd_sig(a, eps):
    e = find(eps, a.method, a.path)
    if not e:
        die(f"necunoscut: {a.method} {a.path}. Vezi: scripts_cli.py endpoints")
    mark = "📖 citire" if e["kind"] == "read" else ("🔴 MUTAȚIE high-risk" if e["risk"] == "high" else "✏️  mutație")
    print(f"\n{e['method']} {e['path']}   [{e['area']}.{e['fn']}]   {mark}\n")
    if e["path_params"]:
        print("  path params (pune-le direct în URL): " + ", ".join(e["path_params"]))
    if e["body_fields"]:
        print(f"  body JSON ({e['body_model']}):")
        for f in e["body_fields"]:
            d = f" = {f['default']}" if f.get("default") else ""
            print(f"     {f['name']}: {f['type']}{d}")
    if e["other_params"]:
        print("  alți parametri: " + ", ".join(
            f"{p['name']}: {p['type'] or 'any'}" for p in e["other_params"]))
    if not e["body_fields"] and not e["other_params"]:
        print("  (fără model de body detectat — trece un --json '{...}' brut dacă e nevoie)")


def cmd_call(a, eps):
    e = find(eps, a.method, a.path)
    if not e:
        die(f"necunoscut: {a.method} {a.path}. Vezi: scripts_cli.py endpoints")
    body = None
    if a.json:
        try:
            body = json.loads(a.json)
        except Exception:
            die("--json nu e JSON valid")
    query = dict(q.split("=", 1) for q in a.query) if a.query else None
    method = e["method"]

    if e["kind"] == "mutation":
        if not a.apply:
            print(f"DRY-RUN — aș trimite:\n  {method} {BASE}{a.path}")
            if body is not None: print(f"  body: {json.dumps(body, ensure_ascii=False)}")
            if query: print(f"  query: {query}")
            print(f"\n  Adaugă --apply ca să execut." +
                  ("  (⚠️ HIGH-RISK: cere și --confirm)" if e["risk"] == "high" else ""))
            if e["risk"] == "high":
                print("  " + _risk_note(a.path, method))
            return
        if e["risk"] == "high" and not a.confirm:
            die(f"🔴 {method} {a.path} e HIGH-RISK ({_risk_note(a.path, method)}). "
                f"Adaugă --confirm pe lângă --apply.")

    status, resp = http(method, a.path, body, query)
    ok = 200 <= status < 300
    print(f"{'✅' if ok else '✖'} HTTP {status}")
    print(json.dumps(resp, ensure_ascii=False, indent=2)[:6000] if not isinstance(resp, str) else resp[:6000])
    if not ok:
        sys.exit(1)


def _risk_note(path, method="POST"):
    p = path.lower()
    if method == "DELETE": return "ȘTERGERE (ireversibilă)"
    if "clear" in p: return "ȘTERGE datele de profit ale lunii"
    if "send-to-tom" in p: return "trimite comanda în TOM (WMS) — creează PO real"
    if "push-to-stores" in p or "generate-and-push" in p: return "scrie în magazinele Shopify"
    if "download" in p: return "marchează etichete ca descărcate → ies din coada de print"
    if "cancel" in p or "reject" in p: return "anulează / respinge (AWB/PO/comandă/coadă)"
    if "execute" in p or "split" in p: return "execută split/AWB real la curier"
    return "efect greu reversibil"


def main():
    eps = load_manifest()
    ap = argparse.ArgumentParser(description="Operează scripts.arona.ro din CLI (dry-run implicit).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("areas")
    g = sub.add_parser("endpoints"); g.add_argument("area", nargs="?"); g.add_argument("--mutations", action="store_true")
    g = sub.add_parser("sig"); g.add_argument("method"); g.add_argument("path")
    g = sub.add_parser("call")
    g.add_argument("method"); g.add_argument("path")
    g.add_argument("--json"); g.add_argument("--query", action="append", default=[])
    g.add_argument("--apply", action="store_true"); g.add_argument("--confirm", action="store_true")
    a = ap.parse_args()
    {"areas": cmd_areas, "endpoints": cmd_endpoints, "sig": cmd_sig, "call": cmd_call}[a.cmd](a, eps)


if __name__ == "__main__":
    main()
