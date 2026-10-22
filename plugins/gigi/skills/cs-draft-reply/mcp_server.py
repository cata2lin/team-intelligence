# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","psycopg2-binary>=2.9","requests>=2.31"]
# ///
"""arona-cs-inbox MCP — operarea inboxului CS Richpanel: draft răspuns, triaj, sentiment, SLA, curățenie backlog,
plus CS internațional (tichete pe piață, tracking FAN MD/multi-curier, politica de retur pe piață).

Complementează arona-fulfillment (căutare comandă/client) + MCP-ul Richpanel oficial. Strat SUBȚIRE peste CLI-ul
testat. Read-only by default; scrierile (draft/triaj/janitor) sunt DRY-RUN dacă nu pui apply=true.
Credențiale din KB. Python/FastMCP stdio.

Register: claude mcp add --scope user arona-cs-inbox -- uv run <abs path>/mcp_server.py
"""
import os, re, subprocess
max_=max
HERE=os.path.dirname(os.path.abspath(__file__))
KB=os.path.join(HERE,"..","..","..","core","scripts","kb.py")
def _kb(k): return subprocess.run(["uv","run",KB,"secret-get",k],capture_output=True,text=True, stdin=subprocess.DEVNULL, timeout=120).stdout.strip()
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
INTL=os.path.join(HERE,"..","cs-international","cs_intl.py")
def _run(script, args, timeout=200):
    r=subprocess.run(["uv","run",script]+args,capture_output=True,text=True,env=_env(),timeout=timeout, stdin=subprocess.DEVNULL)
    if r.returncode!=0:
        # eroare = eroare MCP (isError), nu text care arată a rezultat gol: din oct-2026 Richpanel a
        # schimbat schema și uneltele „mergeau" cu 0 tichete
        raise RuntimeError("%s a eșuat (exit %d): %s\n%s"%(os.path.basename(script),r.returncode,
                           (r.stderr or "")[-1200:],(r.stdout or "")[-600:]))
    return (r.stdout or "").strip() or "(fără output)"

@mcp.tool()
def cs_draft(conv: str, create_draft: bool = False) -> str:
    """Draft de răspuns pt un tichet Richpanel (în vocea CS, cu date reale + macro-uri ClickUp). create_draft=true îl scrie ca DRAFT în Richpanel (NICIODATĂ trimite). Fără = doar arată propunerea."""
    return _run(DRAFT,["--only",conv]+(["--create-draft"] if create_draft else []))
@mcp.tool()
def richpanel_triage(limit: int = 20, apply: bool = False) -> str:
    """Auto-triaj conversații OPEN: propune magazin/categorie/prioritate/tag (pagina FB/IG din to.id sau prefixul id-ului, apoi e-mail/comandă). DRY-RUN dacă apply=false (scrie tag+prioritate, niciun mesaj la client)."""
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

PIETE_INTL={"","BG","HU","CZ","SK","PL","GR","MD","HR"}
@mcp.tool()
def cs_intl_tichete(piata: str = "", toate: bool = False, pagini: int = 20, max: int = 60) -> str:
    """CS INTERNAȚIONAL: tichetele OPEN din Richpanel pe piață (BG/HU/CZ/SK/PL/GR/MD), implicit doar cele la care ultimul mesaj e al CLIENTULUI (așteaptă răspuns). Magazinul din richpanel_schema (pagină/cutie/prefix comandă), altfel piața ghicită din limba clientului. Fără text de client: nr. tichet, canal, magazin, vârstă, categorie, escaladare (КЗП/ČOI/SOI/UOKiK…). DOAR CITIRE. piata=gol → toate piețele; toate=true include și tichetele la care am răspuns ultimii."""
    p=(piata or "").strip().upper()
    if p not in PIETE_INTL:
        raise ValueError("piata necunoscută: %s (ai: BG HU CZ SK PL GR MD HR)"%piata)
    a=["tichete","--pagini",str(max_(1,min(int(pagini),40))),"--max",str(max_(1,min(int(max),500)))]
    if p: a+=["--piata",p]
    if toate: a.append("--toate")
    return _run(INTL,a,timeout=280)
@mcp.tool()
def tracking_intl(awb: str) -> str:
    """Tracking colete INTERNAȚIONALE: FAN Courier Moldova (AWB FAN+cifre, din pagina publică app.fancourier.md — decide „Starea actuală”, prinde capcana „livrat” după refuz) + DPD/Sameday/Econt/Packeta prin awb-track. awb = unul sau mai multe, separate prin virgulă. DOAR CITIRE."""
    if not re.fullmatch(r"[A-Za-z0-9 ,;]{4,600}",awb or ""):
        raise ValueError("awb invalid: doar litere/cifre separate prin virgulă")
    return _run(INTL,["tracking","--awb",awb.replace(" ","")],timeout=300)
@mcp.tool()
def politica_retur(piata: str = "") -> str:
    """Politica de RETUR / rambursare / livrare / plată / contact / autoritate de protecția consumatorului pe piață (BG/HU/CZ/SK/PL/GR/MD), verificată pe paginile publice ale magazinelor (Duppo EU/MD, Bonhaus CZ/BG). piata=gol → toate. DOAR CITIRE."""
    p=(piata or "").strip().upper()
    if p not in PIETE_INTL:
        raise ValueError("piata necunoscută: %s"%piata)
    return _run(INTL,["politica"]+(["--piata",p] if p else []),timeout=60)

if __name__=="__main__":
    mcp.run()
