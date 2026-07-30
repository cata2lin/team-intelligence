# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-profit MCP — profitabilitate & livrabilitate: P&L per brand, breakeven, refuz/transport/COD/retenție.

Strat SUBȚIRE peste CLI-ul testat: `multi_brand_pnl.py` (local, SSH la VPS), `fulfillment_analytics.py` + `breakeven.py`
(../fulfillment-analytics, citesc AWBprint). Read-only. Credențiale din KB. Python/FastMCP stdio.

Register: claude mcp add --scope user arona-profit -- uv run <abs path>/mcp_server.py
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
mcp=FastMCP("arona-profit")
PNL=os.path.join(HERE,"multi_brand_pnl.py")
FA=os.path.join(HERE,"..","fulfillment-analytics","fulfillment_analytics.py")
BE=os.path.join(HERE,"..","fulfillment-analytics","breakeven.py")
def _run(script, args, timeout=200):
    r=subprocess.run(["uv","run",script]+args,capture_output=True,text=True,env=_env(),timeout=timeout)
    return ((r.stdout or "").strip() or "(fără output)")+(("\n[stderr] "+(r.stderr or "")[:400]) if r.returncode!=0 else "")

@mcp.tool()
def pnl(brands: str = "all", from_date: str = "", to_date: str = "", consolidated: bool = False) -> str:
    """P&L REAL per brand (venit LIVRAT ex-TVA − COGS − transport − marketing) din engine-ul canonic. brands=csv sau 'all'. from/to = YYYY-MM-DD (implicit luna curentă). consolidated=un singur P&L agregat."""
    a=["--brands",brands]
    if from_date: a+=["--from",from_date]
    if to_date: a+=["--to",to_date]
    if consolidated: a.append("--consolidated")
    return _run(PNL,a)
@mcp.tool()
def pnl_today() -> str:
    """Snapshot executiv: ieri + month-to-date, o linie per brand (toate brandurile)."""
    return _run(PNL,["--today"])
@mcp.tool()
def fulfillment(report: str = "refuse", by: str = "brand", days: int = 0, months: int = 3, stores: str = "") -> str:
    """Livrabilitate din AWBprint: report=refuse/sales/transport/stuck/repeat/cod/geo. by depinde de report (brand/courier/product/payment/discount; geo:province/city). days>0 override months."""
    a=["--report",report,"--by",by]
    if days>0: a+=["--days",str(days)]
    else: a+=["--months",str(months)]
    if stores: a+=["--stores",stores]
    return _run(FA,a)
@mcp.tool()
def breakeven(store: str = "all", days: int = 60) -> str:
    """Breakeven CPA/ROAS per magazin (model de planificare: COGS% + transport median). store=nume sau 'all'."""
    return _run(BE,["--store",store,"--days",str(days)])

if __name__=="__main__":
    mcp.run()
