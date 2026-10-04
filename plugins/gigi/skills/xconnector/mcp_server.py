# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-fulfillment MCP — Customer Service + Fulfillment: caut comandă/client, status, AWB, factură, acțiuni.

Strat SUBȚIRE peste CLI-ul testat: `xconnector.py` (local) + `cs360.py` (../cs-360). Read-only by default;
ACȚIUNILE (anulare/AWB/factură) sunt DRY-RUN dacă nu pui apply=true (garda „plecată" din xconnector rămâne).
Credențiale din KB. Python/FastMCP stdio.

Register: claude mcp add --scope user arona-fulfillment -- uv run <abs path>/mcp_server.py
"""
import os, subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
def _kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
for s in ("DATABASE_URL_AWBPRINT","DATABASE_URL_METRICS"):
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
        r=subprocess.run(["uv","run",script]+args,capture_output=True,text=True,encoding="utf-8",errors="replace",env=_env(),timeout=timeout)
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
    return _run(CS,["conversation","--conv",conv]+(["--llm"] if llm else []))

# ─────────── FULFILLMENT / AWB (xconnector) — READ ───────────
@mcp.tool()
def xc_links(order: str = "", awb: str = "") -> str:
    """Status comandă + linkuri (Shopify/xConnector/tracking) + livrare reală din AWBprint. Caută după order# SAU awb. (xConnector NU caută după telefon/nume — pt aia = cs_customer.)"""
    a=["links"]
    if order: a+=["--order",order]
    if awb: a+=["--awb",awb]
    return _run(XC,a)
@mcp.tool()
def xc_summary() -> str:
    """Sumar xConnector: comenzi pe stări, ce e de procesat (AWB-uri de făcut, etichete de descărcat)."""
    return _run(XC,["summary"])
@mcp.tool()
def xc_address_issues(shop: str = "", days: int = 60) -> str:
    """Comenzi cu probleme de adresă (înainte de pickup) — coada de reparat. Opțional filtrează pe --shop."""
    a=["address-issues","--days",str(days)]
    if shop: a+=["--shop",shop]
    return _run(XC,a)
@mcp.tool()
def xc_not_downloaded(min_age_hours: int = 48) -> str:
    """AWB-uri emise dar NEscanate de curier (ghost shipments) mai vechi de min_age_hours."""
    return _run(XC,["not-downloaded","--min-age-hours",str(min_age_hours)])

# ─────────── ACȚIUNI (dry-run default) ───────────
def _cine(agent, motiv): return (['--agent',agent] if agent else [])+(['--motiv',motiv] if motiv else [])
@mcp.tool()
def xc_order_cancel(order: str, apply: bool = False, force: bool = False, agent: str = "", motiv: str = "") -> str:
    """Anulează o comandă. Întreabă ÎNTÂI Order Hub: el anulează eticheta la curier, apoi comanda, și rambursează singur ce e plătit cu cardul (force nu există acolo); xConnector doar dacă Order Hub nu cunoaște comanda. agent = cine cere (ajunge în istoricul comenzii din Order Hub), motiv = de ce (în nota comenzii). DRY-RUN dacă apply=false; citește planul probei înainte de apply=true."""
    return _run(XC,["order-cancel","--order",order]+(["--apply"] if apply else [])+(["--force"] if force else [])+_cine(agent,motiv),timeout=300)
@mcp.tool()
def xc_awb_make(order: str, apply: bool = False) -> str:
    """Fă AWB prin xConnector. Se refuză pe o comandă care are deja AWB în Order Hub (acolo: xc_awb_regen) și nu eliberează hold-urile puse de Order Hub. Folosește-l doar când Order Hub cere eticheta făcută manual în xConnector. DRY-RUN dacă apply=false."""
    return _run(XC,["awb-make","--order",order]+(["--apply"] if apply else []),timeout=300)
@mcp.tool()
def xc_awb_void(order: str, apply: bool = False, agent: str = "", motiv: str = "") -> str:
    """OPREȘTE o comandă a Order Hub: anulează AWB-ul și o ține pe hold (se eliberează din Order Hub). Pe o comandă pe care Order Hub n-o cunoaște doar anulează AWB-ul în xConnector. NU e pasul întâi din „anulez și fac alt AWB" — pentru asta e xc_awb_regen. DRY-RUN dacă apply=false."""
    return _run(XC,["awb-void","--order",order]+(["--apply"] if apply else [])+_cine(agent,motiv),timeout=300)
@mcp.tool()
def xc_awb_regen(order: str, parcels: int = 0, awb: str = "", apply: bool = False, agent: str = "", motiv: str = "") -> str:
    """Reface AWB-ul unei comenzi (anulează + face altul, pe același curier), prin Order Hub. Întâi probă (apply=false): arată eticheta de refăcut și numărul de colete. Execuția (apply=true) cere parcels și awb = eticheta din proba făcută ÎNAINTEA execuției; o cerere repetată pe aceeași etichetă e refuzată, deci nu iese a treia. Dacă execuția nu întoarce răspuns (timeout, eroare, „POATE să fi fost executată”): repetă EXACT același apel, cu același awb — Order Hub îl refuză (eticheta_anulata) dacă refacerea s-a făcut, altfel o face o singură dată. NU lua awb dintr-o probă nouă după un răspuns pierdut: proba arată deja eticheta nouă, iar refacerea ei face a treia. Se folosește și după o schimbare de adresă pe o comandă care are deja AWB — la 1–2 minute după schimbare, ca adresa nouă să fi ajuns peste tot. Pe o comandă pe care Order Hub n-o cunoaște: anulează + reface prin xConnector."""
    return _run(XC,["awb-regen","--order",order]+(["--parcels",str(parcels)] if parcels else [])+(["--awb",awb] if awb else [])+(["--apply"] if apply else [])+_cine(agent,motiv),timeout=300)
@mcp.tool()
def xc_inv_make(order: str, apply: bool = False) -> str:
    """Creează factură pt o comandă (SmartBill). DRY-RUN dacă apply=false."""
    return _run(XC,["inv-make","--order",order]+(["--apply"] if apply else []))


# ─── Tracking multi-curier + livrabilitate (erau doar CLI) ───────────────
AWBTRACK=os.path.join(HERE,"..","awb-track","awb_track.py")
DELIV=os.path.join(HERE,"..","deliverability-monitor","deliverability_monitor.py")

@mcp.tool()
def awb_track(awb: str = "", courier: str = "", problems_only: bool = False) -> str:
    """Status LIVE la unul sau mai multe AWB-uri, pe orice curier (DPD/Sameday/Econt/Packeta).
    awb = unul sau mai multe separate prin virgulă. problems_only=true arată doar cele blocate.
    Pentru statusul din baza noastră (fără să lovești curierul), folosește `xc_links`."""
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
