# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-fulfillment MCP — Customer Service + Fulfillment: caut comandă/client, status, AWB, factură, acțiuni.

Strat SUBȚIRE peste CLI-uri testate: `cs360.py` (../cs-360), `oh_links.py` (Order Hub), `awb_track.py`,
`deliverability_monitor.py`. Doar citire.

xConnector e DEZINSTALAT din 19-oct-2026 (cheile API dau 401): uneltele care citeau xConnector (xc_summary,
xc_address_issues, xc_not_downloaded) întorc doar trimiterea la înlocuitor, iar ACȚIUNILE xc_* (anulare, AWB, factură)
sunt DEPRECIATE din 6-oct (shared/CS.md §4): întorc „folosește gigi:order-hub <acțiune>" și nu mai ating nimic.
`xc_links` citește Order Hub (GET /api/cs/comanda), nu AWBprint. MCP-ul nu mai pune DATABASE_URL_AWBPRINT în env:
nicio unealtă rămasă nu deschide AWBprint (baza nu mai are voie să fie citită de pe laptopuri).
Credențiale din KB. Python/FastMCP stdio.

Register: claude mcp add --scope user arona-fulfillment -- uv run <abs path>/mcp_server.py
"""
import os, subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
def _kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True, stdin=subprocess.DEVNULL, timeout=120).stdout.strip()
for s in ("DATABASE_URL_METRICS",):   # cs360 citește metrics; AWBprint NU se mai injectează (vezi docstring)
    if not os.environ.get(s): os.environ[s]=_kb(s)
def _env():
    e=dict(os.environ); e.pop("VIRTUAL_ENV",None); e["PYTHONIOENCODING"]="utf-8"; return e
from mcp.server.fastmcp import FastMCP
mcp=FastMCP("arona-fulfillment")
XC=os.path.join(HERE,"xconnector.py"); CS=os.path.join(HERE,"..","cs-360","cs360.py")
def _run(script, args, timeout=180):
    # UTF-8 la ambele capete: cu codecul consolei (cp1252 pe Windows), „═ ✅ ⛔" din ieșire nu se decodau și unealta
    # întorcea „(fără output)" chiar după o acțiune executată.
    try:
        r=subprocess.run(["uv","run",script]+args,capture_output=True,text=True,encoding="utf-8",errors="replace",env=_env(),timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:   # pe Windows, `uv run` nu-și oprește copilul: ce a apucat să spună rămâne
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if args[:1] == ["awb-regen"] and "--apply" in args:   # o probă nouă ar arăta deja eticheta nouă → a treia
            awb = args[args.index("--awb") + 1] if "--awb" in args[:-1] else ""
            sfat = ("⚠ Comanda a depășit %d s: refacerea POATE să fi fost executată. Repetă EXACT același apel "
                    "(aceleași order și parcels, awb=%s, apply=true): dacă s-a făcut, Order Hub îl refuză "
                    "(eticheta_anulata) și nu iese a treia etichetă; dacă nu, îl face o singură dată. NU lua awb dintr-o "
                    "probă nouă după un timeout: proba ar arăta eticheta nouă, iar refacerea ei ar face a treia." % (timeout, awb))
        else:
            sfat = ("⚠ Comanda a depășit %d s. Dacă era o acțiune cu apply=true, POATE să fi fost executată: verifică "
                    "starea comenzii (apply=false) înainte de a repeta." % timeout)
        return ((out.strip() + "\n") if out.strip() else "") + sfat
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[cod de ieșire %d] %s" % (r.returncode,(r.stderr or "")[:400])) if r.returncode!=0 else "")

# ─────────── CUSTOMER SERVICE (cs-360) ───────────
@mcp.tool()
def cs_customer(phone: str = "", name: str = "", email: str = "") -> str:
    """Profil 360 client: toate comenzile din toate magazinele + LTV + refuzuri + flag refuznic serial. Caută după telefon (merge 07../40../+40.. — ultimele 9 cifre), nume sau email."""
    a=["customer"]
    if phone: a+=["--phone",phone]
    if name: a+=["--name",name]
    if email: a+=["--email",email]
    if len(a)==1: return "Dă --phone / --name / --email."
    return _run(CS,a)
@mcp.tool()
def cs_wismo(order: str = "", phone: str = "", awb: str = "") -> str:
    """„Unde e comanda?" (WISMO): status complet + tracking AWB live + răspuns gata. Caută după order# / telefon / AWB."""
    a=["wismo"]
    if order: a+=["--order",order]
    if phone: a+=["--phone",phone]
    if awb: a+=["--awb",awb]
    return _run(CS,a)
@mcp.tool()
def cs_conversation(conv: str, llm: bool = False) -> str:
    """Profil 360 al unei conversații Richpanel (client+comandă+categorie+sentiment+acțiune). llm=true pt sinteză LLM."""
    out=_run(CS,["conversation","--conv",conv]+(["--llm"] if llm else []))
    if "\n[cod de ieșire " in out:   # citire Richpanel picată (schemă/503) = eroare MCP, nu „profil gol"
        raise RuntimeError(out)
    return out

# ─────────── FULFILLMENT / AWB — READ ───────────
OHL=os.path.join(HERE,"oh_links.py")
@mcp.tool()
def xc_links(order: str = "", awb: str = "") -> str:
    """Status comandă din ORDER HUB (sursa de adevăr): stare livrare, etichete (AWB, curier, status), hold, tichet CS +
    linkuri (Order Hub, Shopify, tracking). Caută DOAR după numărul comenzii; după AWB → `awb_track` (status live).
    Telefon/nume → cs_customer."""
    a=[]
    if order: a+=["--order",order]
    if awb: a+=["--awb",awb]
    return _run(OHL,a)

RETRAS=("RETRAS: xConnector e dezinstalat din 19-oct-2026 (API-ul dă 401), unealta nu mai are de unde citi. %s")
@mcp.tool()
def xc_summary() -> str:
    """RETRASĂ (xConnector dezinstalat 19-oct-2026). Coada de lucru: Order Hub."""
    return RETRAS % "Coada CS / ce e de procesat: Order Hub (https://orderhub.arona.ro/app/orders); stare pe comandă: oh_comanda."
@mcp.tool()
def xc_address_issues(shop: str = "", days: int = 60) -> str:
    """RETRASĂ (xConnector dezinstalat 19-oct-2026). Adrese proaste înainte de pickup: arona-cs-guard address_guard."""
    return RETRAS % "Adrese de reparat înainte de pickup: MCP arona-cs-guard → address_guard."
@mcp.tool()
def xc_not_downloaded(min_age_hours: int = 48) -> str:
    """RETRASĂ (xConnector dezinstalat 19-oct-2026). AWB-uri nescanate de curier: arona-cs-guard ghost_shipments."""
    return RETRAS % "AWB-uri emise dar nescanate (ghost shipments): MCP arona-cs-guard → ghost_shipments."

# ─────────── ACȚIUNI: DEPRECIATE (shared/CS.md §4) — nu ating nimic ───────────
# Din 6-oct orice modificare pe o comandă trece prin gigi:order-hub (proba + execuția cu confirmare, gărzi, istoric).
# Calea xConnector e moartă din 19-oct; order-cancel și awb-make citeau și AWBprint. Semnăturile rămân, ca agenții
# care le cheamă să primească trimiterea, nu „unealtă necunoscută".
def _depreciat(actiune, ex):
    return ("DEPRECIAT (nimic executat): acțiunile xc_* nu se mai folosesc (shared/CS.md §4; xConnector dezinstalat "
            "19-oct-2026). Folosește gigi:order-hub `%s` (MCP arona-orderhub: oh_comanda → oh_proba → acordul omului → "
            "oh_executa), ex: %s." % (actiune, ex))
@mcp.tool()
def xc_order_cancel(order: str, apply: bool = False, force: bool = False, agent: str = "", motiv: str = "") -> str:
    """DEPRECIATĂ: nu execută nimic. Anularea = gigi:order-hub `anulare`."""
    return _depreciat("anulare", "comanda: %s" % order)
@mcp.tool()
def xc_awb_make(order: str, apply: bool = False) -> str:
    """DEPRECIATĂ: nu execută nimic. AWB nou = gigi:order-hub `awb-nou`."""
    return _depreciat("awb-nou", "comanda: %s" % order)
@mcp.tool()
def xc_awb_void(order: str, apply: bool = False, agent: str = "", motiv: str = "") -> str:
    """DEPRECIATĂ: nu execută nimic. Oprirea (AWB anulat + hold) = gigi:order-hub `oprire`."""
    return _depreciat("oprire", "comanda: %s" % order)
@mcp.tool()
def xc_awb_regen(order: str, parcels: int = 0, awb: str = "", apply: bool = False, agent: str = "", motiv: str = "") -> str:
    """DEPRECIATĂ: nu execută nimic. Refacerea AWB-ului = gigi:order-hub `refacere` (+ `awb` = eticheta din probă)."""
    return _depreciat("refacere", "comanda: %s, colete: %s" % (order, parcels or "N"))
@mcp.tool()
def xc_inv_make(order: str, apply: bool = False) -> str:
    """DEPRECIATĂ: nu execută nimic. Factura = gigi:order-hub `factura`."""
    return _depreciat("factura", "comanda: %s" % order)


# ─── Tracking multi-curier + livrabilitate (erau doar CLI) ───────────────
AWBTRACK=os.path.join(HERE,"..","awb-track","awb_track.py")
DELIV=os.path.join(HERE,"..","deliverability-monitor","deliverability_monitor.py")

@mcp.tool()
def awb_track(awb: str = "", courier: str = "", problems_only: bool = False) -> str:
    """Status LIVE la unul sau mai multe AWB-uri, pe orice curier (DPD/Sameday/Econt/Packeta).
    awb = unul sau mai multe separate prin virgulă. problems_only=true arată doar cele blocate.
    Pentru statusul din Order Hub (fără să lovești curierul), folosește `xc_links` (după numărul comenzii)."""
    a=[]
    for x in [s.strip() for s in awb.split(",") if s.strip()]:
        a+=["--awb",x]
    if courier: a+=["--courier",courier]
    if problems_only: a.append("--problems")
    return _run(AWBTRACK,a,timeout=300)

@mcp.tool()
def deliverability(brand: str = "", by: str = "", month: str = "",
                   min_sent: int = 0, limit: int = 40) -> str:
    """Scurgerea de bani din REFUZURI / livrări eșuate, pe magazin sau altă dimensiune.
    by = dimensiunea de grupare; month = YYYY-MM. Complementar cu `arona-cs-guard`,
    care dă cozile de acțiune, nu diagnosticul agregat."""
    a=[]
    for k,v in (("--brand",brand),("--by",by),("--month",month),
                ("--min-sent",min_sent),("--limit",limit)):
        if v: a+=[k,str(v)]
    return _run(DELIV,a,timeout=420)

if __name__=="__main__":
    mcp.run()
