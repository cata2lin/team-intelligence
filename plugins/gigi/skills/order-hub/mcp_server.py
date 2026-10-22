# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2"]
# ///
"""arona-orderhub MCP — Order Hub (orderhub.arona.ro) = SURSA DE ADEVĂR pt comenzi și statusuri.

Strat SUBȚIRE peste scripturile testate din folderul ăsta: `order_hub.py` (clientul /api/cs, testat de
`test_order_hub.py`) și `scripts/oh_delivery_rate.py` (citire din DB-ul OH prin ssh + docker exec, doar SELECT).
Citirile sunt libere; acțiunile au PROBĂ (nu scrie) și EXECUȚIE (cere confirmarea din probă + acordul omului).
Cheia OH_CS_SB_TOKEN vine din env sau din KB (kb.py) și nu se printează niciodată.

Register: claude mcp add --scope user arona-orderhub -- uv run <abs path>/mcp_server.py
"""
import json
import os
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
OH = os.path.join(HERE, "order_hub.py")
RATA = os.path.join(HERE, "scripts", "oh_delivery_rate.py")
CITIRI = {"actiuni", "comanda", "operatie", "neplecate"}


def _kb(k):
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL).stdout.strip()
    except Exception:
        return ""


# CARE CHEIE, ÎN CE ORDINE (21-oct-2026, când MCP-ul a ajuns pe mașinile echipei)
#   1. `OH_CS_SB_TOKEN` din ENV — mașinile CS o pun explicit; are scopurile de SCRIERE;
#   2. cheia OMULUI de pe mașina asta, din seif: `OH_CS_TOKEN_<handle>` (handle-ul din `kb.py whoami`). Așa
#      istoricul comenzii arată cine a apăsat, iar revocarea e pe un singur om;
#   3. `OH_CS_SB_TOKEN` din seif — comportamentul de până acum, ca mașinile CS de azi să nu se strice;
#   4. `OH_TEAM_READ_TOKEN` din seif — DOAR `cs.citire`.
# Cu o cheie de citire, `oh_proba` și `oh_executa` nu se înregistrează deloc: nu apar în lista de unelte a
# sesiunii. Altfel omul le-ar vedea, le-ar chema și ar primi 403 — același refuz, dar după ce a crezut că poate.
#
# ⚠️ ONEST: gardul ăsta e o CONVENȚIE, nu o barieră. `kb.py secret-get` citește `SELECT value FROM secrets WHERE
# key = …`, fără nicio filtrare pe om, deci orice mașină cu bootstrap-ul de KB poate lua ORICE cheie, inclusiv pe
# cea de scriere. Bariera reală se face doar scoțând cheia comună din seif (să stea doar în env-ul mașinilor CS)
# sau filtrând secretele pe om în KB. Decizia e a ownerului; aici doar nu dăm scrierea din greșeală.
def _handle():
    try:
        out = subprocess.run(["uv", "run", KB, "whoami"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL).stdout
        for bucata in out.split("|"):
            if "employee:" in bucata:
                return bucata.split(":", 1)[1].strip().lower()
    except Exception:
        pass
    return ""


def _alege_cheia():
    """(token, poate_scrie) — vezi ordinea de mai sus. Nu printează niciodată cheia."""
    t = os.environ.get("OH_CS_SB_TOKEN")
    if t:
        return t, True
    h = _handle()
    if h:
        t = _kb("OH_CS_TOKEN_%s" % h.upper())
        if t:
            return t, True
    t = _kb("OH_CS_SB_TOKEN")
    if t:
        return t, True
    return (os.environ.get("OH_TEAM_READ_TOKEN") or _kb("OH_TEAM_READ_TOKEN") or ""), False


_TOKEN, POATE_SCRIE = _alege_cheia()
if _TOKEN:
    os.environ["OH_CS_SB_TOKEN"] = _TOKEN          # clientul `order_hub.py` citește o singură variabilă


def _env():
    e = dict(os.environ); e.pop("VIRTUAL_ENV", None); e["PYTHONIOENCODING"] = "utf-8"; return e


from mcp.server.fastmcp import FastMCP

mcp = FastMCP("arona-orderhub")


def _run(script, args, timeout=180, executie=False):
    try:
        r = subprocess.run(["uv", "run", "-q", script] + args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=_env(), timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        sfat = ("⚠ Execuția a depășit %d s: POATE să fi fost făcută. NU face probă nouă: repetă IDENTIC (aceeași "
                "confirmare) sau verifică cu oh_operatie / oh_comanda." % timeout) if executie else \
               "⚠ A depășit %d s (nimic scris: e o citire / o probă)." % timeout
        return ((out.strip() + "\n") if out.strip() else "") + sfat
    out = (r.stdout or "").strip() or "(fără output)"
    if r.returncode not in (0, None):
        out += "\n[cod de ieșire %d]" % r.returncode
        if r.stderr and r.stderr.strip():
            out += " " + r.stderr.strip()[:400]
    return out


def _parametri(params_json):
    """params_json → flag-uri order_hub.py. Listele/obiectele pleacă JSON; bool → da/nu (order_hub le convertește
    după schema OH, fără ghicit)."""
    if not (params_json or "").strip():
        return []
    p = json.loads(params_json)
    if not isinstance(p, dict):
        raise ValueError("params_json trebuie să fie un obiect JSON, ex. {\"colete\": 2}")
    a = []
    for k, v in p.items():
        k = str(k).strip().lstrip("-")
        if k in ("apply", "confirmare", "dry_run", "utilizator", "comanda", "motiv", "peste", "magazin", "agent"):
            raise ValueError("%s nu se dă în params_json: are câmp propriu în tool" % k)
        if isinstance(v, bool):
            v = "da" if v else "nu"
        elif isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        a += ["--" + k.replace("_", "-"), str(v)]
    return a


def _actiune(actiune, comanda, utilizator, motiv, params_json, magazin, peste, confirmare=""):
    actiune = (actiune or "").strip()
    if actiune.endswith("-apply"):
        raise ValueError("dă numele acțiunii fără „-apply” (ex. „anulare”)")
    if not actiune or actiune in CITIRI or actiune.startswith("-"):
        raise ValueError("acțiune invalidă: %r · vezi oh_actiuni" % actiune)
    a = [actiune]
    for flag, v in (("--comanda", comanda), ("--magazin", magazin), ("--utilizator", utilizator),
                    ("--motiv", motiv), ("--peste", peste), ("--confirmare", confirmare)):
        if (v or "").strip():
            a += [flag, v.strip()]
    return a + _parametri(params_json)


# ─────────── CITIRI ───────────
@mcp.tool()
def oh_comanda(comanda: str, magazin: str = "") -> str:
    """Fișa comenzii din Order Hub (sursa de adevăr, NU AWBprint): status, etichete/AWB, hold-uri, tichet CS, facturi
    și ce acțiuni se pot face acum. comanda = numărul (ex. GT000001); magazin doar dacă OH zice că e ambiguă."""
    return _run(OH, ["comanda", "--comanda", comanda] + (["--magazin", magazin] if magazin else []))


@mcp.tool()
def oh_actiuni() -> str:
    """Lista acțiunilor Order Hub cu parametrii EXACȚI (nume, tip, obligatoriu `nume` vs opțional `nume?`).
    Citește-o înainte de oh_proba ca să construiești params_json corect."""
    return _run(OH, ["actiuni"])


@mcp.tool()
def oh_operatie(id: int) -> str:
    """Rezultatul păstrat al unei execuții (după un timeout / răspuns pierdut). Cod 0 = făcut, 4 = parțial,
    5 = încă în lucru / necunoscut."""
    return _run(OH, ["operatie", "--id", str(int(id))])


@mcp.tool()
def oh_rata_livrare(since: str = "2026-07", store: str = "", min_orders: int = 50, age: int = 14,
                    weeks: int = 8) -> str:
    """Rata de livrare (livrate / livrate+refuzate) pe LUNĂ × MAGAZIN din Order Hub, din colete, plus cohorte
    săptămânale la VÂRSTĂ EGALĂ (age zile) — singura comparație corectă între perioade (luna curentă pare mereu
    mai bună). since = YYYY-MM (statusuri doar din 2026-07); store = filtru pe nume (substring). Doar citire."""
    a = ["--since", since, "--min-orders", str(min_orders), "--age", str(age), "--weeks", str(weeks)]
    if store:
        a += ["--store", store]
    return _run(RATA, a, timeout=330)


@mcp.tool()
def oh_neplecate_pe_sku(sku: str, magazin: str = "", limita: int = 200) -> str:
    """Comenzile NEPLECATE care conțin un SKU (sau un cod de bare), din Order Hub — NU din AWBprint. Doar citire.
    „Neplecată" = exact filtrul „Neplecate" din OH: neanulată, niciun colet plecat, etichetele vii nepreluate de curier;
    fără etichetă, nici Shopify n-o știe expediată sau arhivată. Fără comenzile demo.
    sku = SKU-ul (fără majuscule, merge și SKU-ul unui set nativ Shopify) sau un cod de bare (8-14 cifre, tradus în
    SKU-uri); magazin = opțional: numele EXACT din OH (ex. „MagDeal.ro"), id-ul sau domeniul .myshopify;
    limita = câte comenzi listează (1-1000; totalurile sunt complete).
    Întoarce totalul, pe magazin (fără AWB / AWB nepreluat / pe hold) și pe comandă: număr, data, stare, bucăți, AWB,
    hold + motivul tichetului CS. Folosește-l la „ce comenzi așteaptă produsul X", stoc 0, fantomă pe raft."""
    a = ["neplecate", "--sku", sku, "--limita", str(max(1, min(int(limita or 200), 1000)))]
    if magazin:
        a += ["--magazin", magazin]
    return _run(OH, a, timeout=120)


# ─────────── ACȚIUNI (probă → om → execuție) ───────────
# PROBA și EXECUȚIA se înregistrează DOAR cu cheia de scriere (vezi POATE_SCRIE). Pe o mașină cu cheia
# de citire, uneltele astea nu există — nu sunt doar refuzate.
if POATE_SCRIE:
    @mcp.tool()
    def oh_proba(actiune: str, comanda: str, utilizator: str, params_json: str = "{}", motiv: str = "",
                 magazin: str = "") -> str:
        """PROBA unei acțiuni Order Hub — NU scrie nimic. Întoarce plan, blocaje (clasa imposibil/politica),
        peste_necesar și confirmare (valabilă 15 min). actiune = nume din oh_actiuni (ex. anulare, refacere, adresa,
        oprire, eliberare, factura, rambursare); utilizator = numele omului de la CS, cum îl spune el (nu-l alege tu);
        params_json = parametrii acțiunii ca obiect JSON, ex. {"colete": 2}. comanda-noua: comanda="" și magazin=..."""
        try:
            a = _actiune(actiune, comanda, utilizator, motiv, params_json, magazin, "")
        except ValueError as e:
            return "parametru: %s · nimic trimis" % e
        return _run(OH, a, timeout=150)


    @mcp.tool()
    def oh_executa(actiune: str, comanda: str, utilizator: str, motiv: str, confirmare: str, params_json: str = "{}",
                   peste: str = "", magazin: str = "") -> str:
        """EXECUȚIA unei acțiuni Order Hub (<actiune>-apply). Cheam-o DOAR după ce ai arătat omului planul și blocajele
        din oh_proba și ți-a dat OK explicit — serverul nu mai cere aprobare, tu ești poarta. Aceiași parametri ca la
        probă + motiv + confirmare (din probă); peste = exact codurile din peste_necesar (virgulă), doar cu acordul
        omului; un blocaj `imposibil` nu se trece. ⚠️ Banii (factura, factura-storno, rambursare, ramburs-platit) sunt
        IREVERSIBILI. Cod 2 plan_schimbat/expirata = probă nouă; 4 = parțial, NU repeta; 5 = repetă IDENTIC sau
        oh_operatie, NU probă nouă."""
        if not (confirmare or "").strip():
            return "confirmare: lipsește (vine din oh_proba) · nimic trimis"
        if not (motiv or "").strip():
            return "motiv: lipsește · nimic trimis"
        try:
            a = _actiune(actiune, comanda, utilizator, motiv, params_json, magazin, peste, confirmare)
        except ValueError as e:
            return "parametru: %s · nimic trimis" % e
        return _run(OH, a + ["--apply"], timeout=150, executie=True)



if __name__ == "__main__":
    mcp.run()
