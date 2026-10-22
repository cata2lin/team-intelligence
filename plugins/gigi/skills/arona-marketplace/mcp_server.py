# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","paramiko>=3.4"]
# ///
"""arona-marketplace MCP — marketplace-urile ARONA: eMAG RO/BG/HU, Trendyol RO/BG/GR, Okazii, Pepita, OLX, CEL,
operate PRIN Order Hub (OH). Comenzile marketplace intră în OH ca orice comandă (sursa de adevăr, NU AWBprint).

Strat SUBȚIRE peste scripturile testate, fără să schimbe codul OH:
  - OH (161.97.69.226, container orderhub-web): scripturile lui din /app/scripts (marketplace_oferte, _leaga_oferte,
    _listeaza, okazii_listeaza: toate PROBĂ implicit, scriu doar cu --aplica) + scripts/oh_mkt.py (citiri READ ONLY,
    trimis pe stdin);
  - serverul de scripturi (84.46.242.181): scripts/ty_live.py peste funcțiile testate din trendyol_stock_push.py
    (Product v2 — v1 dă 426 din 13-oct), trendyol_split.sh (dry-run fără --execute);
  - local: ../trendyol-awb/scripts/trendyol_awb.py list, scripts/emag_api.py (tracker-ul eMAG, doar GET).

Read-only implicit. Scrierile: apply=false = PROBĂ. Unde API-ul n-are probă (prețul Trendyol) → preview + apply=true +
confirm exact din preview. Secretele (EMAG_UI_API_TOKEN, chei SSH) vin din KB și nu se printează.

Register: claude mcp add --scope user arona-marketplace -- uv run <abs path>/mcp_server.py
"""
import json
import logging
import math
import os
import shlex
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "core", "scripts"))
KB = os.path.join(CORE, "kb.py")
OH_MKT = os.path.join(HERE, "scripts", "oh_mkt.py")
TY_LIVE = os.path.join(HERE, "scripts", "ty_live.py")
EMAG_API = os.path.join(HERE, "scripts", "emag_api.py")
TY_AWB = os.path.normpath(os.path.join(HERE, "..", "trendyol-awb", "scripts", "trendyol_awb.py"))
OH_HOST = "root@161.97.69.226"
OH_PY = "docker exec -i -w /app -e PYTHONPATH=/app orderhub-web /opt/venv/bin/python"
VPS_PRE = "cd /root/Scripturi && set -a && . ./.env && set +a && "
MAX_OUT = 60000

# regula ownerului (15-oct-2026, memoria marketplace-pret-tinta-profit-20): profit NET 20% după tot
RETURURI, RECLAME, PROFIT, TVA, TRANSPORT = 0.03, 0.10, 0.20, 1.21, 15.0
COMISION = {"emag": 0.17, "trendyol": 0.18, "pepita": 0.15}

sys.path.insert(0, CORE)
logging.getLogger("paramiko").setLevel(logging.WARNING)

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("arona-marketplace")


# ─────────── infrastructură ───────────
def _env():
    e = dict(os.environ); e.pop("VIRTUAL_ENV", None); e["PYTHONIOENCODING"] = "utf-8"; return e


def _kb(k):
    try:
        return subprocess.run(["uv", "run", "-q", KB, "secret-get", k], capture_output=True, text=True,
                              timeout=60, env=_env(), stdin=subprocess.DEVNULL).stdout.strip()
    except Exception:
        return ""


def _fmt(out, err, rc):
    out = (out or "").strip() or "(fără output)"
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT] + "\n… (trunchiat)"
    if rc not in (0, None):
        out += "\n[exit %s] %s" % (rc, (err or "").strip()[-600:])
    return out


def _local(script, args, timeout=300, env=None):
    try:
        r = subprocess.run(["uv", "run", "-q", script] + args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env or _env(), timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "⚠ a depășit %d s" % timeout
    return _fmt(r.stdout, r.stderr, r.returncode)


def _oh(cmd, stdin=None, timeout=900):
    """Comandă pe serverul OH (ssh cu cheie, ca order-hub/oh_delivery_rate.py)."""
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", OH_HOST, cmd], input=stdin,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "⚠ a depășit %d s pe OH (o probă nu scrie nimic; o aplicare POATE să fi rulat — verifică cu mkt_listare)" % timeout
    out = _fmt(r.stdout, r.stderr, r.returncode)
    if "is not running" in (r.stderr or ""):
        out += "\n(containerul orderhub-web se repornește — un deploy OH e în curs; reîncearcă peste un minut)"
    return out


def _oh_read(*args):
    with open(OH_MKT, encoding="utf-8") as f:
        src = f.read()
    return _oh(OH_PY + " - " + " ".join(shlex.quote(str(a)) for a in args), stdin=src, timeout=300)


def _oh_script(script, args):
    return _oh(OH_PY.replace(" -i ", " ") + " scripts/%s %s" % (script, " ".join(shlex.quote(str(a)) for a in args)))


def _oh_file(text):
    """Pune un fișier temporar în container (/tmp, nu în codul OH) și întoarce calea."""
    p = "/tmp/mkt_mcp_%s.txt" % uuid.uuid4().hex[:10]
    r = _oh("docker exec -i orderhub-web sh -c %s" % shlex.quote("cat > " + p), stdin=text, timeout=60)
    if "[exit" in r:
        raise RuntimeError(r)
    return p


def _oh_rm(p):
    _oh("docker exec orderhub-web rm -f %s" % shlex.quote(p), timeout=60)


def _vps(cmd, stdin_file=None, timeout=600):
    import arona_ssh
    for k in ("PROFIT_SSH_HOST", "PROFIT_SSH_USER"):
        if not os.environ.get(k):
            os.environ[k] = arona_ssh._secret(k) or (arona_ssh.DEFAULT_HOST if k.endswith("HOST") else arona_ssh.DEFAULT_USER)
    try:
        cl = arona_ssh.connect()
    except Exception as e:
        return "SSH către serverul de scripturi a eșuat: %s" % str(e)[:500]
    try:
        i, o, e = cl.exec_command(VPS_PRE + cmd, timeout=timeout)
        if stdin_file:
            with open(stdin_file, "rb") as f:
                i.write(f.read())
        i.channel.shutdown_write()
        out, err = o.read().decode("utf-8", "replace"), e.read().decode("utf-8", "replace")
        rc = o.channel.recv_exit_status()
    except Exception as ex:
        return "eroare pe serverul de scripturi: %s: %s" % (type(ex).__name__, str(ex)[:300])
    finally:
        cl.close()
    return _fmt(out, err, rc)


def _ty(*args):
    return _vps(".venv/bin/python - " + " ".join(shlex.quote(str(a)) for a in args), stdin_file=TY_LIVE)


def _pret_tinta(cogs, comision, transport=TRANSPORT, reclame=True):
    brut = (cogs + transport) / (1 - comision - RETURURI - (RECLAME if reclame else 0) - PROFIT) * TVA
    return round(math.floor(brut) + 0.99, 2)


def _skus(skus):
    out = [s.strip() for s in (skus or "").replace("\n", ",").split(",") if s.strip()]
    if not out:
        raise ValueError("lista de SKU-uri e goală")
    return out


# ─────────── CITIRI ───────────
@mcp.tool()
def mkt_conexiuni() -> str:
    """Conexiunile marketplace din Order Hub (id, marketplace, storefront, activ, import comenzi, mod stoc, magazinul
    local al comenzilor) + listările pe stare (aprobat / in_aprobare / eroare) + ultimul import și ultima eroare.
    id-uri: 1 eMAG RO, 2 BG, 3 HU, 4 OLX, 5 Trendyol, 6 CEL, 7 Pepita, 8 Okazii. Doar citire."""
    return _oh_read("conexiuni")


@mcp.tool()
def mkt_comenzi(marketplace: str = "", comanda: str = "", zile: int = 7, doar_neexpediate: bool = False,
                limita: int = 50) -> str:
    """Comenzile marketplace din ORDER HUB (sursa de adevăr — NU AWBprint): a intrat comanda eMAG/Trendyol/Okazii?
    are AWB? e printată (coada_print = are AWB neprintat)? status colet, factură, SKU-uri. marketplace = emag |
    trendyol | okazii | pepita (gol = toate); comanda = număr (sau parte, ex. EMAG545361673) — atunci zile se ignoră;
    doar_neexpediate = fără colet plecat la curier. Fără date personale. Doar citire."""
    return _oh_read("comenzi", marketplace.strip().lower() or "-", comanda.strip() or "-", int(zile),
                    "1" if doar_neexpediate else "0", int(limita))


@mcp.tool()
def mkt_listare(sku: str, trendyol_live: bool = True) -> str:
    """Starea unui SKU (sau barcode) pe TOATE marketplace-urile: listările OH (eMAG RO/BG/HU, Okazii, Pepita, OLX:
    stare, id ofertă, PNK, preț/stoc/status eMAG, validare documentație 9=aprobată, erori, ultimul stoc trimis) +
    (trendyol_live) varianta live de pe Trendyol RO (onSale, cantitate, salePrice/listPrice, blacklist/arhivat).
    ⚠️ date_marketplace.pret/stoc = citite la legare (citit_la), nu neapărat azi. Doar citire."""
    out = _oh_read("listare", sku.strip())
    if trendyol_live:
        out += "\n\n── Trendyol RO live ──\n" + _ty("produse", sku.strip(), 10, "RO")
    return out


@mcp.tool()
def trendyol_produse(filtru: str = "", storefront: str = "RO", limita: int = 50) -> str:
    """Catalogul Trendyol LIVE (Product v2 `/products/approved`; v1 dă 426 din 13-oct): sumar (variante, onSale,
    stoc 0, blacklist, arhivate, blocate) + variantele care conțin `filtru` în SKU/titlu sau au exact barcode-ul.
    storefront RO | BG | GR. ⚠️ Capcana onSale: Trendyol pune onSale=false CÂND stocul e 0 — nu înseamnă inactiv
    (doar archived/locked/blacklisted = inactiv); stocul îl ține stock-sync, NU-l împinge de aici. Doar citire."""
    return _ty("produse", filtru.strip() or "-", int(limita), storefront)


@mcp.tool()
def trendyol_awb_list() -> str:
    """Comenzile Trendyol cu etichetă de descărcat (Picking/Created/Invoiced, nedescărcate) din app-ul scripts.arona.ro
    — `trendyol_awb.py list`, nu atinge nimic. (Printul Trendyol s-a mutat în OH pe 14-oct: vezi și mkt_comenzi
    marketplace=trendyol pt AWB/printat.) Descărcarea (marchează downloaded) NU e expusă aici."""
    return _local(TY_AWB, ["list"])


@mcp.tool()
def trendyol_split(zile: int = 14, comanda: str = "", apply: bool = False) -> str:
    """Pachetele Trendyol multi-bucată de spart în colete individuale (DPD taxează greșit la greutate) —
    trendyol_split.sh pe serverul de scripturi. apply=false = DRY-RUN (doar listează; `Not splittable` = UnPacked/
    Shipped). apply=true = execută split-ul prin API (ireversibil). Cronul rulează oricum la 5 min cu --cron."""
    a = ["--days", str(int(zile))] + (["--order", comanda.strip()] if comanda.strip() else []) + (["--execute"] if apply else [])
    return _vps("/root/Scripturi/trendyol_split.sh " + " ".join(shlex.quote(x) for x in a), timeout=600)


@mcp.tool()
def emag_tracker(actiune: str, params_json: str = "{}", format: str = "table") -> str:
    """Tracker-ul eMAG (emg.arona.ro, tot catalogul eMAG.ro + istoric, doar GET): prețuri, istoric, concurenți,
    buy-box, movers, ranking. actiune = summary | search | product | timeline | history | competitors | score |
    reviews | questions | tracked | movers | ranking | taxonomy | taxonomy-node | categories | sellers | seller |
    sweeps. params_json = flag-urile emag_api.py fără „--", ex. {"q": "covor baie", "limit": 20}, {"pnk":
    "D8XTW9MBM"}, {"kind": "price_drops", "window": "7d", "min_reviews": 20}. format = table | json | csv."""
    try:
        p = json.loads(params_json or "{}")
        assert isinstance(p, dict)
    except Exception:
        return "params_json trebuie să fie un obiect JSON · nimic rulat"
    a = [actiune.strip(), "--format", format if format in ("table", "json", "csv") else "table"]
    for k, v in p.items():
        k = str(k).strip().lstrip("-").replace("_", "-")
        if k in ("format", "action"):
            continue
        if v is True:
            a.append("--" + k)
        elif v not in (False, None, ""):
            a += ["--" + k, str(v)]
    env = _env()
    if not env.get("EMAG_UI_API_TOKEN"):
        env["EMAG_UI_API_TOKEN"] = _kb("EMAG_UI_API_TOKEN")
    return _local(EMAG_API, a, timeout=180, env=env)


@mcp.tool()
def emag_concurenta(sku: str, zile: int = 90) -> str:
    """Prețul NOSTRU pe eMAG vs piață pentru un SKU: găsește PNK-ul ofertei noastre (listarea OH, conexiunea eMAG RO)
    și citește din tracker fișa produsului + concurenții/buy-box (dacă e urmărit în profunzime). Doar citire."""
    raw = _oh_read("listare", sku.strip())
    try:
        d = json.loads(raw[raw.index("{"):])
        dm = next((l["date_marketplace"] for l in d["listari_oh"]
                   if l.get("conexiune") == 1 and isinstance(l.get("date_marketplace"), dict)
                   and l["date_marketplace"].get("pnk")), None)
    except Exception:
        return "nu am putut citi listarea OH:\n" + raw[:2000]
    if not dm:
        return "SKU-ul %s n-are ofertă eMAG RO legată în OH (fără PNK). Caută-l cu emag_tracker search." % sku
    pnk = dm["pnk"]
    out = "PNK %s (oferta noastră, eMAG RO; preț citit la legare: %s %s, %s)\n\n" % (
        pnk, dm.get("pret"), dm.get("moneda") or "", dm.get("citit_la"))
    prod = emag_tracker("product", json.dumps({"pnk": pnk, "days": int(zile)}), "table")
    if "404" in prod[:400]:
        q = " ".join(str(dm.get("nume") or "").replace(",", " ").split()[:5])
        out += ("PNK-ul nostru nu e (încă) în catalogul tracker-ului. Produse asemănătoare după titlu („%s”):\n" % q
                + emag_tracker("search", json.dumps({"q": q, "limit": 10}), "table"))
        return out
    out += prod
    comp = emag_tracker("competitors", json.dumps({"pnk": pnk}), "table")
    out += "\n\n── concurenți / buy-box ──\n" + (comp if "404" not in comp[:300] else
                                                 "produsul nu e urmărit în profunzime (cere-i lui Cătălin `emag storefront track add %s`)" % pnk)
    return out


@mcp.tool()
def okazii_feed(conexiune: int = 8, sku: str = "", limita: int = 50) -> str:
    """Conținutul feed-ului XML citit de Okazii (conexiune 8) sau Pepita (7): câte articole sunt listate, câte apar
    în feed cu stoc (stock-sync, plafonat la 20), câte lipsesc (stoc 0), și articolele (titlu, preț, cantitate,
    categorie, nr. poze); sku = filtru. Construit cu funcțiile OH (services/marketplace/feed), sesiune READ ONLY."""
    return _oh_read("feed", int(conexiune), sku.strip() or "-", int(limita))


@mcp.tool()
def emag_pret_proba(conexiune: int = 1) -> str:
    """Ce preț AR trimite sincronizarea de preț OH pe eMAG (prețul magazinului Shopify, fără TVA, curs BCE pt BG/HU)
    față de prețul de acum — `marketplace_oferte.py --pret`, nu scrie nimic (pret_sync e OPRIT, decizia ownerului).
    ⚠️ NU e regula de profit 20% — pt aia vezi mkt_pret_tinta."""
    return _oh_script("marketplace_oferte.py", ["--conexiune", int(conexiune), "--pret"])


@mcp.tool()
def mkt_pret_tinta(cogs: float, marketplace: str = "emag", storefront: str = "RO", pret_actual: float = 0,
                   comision: float = 0, transport: float = TRANSPORT) -> str:
    """Prețul ȚINTĂ pe marketplace după regula aprobată de owner (15-oct): profit NET 20% după comision, transport,
    3% retururi și 10% reclame: preț = (COGS + transport) / (1 − comision − 0,03 − 0,10 − 0,20) × 1,21, la ,99.
    Comision implicit: eMAG 17%, Trendyol 18%, Pepita 15% (Okazii: dă-l explicit). eMAG BG/HU: fără reclame (fără
    10%), iar prețul BG/HU OGLINDEȘTE RO (se controlează din RO). cogs = RON fără TVA (Shopify unitCost).
    Dacă ținta > 2× pret_actual → COGS suspect / nevandabil pe marketplace: NU schimba, du-l la owner."""
    mp = marketplace.strip().lower()
    com = comision or COMISION.get(mp)
    if not com:
        return "comision necunoscut pt %s — dă-l explicit (ex. 0.12)" % mp
    reclame = not (mp == "emag" and storefront.upper() in ("BG", "HU"))
    t = _pret_tinta(float(cogs), com, float(transport), reclame)
    net = t / TVA * (1 - com - RETURURI - (RECLAME if reclame else 0)) - cogs - transport
    r = {"marketplace": mp, "storefront": storefront.upper(), "cogs": cogs, "transport": transport, "comision": com,
         "reclame": RECLAME if reclame else 0, "pret_tinta_cu_tva": t, "profit_net_pe_bucata": round(net, 2)}
    if pret_actual:
        r["pret_actual"] = pret_actual
        r["diferenta_pct"] = round((t / pret_actual - 1) * 100, 1)
        if t > 2 * pret_actual:
            r["atentie"] = "ținta > 2× prețul actual → COGS suspect sau nevandabil pe marketplace: listează-l ownerului"
        elif pret_actual < t:
            r["atentie"] = "prețul actual e SUB țintă (profit < 20%)"
    return json.dumps(r, ensure_ascii=False, indent=1)


# ─────────── SCRIERI (gated) ───────────
@mcp.tool()
def emag_oferte(conexiune: int = 1, candidati: bool = True, oferte: str = "", dezactiveaza: bool = False,
                apply: bool = False, acord_owner: bool = False) -> str:
    """Activează (sau dezactivează) oferte eMAG prin OH — `marketplace_oferte.py`. candidati = ofertele INACTIVE cu
    documentația aprobată și marfă peste tampon; oferte = id-uri eMAG anume („173,174”, are prioritate).
    apply=false = PROBĂ (doar citește). apply=true scrie în eMAG; activarea în MASĂ (candidati) cere și
    acord_owner=true (regula ownerului: fără activări în masă fără acordul lui)."""
    a = ["--conexiune", int(conexiune)]
    if oferte.strip():
        a += ["--oferte", oferte.strip()]
    elif candidati:
        a += ["--candidati"]
    else:
        return "alege candidati=true sau oferte=\"id,id\" · nimic rulat"
    if dezactiveaza:
        a.append("--dezactiveaza")
    if apply:
        if not oferte.strip() and not acord_owner:
            return "activare în masă: cere acord_owner=true (după ce ownerul a văzut proba) · nimic scris"
        a.append("--aplica")
    return _oh_script("marketplace_oferte.py", a)


@mcp.tool()
def mkt_leaga_oferte(conexiune: int = 1, magazine: str = "", apply: bool = False) -> str:
    """Leagă ofertele EXISTENTE de pe marketplace (eMAG) de variantele Shopify, după SKU/EAN —
    `marketplace_leaga_oferte.py`. magazine = id-urile magazinelor sursă în ordinea priorității (ex. „31,17,24,51”).
    apply=false = PROBĂ (sesiune READ ONLY, tipărește potrivirile/conflictele). apply=true scrie DOAR în baza OH
    (marketplace_listari) — nimic nu pleacă spre eMAG sau Shopify."""
    a = ["--conexiune", int(conexiune)] + (["--magazine", magazine.strip()] if magazine.strip() else [])
    return _oh_script("marketplace_leaga_oferte.py", a + (["--aplica"] if apply else []))


@mcp.tool()
def emag_listeaza(plan_csv: str, conexiune: int = 1, magazine: str = "33,38,26,32,27,37,51,17,24,31,35",
                  doar: str = "", apply: bool = False) -> str:
    """Listează produse Shopify NOI pe eMAG prin OH — `marketplace_listeaza.py`. plan_csv = conținutul planului:
    antet `sku,categorie_id,brand[,titlu][,atribute][,descriere]`, un rând pe produs (categorie_id = frunza eMAG
    permisă contului sau `X:motiv` = blocat). Reguli în script: titlu/descriere RO din Shopify, ≥1 poză, fără
    parfumuri/R211, EAN doar din registrul `ean_alocari` (nu se inventează), oferta existentă se LEAGĂ, nu se
    relistează. apply=false = PROBĂ (citiri, arată blocajele). apply=true trimite în loturi ≤50."""
    try:
        p = _oh_file(plan_csv if plan_csv.endswith("\n") else plan_csv + "\n")
    except Exception as e:
        return "nu am putut pune planul în container: %s" % str(e)[:300]
    a = ["--conexiune", int(conexiune), "--plan", p, "--magazine", magazine.strip()]
    if doar.strip():
        a += ["--doar", doar.strip()]
    try:
        return _oh_script("marketplace_listeaza.py", a + (["--aplica"] if apply else []))
    finally:
        _oh_rm(p)


@mcp.tool()
def okazii_adauga(skus: str, conexiune: int = 8, magazine: str = "26,33,38,32,27,37,51,17", pret: str = "magazin",
                  comision: float = 0, apply: bool = False) -> str:
    """Adaugă SKU-uri în feed-ul Okazii (conexiune 8; Pepita = 7) — `okazii_listeaza.py`. skus = listă cu virgulă
    sau rânduri. Conținutul din primul magazin sursă cu varianta ACTIVĂ + poze + descriere; stocul în feed =
    stock-sync plafonat la 20. pret = magazin (implicit, ales de owner 16-oct) | formula (regula profit 20%, cere
    comision, ex. 0.12; fără COGS în Shopify produsul se blochează). apply=false = PROBĂ (arată ce s-ar lista și ce
    e blocat, ex. „deja pe Okazii”); apply=true pune articolele în feed (Okazii îl citește zilnic la 6)."""
    try:
        lst = _skus(skus)
    except ValueError as e:
        return "%s · nimic rulat" % e
    if pret not in ("magazin", "formula"):
        return "pret = magazin | formula · nimic rulat"
    if pret == "formula" and not comision:
        return "pret=formula cere comision (ex. 0.12) · nimic rulat"
    p = _oh_file("\n".join(lst) + "\n")
    a = ["--conexiune", int(conexiune), "--skus", p, "--magazine", magazine.strip(), "--pret", pret]
    if comision:
        a += ["--comision", float(comision)]
    try:
        return _oh_script("okazii_listeaza.py", a + (["--aplica"] if apply else []))
    finally:
        _oh_rm(p)


@mcp.tool()
def trendyol_pret(sku: str, pret_vanzare: float, cogs: float, pret_lista: float = 0, storefront: str = "RO",
                  sub_tinta_acord_owner: bool = False, apply: bool = False, confirm: str = "") -> str:
    """Schimbă prețul unei variante Trendyol (Product v2 + `price-and-inventory`, funcțiile testate din
    trendyol_stock_push.py). Trendyol N-ARE dry-run → apply=false = PREVIEW (citește varianta, arată acum vs nou și
    ținta regulii de profit 20%). Pentru scriere: apply=true + confirm = exact șirul `confirm_cerut` din preview.
    Regula ownerului: pret_vanzare ≥ ținta (cogs RON fără TVA, transport 15, comision 18%) — sub țintă doar cu
    sub_tinta_acord_owner=true. Cantitatea se retrimite NESCHIMBATĂ (stocul îl ține stock-sync); varianta cu stoc 0
    rămâne onSale=false. pret_lista implicit = max(pret_lista actual, pret_vanzare)."""
    sku = sku.strip()
    pv = round(float(pret_vanzare), 2)
    if pv <= 0 or cogs <= 0:
        return "pret_vanzare și cogs trebuie > 0 · nimic trimis"
    tinta = _pret_tinta(float(cogs), COMISION["trendyol"])
    cheie = "%s@%s@%s" % (storefront.upper(), sku, pv)
    if pv < tinta and not sub_tinta_acord_owner:
        return json.dumps({"blocat": "pret_vanzare %.2f < ținta %.2f (profit net 20%%, comision 18%%, transport 15)"
                           % (pv, tinta), "ce_faci": "propune ownerului ținta sau cere-i acordul explicit "
                           "(sub_tinta_acord_owner=true)", "nimic_trimis": True}, ensure_ascii=False, indent=1)
    if pret_lista and pret_lista < pv:
        return "pret_lista < pret_vanzare (Trendyol refuză: TSF PSF den büyük olamaz) · nimic trimis"
    prev = _ty("pret", sku, pv, float(pret_lista or pv), "0", storefront)
    try:
        d = json.loads(prev[prev.index("{"):])
        lista = float(pret_lista or max(pv, float(d["acum"].get("listPrice") or 0)))
    except Exception:
        return prev
    if not pret_lista and lista != pv:
        prev = _ty("pret", sku, pv, lista, "0", storefront)
    head = json.dumps({"tinta_regula_20": tinta, "sub_tinta": pv < tinta, "confirm_cerut": cheie,
                       "pret_lista_folosit": lista}, ensure_ascii=False)
    if not apply:
        return head + "\n" + prev + "\n→ ca să scrii: apply=true, confirm=\"%s\" (după OK-ul omului)" % cheie
    if confirm.strip() != cheie:
        return head + "\nconfirm lipsă/greșit (trebuie exact %s) · nimic trimis" % cheie
    return head + "\n" + _ty("pret", sku, pv, lista, "1", storefront)


if __name__ == "__main__":
    mcp.run()
