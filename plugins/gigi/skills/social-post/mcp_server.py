# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-social MCP — social organic: listening (mențiuni RO), postare (Metricool), competitor ads.

Strat SUBȚIRE peste CLI-ul testat: social_post.py (local), social_listen.py + competitor_ads.py (skill-uri vecine).
Read-only by default; POSTAREA = DRY-RUN dacă nu pui apply=true. Credențiale din KB. Python/FastMCP stdio.

Register: claude mcp add --scope user arona-social -- uv run <abs path>/mcp_server.py
"""
import os, subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
def _kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
if not os.environ.get("DATABASE_URL_METRICS"): os.environ["DATABASE_URL_METRICS"]=_kb("DATABASE_URL_METRICS")
def _env():
    e=dict(os.environ); e.pop("VIRTUAL_ENV",None); return e
from mcp.server.fastmcp import FastMCP
mcp=FastMCP("arona-social")
POST=os.path.join(HERE,"social_post.py")
LISTEN=os.path.join(HERE,"..","social-listening","scripts","social_listen.py")
COMP=os.path.join(HERE,"..","competitor-ads","scripts","competitor_ads.py")
def _run(script, args, timeout=200):
    r=subprocess.run(["uv","run",script]+args,capture_output=True,text=True,env=_env(),timeout=timeout)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:400]) if r.returncode!=0 else "")

@mcp.tool()
def social_listen(mode: str = "scan", days: int = 7, only: str = "") -> str:
    """Social listening RO — mențiuni/buzz despre branduri. mode=scan/brands/ig-discover. only=filtru brand."""
    a=[mode,"--days",str(days)]
    if only: a+=["--only",only]
    return _run(LISTEN,a)
@mcp.tool()
def social_post_list(brand: str = "") -> str:
    """Listează conturile/coada de postări sociale (Metricool). brand gol = toate."""
    a=["list"]
    if brand: a+=["--brand",brand]
    return _run(POST,a)
@mcp.tool()
def social_post(brand: str, text: str, to: str = "", image: str = "", link: str = "", schedule: str = "", apply: bool = False) -> str:
    """Postează pe social (Metricool). to=rețele csv. DRY-RUN dacă apply=false."""
    a=["post","--brand",brand,"--text",text]
    if to: a+=["--to",to]
    if image: a+=["--image",image]
    if link: a+=["--link",link]
    if schedule: a+=["--schedule",schedule]
    if apply: a.append("--apply")
    return _run(POST,a)
@mcp.tool()
def competitor_ads(mode: str = "best", region: str = "RO", top: int = 20, vs: str = "") -> str:
    """Reclamele competiției (Meta/TikTok ads library). mode=best/analyze. vs=domeniu competitor."""
    a=[mode,"--region",region,"--top",str(top)]
    if vs: a+=["--vs",vs]
    return _run(COMP,a)

if __name__=="__main__":
    mcp.run()
