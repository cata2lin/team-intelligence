# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# -*- coding: utf-8 -*-
"""Un server MCP nu trebuie sa se blocheze cand porneste un subproces.

Incidentul (Anne, 22-oct-2026, Windows): dupa ce installerul a inceput sa inregistreze MCP-urile pe toate masinile,
`arona-stock`, `arona-depozit` si `arona-orderhub` se blocau LA INFINIT — nu raspundeau nici cu eroare. Cauza: un
server MCP vorbeste pe STDIO, iar `subprocess.run(...)` fara `stdin=` ii da copilului stdin-ul SERVERULUI. Copilul
(`uv run kb.py secret-get ...`) il tine deschis sau inghite octeti din protocol, deci cererea urmatoare nu mai
ajunge niciodata la server.

Trei straturi, de la ieftin la scump:
  A. GARDA STATICA — fiecare apel de subproces din inchiderea de importuri a serverelor MCP are `stdin=`. Fara
     retea, fara `uv`: asta e plasa care prinde regresia la urmatorul server MCP scris.
  B. REPRODUCERE — un server stdio minimal, scris de test, care porneste un copil ce citeste stdin pana la EOF.
     Varianta fara `stdin=DEVNULL` NU mai raspunde (bug-ul, cu dinti), cea cu DEVNULL raspunde. Fara retea.
  C. LIVE — porneste serverele MCP adevarate cu stdin DESCHIS (pipe, nu DEVNULL), face `initialize` + `tools/list`
     si cheama o unealta care CHIAR citeste un secret din KB. Cere `uv` si reteaua; se sare singur daca lipsesc.

Rulare: `uv run plugins/core/scripts/test_mcp_stdin.py` (doar A+B) · `MCP_STDIN_LIVE=1 uv run …` (si C).
"""
import ast
import io
import json
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAD = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
METODE = {"run", "Popen", "check_output", "check_call", "call"}
MODULE = {"subprocess", "sp", "sub"}
# serverele pe care s-a vazut blocajul + cate o unealta ieftina care trece prin `kb.py secret-get`
LIVE = [
    ("plugins/gigi/skills/arona-stock/mcp_server.py", "stoc_produs", {"sku": "HA-0002"}),
    ("plugins/gigi/skills/order-hub/mcp_server.py", "oh_comanda", {"comanda": "GT000001"}),
    ("plugins/gigi/skills/arona-depozit/mcp_server.py", "print_coada", {"zile": 1}),
]

# EXCEPTIE, si e importanta: lansatoarele care PORNESC serverul MCP (`pg_mcp_launch`, `npx_mcp_launch` — ramura
# de Windows, unde nu exista `os.execvp`) trebuie sa-i dea copilului stdin-ul NOSTRU: copilul ALA e serverul MCP,
# iar `stdin=DEVNULL` i-ar taia canalul si ar omori toate MCP-urile `postgres-*` pe Windows. Deci regula e „orice
# subproces AJUTATOR primeste DEVNULL", nu „orice subproces".
SCUTITE = {
    ("plugins/core/scripts/pg_mcp_launch.py", "run"): "copilul npx E serverul MCP",
    ("plugins/library/scripts/npx_mcp_launch.py", "run"): "copilul npx E serverul MCP",
}

erori = []
# apelurile la care cheile vin prin `**{...}`: nu se pot verifica pe AST, deci se listeaza la rulare
NEVERIFICABILE: list = []


def ok(nume, cond, detaliu=""):
    print(("  OK   " if cond else "  PICA ") + nume + (" — " + detaliu if detaliu else ""))
    if not cond:
        erori.append(nume + (" — " + detaliu if detaliu else ""))


# ───────────────────────── A. garda statica ─────────────────────────
def _intrari():
    out = []
    for d, _, fs in os.walk(os.path.join(RAD, "plugins")):
        for f in fs:
            if f == "mcp_server.py" or f.endswith("_mcp_launch.py") or f.endswith("_mcp.py"):
                out.append(os.path.join(d, f))
    return sorted(set(out))


def _importuri_locale(cale):
    try:
        arb = ast.parse(io.open(cale, encoding="utf-8").read())
    except Exception:
        return []
    nume = set()
    for n in ast.walk(arb):
        if isinstance(n, ast.Import):
            nume.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            nume.add(n.module.split(".")[0])
    aici = os.path.dirname(cale)
    caute = [aici, os.path.join(aici, "scripts"), os.path.join(RAD, "plugins/core/scripts"),
             os.path.join(RAD, "plugins/library/scripts"), os.path.join(RAD, "shared/scripturi-tools")]
    gasite = []
    for nm in nume:
        for d in caute:
            p = os.path.join(d, nm + ".py")
            if os.path.exists(p):
                gasite.append(os.path.realpath(p))
                break
    return gasite


def inchiderea_mcp():
    """Tot ce se EXECUTA in procesul unui server MCP: serverele + modulele locale pe care le importa."""
    vazut, coada = set(), list(_intrari())
    while coada:
        p = os.path.realpath(coada.pop())
        if p in vazut or not p.endswith(".py"):
            continue
        vazut.add(p)
        coada.extend(_importuri_locale(p))
    return sorted(vazut)


def apeluri_fara_stdin(cale):
    """Apelurile de subproces fara `stdin=`. Pe AST, nu pe regex: in `arona-ops/mcp_server.py` exista un
    `subprocess.run` scris INTR-UN STRING, rulat pe VPS prin ssh — acolo stdin-ul nu e canalul nostru."""
    try:
        arb = ast.parse(io.open(cale, encoding="utf-8").read())
    except SyntaxError:
        return ["(nu se parseaza)"]
    rele, neverificabile = [], NEVERIFICABILE
    for n in ast.walk(arb):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr in METODE
                and isinstance(f.value, ast.Name) and f.value.id in MODULE):
            continue
        if (os.path.relpath(cale, RAD), f.attr) in SCUTITE:
            continue
        chei = {k.arg for k in n.keywords if k.arg}
        # `**{...}` (keyword cu arg None) = cheile se decid la rulare, deci AST-ul nu le vede. Tiparul e legitim
        # acolo unde `stdin=` si `input=` se exclud (`arona-marketplace`: ori trimiti text, ori inchizi stdin),
        # deci nu e eroare — dar se RAPORTEAZA, ca sa nu se ascunda nimic in spatele lui.
        if any(k.arg is None for k in n.keywords):
            neverificabile.append("linia %d: %s.%s( prin **kwargs" % (n.lineno, f.value.id, f.attr))
            continue
        if "stdin" not in chei:
            rele.append("linia %d: %s.%s(" % (n.lineno, f.value.id, f.attr))
    return rele


def apeluri_cu_stdin_si_input(cale):
    """Apelurile care dau `stdin=` ȘI `input=` — subprocess le refuza: „stdin and input arguments may not both
    be used".

    De ce exista testul: fixul din 22-oct a adaugat `stdin=DEVNULL` peste un `input=stdin` existent
    (`arona-marketplace/mcp_server.py`) si a picat `mkt_comenzi`. Garda de atunci cerea doar PREZENTA lui
    `stdin=`, deci n-a vazut coliziunea — o garda care verifica forma, nu efectul.
    `input=` inseamna deja stdin, deci acolo NU se mai pune `stdin=`.
    """
    try:
        arb = ast.parse(io.open(cale, encoding="utf-8").read())
    except SyntaxError:
        return []
    rele = []
    for n in ast.walk(arb):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if not (isinstance(f, ast.Attribute) and f.attr in METODE
                and isinstance(f.value, ast.Name) and f.value.id in MODULE):
            continue
        chei = {k.arg for k in n.keywords if k.arg}
        if "stdin" in chei and "input" in chei:
            rele.append("linia %d: %s.%s( stdin= SI input=" % (n.lineno, f.value.id, f.attr))
    return rele


def test_niciun_apel_nu_da_stdin_si_input():
    """Pe TOT repo-ul, nu doar pe inchiderea MCP: coliziunea crapa la rulare, oriunde e."""
    import glob
    rele = {}
    for p in sorted(glob.glob(os.path.join(RAD, "plugins/**/*.py"), recursive=True)
                    + glob.glob(os.path.join(RAD, "shared/**/*.py"), recursive=True)):
        r = apeluri_cu_stdin_si_input(p)
        if r:
            rele[os.path.relpath(p, RAD)] = r
    ok("niciun apel cu stdin= si input= deodata", not rele, str(rele)[:400])


def test_garda_statica():
    print("A. garda statica (fara retea)")
    fise = inchiderea_mcp()
    ok("inchiderea MCP e gasita", len(fise) >= 20, "%d fisiere" % len(fise))
    for p in ("plugins/gigi/skills/arona-stock/mcp_server.py",
              "plugins/gigi/skills/arona-depozit/depozit.py",
              "plugins/gigi/skills/order-hub/mcp_server.py",
              "plugins/core/scripts/arona_pg.py"):
        ok("in inchidere: " + p, os.path.realpath(os.path.join(RAD, p)) in fise)
    rele = {}
    for p in fise:
        r = apeluri_fara_stdin(p)
        if r:
            rele[os.path.relpath(p, RAD)] = r
    ok("niciun subproces fara stdin= in serverele MCP", not rele,
       "; ".join("%s %s" % (k, v) for k, v in rele.items())[:600])
    if NEVERIFICABILE:
        print("  nota  %d apel(uri) cu chei prin **kwargs (verificate de mana): %s"
              % (len(NEVERIFICABILE), "; ".join(NEVERIFICABILE)[:300]))
    # timeout: un copil fara stdin, dar fara termen, tot poate atarna
    fara_tm = {}
    for p in fise:
        try:
            arb = ast.parse(io.open(p, encoding="utf-8").read())
        except SyntaxError:
            continue
        for n in ast.walk(arb):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ("run", "check_output", "check_call", "call") \
                    and isinstance(n.func.value, ast.Name) and n.func.value.id in MODULE:
                if (os.path.relpath(p, RAD), n.func.attr) in SCUTITE:
                    continue
                if "timeout" not in {k.arg for k in n.keywords if k.arg}:
                    fara_tm.setdefault(os.path.relpath(p, RAD), []).append(n.lineno)
    ok("fiecare subproces blocant are timeout=", not fara_tm, str(fara_tm)[:400])


# ───────────────────────── B. reproducere ─────────────────────────
def test_lansatoarele_nu_primesc_devnull():
    """Oglinda gardii: `stdin=DEVNULL` pe lansator = toate MCP-urile `postgres-*` moarte pe Windows."""
    for rel, _m in SCUTITE:
        t = io.open(os.path.join(RAD, rel), encoding="utf-8").read()
        ok("%s lasa copilul sa mosteneasca stdin" % rel.split("/")[-1],
           "subprocess.run(argv)" in t and "subprocess.run(argv, stdin" not in t)


SERVER_FALS = r'''
import json, subprocess, sys
DEVNULL = %s
while True:
    linie = sys.stdin.readline()
    if not linie:
        break
    cer = json.loads(linie)
    kw = {"capture_output": True, "text": True}
    if DEVNULL:
        kw["stdin"] = subprocess.DEVNULL
    # exact ce face un server al nostru: porneste un copil (la noi: `uv run kb.py secret-get`).
    # Copilul de aici citeste stdin pana la EOF — adica, fara DEVNULL, INGHITE cererea urmatoare.
    subprocess.run([sys.executable, "-c", "import sys; sys.stdin.read()"], timeout=20, **kw)
    sys.stdout.write(json.dumps({"id": cer["id"], "ok": True}) + "\n")
    sys.stdout.flush()
'''


def _raspunde(devnull, cereri=3, termen=12):
    """Cate cereri raspunde un server stdio care porneste cate un copil, cu/fara DEVNULL."""
    d = tempfile.mkdtemp(prefix="mcp-stdin-")
    cale = os.path.join(d, "server_fals.py")
    io.open(cale, "w", encoding="utf-8").write(SERVER_FALS % ("True" if devnull else "False"))
    p = subprocess.Popen([sys.executable, cale], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
    n = 0
    try:
        for i in range(cereri):
            # TOATE cererile se trimit o data, ca un client MCP real care nu asteapta ping-pong
            p.stdin.write(json.dumps({"id": i}) + "\n")
        p.stdin.flush()
        t0 = time.time()
        while n < cereri and time.time() - t0 < termen:
            import selectors
            s = selectors.DefaultSelector()
            s.register(p.stdout, selectors.EVENT_READ)
            if not s.select(timeout=max(0.2, termen - (time.time() - t0))):
                break
            if not p.stdout.readline():
                break
            n += 1
    finally:
        p.kill()
    return n


def test_reproducerea():
    print("B. reproducerea (fara retea)")
    cu = _raspunde(True)
    ok("cu stdin=DEVNULL serverul raspunde la toate cererile", cu == 3, "%d/3" % cu)
    fara = _raspunde(False)
    ok("fara stdin= serverul se BLOCHEAZA (testul are dinti)", fara < 3, "%d/3 raspunsuri" % fara)


# ───────────────────────── C. serverele adevarate ─────────────────────────
def _rpc(srv, tool, args, termen=90):
    """Porneste serverul MCP cu stdin DESCHIS (pipe), ca Claude Code, si cheama o unealta."""
    p = subprocess.Popen(["uv", "run", srv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=RAD)

    def trimite(o):
        p.stdin.write(json.dumps(o) + "\n")
        p.stdin.flush()

    try:
        trimite({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                            "clientInfo": {"name": "test-mcp-stdin", "version": "0"}}})
        t0 = time.time()
        if not p.stdout.readline():
            return None, 0, "serverul n-a raspuns la initialize"
        trimite({"jsonrpc": "2.0", "method": "notifications/initialized"})
        trimite({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        unelte = json.loads(p.stdout.readline()).get("result", {}).get("tools", [])
        trimite({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": tool, "arguments": args}})
        linie = p.stdout.readline()
        if not linie:
            return None, len(unelte), "serverul a tacut dupa tools/call"
        return json.loads(linie), len(unelte), "%.1fs" % (time.time() - t0)
    except Exception as e:
        return None, 0, "%s: %s" % (type(e).__name__, e)
    finally:
        p.kill()


def test_live():
    print("C. serverele MCP adevarate, cu stdin deschis")
    if not os.environ.get("MCP_STDIN_LIVE"):
        print("  (sarit: pune MCP_STDIN_LIVE=1; cere `uv` + retea + secrete din KB)")
        return
    for rel, tool, args in LIVE:
        cale = os.path.join(RAD, rel)
        if not os.path.exists(cale):
            print("  (sarit, lipseste: %s)" % rel)
            continue
        rasp, n, nota = _rpc(cale, tool, args)
        # ORICE raspuns e bun: inseamna ca serverul a pornit copilul pentru secret si a revenit la protocol.
        # O eroare de date (comanda inexistenta, DB picat) nu e blocaj — blocajul e TACEREA.
        ok("%s raspunde la %s cu stdin deschis" % (rel.split("/")[-2], tool),
           rasp is not None and n > 0, nota)


if __name__ == "__main__":
    test_garda_statica()
    test_niciun_apel_nu_da_stdin_si_input()
    test_lansatoarele_nu_primesc_devnull()
    test_reproducerea()
    test_live()
    print("\n%s" % ("TOATE TRECE" if not erori else "PICA %d: %s" % (len(erori), " | ".join(erori))))
    sys.exit(1 if erori else 0)
