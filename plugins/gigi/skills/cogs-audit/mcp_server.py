# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","google-api-python-client>=2.100","google-auth>=2.30"]
# ///
"""arona-data MCP — diagnostic pe DATE: de ce minte o cifră.

Strat SUBȚIRE peste CLI-ul testat: `cogs_audit.py` (COGS Shopify vs formula canonică „COGS 2026")
și `../sheet-forensics/scripts/sheet_forensics.py` (formule, constante hardcodate, lookup-uri
care nu potrivesc, taburi care se contrazic).

Read-only implicit. Singura scriere (`cogs_fix`) e DRY-RUN dacă nu pui apply=true.
Register: claude mcp add --scope user arona-data -- uv run <abs path>/mcp_server.py
"""
import os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
COGS = os.path.join(HERE, "scripts", "cogs_audit.py")
FOREN = os.path.join(HERE, "..", "sheet-forensics", "scripts", "sheet_forensics.py")


def _env():
    e = dict(os.environ)
    e.pop("VIRTUAL_ENV", None)          # uv nested
    e.setdefault("PYTHONIOENCODING", "utf-8")
    return e


def _run(script, args, timeout=600):
    r = subprocess.run(["uv", "run", script] + args,
                       capture_output=True, text=True, env=_env(), timeout=timeout)
    out = (r.stdout or "").strip() or "(fără output)"
    if r.returncode != 0:
        out += "\n[stderr] " + (r.stderr or "")[:500]
    return out


from mcp.server.fastmcp import FastMCP
mcp = FastMCP("arona-data")


# ─── COGS ────────────────────────────────────────────────────
@mcp.tool()
def cogs_audit(sku: str = "", store: str = "", only_bugs: bool = True, limit: int = 60) -> str:
    """Auditează costPerItem din Shopify față de formula canonică ARONA ((marfă$+ship$)x4.46x1.10x1.21,
    sheet „Stoc ARONA"/tab „COGS 2026"). Clasifică: MISSING_VAT_DUTY (oprit la coloana „COGS lei",
    fără vamă+TVA) · MISMATCH (placeholder) · DIVERGENT (același SKU, costuri diferite între magazine)
    · NO_COST. sku/store = filtre substring, csv la store (ex RED,OFER). Read-only."""
    a = ["audit", "--limit", str(limit)]
    if only_bugs: a.append("--only-bugs")
    for s in [x.strip() for x in sku.split(",") if x.strip()]:
        a += ["--sku", s]
    if store: a += ["--store", store]
    return _run(COGS, a)


@mcp.tool()
def cogs_fix(sku: str = "", store: str = "", apply: bool = False, force: bool = False) -> str:
    """Repară costPerItem în Shopify la valoarea canonică. DRY-RUN dacă apply=false.
    Sare peste MISMATCH (nederivabil) dacă force=false — acolo cifra corectă nu e sigură.
    ⚠️ Scrie în Shopify producție când apply=true."""
    a = ["fix"]
    for s in [x.strip() for x in sku.split(",") if x.strip()]:
        a += ["--sku", s]
    if store: a += ["--store", store]
    if force: a.append("--force")
    if apply: a.append("--apply")
    return _run(COGS, a)


# ─── Sheet forensics ─────────────────────────────────────────
@mcp.tool()
def sheet_tabs(sheet_id: str) -> str:
    """Listează taburile unui Google Sheet (titlu + gid). Caută tabul pe NUME, nu pe gid —
    gid-urile din linkuri vechi pot să nu mai existe."""
    return _run(FOREN, ["tabs", sheet_id])


@mcp.tool()
def sheet_lookups(sheet_id: str, tab: str) -> str:
    """Cel mai rapid diagnostic pentru „brandul X arată 0": extrage literalii căutați de
    SUMIFS/FILTER/VLOOKUP și verifică dacă EXISTĂ în tabul sursă (potrivire case-insensitivă,
    ca în Sheets). Un ❌ = formulă care nu potrivește nimic → 0 fără nicio eroare vizibilă."""
    return _run(FOREN, ["lookups", sheet_id, "--tab", tab])


@mcp.tool()
def sheet_consts(sheet_id: str, tab: str) -> str:
    """Constantele numerice HARDCODATE în formulele unui tab (cursuri, TVA, taxe), ordonate după
    frecvență. O constantă repetată în sute de celule nu se poate schimba dintr-un singur loc."""
    return _run(FOREN, ["consts", sheet_id, "--tab", tab])


@mcp.tool()
def sheet_deadrefs(sheet_id: str, tab: str) -> str:
    """Celule etichetate ca parametru („curs", „editabil") pe care NICIO formulă nu le referă —
    comenzi false: le editezi și nu se schimbă nimic în calcul."""
    return _run(FOREN, ["deadrefs", sheet_id, "--tab", tab])


@mcp.tool()
def sheet_compare(sheet_id: str, key: str, col: str, tabs: str) -> str:
    """Compară aceeași cheie între mai multe taburi și listează CONTRAZICERILE.
    tabs = csv. Dacă două taburi nu-s de acord, niciunul nu e sursă de adevăr."""
    return _run(FOREN, ["compare", sheet_id, "--key", key, "--col", col, "--tabs", tabs])


@mcp.tool()
def sheet_formulas(sheet_id: str, tab: str, row: int = 0, cols: str = "A1:Z200") -> str:
    """Formula ȘI valoarea rezultată, celulă cu celulă. row>0 = doar rândul ăla (citire fină)."""
    a = ["formulas", sheet_id, "--tab", tab, "--cols", cols]
    if row > 0: a += ["--row", str(row)]
    return _run(FOREN, a)


if __name__ == "__main__":
    mcp.run()
