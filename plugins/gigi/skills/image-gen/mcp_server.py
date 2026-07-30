# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2","requests>=2.31"]
# ///
"""arona-studio MCP — creative/content: generare imagine AI, de-AI (ai-scrub), date canal YouTube.

Strat SUBȚIRE peste CLI-ul testat: gen.py (scripts/), ../ai-scrub/scrub.py, ../youtube/execution/fetch_channel_data.py.
⚠️ image_gen GENEREAZĂ real (cost/credit) — nu e dry-run. Credențiale din KB. Python/FastMCP stdio.

Register: claude mcp add --scope user arona-studio -- uv run <abs path>/mcp_server.py
"""
import os, subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
def _env():
    e=dict(os.environ); e.pop("VIRTUAL_ENV",None); return e
from mcp.server.fastmcp import FastMCP
mcp=FastMCP("arona-studio")
GEN=os.path.join(HERE,"scripts","gen.py")
SCRUB=os.path.join(HERE,"..","ai-scrub","scrub.py")
YT=os.path.join(HERE,"..","youtube","execution","fetch_channel_data.py")
def _run(script, args, timeout=300):
    r=subprocess.run(["uv","run",script]+args,capture_output=True,text=True,env=_env(),timeout=timeout)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:400]) if r.returncode!=0 else "")

@mcp.tool()
def image_gen(prompt: str, aspect: str = "1:1", count: int = 1, ref: str = "", pro: bool = False) -> str:
    """⚠️ GENEREAZĂ imagine(i) AI din text (cost real). aspect=1:1/16:9/9:16. ref=cale imagine de referință (păstrează produsul). pro=model premium."""
    a=["--prompt",prompt,"--aspect",aspect,"--count",str(count)]
    if ref: a+=["--ref",ref]
    if pro: a.append("--pro")
    return _run(GEN,a)
@mcp.tool()
def ai_scrub(text: str = "", file: str = "", fix: bool = False) -> str:
    """De-AI (RO): scoate watermark-uri Unicode + fraze AI-tell, dă scor de curățenie. fix=true întoarce textul curățat. Dă text SAU file."""
    a=[]
    if text: a+=["--text",text]
    if file: a+=["--file",file]
    if fix: a.append("--fix")
    return _run(SCRUB,a or ["--text",""])
@mcp.tool()
def youtube_channel(videos: bool = False) -> str:
    """Date despre canalul YouTube al echipei (abonați/vizionări; videos=true → per-video)."""
    return _run(YT,["--videos"] if videos else [])

if __name__=="__main__":
    mcp.run()
