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
    e=dict(os.environ); e.pop("VIRTUAL_ENV",None); return e
from mcp.server.fastmcp import FastMCP
mcp=FastMCP("arona-fulfillment")
XC=os.path.join(HERE,"xconnector.py"); CS=os.path.join(HERE,"..","cs-360","cs360.py")
def _run(script, args, timeout=180):
    r=subprocess.run(["uv","run",script]+args,capture_output=True,text=True,env=_env(),timeout=timeout)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:400]) if r.returncode!=0 else "")

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
@mcp.tool()
def xc_order_cancel(order: str, apply: bool = False, force: bool = False) -> str:
    """Anulează o comandă (garda „plecată" refuză dacă a fost expediată; force=true forțează). DRY-RUN dacă apply=false."""
    return _run(XC,["order-cancel","--order",order]+(["--apply"] if apply else [])+(["--force"] if force else []))
@mcp.tool()
def xc_awb_make(order: str, apply: bool = False) -> str:
    """Fă AWB pt o comandă (nr. colete auto din metafield). DRY-RUN dacă apply=false."""
    return _run(XC,["awb-make","--order",order]+(["--apply"] if apply else []))
@mcp.tool()
def xc_awb_void(order: str, apply: bool = False) -> str:
    """Anulează AWB-ul unei comenzi. DRY-RUN dacă apply=false."""
    return _run(XC,["awb-void","--order",order]+(["--apply"] if apply else []))
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

# ─── AWB RAPID (awb.sh pe VPS, ~0.3s/comandă vs ~65s prin xconnector.py resolve_order) ───
def _awb_fast(mode, orders):
    host=_kb("PROFIT_SSH_HOST"); user=_kb("PROFIT_SSH_USER"); pw=_kb("PROFIT_SSH_PASS")
    if not (host and user and pw): return "Lipsesc PROFIT_SSH_* din KB."
    args=[o.strip() for o in orders.replace(","," ").split() if o.strip()]
    if not args: return "Dă cel puțin o comandă."
    e=dict(os.environ); e["SSHPASS"]=pw
    cmd=["sshpass","-e","ssh","-o","StrictHostKeyChecking=accept-new","-o","ConnectTimeout=25",
         "%s@%s"%(user,host),"/root/Scripturi/awb.sh %s %s"%(mode," ".join(args))]
    try:
        r=subprocess.run(cmd,capture_output=True,text=True,env=e,timeout=150)
    except Exception as ex:
        return "EROARE ssh: %s" % str(ex)[:200]
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:300]) if r.returncode!=0 else "")

@mcp.tool()
def xc_awb_regen(orders: str) -> str:
    """RAPID (~0.3s/comandă, awb.sh pe VPS): REFACE AWB-ul cu alt nr de colete — anulează vechiul + face unul nou, gestionează 429 singur. Format „ORDER:N" per comandă (ex „OFER48566:1 RED25746:2"); fără „:N" ia nr. colete auto din metafield. Mai multe comenzi deodată. FOLOSEȘTE ASTA pt refacere colete — NU xc_awb_void+xc_awb_make (lente ~65s, dau timeout)."""
    return _awb_fast("regen", orders)

@mcp.tool()
def xc_awb_check(orders: str) -> str:
    """RAPID (~0.3s/comandă): câte colete are fiecare comandă + dacă are AWB. Read-only. Mai multe comenzi separate prin spațiu/virgulă."""
    return _awb_fast("check", orders)

@mcp.tool()
def xc_awb_send(orders: str) -> str:
    """RAPID: descarcă eticheta AWB (PDF) și o TRIMITE pe grupul WhatsApp „AWB" (verifică pagini=colete). Mai multe comenzi deodată."""
    return _awb_fast("send", orders)

if __name__=="__main__":
    mcp.run()
