# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-cs-guard MCP — PREVENȚIA pierderilor în Customer Service.

Cozile care opresc bani să se scurgă ÎNAINTE ca ele să devină pierdere: COD riscant, adresă
greșită la colet neplecat, comenzi dublate, ghost shipments, întârzieri în tranzit, refunduri
promise dar neexecutate, comenzi refuzate de recuperat.

Distinct de `arona-fulfillment` (care e LOOKUP: unde e comanda, ce client e) și de
`arona-cs-inbox` (care operează inboxul Richpanel). Aici sunt COZI DE LUCRU proactive.

Strat SUBȚIRE peste CLI-urile testate din skill-urile surori. Read-only implicit —
generarea de drafturi Richpanel e gated cu `draft=true` (creează DRAFT, niciodată trimitere).
Register: claude mcp add --scope user arona-cs-guard -- uv run <abs path>/mcp_server.py
"""
import os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
SK = os.path.join(HERE, "..")          # .../skills
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


def _script(skill, name):
    return os.path.join(SK, skill, name)


def _run(script, args, timeout=420):
    r = subprocess.run(["uv", "run", script] + [str(a) for a in args],
                       capture_output=True, text=True, env=_env(), timeout=timeout)
    out = (r.stdout or "").strip() or "(fără output)"
    if r.returncode != 0:
        out += "\n[stderr] " + (r.stderr or "")[:400]
    return out


from mcp.server.fastmcp import FastMCP
mcp = FastMCP("arona-cs-guard")


@mcp.tool()
def cod_confirm_queue(brand: str = "", days: int = 3, min_value: float = 0,
                      limit: int = 50, draft: bool = False) -> str:
    """Coada de COD-uri RISCANTE de confirmat telefonic ÎNAINTE de expediere (serial-refuseri,
    valoare mare, istoric de refuz). Previne refuzul, nu-l raportează. draft=true generează
    drafturi Richpanel (DRAFT, nu trimite)."""
    a = ["--days", days, "--limit", limit]
    if brand: a += ["--brand", brand]
    if min_value: a += ["--min-value", min_value]
    if draft: a.append("--draft")
    return _run(_script("cod-confirmation", "cod_confirmation.py"), a)


@mcp.tool()
def address_guard(store: str = "", days: int = 7, reasons: str = "", limit: int = 50) -> str:
    """Comenzi cu ADRESĂ suspectă care încă NU au plecat — de sunat înainte de pickup.
    După ce coletul pleacă, o adresă greșită costă transport dus-întors."""
    a = ["--days", days, "--limit", limit]
    if store: a += ["--store", store]
    if reasons: a += ["--reasons", reasons]
    return _run(_script("cs-address-guard", "cs_address_guard.py"), a)


@mcp.tool()
def duplicate_orders(hours: int = 48, store: str = "", limit: int = 50, draft: bool = False) -> str:
    """Comenzi DUBLATE (același client, de 2 ori) prinse înainte să plece amândouă.
    ⚠️ Anulează doar dublurile identice (produse + sumă) — restul merg la om; vezi
    `gigi:duplicate-orders-guard`."""
    a = ["--hours", hours, "--limit", limit]
    if store: a += ["--store", store]
    if draft: a.append("--draft")
    return _run(_script("cs-duplicate-orders", "cs_duplicate_orders.py"), a)


@mcp.tool()
def ghost_shipments(days: int = 7, store: str = "", per_store: bool = False) -> str:
    """GHOST SHIPMENTS — AWB făcut, dar curierul n-a scanat niciodată coletul. Clientul așteaptă
    un pachet care nu s-a mișcat; se vede doar dacă-l cauți."""
    a = ["--days", days]
    if store: a += ["--store", store]
    if per_store: a.append("--per-store")
    return _run(_script("cs-ghost-shipments", "cs_ghost_shipments.py"), a)


@mcp.tool()
def proactive_delays(brands: str = "", stuck_days: int = 5, limit: int = 50,
                     draft: bool = False) -> str:
    """Colete blocate prea mult în tranzit — contactezi clientul ÎNAINTE să reclame el.
    brands = csv."""
    a = ["--stuck-days", stuck_days, "--limit", limit]
    if brands: a += ["--brands", brands]
    if draft: a.append("--draft")
    return _run(_script("cs-proactive-delays", "cs_proactive_delays.py"), a)


@mcp.tool()
def refund_watchdog(store: str = "", phase: str = "", min_age: int = 0,
                    min_amount: float = 0) -> str:
    """Refunduri PROMISE dar NEEXECUTATE — cel mai scump bug de CS (risc ANPC + chargeback).
    phase filtrează etapa din flux."""
    a = []
    if store: a += ["--store", store]
    if phase: a += ["--phase", phase]
    if min_age: a += ["--min-age", min_age]
    if min_amount: a += ["--min-amount", min_amount]
    return _run(_script("cs-refund-watchdog", "cs_refund_watchdog.py"), a)


@mcp.tool()
def refused_recovery(brand: str = "", days: int = 30, min_value: float = 0,
                     limit: int = 50, draft: bool = False) -> str:
    """Coada de recuperare a comenzilor REFUZATE / cu livrare eșuată — COD-uri pierdute
    care se pot re-câștiga."""
    a = ["--days", days, "--limit", limit]
    if brand: a += ["--brand", brand]
    if min_value: a += ["--min-value", min_value]
    if draft: a.append("--draft")
    return _run(_script("cs-refused-recovery", "cs_refused_recovery.py"), a)


if __name__ == "__main__":
    mcp.run()
