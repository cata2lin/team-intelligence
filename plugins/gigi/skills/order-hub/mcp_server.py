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
CITIRI = {"actiuni", "comanda", "operatie"}


def _kb(k):
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""


if not os.environ.get("OH_CS_SB_TOKEN"):
    os.environ["OH_CS_SB_TOKEN"] = _kb("OH_CS_SB_TOKEN")


def _env():
    e = dict(os.environ); e.pop("VIRTUAL_ENV", None); e["PYTHONIOENCODING"] = "utf-8"; return e


from mcp.server.fastmcp import FastMCP

mcp = FastMCP("arona-orderhub")


def _run(script, args, timeout=180, executie=False):
    try:
        r = subprocess.run(["uv", "run", "-q", script] + args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=_env(), timeout=timeout)
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


# TODO oh_neexpediate_pe_sku(sku): OH n-are încă o citire pe SKU accesibilă cu cheia CS (/api/cs are doar comanda,
# actiuni, operatie; lentila „Unfulfilled” din /api/orders cere sesiunea Shopify). Se adaugă când există un script
# testat (sau un GET /api/cs/...) — NU interoga AWBprint pentru asta.


# ─────────── ACȚIUNI (probă → om → execuție) ───────────
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
