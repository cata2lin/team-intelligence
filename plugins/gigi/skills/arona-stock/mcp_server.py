# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","paramiko>=3.4"]
# ///
"""arona-stock MCP — stocul ARONA: bazinul stock-sync (sync.arona.ro), depozitele Bartolomeu / Uzina 2 / Teliu,
rafturile AWB Arona, sănătatea unui magazin, marfa blocată, ponderile per magazin și planul de reaprovizionare.

Strat SUBȚIRE peste scripturile testate. Aproape toate rulează PE serverul de scripturi (acolo sunt api/scan.py,
data/locations.db și tokenurile Shopify), prin ssh cu cheie (core/scripts/arona_ssh.py):
  - magazin_ok.py, ponderi_stoc.py, station_report.py = fișierele de pe server (aceleași pe care le rulează cronurile);
  - scripts/stoc_live.py și scripts/stoc_blocat.py = din git, trimise pe stdin (`python -`), fără copiere pe server.
reorder_planner.py rulează local (metrics, read-only).

Read-only by default. Scrieri expuse (toate DRY-RUN fără apply=true): ponderi_stoc (reguli PRODUCT_STORE + sync
MANUAL), receptie_container și mutare_stoc (numărătoarea din AWB Arona, locations.db — scripts/stoc_scriere.py). Secretele nu trec prin MCP: serverul de scripturi le ia singur din KB / .env.

Register: claude mcp add --scope user arona-stock -- uv run <abs path>/mcp_server.py
"""
import os
import shlex
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "core", "scripts"))
sys.path.insert(0, CORE)
import arona_ssh  # noqa: E402
import logging  # noqa: E402

logging.getLogger("paramiko").setLevel(logging.WARNING)

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("arona-stock")
STOC_LIVE = os.path.join(HERE, "scripts", "stoc_live.py")
STOC_BLOCAT = os.path.join(HERE, "scripts", "stoc_blocat.py")
STOC_SCRIERE = os.path.join(HERE, "scripts", "stoc_scriere.py")
REORDER = os.path.normpath(os.path.join(HERE, "..", "reorder-planner", "scripts", "reorder_planner.py"))
REMOTE = "cd /root/Scripturi && set -a && . ./.env && set +a && "
OUTDIR = "/tmp/arona_stock_mcp"
MAX_OUT = 60000


def _fmt(out, err, rc, ok_rc=(0,)):
    out = (out or "").strip() or "(fără output)"
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT] + "\n… (trunchiat)"
    if rc not in ok_rc:
        out += "\n[exit %s] %s" % (rc, (err or "").strip()[-600:])
    return out


def _vps(cmd, stdin_file=None, timeout=600, ok_rc=(0,)):
    """Rulează `cmd` pe serverul de scripturi (cu .env încărcat). stdin_file = script local trimis pe stdin."""
    for k in ("PROFIT_SSH_HOST", "PROFIT_SSH_USER"):  # o singură citire din KB pe sesiune, nu la fiecare apel
        if not os.environ.get(k):
            os.environ[k] = arona_ssh._secret(k) or (arona_ssh.DEFAULT_HOST if k.endswith("HOST") else arona_ssh.DEFAULT_USER)
    try:
        cl = arona_ssh.connect()
    except Exception as e:
        return "SSH către serverul de scripturi a eșuat: %s" % str(e)[:800]
    try:
        i, o, e = cl.exec_command(REMOTE + "mkdir -p %s && " % OUTDIR + cmd, timeout=timeout)
        if stdin_file:
            with open(stdin_file, "rb") as f:
                i.write(f.read())
        i.channel.shutdown_write()
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        rc = o.channel.recv_exit_status()
    except Exception as ex:
        return "eroare la rularea pe server: %s: %s" % (type(ex).__name__, str(ex)[:300])
    finally:
        cl.close()
    return _fmt(out, err, rc, ok_rc)


def _py(script, args):
    """Comanda pentru un script de pe server (cale relativă la /root/Scripturi) sau '-' = stdin."""
    return ".venv/bin/python %s %s" % (script, " ".join(shlex.quote(str(a)) for a in args))


def _env():
    e = dict(os.environ)
    e.pop("VIRTUAL_ENV", None)
    return e


@mcp.tool()
def stoc_produs(sku: str, top: int = 12) -> str:
    """Stocul unui produs dintr-o privire, LIVE: bazinul stock-sync (total, pe depozite BARTOLOMEU/UZINA_2/TELIU,
    dacă e NUMĂRAT în AWB Arona — atunci cifra pusă în Shopify e ignorată; Teliu apare ESTIMAT (neinventariat)), fiecare magazin (cât are, cât i s-a alocat,
    din ce depozit, regula de distribuție), listingurile NElegate (MISSING_BARCODE, NO_MASTER_MATCH, INACTIVE…)
    și rafturile fizice din AWB Arona per stație (cu sinonimele SKU-ului). Răspunde la „depozitul are marfă dar
    magazinul arată 0", „de ce se vinde pe stoc 0", „unde e fizic produsul". sku = SKU exact (ex. HA-0000). Doar citire."""
    return _vps(_py("-", ["produs", "--sku", sku, "--top", top]), STOC_LIVE, timeout=180)


@mcp.tool()
def stoc_statie(statie: str, sku_prefix: str = "", top: int = 25) -> str:
    """Ce e pe rafturile unei STAȚII (depozit fizic) în AWB Arona: total bucăți, nr SKU, rafturi cu marfă,
    bucket-urile fără raft (LOC-*), top SKU-uri și poziții negative. statie = Bartolomeu / Uzina 2 / Teliu / Parfumuri;
    sku_prefix filtrează (ex. „ha-"). Doar citire (locations.db read-only)."""
    return _vps(_py("-", ["statie", "--statie", statie, "--sku-prefix", sku_prefix, "--top", top]), STOC_LIVE, timeout=120)


@mcp.tool()
def stoc_erori(cod: str = "", severitate: str = "", top: int = 25) -> str:
    """Coada de erori DESCHISE din stock-sync: număr pe cod + cele mai recente. Cu cod= filtrezi exact, ex.
    RECONCILE_WAREHOUSE_DISAPPEARED / RECONCILE_UNMANAGED_LOCATION_STOCK / STORE_FETCH_FAILED (bazine ÎNGHEȚATE),
    COUNTED_STORE_RISE_IGNORED (cifră pusă în Shopify peste numărătoare), MISSING_BARCODE. severitate = INFO/WARNING/
    ERROR/CRITICAL. Fără filtru citește max ~3.000 (listă trunchiată). Doar citire."""
    return _vps(_py("-", ["erori", "--cod", cod, "--severitate", severitate, "--top", top]), STOC_LIVE, timeout=300)


@mcp.tool()
def magazin_ok(magazin: str, sku_prefix: str = "", top: int = 12) -> str:
    """E OK magazinul X? Combină Shopify (activ, stoc arătat, politica), stock-sync (starea listingului) și AWB Arona
    (marfa fizică) și dă pe fiecare variantă PROBLEMA (VINDE FĂRĂ MARFĂ, MARFA NU AJUNGE, DRAFT CU MARFĂ, STOC FĂRĂ SYNC,
    EXPUS FĂRĂ MARFĂ, DE CURĂȚAT) + CAUZA (fără barcode, fără master, depozit greșit, nu expune depozitul…) + ce e de făcut.
    magazin = prefix (MAG, RED, OFER, BON, ORC, BUC, EST, GT, GRAN…) sau numele din stock-sync; sku_prefix ex. „HA-".
    Citește tot catalogul Shopify al magazinului (1–5 min). CSV-ul complet rămâne pe server în /tmp/arona_stock_mcp/.
    ⚠️ Vânzările 30z vin din AWBprint (în urmă față de Order Hub). Doar citire."""
    out = "%s/magazin_ok_%s.csv" % (OUTDIR, "".join(ch for ch in magazin.lower() if ch.isalnum())[:20] or "x")
    args = [magazin, "--top", top, "--out", out] + (["--sku", sku_prefix] if sku_prefix else [])
    return _vps(_py("magazin_ok.py", args), timeout=900, ok_rc=(0, 2))


@mcp.tool()
def stoc_blocat(magazine: str = "EST,GT", nume: str = "", parfumuri: bool = False, min_stoc: int = 1, top: int = 25) -> str:
    """Marfa care EXISTĂ în stock-sync dar pe care NICIUN magazin n-o vede (masteri cu stoc, zero listing MATCHED),
    cu CAUZA: LEGAT GREȘIT, DRAFT (bucla draft), DUBLURĂ de master pe alt barcode, NELISTAT; semnalează barcode-urile
    placeholder (o recepție pe coduri inventate). magazine = prefixe Shopify separate prin virgulă (MAG,BON,RED…) sau
    TOATE; nume = regex pe NUMELE masterului (titlul produsului, nu SKU — ex. „Covor"); parfumuri=true = preset Esteban+GT.
    Masterii fără niciun listing apar NELISTAT relativ la magazinele cerute (parfumurile pe magazinele de deals = normal). Citește toți masterii +
    listingurile + catalogul Shopify (câteva minute). Doar citire."""
    args = ["--magazine", magazine, "--min-stoc", min_stoc, "--top", top, "--out", OUTDIR + "/stoc_blocat.csv"]
    if nume:
        args += ["--nume", nume]
    if parfumuri:
        args.append("--parfumuri")
    return _vps(_py("-", args), STOC_BLOCAT, timeout=900)


@mcp.tool()
def ponderi_stoc(magazin: str = "MagDeal.ro", zilnic: bool = False, refresh: bool = False, fereastra_zile: int = 30,
                 pe_expediat: bool = False, boost: float = 1.0, min_cerere: int = 30, factor: float = 1.4,
                 max_produse: int = 0, apply: bool = False) -> str:
    """Ponderile de stoc per magazin (cine vinde mai mult primește mai mult): calculează procente PRODUCT_STORE pe
    bazinul (produs × DEPOZIT), doar pe magazinele care expun depozitul. Moduri: magazin=X (sub-alocare pe un magazin,
    numele din stock-sync, ex. „MagDeal.ro"), zilnic=true (ce ar face rularea de seară 22:30 pe toate produsele,
    pe comenzile 7z), refresh=true (recalculează produsele care au deja reguli). Magazin în rampă: pe_expediat=true +
    fereastra_zile=7 + boost. DRY-RUN dacă apply=false (doar tabelul). apply=true SCRIE regulile și pornește un sync
    MANUAL (production, pe toate magazinele) — salvează pe server reguli_inainte_<ts>.json pentru revenire.
    ⚠️ Modul magazin=X crapă azi în scriptul de pe server (depozit None la afișare) — zilnic/refresh merg."""
    args = ["--magazin", magazin, "--fereastra-zile", fereastra_zile, "--boost", boost,
            "--min-cerere", min_cerere, "--factor", factor, "--max-produse", max_produse]
    if zilnic:
        args.append("--zilnic")
    if refresh:
        args.append("--refresh")
    if pe_expediat:
        args.append("--pe-expediat")
    if apply:
        args.append("--apply")
    return _vps(_py("ponderi_stoc.py", args), timeout=1200)


@mcp.tool()
def stoc_bucket_vs_shopify(cod: str = "LOC-TELIU") -> str:
    """Tablou pentru o numărătoare pe un cod de raft/bucket (ex. LOC-TELIU, LOC-BARTOLOMEU, LOC-UZINA-2): numărat vs alte
    rafturi vs POOL live în Shopify (suma pe magazine+locații) vs master stock-sync, cu marcaje CONTINUE (!), netrackuit (~)
    și DUBLURĂ de variantă. Interoghează live fiecare magazin Shopify (poate dura câteva minute pe bucket-uri mari).
    Doar citire (pasul 1 din gigi:station-stock; pașii de scriere NU sunt expuși)."""
    cmd = ("export SS_URL=\"$(.venv/bin/python -c 'import sys;sys.path.insert(0,\".\");from api.scan import _ss_creds;"
           "print(_ss_creds()[0])')\" SS_TOK=\"$(.venv/bin/python -c 'import sys;sys.path.insert(0,\".\");"
           "from api.scan import _ss_creds;print(_ss_creds()[1])')\" && ")
    cmd += _py("station_report.py", ["--code=" + cod, "--out=%s/station_live.json" % OUTDIR])
    return _vps(cmd, timeout=900)


@mcp.tool()
def receptie_container(container: str, statie: str, lista: str = "", include_grandia: bool = False,
                       include_necunoscute: bool = False, de_la: str = "", motiv: str = "", apply: bool = False,
                       force: bool = False) -> str:
    """RECEPȚIA unui container în AWB Arona: pune marfa pe „Fără raft · <stație>" (LOC-UZINA-2 / LOC-TELIU).
    container = „C66" sau mai multe („C54, 55, 56"); statie = Uzina 2 / Teliu (Bartolomeu refuzat: acolo marfa se
    scanează pe raft). Liniile vin din container_expected (sincronizat lunea din foaia „Tom - recepții containere")
    sau din `lista` lipită („ha-0941 3000, ha-1177 2000", cu UN container). Sare Grandia (include_grandia=true o pune),
    liniile fără SKU și ambalajele necunoscute în catalog (include_necunoscute=true); codul de bare ambiguu oprește.
    Pe fiecare SKU scade ce s-a RECEPȚIONAT deja (MOTIV „Receptie Cxx" / jurnal receipts) și ce a pus deja depozitul
    pe rafturi sau pe „Fără raft" de la sosire (fereastra de_la = data sosirii − 1 zi). DRY-RUN implicit (tabel per SKU:
    foaie / recepționat / pus deja / DE PUS / înainte→după). apply=true scrie DOAR coloana DE PUS (user receptie-owner,
    MOTIV, receipts, container_meta.arrived_at), după backup al locations.db + jurnal; refuză un container deja
    recepționat parțial și aceeași recepție repetată în 24h fără force=true. Masterul stock-sync se actualizează la
    rularea awb_to_stocksync (L–V 15:30 / 23:30) — nu se scrie în stock-sync și nici în Shopify (D-023)."""
    args = ["receptie", "--container", container, "--statie", statie, "--lista", lista, "--de-la", de_la,
            "--motiv", motiv]
    for flag, on in (("--include-grandia", include_grandia), ("--include-necunoscute", include_necunoscute),
                     ("--apply", apply), ("--force", force)):
        if on:
            args.append(flag)
    return _vps(_py("-", args), STOC_SCRIERE, timeout=300, ok_rc=(0, 3))


@mcp.tool()
def mutare_stoc(lista: str, din: str, in_: str, rafturi: str = "", de_la: str = "", fara_scanare: bool = False,
                prag: int = 5, motiv: str = "", apply: bool = False, force: bool = False) -> str:
    """MUTARE de marfă între stații (Bartolomeu / Uzina 2 / Teliu) în AWB Arona, fără dublă numărare.
    lista = cum scrie ownerul: „1844 -400 0115-240", „HA-0115 240", „0302-80buc" (1844 -> ha-1844), sau „azi" =
    ce a apărut NOU azi pe rafturile destinației (≥ prag buc/SKU) și are stoc la sursă („ce s-a pus azi la Bartolomeu
    trebuie scăzut din Uzina"). din / in_ = stațiile. Spre BARTOLOMEU: se scade DOAR la sursă și doar cât a scanat
    Bartolomeu (restul rămâne la sursă, altfel dispare de pe site; fara_scanare=true scade oricum, la cererea
    ownerului); la Bartolomeu nu se scrie nimic. Spre Uzina 2 / Teliu: adaugă pe „Fără raft" al destinației (minus ce
    a scanat deja) și scade la sursă, în aceeași tranzacție. Nu scade a doua oară ce a trecut deja prin aplicație
    („din Fără raft · Uzina 2", transfer între stații) sau printr-o mutare anterioară cu MOTIV. Sursa = „Fără raft"
    al stației (apoi Depozit mic); dacă nu ajunge, se oprește și listează rafturile — alegi cu rafturi="ha-1844=RF-…".
    de_la = începutul ferestrei (implicit azi). DRY-RUN implicit; apply=true scrie (user corectie-owner, MOTIV,
    backup locations.db + jurnal), refuză aceeași listă în 24h fără force=true. Masterul se actualizează la awb_to_stocksync."""
    args = ["mutare", "--lista", lista, "--din", din, "--in", in_, "--rafturi", rafturi, "--de-la", de_la,
            "--prag", prag, "--motiv", motiv]
    for flag, on in (("--fara-scanare", fara_scanare), ("--apply", apply), ("--force", force)):
        if on:
            args.append(flag)
    return _vps(_py("-", args), STOC_SCRIERE, timeout=300, ok_rc=(0, 3))


@mcp.tool()
def reorder_plan(brand: str, days: int = 28, lead_days: int = 30, safety_days: int = 14, top: int = 40,
                 only_reorder: bool = False) -> str:
    """Cât și când reaprovizionezi fiecare SKU al unui brand: viteza din metrics.inventory_daily_snapshots, cover în zile,
    cantitatea de comandat = viteză × (lead + safety) − onHand − incoming, 🔴 = se termină înainte să sosească marfa.
    brand = nume (ILIKE) ex. Grandia / Esteban. ⚠️ Stocul e cel din Shopify (snapshot), nu raftul. Doar citire."""
    args = ["--brand", brand, "--days", str(days), "--lead-days", str(lead_days), "--safety-days", str(safety_days),
            "--top", str(top)] + (["--only-reorder"] if only_reorder else [])
    try:
        r = subprocess.run(["uv", "run", "-q", REORDER] + args, capture_output=True, text=True, env=_env(), timeout=300, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "reorder_planner: timeout (300 s)"
    return _fmt(r.stdout, r.stderr, r.returncode)


if __name__ == "__main__":
    mcp.run()
