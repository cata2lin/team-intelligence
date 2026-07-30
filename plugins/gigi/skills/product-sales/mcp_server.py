# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-catalog MCP — produse & inventar: vânzări/produs, stoc/restock, retururi (RMA), reviews.

Strat SUBȚIRE peste CLI-ul testat: product_sales.py (local), stock_restock_alerts.py, returns_rma_report.py,
reviews_manager.py (skill-uri vecine). Read-only. Credențiale din KB. Python/FastMCP stdio.

Register: claude mcp add --scope user arona-catalog -- uv run <abs path>/mcp_server.py
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
mcp=FastMCP("arona-catalog")
PS=os.path.join(HERE,"product_sales.py")
STOCK=os.path.join(HERE,"..","stock-restock-alerts","stock_restock_alerts.py")
RMA=os.path.join(HERE,"..","returns-rma-report","returns_rma_report.py")
REV=os.path.join(HERE,"..","reviews-manager","reviews_manager.py")
def _run(script, args, timeout=240):
    r=subprocess.run(["uv","run",script]+args,capture_output=True,text=True,env=_env(),timeout=timeout)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:400]) if r.returncode!=0 else "")

@mcp.tool()
def product_sales(stores: str = "", months: int = 1, metric: str = "units", limit: int = 30, source: str = "") -> str:
    """Câte BUCĂȚI a vândut fiecare produs (top/bottom) pe magazin(e), pe perioadă. stores=prefixe csv (EST,GT). metric=units/revenue. source=shopify pt 100% (mai lent)."""
    a=["--months",str(months),"--metric",metric,"--limit",str(limit),"--no-sheet"]
    if stores: a+=["--stores",stores]
    if source: a+=["--source",source]
    return _run(PS,a)
@mcp.tool()
def stock_alerts(brand: str = "", report: str = "restock", threshold: int = 0, limit: int = 40) -> str:
    """Stoc mic / epuizat / prioritate restock pe magazinele Shopify. report=restock/low/out. brand gol = toate."""
    a=["--report",report,"--limit",str(limit)]
    if brand: a+=["--brand",brand]
    if threshold>0: a+=["--threshold",str(threshold)]
    return _run(STOCK,a)
@mcp.tool()
def returns_rma(month: str = "", pipeline: bool = False, products: bool = False, reasons: bool = False, sla: bool = False) -> str:
    """Retururi & schimburi Grandia (rma_requests): pipeline (NEW/IN_PROGRESS/AWAITING_REFUND), produse, motive, SLA. month=YYYY-MM."""
    a=[]
    if month: a+=["--month",month]
    for flag,on in [("--pipeline",pipeline),("--products",products),("--reasons",reasons),("--sla",sla)]:
        if on: a.append(flag)
    return _run(RMA,a or ["--pipeline"])
@mcp.tool()
def reviews(mode: str = "coverage", brand: str = "", limit: int = 30) -> str:
    """Reviews produse (Judge.me) pe branduri: mode=coverage/bestsellers/low/recent. brand gol = toate."""
    a=[mode]
    if brand: a+=["--brand",brand]
    a+=["--limit",str(limit)]
    return _run(REV,a)

if __name__=="__main__":
    mcp.run()
