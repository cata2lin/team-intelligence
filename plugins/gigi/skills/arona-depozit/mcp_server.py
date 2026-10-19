# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","paramiko>=3.4"]
# ///
"""arona-depozit MCP — operațiile de DEPOZIT: coada de print pe stații (Order Hub → Printing), istoricul și resetul
unui print, rafturile, culegerea (picking), loturile, scanările și utilizatorii aplicației de scanare AWB Arona.

Strat SUBȚIRE peste `depozit.py` (același folder; testat de `test_depozit.py`), care cheamă rutele existente:
Order Hub `/api/print/*` (acces local OH) și aplicația de scanare `scripts.arona.ro/api/*` (contul de serviciu awb-svc);
plus două citiri read-only directe pe bazele scanerului (`scripts/depozit_sql.py`, sqlite mode=ro, prin ssh).

Read-only implicit. Scrierile (print_reset, scanner_aproba, scanner_aloca_statie, scanner_acces) fac PROBĂ și scriu
doar cu apply=true, după OK-ul omului. Secretele vin din KB și nu se printează; tokenurile stau doar în memorie.
NU construiește/trimite loturi pe WhatsApp: fluxul ăla e în Order Hub (vezi print_istoric, stația „WhatsApp").

Register: claude mcp add --scope user arona-depozit -- uv run <abs path>/mcp_server.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import logging  # noqa: E402

logging.getLogger("paramiko").setLevel(logging.WARNING)

import depozit as D  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("arona-depozit")


# ─────────── PRINT (Order Hub) ───────────
@mcp.tool()
def print_coada(statie: str = "", zile: int = 30, facute: str = "", detaliu: bool = False) -> str:
    """Câte etichete are de PRINTAT fiecare stație ACUM, din coada Order Hub (exact ce vede omul în Printing):
    pe stație (Parfumuri / Bartolomeu / Uzina 2 / Neplecate), pe magazin și pe categoriile de print cu grupele mari
    (SKU × bucăți). statie = filtru (substring); zile = fereastra pe data comenzii (implicit 30); facute = 'oh' /
    'altii' (cine a făcut eticheta). Doar citire — printul se face din orderhub.arona.ro/app/printing."""
    return D.ruleaza("print_coada", statie=statie, zile=zile, facute=facute, detaliu=detaliu)


@mcp.tool()
def print_istoric(statie: str = "", cauta: str = "", limit: int = 15) -> str:
    """Istoricul printurilor din Order Hub (nou → vechi): id-ul printului, ora (UTC), stația, ce s-a printat, câte
    etichete, loturile de pe scaner și dacă a fost resetat. cauta = o comandă sau un AWB (doar printurile care o
    conțin); statie = Parfumuri / Bartolomeu / Uzina 2 / Neplecate / Altele / WhatsApp (loturile trimise de OH pe
    grup). Doar citire. Id-ul de aici intră în print_reset."""
    return D.ruleaza("print_istoric", statie=statie, cauta=cauta, limit=limit)


@mcp.tool()
def print_reset(print_id: int = 0, awb: str = "", motiv: str = "", parola: str = "", apply: bool = False) -> str:
    """Pune ÎNAPOI ÎN COADĂ un print eșuat: tot printul (print_id, din print_istoric) sau o singură etichetă (awb —
    se ia cel mai nou print care o conține). Fără apply = PROBĂ: arată etichetele încă marcate printate și loturile
    de pe scaner care se anulează. apply=true cheamă resetul Order Hub (același ca butonul din Istoric) și cere
    `motiv` + `parola` = PAROLA DE RESET PRINT pe care o dă OMUL (nu e în KB; nu o reține). OH scrie auditul.
    ⚠ Doar dacă etichetele N-AU ieșit pe hârtie — altfel se printează de două ori."""
    return D.ruleaza("print_reset", print_id=print_id, awb=awb, motiv=motiv, parola=parola, apply=apply)


# ─────────── UTILIZATORII SCANERULUI ───────────
@mcp.tool()
def scanner_utilizatori(statie: str = "", cauta: str = "", doar_neaprobati: bool = False) -> str:
    """Conturile aplicației de scanare (AWB Arona / scripts.arona.ro): id, nume, cont, rol, aprobat / email
    neconfirmat (*), stațiile alocate și drepturile de scaner (stock, locations). Semnalează conturile DUBLE (același
    nume). statie = doar cei alocați stației; cauta = nume/cont; doar_neaprobati = cei care încă nu pot intra. Doar citire."""
    return D.ruleaza("scanner_utilizatori", statie=statie, cauta=cauta, doar_neaprobati=doar_neaprobati)


@mcp.tool()
def scanner_aproba(user_id: int, rol: str = "user", apply: bool = False) -> str:
    """Aprobă un cont de scaner (approved=1 + rolul user/admin). PROBĂ fără apply. Atenție: un cont cu emailul
    NECONFIRMAT tot nu se poate loga — proba o spune. rol='admin' dă acces la tot (inclusiv la utilizatori)."""
    return D.ruleaza("scanner_aproba", user_id=user_id, rol=rol, apply=apply)


@mcp.tool()
def scanner_aloca_statie(user_id: int, statii: str, apply: bool = False) -> str:
    """Alocă stațiile unui om în aplicația de scanare: statii = listă separată prin virgulă (Bartolomeu, Uzina 2,
    Parfumuri, Teliu); șir gol = toate. Validat pe stațiile reale. PROBĂ (înainte → după) fără apply."""
    return D.ruleaza("scanner_aloca_statie", user_id=user_id, statii=statii, apply=apply)


@mcp.tool()
def scanner_acces(user_id: int, capabilitati: str, apply: bool = False) -> str:
    """Drepturile de scaner ale unui cont non-admin: capabilitati = „stock,locations” (stock = editează stocul Shopify
    din app; locations = locații/rafturi admin); șir gol = niciuna. Păstrează celelalte permisiuni ale contului
    (module/tab-uri din dashboard). PROBĂ fără apply."""
    return D.ruleaza("scanner_acces", user_id=user_id, capabilitati=capabilitati, apply=apply)


# ─────────── RAFTURI ───────────
@mcp.tool()
def raft_continut(raft: str, statie: str = "", istoric: int = 10) -> str:
    """Ce e pe un RAFT: produse, cantități, de când stau acolo, plus ultimele `istoric` mișcări (pus/scos/cules/
    mutat, cine, comanda). raft = codul RF-… sau numele (A1, F1, „Depozit mic”); numele se repetă pe stații →
    dă și statie. Doar citire."""
    return D.ruleaza("raft_continut", raft=raft, statie=statie, istoric=istoric)


@mcp.tool()
def unde_e_produsul(sku: str, picks: int = 8) -> str:
    """UNDE e fizic un produs: rafturile și bucket-urile „Fără raft” care îl au, pe stații, cu cantități, plus
    ultimele mișcări pe rafturi (inclusiv culegerile, cu comanda și omul). sku = SKU exact (ex. HA-0364).
    Pentru stocul din stock-sync/Shopify: arona-stock stoc_produs. Doar citire."""
    return D.ruleaza("unde_e_produsul", sku=sku, picks=picks)


@mcp.tool()
def rafturi_statie(statie: str, top: int = 25) -> str:
    """Rafturile unei stații: câte reale, care sunt GOALE, buc pe categorii și marfa încă NEpusă pe raft
    („Fără raft” / Depozit mic / Rebuturi, top SKU). statie = Bartolomeu / Uzina 2 / Parfumuri / Teliu.
    Pozițiile negative: arona-stock stoc_statie. Doar citire."""
    return D.ruleaza("rafturi_statie", statie=statie, top=top)


@mcp.tool()
def produs_istoric(sku: str, zile: int = 30, top: int = 30) -> str:
    """Jurnalul unui SKU pe rafturi (shelf_events, cu sinonimele lui): sumar pe acțiune în `zile` (pick, add,
    transfer, reduce, remove, return…) și ultimele `top` mișcări (stație, raft, înainte→după, cine, comanda). Doar citire."""
    return D.ruleaza("produs_istoric", sku=sku, zile=zile, top=top)


# ─────────── CULEGERE / SCANĂRI ───────────
@mcp.tool()
def picking_live() -> str:
    """Culegerea de AZI, live: total AWB/colete, pe stație, ritmul, fiecare om (comenzi, colete, ultima scanare,
    lotul alocat cu culese/total) și loturile de azi neterminate. Doar citire."""
    return D.ruleaza("picking_live")


@mcp.tool()
def picking_raport(de_la: str = "", pana_la: str = "", luna: str = "", statie: str = "", om: str = "") -> str:
    """PRODUCTIVITATEA la cules pe o perioadă (YYYY-MM-DD sau luna=YYYY-MM; implicit luna curentă): total, pe
    stație, pe om (AWB, colete, zile, colete/zi, prima→ultima scanare) și pe zi. statie / om = filtre. Doar citire."""
    return D.ruleaza("picking_raport", de_la=de_la, pana_la=pana_la, luna=luna, statie=statie, om=om)


@mcp.tool()
def picking_comanda(cod: str) -> str:
    """S-a cules comanda / AWB-ul X? Toate scanările ei (cine, când, picked/unpicked/returned) și loturile de print
    în care a intrat. Doar citire."""
    return D.ruleaza("picking_comanda", cod=cod)


@mcp.tool()
def lot_continut(lot: str) -> str:
    """Ce e într-un lot de print de pe scaner (LOT-…): eticheta, stația, nr etichete, cui e alocat, comenzile. Doar citire."""
    return D.ruleaza("lot_continut", lot=lot)


@mcp.tool()
def picking_neculese(ore: int = 24, zile: int = 7, statie: str = "", top: int = 30) -> str:
    """Etichete PRINTATE (intrate într-un lot) și NECULESE de peste `ore`, cu eticheta încă vie și nepreluată de
    curier — riscul de colet-fantomă / marfă uitată. Pe stație, pe lot și cele mai vechi. Sare etichetele anulate/
    înlocuite, comenzile anulate și ce a plecat deja. Starea vine din oglinda OH a scanerului (~10 min); verifică
    cu oh_comanda înainte de o acțiune. Doar citire."""
    return D.ruleaza("picking_neculese", ore=ore, zile=zile, statie=statie, top=top)


@mcp.tool()
def scanari_anomalii(zile: int = 1, top: int = 20) -> str:
    """Anomalii de scanare pe ultimele `zile`: coduri scanate NEGĂSITE (top + câte), AWB-uri culese de doi oameni,
    „unpicked” pe om, culegeri fără comandă. Primul pas la „nu găsește AWB-ul / produsul”. Doar citire."""
    return D.ruleaza("scanari_anomalii", zile=zile, top=top)


@mcp.tool()
def lipsa_marfa(zile: int = 7, sku: str = "", toate: bool = False) -> str:
    """Semnalările „NU GĂSESC” din aplicație (omul n-a găsit marfa pe raft) din ultimele `zile`, cu comenzile încă
    deschise pe SKU-urile lor. toate=true include și cele închise. Doar citire."""
    return D.ruleaza("lipsa_marfa", zile=zile, sku=sku, toate=toate)


if __name__ == "__main__":
    mcp.run()
