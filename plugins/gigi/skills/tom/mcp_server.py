# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-tom MCP — TOM WMS: comenzi de aprovizionare (PO), containere/shipments, ghost, produse, evenimente.

Strat SUBȚIRE peste `scripts/tom.py` (testat). Read-only by default; PO write (create/amend/cancel) = DRY-RUN
dacă nu pui apply=true (tom.py cere --yes). Credențiale din KB. Python/FastMCP stdio.

Register: claude mcp add --scope user arona-tom -- uv run <abs path>/mcp_server.py
"""
import os, subprocess
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
def _kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True).stdout.strip()
if not os.environ.get("DATABASE_URL_METRICS"): os.environ["DATABASE_URL_METRICS"]=_kb("DATABASE_URL_METRICS")
def _env():
    e=dict(os.environ); e.pop("VIRTUAL_ENV",None); return e
from mcp.server.fastmcp import FastMCP
mcp=FastMCP("arona-tom")
TOM=os.path.join(HERE,"scripts","tom.py")
def _run(args, timeout=200):
    r=subprocess.run(["uv","run",TOM]+args,capture_output=True,text=True,env=_env(),timeout=timeout)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:400]) if r.returncode!=0 else "")

@mcp.tool()
def tom_pos() -> str:
    """Listează comenzile de aprovizionare (PO) din TOM WMS."""
    return _run(["pos"])
@mcp.tool()
def tom_po_get(po: str) -> str:
    """Detaliu al unui PO (liniile, cantitățile, statusul). po=id/nume PO."""
    return _run(["po-get",po])
@mcp.tool()
def tom_shipments() -> str:
    """Containere / shipments în tranzit (ce marfă vine). ⚠️ TOM nu e sursa de adevăr pt CONȚINUT (vezi container-pipeline-kdocs)."""
    return _run(["shipments"])
@mcp.tool()
def tom_ghost() -> str:
    """Ghost / discrepanțe de stoc în TOM."""
    return _run(["ghost"])
@mcp.tool()
def tom_events(limit: int = 40) -> str:
    """Evenimente recente în TOM WMS (mișcări de stoc, PO, etc.)."""
    return _run(["events"])
@mcp.tool()
def tom_product(sku: str) -> str:
    """Info produs în TOM după SKU (stoc, PO-uri asociate)."""
    return _run(["product",sku])

if __name__=="__main__":
    mcp.run()
