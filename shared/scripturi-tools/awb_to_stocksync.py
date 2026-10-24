#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Impinge stocul FIZIC din AWB Arona in MASTERUL stock-sync.

De ce exista: pana acum singura sursa de adevar pentru stoc era Shopify. De la 21-aug-2026 sursa
e numaratoarea din AWB Arona (rafturile RF-* = Bartolomeu, LOC-UZINA-2 = Uzina 2). TELIU NU e
inventariat fizic, deci nu-l atingem: cantitatea lui de acum se pastreaza ca atare in total.

    BARTOLOMEU = AWB(Bartolomeu)   UZINA_2 = AWB(Uzina 2)   — scrise PE DEPOZIT (warehouseCode)
    TELIU = cifra noastra / 0 daca numarat si golit / gramada stock-sync (NU Shopify: e ecoul lui)
    (pana la 24-sep: total = AWB + Teliu din Shopify, fara depozit => bucla, vezi tinte_pe_depozit)

Rulare: de doua ori pe zi (systemd timer)
  15:30 RO — dupa ce se opreste scanarea in Bartolomeu
  23:30 RO — dupa cronul `ship_deduct` (23:00 RO) care scade ce a plecat cu curierul din Uzina 2

REZERVARI (01-oct-2026, vezi awb_rezervari.py): raftul numara si bucatile VANDUTE care n-au
plecat inca. Tinta pe depozit = raft + inapoi − rezervat, cu granita = ultima livrare stock-sync a
masterului (comenzile de dupa ea le scade reconcilierea). Fara asta jobul repunea la vanzare marfa
vanduta de doua ori pe zi (covorul negru: 22 pe raft, 23 vandute, jobul a publicat 22).
AUTORITATE (D-023): pentru fiecare master numarat aici, jobul declara in stock-sync depozitele pe
care le numara; acolo o cifra tastata in Shopify peste ce a primit magazinul nu mai e adoptata.

Implicit e DRY-RUN. Scrie doar cu --apply.
"""
import argparse, csv, json, os, sqlite3, smtplib, sys, time, collections

# --- owner-mail -> briefing/alerts.md (scripturi-39) ---
try:
    import smtplib as _sm, email as _em
    _OWNER_ADDR = {"gheorghe.beschea@overheat.agency", "contact@nocturna.ro"}
    _orig_sendmail = _sm.SMTP.sendmail
    def _guard_sendmail(self, from_addr, to_addrs, msg, *a, **k):
        tos = [to_addrs] if isinstance(to_addrs, str) else list(to_addrs)
        rest = [t for t in tos if t not in _OWNER_ADDR]
        if len(rest) < len(tos):
            try:
                import briefing_alert as _ba
                _raw = msg if isinstance(msg, str) else msg.decode("utf-8", "replace")
                _m = _em.message_from_string(_raw)
                _pl = _m.get_payload(decode=True)
                _bt = _pl.decode("utf-8","replace") if isinstance(_pl,(bytes,bytearray)) else (_m.get_payload() if isinstance(_m.get_payload(),str) else "")
                _ba.scrie(_m.get("Subject", "alerta"), _bt)
            except Exception:
                pass
        if not rest:
            return {}
        return _orig_sendmail(self, from_addr, rest, msg, *a, **k)
    _sm.SMTP.sendmail = _guard_sendmail
except Exception:
    pass
# --- end owner-mail guard ---
from datetime import datetime, timedelta
from email.mime.text import MIMEText
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import awb_rezervari as rz  # noqa: E402
LOC_DB = "/root/Scripturi/data/locations.db"
ENV_FILE = "/root/Scripturi/.env"
STORES_CSV = "/root/Scripturi/.shopify_stores.csv"
KB_PY = "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
API = "2026-04"
EMAIL_TO = "gheorghe.beschea@overheat.agency"
STATE = "/root/Scripturi/data/awb_to_stocksync_state.json"
SUPRAVANDUTE = "/root/Scripturi/data/awb2ss_supravandute.json"   # ultima lista: comenzi peste raft
SCAN_EVENTS_DB = "/root/Scripturi/data/scan_events.db"          # marcarile „pregatita" (etichete moarte)
STOC_LA_ZI = "/root/Scripturi/data/stock_uptodate_until.json"    # aceeasi lista ca ship_deduct
# Importul AWBprint aduce comenzile cu cateva minute intarziere. Rezervarile au nevoie de TOATE
# comenzile de dinaintea ultimei livrari stock-sync, deci asteptam pana apare in AWBprint o comanda
# MAI NOUA decat livrarea (importul e in ordine). Daca nu apare in atat, rularea se opreste (cod 1)
# si systemd o reia peste 5 minute — mai bine nimic decat rezervari prea mici (supra-vanzare).
IMPORT_ASTEPTARE_MAX = timedelta(minutes=12)
# SKU-uri lasate DELIBERAT pe dinafara: numaratoarea lor e incompleta, asa ca a le scrie in master
# ar duce stocul sub realitate. Se scot de aici imediat ce stim cate bucati are cutia.
# (Se listeaza la FIECARE rulare — o excludere tacuta e mai periculoasa decat cifra gresita.)
IN_ASTEPTARE = {
    # Sase produse sosite in containerele din august (C46/C47/C48) si numarate doar partial la
    # scanul din 15 august. Masterul se potriveste cu ce a venit; numaratoarea nu. A scrie acum
    # ar taia peste 3.000 de bucati. Se scot pe masura ce depozitul le renumara.
    "ha-1766": "C48 (5 aug): 1.000 venite, numarate 36 (4%)",
    "ha-1662": "C47 (5 aug): 1.000 venite, numarate 377 (38%)",
    # ha-1500 scos 16-oct (bilant): C46=480, plecate 481 -> raftul (6) e adevarul, nu mai asteptam numaratoare
    # ha-1699 scos 16-oct (owner): renumarat complet, 992 pe raft (D18 392 + E15 600)
    # ha-1464 scos 23-oct (Vali, depozit): restul din C46 s-a vandut -> raftul e adevarul
    # ha-1661 scos 23-oct (Vali, depozit): eroare de receptie, C48 150 n-a existat -> raftul e adevarul
}
# magazin (nume in AWBprint) -> prefix in .shopify_stores.csv
PREFIX = {"magdeal.ro": "MAG", "casaofertelor.ro": "BON", "reduceribune.ro": "RED",
          "ofertelezilei.ro": "OFER", "grandia.ro": "GRAN", "bonhaus.cz": "CZ",
          "bonhaus.bg": "BONBG", "bonhaus.pl": "PL", "nocturna.ro": "NOC",
          "nocturnalux.ro": "LUX", "covoria.ro": "COV", "carpetto.ro": "CARP",
          "gento.ro": "GEN", "apreciat.ro": "APR"}


def env(path=ENV_FILE):
    d = {}
    for line in open(path):
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"').strip("'")
    return d


def kb_secret(key):
    """Secretele stau DOAR in baza de cunostinte — niciodata in fisiere sau in cod."""
    import subprocess
    # kb.py are nevoie de KB_DATABASE_URL, care sta in .env — nu in mediul cronului/systemd.
    e = {**os.environ, **env()}
    out = subprocess.run(["/root/.local/bin/uv", "run", KB_PY, "secret-get", key],
                         capture_output=True, text=True, timeout=120,
                         cwd="/root/Scripturi", env=e).stdout.strip()
    if not out or out.startswith("[kb]"):
        return ""
    return out.splitlines()[-1]


def fizic(con=None):
    """{sku: {'bart': n, 'uz2': n, 'teliu': n}} — numaratoarea din AWB Arona.

    Cu `con`, citeste in conexiunea (si tranzactia) apelantului si n-o inchide — rezervarile se
    citesc in ACEEASI tranzactie, ca un pick sa nu cada intre raft si comenzi.

    Teliu e un caz aparte: gestiunea NU e inventariata in intregime. Unde AVEM o cifra pe
    `LOC-TELIU` (HA-urile numarate + diferentele neexplicate parcate acolo de owner) o folosim
    pe aia; unde nu, luam ce arata Shopify, ca sa nu stergem marfa despre care nu stim nimic.
    """
    proprie = con is None
    if proprie:
        con = sqlite3.connect(LOC_DB)
    out = collections.defaultdict(lambda: {"bart": 0, "uz2": 0, "teliu": 0})

    # ⚠️ 17-sep: statia unei locatii se citeste DOAR din `shelves.station`. Codul NU o spune.
    # Clasificarea de dinainte („daca nu e LOC-UZINA-2 sau LOC-TELIU, atunci e Bartolomeu")
    # trimitea TOATE rafturile RF-* pe „bart", desi majoritatea sunt la Uzina 2:
    # 29.745 de bucati numarate la Uzina 2 intrau in total ca Bartolomeu.
    # Parea ca merge fiindca bucketele (LOC-*) chiar erau corecte — se spargea doar pe rafturi.
    statii = {code: (st or "") for code, st in con.execute("SELECT code, station FROM shelves")}

    def _zona(code):
        s = statii.get(code)
        if s is None:
            return None                      # locatie care nu exista in `shelves`
        if s.startswith("Uzina"):
            return "uz2"
        if s.startswith("Teliu"):             # inclusiv „Teliu - stoc suspect"
            return "teliu"
        if s.startswith("Bartolomeu"):
            return "bart"
        return None                           # statie goala: nu ghicim

    necunoscute = []
    for code, sku, qty in con.execute("SELECT code, sku, qty FROM shelf_items"):
        # REBUTURILE (LOC-REBUT-<STATIE>) NU sunt stoc vandabil: marfa e scoasa din vanzare, deci
        # nu are ce cauta in masterul care alimenteaza magazinele. Fara linia asta cadeau pe "bart".
        if code.startswith("LOC-REBUT"):
            continue
        cheie = _zona(code)
        if cheie is None:
            # NU sarim tacut: un `continue` aici scade marfa din total si produce delte
            # negative in masa pe toate magazinele. Mai bine oprim rularea si se uita un om.
            necunoscute.append((code, sku, qty))
            continue
        out[sku][cheie] += qty

    if necunoscute:
        print("\n\u26d4 OPRIT: %d randuri stau pe locatii fara statie in `shelves` "
              "(nu pot sti la ce depozit sunt):" % len(necunoscute))
        for code, sku, qty in necunoscute[:10]:
            print("   %-20s %-20s %d buc" % (code, sku, qty))
        if proprie:
            con.close()
        raise SystemExit(3)

    # 31-aug: un SKU ajuns la 0 nu mai are RAND in shelf_items (aplicatia il sterge), deci
    # pana acum iesea complet din sincronizare si ramanea in magazine cu ultima cifra POZITIVA.
    # Asa se vindea zilnic „Mini Masina de Cusut" (GD-CS-52964) desi inventarul o pusese pe 0:
    # 5 comenzi anulate intr-o saptamana. Orice SKU care a avut vreodata stoc prin noi si acum
    # n-are rand = 0 REAL, si trebuie impins ca atare. Garda relativa de mai jos decide daca
    # taierea e destul de mare cat sa ceara ochi de om.
    for (sku,) in con.execute(
            "SELECT DISTINCT sku FROM shelf_events WHERE sku IS NOT NULL AND sku <> ''"):
        if sku not in out:
            out[sku] = {"bart": 0, "uz2": 0, "teliu": 0, "epuizat": True}
    if proprie:
        con.close()
    return dict(out)


def rezervari(pg_url):   # NEFOLOSITA din 10-sep (vezi comentariul din main)
    """{sku: buc} — marfa deja PROMISA in comenzi pe care cronul de expediere n-a scazut-o inca.

    De ce: stocul din AWB Arona e FIZIC si scade abia cand marfa pleaca efectiv (cron 22:00).
    Intre plasarea comenzii si expediere, marfa e in depozit dar e a clientului. Shopify isi
    scade singur numarul la plasarea comenzii; daca noi scriem inapoi stocul fizic, REDESCHIDEM
    spre vanzare marfa deja vanduta. Dovada (GD-DEP-13353, 31-aug): pe 24-aug magazinul avea 26
    si noi am scris 30; pe 27-aug avea 19 si am scris 23; pe 31-aug avea 0 si am scris 19.
    Rezultatul: comenzi peste stoc, apoi anulari (14 buc anulate intr-o saptamana la produsul asta,
    5 comenzi anulate consecutiv la GD-CS-52964).

    Garda: o comanda se numara DOAR daca e mai noua decat ultima numaratoare a SKU-ului
    (ultimul eveniment `set`). Altfel am scadea de doua ori — inventarul deja n-a gasit marfa aia.
    """
    import psycopg2
    con = sqlite3.connect(LOC_DB)
    vazute = {r[0] for r in con.execute("SELECT order_number FROM shipped_seen")}
    numarat = {}          # sku -> momentul ultimei numaratori
    for sku, ts in con.execute(
            "SELECT lower(sku), MAX(ts) FROM shelf_events WHERE action='set' GROUP BY 1"):
        numarat[sku] = (ts or "")[:10]
    con.close()

    pg = psycopg2.connect(pg_url)
    try:
        cur = pg.cursor()
        cur.execute("""
            SELECT o.order_number, lower(li->'inventory_item'->>'sku'),
                   (li->>'quantity')::numeric::int, o.frisbo_created_at::date
            FROM orders o, jsonb_array_elements(o.line_items::jsonb) li
            WHERE o.frisbo_created_at > now() - interval '90 days'
              AND COALESCE(o.aggregated_status,'') NOT IN
                  ('cancelled','back_to_sender','returned','refused')
              AND li->'inventory_item'->>'sku' IS NOT NULL""")
        out = collections.Counter()
        for onum, sku, q, zi in cur.fetchall():
            if not sku or onum in vazute:
                continue
            if str(zi) <= numarat.get(sku, ""):   # inventarul e mai nou decat comanda
                continue
            out[sku] += int(q or 0)
        return dict(out)
    finally:
        pg.close()


def teliu_numarat():
    """SKU-urile pe care AWB Arona le-a numarat VREODATA la Teliu (LOC-TELIU* sau raft cu statia Teliu).

    Pentru ele Teliu = cifra noastra, si cand randul a disparut (qty 0 => aplicatia il sterge)
    inseamna 0 numarat, nu „nu stim". Asa a hotarat ownerul la 17 si 21-sep („tai stocul suspect").
    """
    con = sqlite3.connect(LOC_DB)
    coduri = {c for c, st in con.execute("SELECT code, station FROM shelves")
              if (st or "").startswith("Teliu")}
    out = set()
    for code, sku in con.execute("SELECT DISTINCT code, sku FROM shelf_events WHERE sku IS NOT NULL"):
        if code.startswith("LOC-TELIU") or code in coduri:
            out.add(sku)
    con.close()
    return out


def gramezi(base, tok, barcodes):
    """{master_id: {"total": n, "piles": {DEPOZIT: n}}} — cat tine stock-sync pe fiecare depozit.

    /v1/stock da pe depozit SUMA MAGAZINELOR (ce a alocat la ultima rulare), nu gramada interna,
    care nu e salvata nicaieri: reconcilierea o reface la fiecare rulare din sumele magazinelor.
    Diferenta pana la total (unitati nealocate) se imparte pe gramezi proportional, cum face si
    stock-sync. Depozitele care apar aici sunt cele EXPUSE (listing inregistrat pe locatie).
    """
    out = {}
    lista = sorted(set(barcodes))
    for i in range(0, len(lista), 40):
        r = requests.get(base + "/v1/stock", params={"barcodes": ",".join(lista[i:i + 40])},
                         headers={"Authorization": "Bearer " + tok}, timeout=60, verify=False)
        r.raise_for_status()
        for p in (r.json() or {}).get("products") or []:
            piles = {w["warehouse"]: int(w["units"] or 0)
                     for w in (p.get("byWarehouse") or []) if w.get("warehouse")}
            total = int(p.get("totalUnits") or 0)
            alocat = sum(piles.values())
            rest = total - alocat
            if piles and rest:
                baza = sum(piles.values())
                if baza > 0:
                    cote = {w: rest * u // baza for w, u in piles.items()}
                    ramas = rest - sum(cote.values())
                    for w in sorted(piles, key=lambda k: -piles[k])[:abs(ramas)]:
                        cote[w] += 1 if ramas > 0 else -1
                else:
                    cote = {w: 0 for w in piles}
                    cote[_primul(piles, ("UZINA_2", "BARTOLOMEU", "TELIU"))] = rest
                piles = {w: piles[w] + cote[w] for w in piles}
            out[p["masterProductId"]] = {"total": total, "piles": piles, "alocat": alocat,
                                         # D-023: cand a livrat stock-sync ultima oara (granita
                                         # rezervarilor) si ce depozite numaram deja oficial
                                         "deliveredAt": p.get("deliveredAt"),
                                         "counted": sorted(p.get("countedWarehouses") or []),
                                         # magazinele cu listing in pool: doar vanzarile LOR
                                         # le vede reconcilierea (awb_rezervari.e_vazuta)
                                         "magazine": sorted({s["storeId"] for s in (p.get("stores") or [])
                                                             if s.get("storeId")})}
    return out


def _primul(expuse, ordine):
    return next((w for w in ordine if w in expuse), sorted(expuse)[0])


# unde ajunge cantitatea unui depozit pe care produsul NU e expus (fara listing pe locatie):
# o scriere cu warehouseCode acolo ar fi „misrouted" si ar ateriza pe gramada cea mai mare.
PLIERE = {"BARTOLOMEU": ("UZINA_2", "TELIU"), "UZINA_2": ("BARTOLOMEU", "TELIU"),
          "TELIU": ("UZINA_2", "BARTOLOMEU")}


def tinte_pe_depozit(rec, g):
    """({DEPOZIT: tinta}, [(DEPOZIT, delta)], regula_teliu) pentru un master.

    ⛔ 24-sep: pana azi Teliu se lua din Shopify, locatia „Teliu". Dar din PER_WAREHOUSE
    locatia aia o scrie CHIAR stock-sync (alocarea gramezii TELIU), deci jobul isi citea propriul
    ecou si ii aduna raftul: tinta = raft + master. Scrisa FARA depozit, diferenta ateriza pe
    gramada cea mai mare (TELIU), Uzina 2 ramanea la fel, si la rularea urmatoare diferenta
    reaparea. 36 de mastere, +6.107 buc fantoma in 3 zile (pijamale Rosu 4XL 258 -> 2.236,
    lipici 21 -> 242). Acum: Bartolomeu si Uzina 2 = numaratoarea, scrise PE DEPOZIT; Teliu =
      - cifra noastra, daca exista;
      - 0, daca SKU-ul a fost numarat vreodata la Teliu si randul a disparut;
      - altfel gramada TELIU din stock-sync, MINUS ce lipseste pe Bartolomeu+Uzina 2 (acolo au
        aterizat scrierile vechi fara depozit) — deci o rulare nu poate creste totalul din ecou.
    """
    B, U, T = "BARTOLOMEU", "UZINA_2", "TELIU"
    piles = dict(g["piles"])
    gap = rec["bart"] + rec["uz2"] - piles.get(B, 0) - piles.get(U, 0)
    if rec["teliu"] > 0:
        tT, regula = rec["teliu"], "numarat"
    elif rec["teliu_istoric"]:
        tT, regula = 0, "numarat 0"
    else:
        tT, regula = max(0, piles.get(T, 0) - max(0, gap)), "pastrat"
    tinta = {B: rec["bart"], U: rec["uz2"], T: tT}
    for w in (B, U, T):
        if w not in piles and tinta[w]:
            dest = next((d for d in PLIERE[w] if d in piles), None)
            if dest:
                tinta[dest] += tinta[w]
                tinta[w] = 0
    pasi = [(w, tinta[w] - piles[w]) for w in piles if tinta.get(w, 0) != piles[w]]
    pasi.sort(key=lambda x: -x[1])      # ADAUGA intai, SCADE dupa (vezi mutare-adauga-intai-scade-dupa)
    # Cand stock-sync tine mult stoc NEALOCAT (reguli mici pe magazine), gramezile de mai sus sunt
    # ghicite, iar o mutare intre depozite nu „se prinde": la rularea urmatoare apare iar. Masurat
    # 24-sep: ha-1193-1 avea 451 din 502 nealocate, jobul muta 434 B->U la fiecare rulare. Atunci
    # scriem doar DIFERENTA NETA, pe depozitul cu tinta cea mai mare — fara mutari degeaba.
    tot = g.get("total", sum(g["piles"].values()))
    nealocat = tot - g.get("alocat", tot)
    if pasi and nealocat > max(5, 0.2 * tot):
        net = sum(d for _, d in pasi)
        tinte = {w: tinta.get(w, 0) for w in piles}
        pasi = [(max(tinte, key=lambda k: tinte[k]), net)] if net else []
        regula += " (gramezi incerte)"
    return {w: tinta.get(w, 0) for w in piles}, pasi, regula


MAX_DELTA_IMPLICIT = 3000   # garda absoluta (--max-delta); o citeste si raport_stoc.py --umbra


def alege_master(sku, bc_map, bc_shopify, M):
    """(stare, master_id, M[barcode]) pentru un SKU — stare: ok / dezambiguat / fara_master / ambiguu.

    Scoasa din main pe 25-sep ca umbra (raport_stoc.py) sa aleaga EXACT acelasi master ca jobul.
    La ambiguu, al treilea element e lista masterelor gasite.
    """
    gasite = {M[b][0]: M[b] for b in bc_map.get(sku, []) if b in M}
    if not gasite:
        return "fara_master", None, None
    stare = "ok"
    if len(gasite) > 1:
        # 17-sep: AWBprint e o OGLINDA, si e veche — `products.frisbo_updated_at` sta din iulie
        # desi `synced_at` se misca zilnic. Cand un SKU pare sa aiba doua mastere, codul ramas
        # in urma e mereu cel din AWBprint; Shopify (viu) si masterul sunt de acord.
        # Masurat: 7 SKU-uri ambigue, 7 rezolvate de Shopify, 7 coduri orfane doar in AWBprint
        # (`baie-negru` → cod 7865789552438 pe un master FARA niciun listing).
        # Pana acum jobul le sarea complet, deci 7.716 buc fizice nu ajungeau niciodata in magazine.
        din_shopify = {M[b][0]: M[b] for b in (bc_shopify.get(sku) or []) if b in M}
        if len(din_shopify) != 1:
            return "ambiguu", None, list(gasite)
        gasite, stare = din_shopify, "dezambiguat"
    mid, info = list(gasite.items())[0]
    return stare, mid, info


def acumuleaza(pe_master, mid, info, sku, v, t_istoric):
    """Aduna numaratoarea unui SKU pe masterul lui (doua SKU-uri pot alimenta acelasi master)."""
    _, nume, glob = info
    teliu_nostru = v.get("teliu", 0)
    rec = pe_master.setdefault(mid, {"nume": nume, "glob": glob, "tinta": 0, "skus": [],
                                     "bart": 0, "uz2": 0, "teliu": 0, "teliu_istoric": False})
    rec["tinta"] += v["bart"] + v["uz2"] + teliu_nostru
    rec["bart"] += v["bart"]
    rec["uz2"] += v["uz2"]
    rec["teliu"] += teliu_nostru
    rec["teliu_istoric"] = rec["teliu_istoric"] or sku in t_istoric
    rec["skus"].append(sku)
    return rec


def tinta_master(rec, g):
    """Unde duce jobul masterul: {regula, de_la, la, pasi[, gramezi, tinta_dep]}. `pasi` gol = nimic de scris."""
    if not g or not g["piles"]:
        # fara niciun listing expus: nu exista gramezi, se scrie totalul fara depozit (ca inainte),
        # iar Teliu necunoscut ramane cat e (nu-l citim din Shopify).
        glob = g["total"] if g else rec["glob"]
        if rec["teliu"] > 0 or rec["teliu_istoric"]:
            la = rec["bart"] + rec["uz2"] + rec["teliu"]
        else:
            la = max(rec["bart"] + rec["uz2"], glob)
        return {"regula": "fara listing", "de_la": glob, "la": la,
                "pasi": [(None, la - glob)] if la != glob else []}
    tinta, pasi, regula = tinte_pe_depozit(rec, g)
    return {"regula": regula, "de_la": g["total"], "la": sum(tinta.values()), "pasi": pasi,
            "gramezi": g["piles"], "tinta_dep": tinta}


def taiere_mare(p):
    """GARDA RELATIVA: numaratoarea gaseste sub jumatate din ce ar trebui sa fie pe raft (>=100 buc).

    Ce „ar trebui" sa fie pe raft = ce vinde stock-sync + ce e vandut si inca necules (rezervat) −
    ce s-a scos deja pentru comenzi de dupa livrare (inapoi). Asa o scadere venita din REZERVARI
    (marfa vanduta, nu pierduta) nu mai pare numaratoare incompleta; fara rezervari, formula e
    exact cea veche (de_la vs la)."""
    rez = p.get("rezervat", 0)
    asteptat = p["de_la"] + rez - p.get("inapoi", 0)
    gasit = p.get("fizic_total", p["la"])
    taiere = asteptat - gasit
    return (p["delta"] < 0 and taiere >= 100 and asteptat > 0 and gasit < 0.5 * asteptat)


# V82 (29-sep, owner: «rezolvă sistemul să ia noile valori»). Garda de mai sus sărea la NESFÂRȘIT și
# corecțiile făcute DE OAMENI: „Grandia fără raft Uzina 2 → 0” (owner, 22-sep) n-a ajuns în magazine la
# 7 produse în 7 zile (gd-mob-19050: 227 în master, 3 fizic), iar comenzile intrau pe marfă inexistentă.
# Garda a fost pusă pentru o NUMĂRĂTOARE INCOMPLETĂ (HA-1766: 810 → 1, 26-aug), unde nimeni n-a scos
# nimic — pur și simplu restul n-a fost numărat. Diferența se vede în istoric: o corecție explicită
# (set/remove/reduce în jos, făcută de un om sau de owner, cu motiv) explică scăderea; o numărătoare
# incompletă nu are nicio scădere în spate. Deci: tăierea mare SE SCRIE când corecțiile explicite
# din ultimele REDUCERI_ZILE acoperă cel puțin jumătate din ea, sau când ownerul a aprobat SKU-ul în
# APROBATE. Restul rămâne parcat și se listează în PARCATE, pentru aprobare.
REDUCERI_ZILE = 45
APROBATE = "/root/Scripturi/data/awb2ss_aprobate.json"    # {"sku": "cine/când a aprobat"}
PARCATE = "/root/Scripturi/data/awb2ss_parcate.json"      # ultima listă de tăieri mari neaplicate
BARCODE_ISTORIC = "/root/Scripturi/data/awb2ss_barcode_shopify.json"   # {prefix: {cand, coduri}} — ultima traversare reusita


def reduceri_explicite(skus, zile=REDUCERI_ZILE):
    """Bucăți scoase EXPLICIT în AWB Arona pe ultimele `zile`, pe SKU-urile date (și sinonimele lor).

    Intră: set/remove/reduce în jos făcute de oameni (cont cu @), de owner (corectie-/receptie-owner),
    de numărători (inventar-*, numaratoare-*) sau orice rând cu MOTIV. Nu intră: culesul, cronul de
    expediere și mutările raft ↔ Fără raft („numarat la raftul”, „mutat la raftul”), care nu schimbă totalul."""
    skus = {(x or "").lower() for x in skus if x}
    if not skus:
        return 0
    con = sqlite3.connect(LOC_DB)
    try:
        rude = set(skus) | {(a or "").lower() for a, k in con.execute("SELECT alias, canonical FROM sku_aliases")
                            if (k or "").lower() in skus}
        prag = (datetime.now() - timedelta(days=zile)).strftime("%Y-%m-%d")
        ph = ",".join("?" * len(rude))
        r = con.execute(
            "SELECT COALESCE(SUM(qty_before - qty), 0) FROM shelf_events WHERE lower(sku) IN (%s) "
            "AND action IN ('set','remove','reduce') AND qty_before IS NOT NULL AND qty < qty_before "
            "AND ts >= ? AND COALESCE(username,'') <> 'cron-expediere' "
            "AND COALESCE(order_ref,'') NOT LIKE 'numarat la raftul%%' "
            "AND COALESCE(order_ref,'') NOT LIKE 'mutat la raftul%%' "
            "AND (username LIKE '%%@%%' OR username IN ('corectie-owner','receptie-owner') "
            "     OR username LIKE 'inventar%%' OR username LIKE 'numaratoare%%' "
            "     OR COALESCE(order_ref,'') LIKE 'MOTIV%%')" % ph, (*sorted(rude), prag)).fetchone()
        return int(r[0] or 0)
    finally:
        con.close()


def aprobate():
    try:
        return {k.lower(): v for k, v in (json.load(open(APROBATE)) or {}).items()}
    except Exception:
        return {}


def taiere_permisa(p, apr=None):
    """True dacă o tăiere mare (taiere_mare) are voie să se scrie: aprobată de owner sau explicată de
    corecții explicite (>= jumătate din scădere). Pune în p și 'reduceri_explicite' / 'motiv_taiere'."""
    skus = [x for x in str(p.get("sku", "")).split("+") if x]
    apr = aprobate() if apr is None else apr
    if any(x.lower() in apr for x in skus):
        p["motiv_taiere"] = "aprobat de owner"
        return True
    red = reduceri_explicite(skus)
    p["reduceri_explicite"] = red
    taiere = p["de_la"] + p.get("rezervat", 0) - p.get("inapoi", 0) - p.get("fizic_total", p["la"])
    if red >= 0.5 * max(taiere, 0):
        p["motiv_taiere"] = "corecții explicite %d buc" % red
        return True
    return False


def shopify_teliu(skus, stores):
    """({sku: bucati la Teliu}, {sku: [barcode]}) — o singura traversare a magazinelor.

    Barcode-ul se ia si de aici pentru ca in catalogul AWBprint lipseste la zeci de produse
    (66 de SKU-uri / ~29.000 buc la 21-aug), iar fara el nu se poate gasi masterul.

    09-oct: un magazin care nu raspunde NU mai opreste rularea. Covoria a fost inchis pe 07-oct,
    Shopify raspundea {'errors': 'Not Found'}, iar KeyError-ul de aici a tinut jobul cazut doua
    zile (nicio numaratoare in magazine; comenzi intrate pe marfa deja corectata pe raft).
    Magazinul se sare si i se iau barcode-urile de la ultima traversare reusita (BARCODE_ISTORIC).
    """
    Q = ("query($c:String){ productVariants(first:100, after:$c){ pageInfo{hasNextPage endCursor} "
         "nodes{ sku barcode inventoryItem{ inventoryLevels(first:10){ nodes{ location{ name } "
         'quantities(names:["available"]){ quantity } } } } } } }')
    out = collections.Counter()
    coduri = collections.defaultdict(set)
    try:
        istoric = json.load(open(BARCODE_ISTORIC))
    except Exception:
        istoric = {}
    for pref in sorted(set(PREFIX.values())):
        if pref not in stores:
            continue
        cursor = None
        # pe magazin, ca o traversare rupta la jumatate sa nu lase coduri partiale
        teliu, ale_lui = collections.Counter(), collections.defaultdict(set)
        try:
            while True:
                d = gql(stores, pref, Q, {"c": cursor})
                pv = (d.get("data") or {}).get("productVariants")
                if pv is None:
                    raise RuntimeError("raspuns fara date: %s" % str(d.get("errors") or d)[:120])
                for n in pv["nodes"]:
                    s = (n.get("sku") or "").lower()
                    if s not in skus:
                        continue
                    if (n.get("barcode") or "").strip():
                        ale_lui[s].add(n["barcode"].strip())
                    for lvl in n["inventoryItem"]["inventoryLevels"]["nodes"]:
                        if lvl["location"]["name"] == "Teliu":
                            teliu[s] += lvl["quantities"][0]["quantity"]
                if not pv["pageInfo"]["hasNextPage"]:
                    break
                cursor = pv["pageInfo"]["endCursor"]
        except Exception as exc:
            vechi = istoric.get(pref) or {}
            print("⚠ magazinul %s (%s) nu raspunde: %s — sarit; barcode-uri din istoric: %d SKU (%s)"
                  % (pref, stores[pref][0], exc, len(vechi.get("coduri") or {}),
                     vechi.get("cand") or "fara istoric"))
            for s, bs in (vechi.get("coduri") or {}).items():
                if s in skus:
                    coduri[s].update(bs)
            continue
        out.update(teliu)
        for s, bs in ale_lui.items():
            coduri[s].update(bs)
        istoric[pref] = {"cand": datetime.now().strftime("%Y-%m-%d %H:%M"),
                         "coduri": {s: sorted(bs) for s, bs in ale_lui.items()}}
    try:
        json.dump(istoric, open(BARCODE_ISTORIC, "w"), ensure_ascii=False)
    except Exception as exc:
        print("   (nu am putut salva %s: %s)" % (BARCODE_ISTORIC, exc))
    return out, {k: sorted(v) for k, v in coduri.items()}


def gql(stores, pref, query, variables=None):
    shop, token = stores[pref]
    for _ in range(8):
        r = requests.post("https://%s/admin/api/%s/graphql.json" % (shop, API),
                          headers={"X-Shopify-Access-Token": token,
                                   "Content-Type": "application/json"},
                          json={"query": query, "variables": variables or {}}, timeout=60)
        if r.status_code == 429:
            time.sleep(4); continue
        d = r.json()
        if d.get("errors") and "THROTTLED" in json.dumps(d["errors"]):
            time.sleep(4); continue
        return d
    raise RuntimeError("Shopify throttled: " + pref)


def masters(base, token):
    """{barcode: (master_id, nume, globalAvailable)} — catalogul complet stock-sync."""
    out, cursor = {}, None
    while True:
        url = base + "/v1/master-products?limit=200" + (("&cursor=" + cursor) if cursor else "")
        d = requests.get(url, headers={"Authorization": "Bearer " + token},
                         timeout=60, verify=False).json()
        for m in d.get("masterProducts", []):
            bc = (m.get("barcodeNormalized") or m.get("barcode") or "").strip()
            if bc:
                out[bc] = (m["id"], m.get("name") or "", int(m.get("globalAvailable") or 0))
        cursor = d.get("nextCursor")
        if not cursor:
            break
    return out


def barcodes_per_sku_oh(skus):
    """{sku: [barcode, ...]} din catalogul canonic Orders (`GET /api/cs/produse`): fiecare SKU al unui master primește
    barcode-ul masterului (pasul 1 din jurnal/2026-10-24-plan-lant-stoc-fara-awbprint.md, 24-oct-2026). SKU-ul în litere
    mici, ca în AWBprint. Orice eroare = excepție (apelantul nu cade tăcut pe altă sursă)."""
    import awb_rezervari as _ar
    url, tok = _ar.oh_acces()
    d = _ar._oh_get(url, tok, "/api/cs/produse", {"cu_raft": "false"})
    vrute = {str(x).lower() for x in skus}
    out = collections.defaultdict(list)
    for p in d.get("produse") or []:
        bc = str(p.get("barcode") or "").strip()
        if not bc:
            continue
        for sk in p.get("skuri") or []:
            k = str(sk or "").strip().lower()
            if k in vrute and bc not in out[k]:
                out[k].append(bc)
    # SKU-urile de raft care nu sunt pe nicio listare (aliasurile `raft_sku` / SKU unic din Orders): o cerere în lot
    lipsa = sorted(k for k in vrute if not out.get(k))
    # „SURPRIZA-EST 20" = parfumul 20 de pe Esteban (stoc_master.sku_fizic): „20" singur e ambiguu între magazine, deci
    # surprizele se întreabă pe magazinul lor
    _SURPRIZA_MAG = {"surpriza-est": "6f9e22-9d.myshopify.com", "surpriza-gt": "ix5bxc-hr.myshopify.com"}
    pe_mag = collections.defaultdict(list)
    for k in list(lipsa):
        for pref, mag in _SURPRIZA_MAG.items():
            if k.startswith(pref):
                pe_mag[mag].append(k)
                lipsa.remove(k)
                break
    for mag, coduri in pe_mag.items():
        r = _ar._oh_get(url, tok, "/api/produs", {"coduri": ",".join(coduri), "magazin": mag})
        for cod, v in ((r or {}).get("produse") or {}).items():
            bc = str((v or {}).get("barcode") or "").strip()
            if bc and (v or {}).get("master_id"):
                k = str(cod).strip().lower()
                if bc not in out[k]:
                    out[k].append(bc)
    for i in range(0, len(lipsa), 200):
        r = _ar._oh_get(url, tok, "/api/produs", {"coduri": ",".join(lipsa[i:i + 200])})
        for cod, v in ((r or {}).get("produse") or {}).items():
            bc = str((v or {}).get("barcode") or "").strip()
            if bc and (v or {}).get("master_id"):
                k = str(cod).strip().lower()
                if bc not in out[k]:
                    out[k].append(bc)
    return out


def barcodes_per_sku(pg_url, skus):
    """{sku: [barcode, ...]} din catalogul AWBprint. Un SKU poate avea mai multe (vezi negru-L).

    COMUTATOR BC_SURSA (24-oct-2026, planul aprobat, pasul 1): `awbprint` (implicit, ca până azi) sau `oh`
    (`barcodes_per_sku_oh`). Umbra (`bc_umbra.py`) le compară pe masterul ales; comutarea doar după 3 rulări cu
    0 mastere diferite."""
    if (os.environ.get("BC_SURSA") or "awbprint").strip().lower() == "oh":
        return barcodes_per_sku_oh(skus)
    import psycopg2
    con = psycopg2.connect(pg_url)
    cur = con.cursor()
    cur.execute("""SELECT lower(sku), barcode FROM products
                   WHERE barcode IS NOT NULL AND btrim(barcode) <> ''
                     AND lower(sku) = ANY(%s) GROUP BY 1,2""", [list(skus)])
    out = collections.defaultdict(list)
    for sku, bc in cur.fetchall():
        out[sku].append(bc.strip())
    con.close()
    return out


def cere_sync(base, tok, motiv, incercari=6):
    """Porneste un sync-run ca magazinele sa primeasca imediat ce a scris jobul.

    stock-sync refuza (409/429) un sync prea apropiat de precedentul; atunci asteapta si reincearca.
    """
    for i in range(incercari):
        try:
            r = requests.post(base + "/v1/sync-runs", headers={"Authorization": "Bearer " + tok,
                              "Content-Type": "application/json", "Idempotency-Key": "awb2ss-sync-%s-%d" % (datetime.now().strftime("%Y%m%d-%H%M%S"), i)},
                              json={"runType": "MANUAL", "reason": motiv}, timeout=120, verify=False)
            if r.status_code in (200, 201, 202):
                return ((r.json() or {}).get("syncRun") or {}).get("status") or "pornit"
            if r.status_code not in (409, 429):
                return "esuat %s %s" % (r.status_code, r.text[:120])
        except Exception as exc:
            if i == incercari - 1:
                return "esuat %s" % exc
        time.sleep(60)
    return "esuat: refuzat de %d ori" % incercari


def masoara_decalaj(pg_url, stores, esantion=6):
    """Cat e AWBprint (`frisbo_created_at`) in urma lui Shopify `createdAt`, masurat ACUM.

    Ia ultimele comenzi din 2-3 magazine si le citeste ora din Shopify. None daca nu se poate
    masura sau perechile nu sunt de acord (atunci 0: comenzile par mai vechi -> se rezerva in plus,
    directia sigura). Vezi awb_rezervari.masoara_decalaj."""
    import psycopg2
    try:
        con = psycopg2.connect(pg_url)
        cur = con.cursor()
        cur.execute("""SELECT order_number, frisbo_created_at FROM orders
                       WHERE frisbo_created_at > (now() AT TIME ZONE 'UTC') - interval '2 days'
                         AND order_number ~ '^(OFER|RED|MAG|BON|GRAN)[0-9]+$'
                       ORDER BY frisbo_created_at DESC LIMIT 40""")
        rows = cur.fetchall()
        con.close()
    except Exception as exc:
        print("  (decalaj: AWBprint %s)" % exc)
        return None
    perechi, pe_magazin = [], collections.Counter()
    Q = "query($q:String){ orders(first:1, query:$q){ nodes{ name createdAt } } }"
    for onum, creata in rows:
        pref = rz.prefix(onum)
        if pref not in stores or pe_magazin[pref] >= 2 or len(perechi) >= esantion:
            continue
        try:
            d = gql(stores, pref, Q, {"q": "name:%s" % onum})
            noduri = ((d.get("data") or {}).get("orders") or {}).get("nodes") or []
            if noduri and noduri[0]["name"] == onum:
                perechi.append((creata, rz.parse_utc(noduri[0]["createdAt"])))
                pe_magazin[pref] += 1
        except Exception as exc:
            print("  (decalaj: %s %s)" % (onum, exc))
    return rz.masoara_decalaj(perechi)


def declara_autoritate(base, tok, pe_master, G, parcate=(), eticheta=""):
    """D-023: spune stock-sync ce depozite NUMARAM pentru fiecare master pe care il administram.

    Bartolomeu + Uzina 2 mereu; Teliu doar unde e numarat. Un master parcat (numaratoare probabil
    incompleta) sau in asteptare primeste lista GOALA: cat timp nu avem incredere in numaratoare,
    cifrele tastate in Shopify trebuie sa ramana valabile (D-013). Se trimite doar ce s-a schimbat.
    """
    vrut = {}
    for mid, rec in pe_master.items():
        if mid in parcate:
            vrut[mid] = []
            continue
        dep = ["BARTOLOMEU", "UZINA_2"]
        if rec.get("teliu", 0) > 0 or rec.get("teliu_istoric"):
            dep.append("TELIU")
        vrut[mid] = sorted(dep)
    schimbari = [{"masterProductId": m, "warehouses": w} for m, w in vrut.items()
                 if sorted((G.get(m) or {}).get("counted") or []) != w]
    if not schimbari:
        return "neschimbata (%d mastere)" % len(vrut)
    trimise = 0
    for i in range(0, len(schimbari), 500):
        bucata = schimbari[i:i + 500]
        r = requests.post(base + "/v1/master-products/counted-warehouses",
                          headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json",
                                   "Idempotency-Key": "awb2ss-autoritate-%s-%s-%d"
                                                      % (eticheta, datetime.now().strftime("%Y%m%d-%H%M%S"), i)},
                          json={"reason": "AWB Arona numara aceste depozite (awb_to_stocksync %s)" % eticheta,
                                "items": bucata}, timeout=120, verify=False)
        if r.status_code != 200:
            return "ESUAT %s %s (trimise %d)" % (r.status_code, r.text[:160], trimise)
        trimise += (r.json() or {}).get("updated", 0)
    return "%d mastere actualizate din %d" % (trimise, len(vrut))


def mail(subject, html, E):
    try:
        msg = MIMEText(html, "html", "utf-8")
        msg["Subject"] = subject
        msg["From"] = E["SMTP_USER"]
        msg["To"] = EMAIL_TO
        with smtplib.SMTP(E.get("SMTP_HOST", "smtp.gmail.com"),
                          int(E.get("SMTP_PORT", "587")), timeout=30) as s:
            s.starttls()
            s.login(E["SMTP_USER"], E["SMTP_PASS"])
            s.sendmail(E["SMTP_USER"], [EMAIL_TO], msg.as_string())
    except Exception as exc:                       # mailul nu are voie sa pice jobul
        print("  ! mail esuat: %s" % exc)


class ImportIncomplet(Exception):
    """AWBprint n-a primit încă nicio comandă de după ultima livrare stock-sync: rezervările ar ieși prea mici."""


def barcode_din_istoric(skus):
    """{sku: [barcode]} din ultima traversare Shopify reușită (BARCODE_ISTORIC) — pentru rularea pe produs, care nu
    traversează toate magazinele."""
    try:
        istoric = json.load(open(BARCODE_ISTORIC))
    except Exception:
        istoric = {}
    out = collections.defaultdict(set)
    for v in istoric.values():
        for s, bs in ((v or {}).get("coduri") or {}).items():
            if s in skus:
                out[s].update(bs)
    return {k: sorted(v) for k, v in out.items()}


def doar_scaderi_pe_nenumarate(t, counted):
    """Regula sync-ului pe produs (owner, 20-oct-2026): pe depozitele NUMĂRATE (D-023, `counted`) raftul decide în
    ambele sensuri; pe celelalte doar scade. Un pas fără depozit (master fără listing) e tratat ca nenumărat."""
    pasi = [(w, d if (w and w in counted) else min(d, 0)) for w, d in t["pasi"]]
    pasi = [(w, d) for w, d in pasi if d]
    if pasi == t["pasi"]:
        return t
    t = dict(t, pasi=pasi, la=t["de_la"] + sum(d for _, d in pasi))
    t["regula"] = t["regula"] + " (doar scăderi pe nenumărate)"
    return t


def calculeaza_tinte(base, tok, E, stores, scop=None, doar_scaderi_nenumarate=False, umbra=None):
    """FUNCȚIA COMUNĂ (20-oct-2026): ce ar trebui scris în master — pentru TOATE produsele (`scop=None`, rularea de
    două ori pe zi) sau doar pentru masterele SKU-urilor din `scop` (sync-ul pe produs). Aceeași numărătoare, aceleași
    rezervări, aceleași reguli pe depozit; gărzile pe produs (taiere_mare / taiere_permisa / --max-delta) le aplică
    apelantul cu aceleași funcții, plafoanele pe toată rularea doar rularea completă.

    Ridică ImportIncomplet când AWBprint e în urma ultimei livrări (rezervările ar ieși prea mici)."""
    # UMBRA S3 (22-oct-2026, „Stoc cu o definiție"): `umbra` = {"linii_oh": [...]} — liniile din Order Hub
    # (awb_rezervari.comenzi_deschise_oh). Pe ACELAȘI instantaneu (raft, pick-uri, gramezi) se mai calculează trei
    # variante, pe copii ale lui pe_master, fără să atingă planul: rezervările din OH cu granița stock-sync („oh"),
    # și stoc_unic = raft − TOT ce e vândut și necules („unic_oh" / „unic_awb", fără graniță). None = nimic în plus.
    # S3 UNIFICAT (22-oct-2026): sursa liniilor = comutatorul din baza OH, consumatorul „push"
    # (awb_rezervari.sursa_comenzi; orice eroare = awbprint). Env-ul AWB2SS_COMENZI e retras.
    scop = {s.lower() for s in scop} if scop is not None else None
    plan = []
    F = fizic()
    print("AWB Arona: %d SKU | Bartolomeu %d | Uzina 2 %d | Teliu numarat %d" % (
        len(F), sum(v["bart"] for v in F.values()), sum(v["uz2"] for v in F.values()),
        sum(v["teliu"] for v in F.values())))
    # cantitatile Shopify de la Teliu NU se mai folosesc (erau ecoul stock-sync, vezi
    # tinte_pe_depozit); traversarea ramane pentru barcode-urile care lipsesc din AWBprint.
    if scop is None:
        _, bc_shopify = shopify_teliu(set(F), stores)
    else:
        bc_shopify = barcode_din_istoric(set(F))
    T_ISTORIC = teliu_numarat()
    bc_map = barcodes_per_sku(E["DATABASE_URL_AWBPRINT"], set(F))
    # completeaza din Shopify SKU-urile fara barcode in AWBprint
    recuperate = 0
    for sku, coduri in bc_shopify.items():
        for b in coduri:
            if b not in bc_map[sku]:
                bc_map[sku].append(b)
                recuperate += 1
    print("barcode-uri luate din Shopify (lipseau in AWBprint): %d" % recuperate)
    M = masters(base, tok)
    print("masters in stock-sync: %d (dupa barcode)" % len(M))


    def construieste(Fx):
        """pe_master din numaratoarea Fx (o data pentru lista de mastere, o data pe instantaneul final)."""
        fara_master, ambigue, sarite, dezambiguate = [], [], [], []
    # ⚠️ Se aduna PE MASTER, nu pe SKU. Doua SKU-uri pot trimite la acelasi master
    # (ha-1766 + ha-1766-2): daca fiecare si-ar scrie propria tinta,
    # ultimul l-ar suprascrie pe primul si masterul ar lua cantitatea unui singur SKU.
    # Asa a fost taiat HA-1766 de la 810 la 1 pe 25-aug: raftul avea 63 sub `ha-1766-2`
    # si 1 sub `ha-1766`, iar jobul a scris 1.
    # ⚠️ Adunarea e corecta DOAR cand e acelasi produs fizic. 2275497 + 2275499 (draperii
    # diferite: rejansa / inele) ajungeau aici pentru ca listingul 2275497 fusese duplicat din
    # 2275499 cu tot cu barcode: 29-sep inelele erau 0 pe raft si se vindeau din cele 15 cu
    # rejansa. 2275497 are de atunci cod propriu (5941237754974). Barcode comun intre produse
    # diferite se repara in Shopify (cod nou), nu aici.
        pe_master = {}
        for sku, v in Fx.items():
            if sku in IN_ASTEPTARE:
                sarite.append(sku); continue
            # Teliu: cifra NOASTRA are prioritate acolo unde exista (HA-urile numarate + ce a
            # parcat ownerul pe „stoc suspect"). Altfel se pastreaza gramada TELIU din stock-sync
            # (NU cifra din Shopify — aia e ecoul lui). Regula intreaga: tinte_pe_depozit.
            teliu_nostru = v.get("teliu", 0)
            cantitate = v["bart"] + v["uz2"] + teliu_nostru
            # REZERVARILE nu se scad AICI, pe SKU, ci dupa ce stim granita fiecarui master (ultima livrare
            # stock-sync) — vezi awb_rezervari.aplica_pe_master. Istoric: pana la 10-sep se scadeau TOATE
            # comenzile neexpediate -> dublu cu reconcilierea pentru cele de DUPA livrare (GD-LUM-EX-6819:
            # 60 fizic, 22 promise, 16 ramase). Intre 10-sep si 01-oct nu se scadea NIMIC -> marfa vanduta
            # si necules se repunea la vanzare (covorul negru: 22 pe raft, 23 vandute, publicat 22).
            # Corect = doar comenzile de DINAINTEA livrarii (+ canalele pe care stock-sync nu le vede).
            stare, mid, info = alege_master(sku, bc_map, bc_shopify, M)
            if stare == "fara_master":
                fara_master.append((sku, cantitate)); continue
            if stare == "ambiguu":
                ambigue.append((sku, info)); continue
            if stare == "dezambiguat":
                dezambiguate.append((sku, mid))
            acumuleaza(pe_master, mid, info, sku, v, T_ISTORIC)
        return pe_master, fara_master, ambigue, sarite, dezambiguate

    pe_master, fara_master, ambigue, sarite, dezambiguate = construieste(F)
    mastere_scop = None
    if scop is not None:
        # tot masterul, nu doar SKU-ul atins: două SKU-uri pe același master se scriu împreună
        # (altfel ultimul îl suprascrie pe primul — HA-1766 810 -> 1, 25-aug)
        mastere_scop = {m for m, rec in pe_master.items() if set(rec["skus"]) & scop}
        pe_master = {m: rec for m, rec in pe_master.items() if m in mastere_scop}
        fara_master = [x for x in fara_master if x[0] in scop]
        ambigue = [x for x in ambigue if x[0] in scop]
        sarite = [x for x in sarite if x in scop]
    cod_master = {}
    for b, (m_id, _, _) in M.items():
        cod_master.setdefault(m_id, b)
    G = gramezi(base, tok, [cod_master[m] for m in pe_master if m in cod_master])

    # ── REZERVARI (01-oct): raftul + comenzile, dintr-un SINGUR instantaneu ────────────────
    # Granita fiecarui master e ultima lui livrare. Daca stock-sync tocmai a livrat, lasam
    # AWBprint sa prinda comenzile de dinainte (import ~2 min).
    livrari = [rz.parse_utc(g.get("deliveredAt")) for g in G.values() if g.get("deliveredAt")]
    sursa = rz.sursa_comenzi("push")
    print("sursa comenzilor (stoc_sursa.push): %s" % sursa)
    # Order Hub da orele in UTC adevarat: decalajul AWBprint nu se aplica
    decalaj = masoara_decalaj(E["DATABASE_URL_AWBPRINT"], stores) if rz.decalaj_aplicabil(sursa) else None
    granita_max = max(livrari) if livrari else None
    termen = datetime.utcnow() + IMPORT_ASTEPTARE_MAX
    while True:
        ultima = rz.ultima_comanda_oh() if sursa == "oh" else rz.ultima_comanda(E["DATABASE_URL_AWBPRINT"])
        # fara decalaj masurat, ora din AWBprint se ia ca atare (daca ar fi in urma, asteptam mai
        # mult sau ne oprim — niciodata nu trecem mai departe cu comenzi lipsa)
        ultima_reala = (ultima + decalaj) if (ultima is not None and decalaj) else ultima
        if ultima_reala is not None and (granita_max is None or ultima_reala > granita_max):
            break
        if datetime.utcnow() >= termen:
            print("\u26d4 OPRIT: %s n-a primit inca nicio comanda de dupa ultima livrare stock-sync "
                  "(%s UTC; ultima comanda %s UTC) — rezervarile ar iesi prea mici. Reincerc." % (
                      "Order Hub" if sursa == "oh" else "AWBprint", granita_max, ultima))
            raise ImportIncomplet(granita_max, ultima)
        print("astept importul %s (ultima comanda %s UTC, livrare %s UTC)" % (
            "Order Hub" if sursa == "oh" else "AWBprint", ultima, granita_max))
        time.sleep(60)
    acum_utc = datetime.utcnow()
    if sursa == "oh":
        linii = rz.comenzi_deschise_oh()[0]
    else:
        linii = rz.comenzi_deschise(E["DATABASE_URL_AWBPRINT"])
    try:                                         # comenzile culese pe o eticheta moarta (V64)
        _ev = sqlite3.connect("file:%s?mode=ro" % SCAN_EVENTS_DB, uri=True)
        taieri = rz.taieri_reetichetate(_ev)
        _ev.close()
    except Exception as exc:
        print("  (scan_events: %s — fara taieri de eticheta)" % exc)
        taieri = {}
    con_dep = sqlite3.connect(LOC_DB, isolation_level=None)
    con_dep.execute("BEGIN")
    try:
        F = fizic(con_dep)                       # raftul ACUM, in aceeasi tranzactie cu pick-urile
        dep = rz.citeste_depozit(con_dep, in_tranzactie=True, taieri=taieri)
    finally:
        con_dep.execute("COMMIT")
        con_dep.close()
    try:
        stoc_la_zi = json.load(open(STOC_LA_ZI))
    except Exception:
        stoc_la_zi = None
    pe_master, fara_master, ambigue, sarite, dezambiguate = construieste(F)
    if mastere_scop is not None:
        pe_master = {m: rec for m, rec in pe_master.items() if m in mastere_scop}
        fara_master = [x for x in fara_master if x[0] in scop]
        ambigue = [x for x in ambigue if x[0] in scop]
        sarite = [x for x in sarite if x in scop]
    if umbra is not None:
        import copy as _copy
        _l_oh = umbra.get("linii_oh") or []
        # comparația cere ÎNTOTDEAUNA și liniile AWBprint, oricare ar fi sursa push-ului
        if sursa == "oh":
            _l_awb, _d_awb = rz.comenzi_deschise(E["DATABASE_URL_AWBPRINT"]), masoara_decalaj(
                E["DATABASE_URL_AWBPRINT"], stores)
        else:
            _l_awb, _d_awb = linii, decalaj
        umbra["sursa_push"] = sursa
        for _nume, _G, _linii, _dec in (("oh", G, _l_oh, None), ("unic_oh", {}, _l_oh, None),
                                         ("unic_awb", {}, _l_awb, _d_awb)):
            _pm = _copy.deepcopy(pe_master)
            rz.aplica_pe_master(_pm, _G, dep, _linii, acum_utc, _dec, stoc_la_zi)
            umbra[_nume] = _pm
        umbra["inainte"] = _copy.deepcopy(pe_master)
    supra = rz.aplica_pe_master(pe_master, G, dep, linii, acum_utc, decalaj, stoc_la_zi)
    tot_rez = sum(r.get("rezervat", 0) for r in pe_master.values())
    tot_inapoi = sum(r.get("inapoi", 0) for r in pe_master.values())
    print("rezervari: %d buc vandute si necules pe %d mastere | %d buc puse inapoi (comenzi dupa "
          "livrare) | decalaj AWBprint %s" % (tot_rez, sum(1 for r in pe_master.values() if r.get("rezervat")),
                                              tot_inapoi, decalaj if decalaj is not None else "nemasurat (0)"))
    reguli = collections.Counter()
    for mid, rec in pe_master.items():
        t = tinta_master(rec, G.get(mid))
        if doar_scaderi_nenumarate:
            t = doar_scaderi_pe_nenumarate(t, (G.get(mid) or {}).get("counted") or [])
        reguli[t["regula"]] += 1
        if t["pasi"]:
            p = {"sku": "+".join(sorted(rec["skus"])), "master": mid, "nume": rec["nume"],
                 "de_la": t["de_la"], "la": t["la"], "delta": t["la"] - t["de_la"], "pasi": t["pasi"],
                 "rezervat": rec.get("rezervat", 0), "inapoi": rec.get("inapoi", 0),
                 "fizic_total": sum((rec.get("fizic") or {}).values())}
            if "tinta_dep" in t:
                p.update(gramezi=t["gramezi"], tinta_dep=t["tinta_dep"], regula=t["regula"])
            plan.append(p)
    print("Teliu pe master: %s" % ", ".join("%s %d" % kv for kv in reguli.most_common()))
    if os.environ.get("AWB2SS_DUMP"):
        json.dump(plan, open(os.environ["AWB2SS_DUMP"], "w"), ensure_ascii=False, default=str)
    multi = [r for r in pe_master.values() if len(r["skus"]) > 1]
    if multi:
        print("mastere alimentate din mai multe SKU-uri: %d" % len(multi))
        for r in multi[:8]:
            print("   %-34s %s -> %d" % (r["nume"][:34], "+".join(sorted(r["skus"])), r["tinta"]))
    return {"F": F, "bc_map": bc_map, "M": M, "G": G, "plan": plan, "pe_master": pe_master,
            "fara_master": fara_master, "ambigue": ambigue, "sarite": sarite, "dezambiguate": dezambiguate,
            "supra": supra}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--eticheta", default="manual", help="ce moment din zi (bartolomeu / uzina2)")
    # Plase de siguranta: un scan gresit nu are voie sa stearga stocul unui magazin intreg.
    ap.add_argument("--max-delta", type=int, default=MAX_DELTA_IMPLICIT, help="deltă maximă per SKU")
    ap.add_argument("--max-parcate", type=int, default=5,
                    help="cate SKU-uri peste --max-delta se pot parca fara sa se opreasca rularea")
    ap.add_argument("--max-total-pct", type=float, default=25.0,
                    help="procent maxim din totalul de unități pe care o rulare are voie să-l miște")
    ap.add_argument("--force", action="store_true", help="trece peste plasele de siguranță")
    ap.add_argument("--no-mail", action="store_true")
    ap.add_argument("--weekend", action="store_true",
                    help="permite rularea sambata/duminica (implicit se sare)")
    ap.add_argument("--fara-autoritate", action="store_true",
                    help="nu declara in stock-sync depozitele numarate (D-023); doar ajustari")
    a = ap.parse_args()

    # SAMBATA/DUMINICA nu pleaca comenzi (abia luni), deci nu are ce impinge — iar un push in weekend
    # ar rescrie masterul cu numaratoarea de VINERI peste vanzarile facute intre timp, umfland stocul.
    # Ziua se ia pe ora ROMANIEI, nu a serverului (VPS-ul e pe Europe/Berlin).
    try:
        from zoneinfo import ZoneInfo
        _azi = datetime.now(ZoneInfo("Europe/Bucharest"))
    except Exception:
        _azi = datetime.now()
    if _azi.weekday() >= 5 and not a.weekend:
        print("WEEKEND (%s) — nu se impinge nimic in stock-sync. Luni-vineri. (--weekend forteaza)"
              % _azi.strftime("%A %Y-%m-%d %H:%M"))
        return 0

    import urllib3
    urllib3.disable_warnings()
    E = env()
    base = (os.environ.get("STOCK_SYNC_API_URL") or kb_secret("STOCK_SYNC_API_URL")).rstrip("/")
    tok = os.environ.get("STOCK_SYNC_API_TOKEN") or kb_secret("STOCK_SYNC_API_TOKEN")
    if not (base and tok):
        print("✗ nu am adresa/tokenul stock-sync"); return 2
    stores = {r["prefix"].strip(): (r["shop"].strip(), r["token"].strip())
              for r in csv.DictReader(open(STORES_CSV))}

    try:
        R = calculeaza_tinte(base, tok, E, stores)
    except ImportIncomplet as e:
        print("\u26d4 OPRIT: AWBprint n-a primit inca nicio comanda de dupa ultima livrare stock-sync "
              "(%s UTC; ultima comanda %s UTC) — rezervarile ar iesi prea mici. Reincerc." % e.args)
        return 1
    F, bc_map, M, G, plan, pe_master = R["F"], R["bc_map"], R["M"], R["G"], R["plan"], R["pe_master"]
    fara_master, ambigue, sarite, dezambiguate, supra = (R["fara_master"], R["ambigue"], R["sarite"],
                                                         R["dezambiguate"], R["supra"])

    tot_now = sum(M[b][2] for b in {b for s in F for b in bc_map.get(s, []) if b in M})
    misca = sum(abs(p["delta"]) for p in plan)
    print("\nde ajustat: %d SKU | %+d buc net | %d buc miscate" % (
        len(plan), sum(p["delta"] for p in plan), misca))
    print("fara master in stock-sync: %d SKU | barcode ambiguu: %d SKU" % (
        len(fara_master), len(ambigue)))
    if dezambiguate:
        print("barcode ambiguu rezolvat dupa Shopify (AWBprint ramas in urma): %d SKU" % len(dezambiguate))
        for _s, _m in dezambiguate[:10]:
            print("   %-24s -> master %s" % (_s, _m[:8]))
    # 27-aug: marfa mutata din Uzina 2 la Bartolomeu pe care depozitul n-a scanat-o inca
    # pe raft. E numarata mai departe la Uzina 2 ca sa nu dispara de pe site. Cand APARE
    # pe un raft RF-*, TREBUIE scazuta din Uzina 2 -- altfel se numara de doua ori.
    try:
        import json as _json, sqlite3 as _sq
        _nesc = _json.load(open("data/mutari_nescanate.json", encoding="utf-8"))
    except Exception:
        _nesc = {}
    if _nesc:
        _c = _sq.connect("data/locations.db")
        _apar = []
        for _s, _q in _nesc.items():
            _baza = _q.get("rf_baza", 0) if isinstance(_q, dict) else 0
            _q = _q.get("qty") if isinstance(_q, dict) else _q
            _rf = _c.execute(
                "select coalesce(sum(qty),0) from shelf_items where sku=? and code like 'RF-%'",
                (_s,)).fetchone()[0]
            if _rf > _baza:
                _apar.append((_s, _q, _rf - _baza))
        _c.close()
        if _apar:
            print("\n\u26a0 SCANATE LA BARTOLOMEU -- scade-le din Uzina 2 (altfel se dubleaza):")
            for _s, _q, _rf in _apar:
                print("   %-12s in asteptare %5d | scanat pe raft de atunci %5d" % (_s, _q, _rf))

    if supra:
        print("\n\u26a0 SUPRA-VANDUTE — comenzi necules PESTE ce e pe raft (anuleaza / anunta clientii):")
        for _m, _n, _s, _r, _f in sorted(supra, key=lambda x: x[4] - x[3]):
            print("   %-24s rezervat %4d | pe raft %4d | lipsa %4d   %s"
                  % ("+".join(_s)[:24], _r, _f, _r - _f, (_n or "")[:30]))
    try:
        _vechi = {x["master"] for x in json.load(open(SUPRAVANDUTE))}
    except Exception:
        _vechi = set()
    _noi = [x for x in supra if x[0] not in _vechi]
    if a.apply:
        try:
            json.dump([{"master": m, "nume": n, "skus": s, "rezervat": r, "pe_raft": f, "lipsa": r - f,
                        "comenzi": pe_master[m].get("comenzi_rezervate", [])[:60]}
                       for m, n, s, r, f in supra], open(SUPRAVANDUTE, "w"), ensure_ascii=False, indent=1)
        except Exception as exc:
            print("   (nu am putut salva %s: %s)" % (SUPRAVANDUTE, exc))
        if _noi and not a.no_mail:
            mail("AWB→stock-sync: %d produse NOI supra-vândute" % len(_noi),
                 "<p>Comenzi necules peste ce e pe raft (produsul e pe 0 în magazine de acum):</p><ul>%s</ul>"
                 % "".join("<li><b>%s</b> %s — vândut %d, pe raft %d, lipsă <b>%d</b></li>"
                           % ("+".join(s), (n or "")[:40], r, f, r - f) for _m, n, s, r, f in _noi), E)

    _epuiz = [p for p in plan if F.get(p["sku"], {}).get("epuizat") and p["de_la"] > 0]
    if _epuiz:
        print("\nEPUIZATE (0 in AWB Arona, se duc pe 0 si in magazine):")
        for p in sorted(_epuiz, key=lambda x: -x["de_la"]):
            print("   %-16s %6d -> 0   %s" % (p["sku"], p["de_la"], p["nume"][:38]))

    if sarite:
        print("IN ASTEPTARE (numaratoare incompleta, NU se scriu):")
        for sk in sarite:
            print("   %-16s %s" % (sk, IN_ASTEPTARE[sk]))
    for p in sorted(plan, key=lambda x: -abs(x["delta"]))[:12]:
        print("   %-24s %6d -> %-6d %+6d   %s" % (p["sku"], p["de_la"], p["la"], p["delta"], p["nume"][:32]))

    # GARDA RELATIVA (26-aug): o numaratoare incompleta a taiat HA-1766 de la 810 la 1.
    # Pragul absolut (3.000) n-a prins-o. Orice SCADERE care sterge peste jumatate din master
    # si depaseste 100 de bucati nu se scrie — se raporteaza, fiindca aproape sigur inseamna
    # "produsul n-a fost numarat", nu "produsul s-a terminat".
    taieri = [p for p in plan if taiere_mare(p)]
    taieri_parcate = []         # D-023: masterele parcate NU primesc autoritate de numaratoare
    if taieri and not a.force:
        _apr = aprobate()
        permise = [p for p in taieri if taiere_permisa(p, _apr)]
        taieri = [p for p in taieri if p not in permise]
        if permise:
            print("\n✓ TĂIERI MARI APLICATE (V82: corecții explicite / aprobate): %d" % len(permise))
            for p in sorted(permise, key=lambda x: x["delta"]):
                print("   %-24s %6d -> %-6d %+6d   %s · %s" % (p["sku"], p["de_la"], p["la"], p["delta"],
                                                             p.get("motiv_taiere", ""), p["nume"][:26]))
    if taieri and not a.force:
        print("\n⚠ SARITE — scaderi de peste jumatate din master, FĂRĂ corecție explicită în spate "
              "(numaratoare probabil incompleta; se aprobă în %s): %d" % (APROBATE, len(taieri)))
        for p in sorted(taieri, key=lambda x: x["delta"]):
            print("   %-24s %6d -> %-6d %+6d   explicit %4d   %s" % (p["sku"], p["de_la"], p["la"], p["delta"],
                                                                p.get("reduceri_explicite", 0), p["nume"][:30]))
        try:
            json.dump([{k: p.get(k) for k in ("sku", "master", "nume", "de_la", "la", "delta", "reduceri_explicite")}
                       for p in taieri], open(PARCATE, "w"), ensure_ascii=False, indent=1)
        except Exception as exc:
            print("   (nu am putut salva %s: %s)" % (PARCATE, exc))
        plan = [p for p in plan if p not in taieri]
        taieri_parcate = list(taieri)
    # GARDA ABSOLUTA. Pana pe 17-sep oprea INTREAGA rulare la primul SKU peste prag — adica o
    # singura anomalie arunca toate corectiile legitime. Masurat: pe 15 si 17-sep un singur SKU
    # (`manusa-magica`, +4.700) a anulat 181 SKU / 24.339 buc. Acum SKU-ul se PARCHEAZA si restul
    # trece, exact ca la garda relativa de mai sus. Daca sar pragul MULTE deodata, atunci chiar e
    # o problema sistemica (scan gresit, fizic() stricat) si se opreste tot.
    prea_mari = [p for p in plan if abs(p["delta"]) > a.max_delta]
    oprit = None
    if prea_mari and not a.force:
        if len(prea_mari) > a.max_parcate:
            oprit = "%d SKU cu deltă peste %d buc — arată a problemă sistemică, nu anomalie izolată (cel mai mare %+d pe %s)" % (
                len(prea_mari), a.max_delta,
                max(prea_mari, key=lambda p: abs(p["delta"]))["delta"],
                max(prea_mari, key=lambda p: abs(p["delta"]))["sku"])
        else:
            print("\n\u26a0 PARCATE \u2014 delt\u0103 peste %d buc (verific\u0103 num\u0103r\u0103toarea, apoi --force): %d"
                  % (a.max_delta, len(prea_mari)))
            for q in sorted(prea_mari, key=lambda x: -abs(x["delta"])):
                print("   %-24s %6d -> %-6d %+6d   %s" % (q["sku"], q["de_la"], q["la"], q["delta"], q["nume"][:30]))
            plan = [q for q in plan if q not in prea_mari]
    # procentul se recalculeaza DUPA parcari, altfel o rulare curata pare mai mare decat e
    misca = sum(abs(q["delta"]) for q in plan)
    pct = (misca * 100.0 / tot_now) if tot_now else 0
    if not oprit and pct > a.max_total_pct and not a.force:
        oprit = "rularea ar mișca %.1f%% din unități (prag %.1f%%)" % (pct, a.max_total_pct)
    if oprit:
        print("\n⛔ OPRIT: %s" % oprit)
        if a.apply and not a.no_mail:
            mail("AWB→stock-sync OPRIT: %s" % oprit,
                 "<p>Rularea <b>%s</b> a fost oprită de plasa de siguranță.</p><p>%s</p>"
                 "<p>Dacă e corect, rulează manual cu <code>--force</code>.</p>" % (a.eticheta, oprit), E)
        return 3

    if not a.apply:
        print("\n[dry-run] — nimic scris.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")  # 31-aug: doar ziua = a 2-a rulare/zi pierde ajustarile (409 KEY_REUSED)
    ok = err = 0
    scrise_aprobate = []    # V82: aprobările ownerului sunt de UNICĂ folosință
    for p in plan:
        # ⚠️ Cheia trebuie sa contina si MOMENTUL. Fara el, un SKU care ajunge a doua oara la
        # aceeasi valoare-tinta reia cheia cu alt delta si stock-sync raspunde 409
        # IDEMPOTENCY_KEY_REUSED — ajustarea se pierde in tacere (873 de cazuri pana la 26-aug).
        key = "awb2ss-%s-%s-%s-%s" % (a.eticheta, stamp, p["sku"], p["la"])
        try:
            # 24-sep: fiecare depozit PRIMESTE pasul lui, cu warehouseCode. Fara depozit, delta
            # ateriza pe gramada cea mai mare si umfla TELIU la fiecare rulare (tinte_pe_depozit).
            r, sold = None, None
            for w, d in p["pasi"]:
                corp = {"delta": d, "reasonCode": "CORRECTION",
                        "reason": "Inventar AWB Arona (%s) — pe depozit, Teliu dupa regula '%s'"
                                  % (a.eticheta, p.get("regula", "fara listing")),
                        "requestedBy": "awb-arona"}
                if w:
                    corp["warehouseCode"] = w
                r = requests.post(base + "/v1/master-products/%s/stock-adjustments" % p["master"],
                                  headers={"Authorization": "Bearer " + tok,
                                           "Content-Type": "application/json",
                                           "Idempotency-Key": "%s-%s" % (key, w or "total")},
                                  json=corp, timeout=60, verify=False)
                if r.status_code not in (200, 201):
                    break
                sold = (r.json() or {}).get("balanceAfter")
            if r is not None and r.status_code in (200, 201):
                # 01-oct: NU mai fortam soldul la tinta (vechiul „compare-and-verify"). Delta e
                # calculat fata de soldul citit; daca intre timp o rulare a scazut vanzari, delta
                # aplicat peste soldul nou da exact rezultatul corect. A forta soldul = tinta
                # repunea la vanzare exact vanzarile de intre citire si scriere.
                if sold is not None and sold != p["la"]:
                    print("   ~ %s: soldul a ajuns %s (tinta calculata %s) — o rulare a scazut vanzari "
                          "intre citire si scriere; corect asa" % (p["sku"], sold, p["la"]))
                ok += 1
                if p.get("motiv_taiere") == "aprobat de owner":
                    scrise_aprobate.append(p["sku"])
            else:
                err += 1
                print("  x %s: %s %s" % (p["sku"], r.status_code, r.text[:110]))
        except Exception as exc:
            err += 1
            print("  x %s: %s" % (p["sku"], exc))
    print("✓ %d ajustate, %d erori" % (ok, err))
    if not a.fara_autoritate:
        declarate = declara_autoritate(base, tok, pe_master, G, parcate={q["master"] for q in taieri_parcate},
                                       eticheta=a.eticheta)
        print("autoritate numaratoare (D-023): %s" % declarate)
    if scrise_aprobate:
        # o aprobare acoperă tăierea de AZI, nu și una viitoare (altă numărătoare, poate incompletă)
        try:
            _apr = json.load(open(APROBATE)) or {}
            _scoase = {x.lower() for sk in scrise_aprobate for x in sk.split("+")}
            json.dump({k: v for k, v in _apr.items() if k.lower() not in _scoase}, open(APROBATE, "w"),
                      ensure_ascii=False, indent=1)
            print("aprobări folosite și scoase: %s" % ", ".join(sorted(scrise_aprobate)))
        except Exception as exc:
            print("(nu am putut actualiza %s: %s)" % (APROBATE, exc))
    if ok:
        # 29-sep: ajustarile stateau in master pana la urmatorul sync (DAILY 02:00 RO sau unul
        # ad-hoc). Masurat pe 1.415 corectii din septembrie: mediana 12,8h de la golirea raftului
        # pana in Shopify, 55h peste weekend — timp in care comenzile intrau pe stoc 0.
        # 20-oct-2026: jobul chema sincronizarea, tiparea "FAILED" si trecea mai departe. Pe 20-oct a picat
        # (SHOPIFY_INVALID_JSON) si cele 36 de ajustari au ramas in master pana la rularea de la 02:00, fara ca
        # cineva sa stie: ecranul zicea "36 ajustate, 0 erori". Acum reincercam o data si, daca tot nu merge,
        # trimitem mail — altfel e exact eșecul tacit care ne-a costat cel mai mult.
        motiv_sync = "awb_to_stocksync %s: %d ajustate" % (a.eticheta, ok)
        stare_sync = cere_sync(base, tok, motiv_sync)
        print("sync magazine: %s" % stare_sync)
        if str(stare_sync).upper() not in ("SUCCEEDED", "PORNIT", "RUNNING", "PENDING", "QUEUED"):
            time.sleep(30)
            stare_sync = cere_sync(base, tok, motiv_sync + " (reincercare)")
            print("sync magazine (reincercare): %s" % stare_sync)
            if str(stare_sync).upper() not in ("SUCCEEDED", "PORNIT", "RUNNING", "PENDING", "QUEUED") \
                    and not a.no_mail:
                mail("AWB→stock-sync: sincronizarea magazinelor a esuat",
                     "<p>Jobul <b>%s</b> a scris <b>%d</b> ajustari in master, dar sincronizarea catre magazine "
                     "a esuat de doua ori: <b>%s</b>.</p><p>Ajustarile stau in master pana la urmatorul sync "
                     "(02:00 RO) — pana atunci magazinele arata cifrele vechi.</p>"
                     % (a.eticheta, ok, stare_sync), E)
    json.dump({"cand": stamp, "eticheta": a.eticheta, "ajustate": ok, "erori": err,
               "fara_master": len(fara_master), "ambigue": len(ambigue)},
              open(STATE, "w"), ensure_ascii=False)
    if err and not a.no_mail:
        mail("AWB→stock-sync: %d erori" % err,
             "<p>Rularea <b>%s</b>: %d ajustate, <b>%d erori</b>.</p>" % (a.eticheta, ok, err), E)
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main())
