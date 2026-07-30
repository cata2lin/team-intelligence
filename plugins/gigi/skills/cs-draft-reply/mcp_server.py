# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-cs-inbox MCP — operarea inboxului CS Richpanel: draft răspuns, triaj, sentiment, SLA, curățenie backlog.

Complementează arona-fulfillment (căutare comandă/client) + MCP-ul Richpanel oficial. Strat SUBȚIRE peste CLI-ul
testat. Read-only by default; scrierile (draft/triaj/janitor) sunt DRY-RUN dacă nu pui apply=true.
Credențiale din KB. Python/FastMCP stdio.

Register: claude mcp add --scope user arona-cs-inbox -- uv run <abs path>/mcp_server.py
"""
import os, subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
def _kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
if not os.environ.get("DATABASE_URL_METRICS"): os.environ["DATABASE_URL_METRICS"]=_kb("DATABASE_URL_METRICS")
def _env():
    e=dict(os.environ); e.pop("VIRTUAL_ENV",None); return e
from mcp.server.fastmcp import FastMCP
mcp=FastMCP("arona-cs-inbox")
DRAFT=os.path.join(HERE,"cs_auto_draft.py")
TRIAGE=os.path.join(HERE,"..","richpanel-auto-triage","richpanel_auto_triage.py")
JANITOR=os.path.join(HERE,"..","richpanel-backlog-janitor","richpanel_backlog_janitor.py")
SENT=os.path.join(HERE,"..","cs-sentiment","cs_sentiment.py")
SLA=os.path.join(HERE,"..","cs-sla-dashboard","cs_sla_dashboard.py")
def _run(script, args, timeout=200):
    r=subprocess.run(["uv","run",script]+args,capture_output=True,text=True,env=_env(),timeout=timeout)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:400]) if r.returncode!=0 else "")

@mcp.tool()
def cs_draft(conv: str, create_draft: bool = False) -> str:
    """Draft de răspuns pt un tichet Richpanel (în vocea CS, cu date reale + macro-uri ClickUp). create_draft=true îl scrie ca DRAFT în Richpanel (NICIODATĂ trimite). Fără = doar arată propunerea."""
    return _run(DRAFT,["--conv",conv]+(["--create-draft"] if create_draft else []))
@mcp.tool()
def richpanel_triage(limit: int = 20, apply: bool = False) -> str:
    """Auto-triaj conversații OPEN: propune magazin/categorie/prioritate/tag (din to.id = pagina FB/IG). DRY-RUN dacă apply=false (scrie tag+prioritate, niciun mesaj la client)."""
    return _run(TRIAGE,["--limit",str(limit)]+(["--apply"] if apply else []))
@mcp.tool()
def richpanel_janitor(type: str = "", apply: bool = False) -> str:
    """Curăță backlogul: auto-close zgomot ad-comments / snooze WISMO. type=filtru. DRY-RUN dacă apply=false."""
    a=[]
    if type: a+=["--type",type]
    return _run(JANITOR,a+(["--apply"] if apply else []))
@mcp.tool()
def cs_sentiment(store: str = "", open_only: bool = True, limit: int = 30) -> str:
    """Sentiment per tichet (negativ/neutru/pozitiv). store=filtru magazin. open_only=doar deschise."""
    a=["--limit",str(limit)]
    if store: a+=["--store",store]
    if open_only: a.append("--open")
    return _run(SENT,a)
@mcp.tool()
def cs_sla(days: int = 7, triage: bool = False) -> str:
    """Dashboard SLA Richpanel — unde rămânem în urmă (timp de răspuns/rezolvare per agent/canal). triage=grupează pe triaj."""
    return _run(SLA,["--days",str(days)]+(["--triage"] if triage else []))

if __name__=="__main__":
    mcp.run()
