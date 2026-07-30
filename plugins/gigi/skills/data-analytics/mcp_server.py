# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-bi MCP — analitică pe CLIENȚI și PRODUSE (nu pe bani: aia e `arona-profit`).

Cine sunt clienții (RFM, cohorte, LTV, churn), ce se cumpără împreună, ce produse merită scalate
sau omorâte, și un lint de integritate pe warehouse.

⚠️ Distincția care contează: se calculează pe comenzi **LIVRATE din AWBprint** (= venit COD real),
NU pe Shopify brut — Shopify include refuzurile, deci LTV-ul iese umflat.

Read-only. `data_slice` acceptă SQL propriu, dar tot read-only.
Register: claude mcp add --scope user arona-bi -- uv run <abs path>/mcp_server.py
"""
import os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.join(HERE, "..")
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")


def _kb(k):
    return subprocess.run(["uv", "run", KB, "secret-get", k],
                          capture_output=True, text=True).stdout.strip()


for s in ("DATABASE_URL_AWBPRINT", "DATABASE_URL_METRICS"):
    if not os.environ.get(s):
        os.environ[s] = _kb(s)


def _env():
    e = dict(os.environ)
    e.pop("VIRTUAL_ENV", None)
    e.setdefault("PYTHONIOENCODING", "utf-8")
    return e


def _run(skill, name, args, timeout=600):
    r = subprocess.run(["uv", "run", os.path.join(SK, skill, name)] + [str(a) for a in args],
                       capture_output=True, text=True, env=_env(), timeout=timeout)
    out = (r.stdout or "").strip() or "(fără output)"
    if r.returncode != 0:
        out += "\n[stderr] " + (r.stderr or "")[:400]
    return out


from mcp.server.fastmcp import FastMCP
mcp = FastMCP("arona-bi")


@mcp.tool()
def customer_analytics(analysis: str = "all", store: str = "", months: int = 0, weeks: int = 0) -> str:
    """Segmentare și valoare pe clienți. analysis = rfm | cohort | ltv | forecast | all.
    Pe comenzi LIVRATE (venit COD real), identitate = email, per magazin/monedă.
    ⚠️ NU pe Shopify brut — acolo refuzurile umflă LTV-ul."""
    a = [analysis]
    if store: a += ["--store", store]
    if months: a += ["--months", months]
    if weeks: a += ["--weeks", weeks]
    return _run("data-analytics", "scripts/data_analytics.py", a)


@mcp.tool()
def cross_sell(brand: str = "", product: str = "", days: int = 90, top: int = 20,
               min_lift: float = 0, min_co: int = 0) -> str:
    """Ce se cumpără ÎMPREUNĂ, din comenzile noastre reale (nu recomandări generice).
    product = ancorează pe un SKU. min_lift/min_co taie coincidențele."""
    a = ["--days", days, "--top", top]
    for k, v in (("--brand", brand), ("--product", product),
                 ("--min-lift", min_lift), ("--min-co", min_co)):
        if v:
            a += [k, v]
    return _run("cross-sell", "cross_sell.py", a)


@mcp.tool()
def product_lifecycle(report: str = "summary", limit: int = 40,
                      stock_days: int = 14, min_atc: int = 2) -> str:
    """Kill-list / scale-list per produs + drop-off de funnel (Grandia).
    report = summary | kill | scale | cro. `kill` = ce pierde bani, `scale` = ce merită împins,
    `cro` = unde se pierde conversia."""
    return _run("grandia-lifecycle", "grandia_lifecycle.py",
                ["--report", report, "--limit", limit,
                 "--stock-days", stock_days, "--min-atc", min_atc])


@mcp.tool()
def data_integrity(window_days: int = 30, threshold_days: int = 3,
                   min_revenue: float = 0) -> str:
    """Lint de integritate pe warehouse: ce brand are date vechi, lipsă sau contradictorii.
    Rulează-l ÎNAINTE de orice raport pe bani — un pipeline poate raporta succes cu date moarte
    (vezi `gigi:silent-data-failures`)."""
    a = ["--window-days", window_days, "--threshold-days", threshold_days]
    if min_revenue:
        a += ["--min-revenue", min_revenue]
    return _run("bi-data-integrity-check", "bi_data_integrity_check.py", a)


@mcp.tool()
def data_slice(sql: str, limit: int = 200, cols: str = "") -> str:
    """Felie ad-hoc de date cu SQL propriu, read-only, formatată ca tabel.
    Pentru întrebări care n-au tool dedicat. NU folosi pentru cifre de profit —
    acolo sursa canonică e `arona-profit`."""
    a = ["--sql", sql, "--limit", limit, "--stdout"]
    if cols:
        a += ["--cols", cols]
    return _run("data-slice", "scripts/data_slice.py", a)


if __name__ == "__main__":
    mcp.run()
