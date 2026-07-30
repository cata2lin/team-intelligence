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

if __name__=="__main__":
    mcp.run()
