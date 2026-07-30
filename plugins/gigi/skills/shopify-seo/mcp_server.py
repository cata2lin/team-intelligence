# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","requests>=2.31"]
# ///
"""arona-seo MCP — SEO tehnic, conversie și autoritate pe magazinele ARONA.

Strat SUBȚIRE peste CLI-urile testate: `shopify-seo` (audit, drift, linkgraph, conformitate
legală), `landing-audit`, `cro`, `shopify-geo` (AEO/GEO), `analytics` (Search Console + autoritate).

⚠️ Nu dublează MCP-urile OFICIALE: pentru date brute GA4 / Search Console / DataForSEO folosește
serverele lor (`google-analytics`, `search-console`, `dataforseo`). Aici sunt AUDITURI și
ACȚIUNI pe magazin, care compun mai multe surse și au logică proprie.

Read-only implicit — orice scriere pe Shopify e gated cu `apply=true`.
Register: claude mcp add --scope user arona-seo -- uv run <abs path>/mcp_server.py
"""
import os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.join(HERE, "..")


def _env():
    e = dict(os.environ)
    e.pop("VIRTUAL_ENV", None)
    e.setdefault("PYTHONIOENCODING", "utf-8")
    return e


def _run(script, args, timeout=600):
    r = subprocess.run(["uv", "run", script] + [str(a) for a in args],
                       capture_output=True, text=True, env=_env(), timeout=timeout)
    out = (r.stdout or "").strip() or "(fără output)"
    if r.returncode != 0:
        out += "\n[stderr] " + (r.stderr or "")[:400]
    return out


def _s(skill, name):
    return os.path.join(SK, skill, name)


from mcp.server.fastmcp import FastMCP
mcp = FastMCP("arona-seo")


@mcp.tool()
def seo_audit(store: str, app_prefix: str = "", csv_prefix: str = "") -> str:
    """Audit SEO complet pe un magazin Shopify: titluri/descrieri lipsă sau duplicate, alt-text,
    structură. Read-only — întoarce golurile, nu le repară."""
    a = ["--store", store]
    if app_prefix: a += ["--app-prefix", app_prefix]
    if csv_prefix: a += ["--csv-prefix", csv_prefix]
    return _run(_s("shopify-seo", "scripts/seo_audit.py"), a)


@mcp.tool()
def seo_drift(site: str = "", url: str = "", max_pages: int = 0) -> str:
    """DRIFT SEO — ce s-a stricat față de starea anterioară (titluri schimbate, pagini căzute,
    redirect-uri noi). Rulează-l după orice modificare de temă sau import de produse."""
    a = []
    if site: a += ["--site", site]
    if url: a += ["--url", url]
    if max_pages: a += ["--max", max_pages]
    return _run(_s("shopify-seo", "drift.py"), a)


@mcp.tool()
def seo_compliance(store: str, apply: bool = False) -> str:
    """Conformitate legală RO pe magazin: GDPR, ANPC/SAL-SOL, date firmă (CUI, Reg. Com., adresă).
    Lipsa lor e risc de amendă, nu doar SEO. DRY-RUN dacă apply=false."""
    a = ["--store", store, "--gdpr", "--anpc"]
    if apply: a.append("--apply")
    return _run(_s("shopify-seo", "scripts/compliance.py"), a)


@mcp.tool()
def internal_links(store: str, top: int = 0, apply: bool = False) -> str:
    """Linkuri interne — găsește paginile orfane și propune legături din cele cu autoritate.
    DRY-RUN dacă apply=false."""
    a = ["--store", store]
    if top: a += ["--top", top]
    if apply: a.append("--apply")
    return _run(_s("shopify-seo", "scripts/internal_links.py"), a)


@mcp.tool()
def link_graph(site: str, max_pages: int = 0, threads: int = 0) -> str:
    """Graful de linkuri interne al site-ului — unde se acumulează autoritatea și ce rămâne izolat."""
    a = ["--site", site]
    if max_pages: a += ["--max", max_pages]
    if threads: a += ["--threads", threads]
    return _run(_s("shopify-seo", "linkgraph.py"), a)


@mcp.tool()
def landing_audit(url: str, speed: bool = False, trust: bool = False, urgency: bool = False) -> str:
    """Audit CRO pe o pagină de landing/produs: viteză, semnale de încredere, urgență.
    ⚠️ Urgența FALSĂ (countdown resetabil, stoc inventat) pică contul Merchant Center —
    vezi `gigi:mc-misrepresentation`."""
    a = ["--url", url]
    if speed: a.append("--speed")
    if trust: a.append("--trust")
    if urgency: a.append("--urgency")
    return _run(_s("landing-audit", "scripts/landing_audit.py"), a)


@mcp.tool()
def cro_audit(url: str) -> str:
    """Audit CRO pe o pagină de magazin — fricțiuni în drumul spre coș."""
    return _run(_s("cro", "cro.py"), ["--url", url])


@mcp.tool()
def geo_readiness(url: str) -> str:
    """GEO/AEO — cât de probabil e ca pagina să fie citată de motoarele cu AI (ChatGPT, AI Overviews):
    structură, răspunsuri directe, date factuale extractibile."""
    return _run(_s("shopify-geo", "geo.py"), ["--url", url])


@mcp.tool()
def gsc_queries(brand: str = "", site: str = "", all_sites: bool = False,
                days: int = 28, limit: int = 50, contains: str = "") -> str:
    """Interogările REALE din Search Console (impresii, clickuri, CTR, poziție) — singura sursă
    de cuvinte-cheie adevărate; merge și când tagul GA4 al magazinului e rupt.
    ⚠️ CTR organic prăbușit cu poziție stabilă = campaniile de brand îți mănâncă organicul
    (vezi `gigi:brand-ads-cannibalization`)."""
    a = ["--days", days, "--limit", limit]
    if brand: a += ["--brand", brand]
    if site: a += ["--site", site]
    if all_sites: a.append("--all")
    if contains: a += ["--contains", contains]
    return _run(_s("analytics", "gsc.py"), a)


@mcp.tool()
def domain_authority(domains: str = "", ours: str = "", vs: str = "") -> str:
    """Autoritatea domeniului nostru vs competiție. domains/vs = csv."""
    a = []
    if domains: a += ["--domains", domains]
    if ours: a += ["--ours", ours]
    if vs: a += ["--vs", vs]
    return _run(_s("analytics", "authority.py"), a)


if __name__ == "__main__":
    mcp.run()
