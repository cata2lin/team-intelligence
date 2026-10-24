#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REZERVARI — bucati VANDUTE care stau inca pe raft in AWB Arona (01-oct-2026).

De ce exista. Numaratoarea din AWB Arona e FIZICA: o bucata vanduta ramane pe raft pana o
scaneaza culegatorul (pick cu numarul comenzii) sau pana o scade cronul de expediere (`ship_deduct`,
23:00 RO) dupa ce a luat-o curierul. Pana atunci raftul o numara, desi e a clientului. Jobul
publica raftul ca stoc VANDABIL, deci la fiecare rulare repunea la vanzare marfa deja vanduta.
Masurat pe covorul pufos negru 200x280: numarat 34 pe 29-sep, au intrat 46 de bucati; pe 30-sep la
15:30 erau 22 pe raft si 23 deja vandute, iar jobul a pus 22 → s-au mai vandut 11 peste.

De ce NU e simplu „raft − toate comenzile neexpediate" (varianta oprita pe 10-sep). stock-sync
scade SINGUR vanzarile de dupa ultima lui livrare (reconcilierea STORE_SUM vede magazinele cum
coboara). Scazandu-le si aici, aceeasi bucata pleca de doua ori (GD-LUM-EX-6819: 60 fizic, 22
promise, jobul a scris 38, reconcilierea a mai taiat 22 → 16). Regula corecta, pe MASTER:

    granita  = momentul ultimei livrari stock-sync a masterului (`deliveredAt` din /v1/stock)
    rezervat = bucatile ramase de cules din comenzile plasate PANA LA granita
               (+ ORICE comanda de pe un canal pe care stock-sync nu-l vede: Lab Noir, Belasil...)
    inapoi   = bucatile deja SCOASE de pe raft (pick / cron) pentru comenzile de DUPA granita —
               reconcilierea le scade oricum, deci raftul le primeste inapoi
    tinta    = raft + inapoi − rezervat   (pe depozit, minim 0)

„Ramas de cules" oglindeste EXACT ce va mai face `ship_deduct` cu raftul: o comanda anulata nu
se scade niciodata; una din `shipped_seen` e deja scazuta; una in lista „stoc la zi" sau cu
fulfilled_at mai vechi de 45 de zile n-o mai scade nimeni; altfel ramas = cantitate − ce s-a
scos deja pentru ea (pick-uri cu numarul comenzii, ale oamenilor sau ale cronului).
"""
import collections
import re
from datetime import datetime, timedelta

try:
    import kituri as _KIT          # KITURI: aceeași desfacere ca scannerul (kituri.py)
except Exception:                  # noqa: BLE001
    _KIT = None

# aceleasi statusuri ca in ship_deduct.IN_DEPOZIT: marfa n-a plecat inca
IN_DEPOZIT = ("waiting_for_courier", "cancelled", "not_fulfilled", "fulfilled", "")
ANULATE = {"cancelled"}
FEREASTRA_CRON_ZILE = 45        # ship_deduct --since implicit
FEREASTRA_COMENZI_ZILE = 90     # comenzi mai vechi nu mai sunt pe raft, oricum ar arata statusul
# Ce vede stock-sync: prefixul numarului de comanda -> magazinul stock-sync care il vinde.
# Verificat 01-oct-2026 pe lant: prefixul comenzilor din AWBprint (45 de zile) -> domeniul din
# .shopify_stores.csv -> stores.shop_domain in stock-sync. Atentie: cheile din csv NU sunt
# prefixele comenzilor (csv GRAN/NUB, comenzile GRAND/NUBRA). O vanzare coboara `available` DOAR
# in magazinul care are listing pe masterul ei (/v1/stock -> stores) — atunci reconcilierea o
# scade dupa granita. Altfel (Lab Noir, Belasil, duppo.md, prefix necunoscut, magazin fara listing
# pe produsul asta) se rezerva MEREU: gresit doar in directia sigura (sub-vanzare pana la rularea
# urmatoare), niciodata invers.
MAGAZIN_PE_PREFIX = {
    "EST": "81d4566b-0a61-42ff-8737-891708b4efc1",     # Esteban
    "GT": "b965596a-739e-4dfb-93b8-9a166e849add",      # GT Parfumuri
    "OFER": "8321aaf5-ef0d-4278-a3ef-69471dec55b5",    # Ofertele-Zilei.ro
    "CZ": "56cfb192-8e3b-4d94-adbf-77894b1799b1",      # Bonhaus CZ
    "MAG": "1a0cc3c4-cdce-4857-a86d-d03f5b09a444",     # MagDeal.ro
    "BON": "69a70c9f-9035-4a4d-8bce-2da2e21b39f3",     # CasaOfertelor (bonhaus.myshopify.com)
    "RED": "fe99b990-0433-4f0f-9527-3999d6f0e5cc",     # Reduceri Bune
    "GRAND": "7e82196a-a0a6-4ce8-8f19-669c031f134f",   # Grandia
    "BUC": "491bcc70-899b-4315-a641-4543b1d4eb43",     # De la Bucsa
    "NUBRA": "b14df4af-717e-44d2-a6c4-f215587e2e03",   # Nubra
    "LUX": "93e1c1e1-0202-4b89-9feb-6be17a4ced43",     # Nocturna Lux
    "GEN": "4d2abe3e-09f1-4026-9cde-72a879590594",     # Gento
    "BONBG": "defbc7ea-14a1-4655-8986-0cede10e05ab",   # Bonhaus BG
    "NOC": "fc9662aa-f56d-440e-9b75-f3477cf620b5",     # Nocturna
    "SK": "0e3c18b9-f141-4f03-bd29-fd1413b5b975",      # Bonhaus SK
    "PL": "215a2b50-2834-4c02-9f3f-3f1d689f7429",      # Bonhaus PL
    "ROSSI": "6f6ab266-3240-4786-a7c1-778af3fe9933",   # ROSSI Nails
    "COV": "a87845be-dc37-4ed5-b9d9-8e8c1b89a60a",     # Covoria
    "CARP": "8a77fc49-6ec1-4c88-8f08-68eebb55ce20",    # Carpetto
    "ORC": "3f604f65-5c14-43a4-b262-b364b15b7918",     # Orice Redus
    "HU": "e2720d55-e5c4-457f-ac3a-d01e944842e7",      # Bonhaus HU
    "APR": "b8f24eda-b074-40b7-8c09-34f2a7d23f34",     # Apreciat
}
PREFIXE_VAZUTE = frozenset(MAGAZIN_PE_PREFIX)
# Unde se cauta o bucata rezervata (ce va scadea viitorul pick / cron) — vezi atribuie().
ZONE = ("bart", "uz2", "teliu")


def prefix(order_number):
    m = re.match(r"^([A-Za-z]+)", order_number or "")
    return m.group(1).upper() if m else ""


def e_vazuta(comanda, sku, magazine_pe_sku=None):
    """stock-sync vede vanzarea: magazinul comenzii e sincronizat si (cand stim) are listing pe
    masterul sku-ului. `magazine_pe_sku`: sku -> magazinele din pool-ul masterului; None = nu stim
    (se judeca doar dupa prefix)."""
    magazin = MAGAZIN_PE_PREFIX.get(prefix(comanda))
    if magazin is None:
        return False
    if magazine_pe_sku is None:
        return True
    return magazin in (magazine_pe_sku.get(sku) or ())


def zona(statie):
    s = statie or ""
    if s.startswith("Uzina"):
        return "uz2"
    if s.startswith("Teliu"):
        return "teliu"
    if s.startswith("Bartolomeu"):
        return "bart"
    return None


def e_ha(sku):
    return bool(re.match(r"^ha-\d{4}", sku or ""))


def _ts(text):
    """Ora unui eveniment AWB Arona: scannerul scrie ISO cu fus (+03:00), cronul scrie ora
    serverului fara fus (Europe/Berlin). None daca nu se poate citi."""
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo
        dt = dt.replace(tzinfo=ZoneInfo("Europe/Berlin"))
    return dt


def _are_tabel(con, nume):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (nume,)).fetchone() is not None


def taieri_reetichetate(ev_con, zile=120):
    """{comanda: ora} — comenzile culese pe o ETICHETA care a murit (comanda modificata, eticheta noua).

    Scannerul (api/scan.py, V64) le trateaza ca pe o comanda noua: ce s-a scos de pe raft pe eticheta
    veche NU se mai socoteste pentru eticheta noua (`_picks_alte_awb` -> `_taiere`). Oglindim exact:
    pick-urile de pana la ultima marcare pe alta eticheta decat cea curenta nu scad rezervarea.
    Masurat 01-oct-2026: 42 din 38.693 de comenzi pregatite din septembrie.
    """
    out = {}
    try:
        rows = ev_con.execute(
            "SELECT order_number, awb, created_at FROM scan_events WHERE action='picked' "
            "AND COALESCE(awb,'') <> '' AND created_at >= date('now', ?) ORDER BY id",
            ("-%d days" % zile,)).fetchall()
    except Exception:
        return out
    pe_comanda = collections.defaultdict(list)
    for onum, awb, cand in rows:
        pe_comanda[onum].append((awb, cand))
    for onum, ev in pe_comanda.items():
        if len({a for a, _ in ev}) < 2:
            continue
        curenta = ev[-1][0]
        moarte = [_ts(c) for a, c in ev if a != curenta]
        moarte = [m for m in moarte if m is not None]
        if moarte:
            out[onum] = max(moarte)
    return out


def citeste_depozit(con, in_tranzactie=False, taieri=None):
    """Raftul + tot ce trebuie pentru rezervari, intr-o SINGURA citire consistenta.

    Raftul si pick-urile se citesc in aceeasi tranzactie: un pick scris intre doua citiri ar face
    ca bucata sa lipseasca de pe raft si totusi sa para nerezervata (supra-vanzare), sau invers.
    `in_tranzactie`: apelantul a deschis deja tranzactia (si a citit raftul in ea).
    `taieri`: taieri_reetichetate() — pick-urile de pe etichete moarte nu se numara.

    „Scos deja pentru comanda" = NET, ca in aplicatie: pick-urile cu numarul comenzii, minus ce a
    pus inapoi reconcilierea de seara (`auto_pick_deduct.restored_at`, pune marfa pe raft FARA
    eveniment), plus creditele de picking potrivite pe comanda (bucata a iesit de pe raft la
    crearea creditului, ca `pick-lot` fara comanda — v2 al cronului de expediere le socoteste la fel).
    """
    taieri = taieri or {}
    if not in_tranzactie:
        con.execute("BEGIN")
    try:
        statii = {code: (st or "") for code, st in con.execute("SELECT code, station FROM shelves")}
        virtual = {code: int(v or 0) for code, v in con.execute("SELECT code, COALESCE(virtual,0) FROM shelves")}
        rafturi = [(code, sku, int(qty or 0))
                   for code, sku, qty in con.execute("SELECT code, lower(sku), qty FROM shelf_items")]
        vazute_vreodata = {s for (s,) in con.execute(
            "SELECT DISTINCT lower(sku) FROM shelf_events WHERE sku IS NOT NULL AND sku <> ''")}
        pickuri = collections.defaultdict(list)   # (comanda, sku) -> [(raft, bucati)] (bucati < 0 = pus inapoi)
        for onum, sku, code, d, ts in con.execute(
                "SELECT order_ref, lower(sku), code, COALESCE(qty_before,0) - COALESCE(qty,0), ts "
                "FROM shelf_events WHERE action='pick' AND order_ref IS NOT NULL AND order_ref <> ''"):
            if not d or int(d) <= 0:
                continue
            taiere = taieri.get(onum)
            if taiere is not None:
                cand = _ts(ts)
                if cand is not None and cand <= taiere:
                    continue                       # culeasa pe o eticheta moarta
            pickuri[(onum, sku)].append((code, int(d)))
        if _are_tabel(con, "auto_pick_deduct"):
            for onum, code, sku, qty in con.execute(
                    "SELECT order_ref, code, lower(sku), qty FROM auto_pick_deduct "
                    "WHERE restored_at IS NOT NULL AND COALESCE(qty,0) > 0"):
                pickuri[(onum, sku)].append((code, -int(qty)))
        if _are_tabel(con, "pick_credit_orders") and _are_tabel(con, "pick_credits"):
            for onum, sku, code in con.execute(
                    "SELECT pco.order_ref, lower(pc.sku), pc.shelf_code FROM pick_credit_orders pco "
                    "JOIN pick_credits pc ON pc.id = pco.credit_id"):
                pickuri[(onum, sku)].append((code, 1))
        expediate = {o for (o,) in con.execute("SELECT order_number FROM shipped_seen")}
        # KITURI: o comandă cu un kit rezervă produsele LUI (kitul n-are raft) — vezi calculeaza()
        if _KIT is None:
            if _are_tabel(con, "kituri") and con.execute("SELECT 1 FROM kituri LIMIT 1").fetchone():
                raise RuntimeError("kituri.py lipsește, dar sunt kituri definite — rezervările ar ieși prea mici")
            kituri, kituri_fix = {}, {}
        else:
            kituri, kituri_fix = _KIT.harta(con), _KIT.inghetate(con)
        sinonime = {a: k for a, k in con.execute(
            "SELECT lower(alias), lower(canonical) FROM sku_aliases")}
    finally:
        if not in_tranzactie:
            con.execute("COMMIT")
    return {"statii": statii, "virtual": virtual, "rafturi": rafturi, "pickuri": dict(pickuri),
            "expediate": expediate, "sinonime": sinonime, "vazute_vreodata": vazute_vreodata,
            "kituri": kituri, "kituri_fix": kituri_fix}


def comenzi_deschise(pg_url, zile=FEREASTRA_COMENZI_ZILE):
    """Liniile comenzilor din ultimele `zile` (AWBprint), fara anulate. `creata` e UTC naiv."""
    import psycopg2
    con = psycopg2.connect(pg_url)
    try:
        cur = con.cursor()
        cur.execute("""
            SELECT o.order_number, COALESCE(o.aggregated_status,''), o.frisbo_created_at,
                   o.fulfilled_at, lower(li->'inventory_item'->>'sku'),
                   (li->>'quantity')::numeric::int
            -- 16-oct-2026: din 14-sep fiecare comanda BUC e de DOUA ori in `orders` (randul Frisbo +
            -- `shopify-6vz3hk-07`). Adunate pe (comanda, sku), rezervarile Bucsa ieseau duble (gri-deschis:
            -- 0 pe site cu 21 libere). O comanda = UN rand; il prefer pe cel din Shopify, fiindca randul
            -- Frisbo al Bucsei ramane des „waiting_for_courier" si dupa livrare.
            FROM (SELECT DISTINCT ON (order_number) * FROM orders
                  WHERE frisbo_created_at > (now() AT TIME ZONE 'UTC') - make_interval(days => %s)
                  ORDER BY order_number, (store_uid LIKE 'shopify-%%') DESC) o,
                 jsonb_array_elements(o.line_items::jsonb) li
            WHERE COALESCE(o.aggregated_status,'') <> 'cancelled'
              AND li->'inventory_item'->>'sku' IS NOT NULL""", (zile,))
        return [{"comanda": o, "status": st, "creata": c, "fulfilled": f, "sku": s, "buc": int(q or 0)}
                for o, st, c, f, s, q in cur.fetchall() if o and s]
    finally:
        con.close()


# ── SURSA LINIILOR: Order Hub (S3 unificat, 22-oct-2026) ─────────────────────────────────────────────────────────────
# UN singur comutator: `hub_settings.settings.stoc_sursa.<consumator>` din baza OH, citit prin GET /api/cs/stoc-sursa
# (`sursa_comenzi`). Jobul raft -> stock-sync e consumatorul „push". Env-ul AWB2SS_COMENZI (copiile din /root/rez_oh,
# 20-oct) e RETRAS: comutarea e o decizie in baza (Orchestrator / owner), nu o variabila pe un server.
# UN singur feed de rezervari: GET /api/cs/comenzi-deschise. Regulile „fara rezervare" stau in OH, pe magazin:
# „Portal parteneri" (BUCSA, scos de pe raft de mana) si OLX (vinde din rebut). Un OH care nu le anunta = cod vechi
# -> exceptie, nu rezervari gresite.
OH_BASE = "https://orderhub.arona.ro"
OH_ENV_TOKEN = "OH_GARDA_TOKEN"            # cheie de serviciu cu `cs.citire` (OH_TEAM_READ_TOKEN da 401 pe /api/cs)
OH_PAGINA = 20000                          # comenzi pe pagina
OH_PAGINI_MAX = 60                         # plasa: 1,2M de comenzi; peste = ceva e stricat, nu „inca o pagina"
FARA_REZERVARE_OBLIGATORII = ("portal-parteneri.arona.invalid", "olx-200970-31.marketplace.invalid")
SURSE = ("awbprint", "oh")


def _env_fisier(path="/root/Scripturi/.env"):
    d = {}
    try:
        for line in open(path):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return d


def oh_acces(oh_url=None, oh_tok=None):
    """(url, cheie) pentru Order Hub: argumentele, apoi env-ul, apoi /root/Scripturi/.env. Cheia nu se afiseaza."""
    import os
    e = None
    if not oh_url:
        oh_url = os.environ.get("OH_API_URL") or os.environ.get("ORDER_HUB_URL")
        if not oh_url:
            e = _env_fisier()
            oh_url = e.get("OH_API_URL") or e.get("ORDER_HUB_URL") or OH_BASE
    if not oh_tok:
        oh_tok = os.environ.get("OH_CS_READ_TOKEN") or os.environ.get(OH_ENV_TOKEN)
        if not oh_tok:
            e = e if e is not None else _env_fisier()
            oh_tok = e.get("OH_CS_READ_TOKEN") or e.get(OH_ENV_TOKEN)
    if not oh_tok:
        raise RuntimeError("lipseste %s — Order Hub nu se poate citi" % OH_ENV_TOKEN)
    return oh_url.rstrip("/"), oh_tok


def _utc_naiv(text):
    """ISO cu fus (OH) -> datetime UTC naiv, ca `frisbo_created_at`."""
    if not text:
        return None
    from datetime import timezone
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _oh_get(oh_url, oh_tok, cale, params=None, incercari=3, timeout=180):
    """GET pe Order Hub cu reincercare pe 5xx / retea. 4xx = exceptie imediata (cheie, scop, cerere)."""
    import time
    import requests
    for i in range(incercari):
        try:
            # gzip: ~4,9 MB in loc de ~72 MB pe 90 de zile; `requests` il desface singur
            r = requests.get(oh_url + cale, params=params or {}, timeout=timeout,
                             headers={"Authorization": "Bearer " + oh_tok, "Accept-Encoding": "gzip"})
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500 or i == incercari - 1:
                raise RuntimeError("Order Hub %s: HTTP %s" % (cale, r.status_code))
        except requests.RequestException:
            if i == incercari - 1:
                raise
        time.sleep(10 * (i + 1))
    raise RuntimeError("Order Hub %s: fara raspuns" % cale)


def verifica_fara_rezervare(d):
    """Pagina feed-ului trebuie sa anunte AMBELE magazine fara rezervare; altfel OH ruleaza cod vechi -> exceptie."""
    fr = (d or {}).get("fara_rezervare") or {}
    dom = {str(m.get("domeniu") or "") for m in fr.get("magazine") or []} | {str(fr.get("magazin") or "")}
    lipsa = [x for x in FARA_REZERVARE_OBLIGATORII if x not in dom]
    if lipsa:
        raise RuntimeError("Order Hub fara regula „fara rezervare” pentru %s — cod vechi, opresc" % ", ".join(lipsa))


def comenzi_deschise_oh(oh_url=None, oh_tok=None, zile=FEREASTRA_COMENZI_ZILE):
    """Aceleasi linii ca `comenzi_deschise`, din ORDER HUB (GET /api/cs/comenzi-deschise, paginat, gzip).

    OH are si marketplace-urile, statusurile internationale vii si fiecare comanda Bucsa o singura data; fara Portal
    parteneri si OLX (regula e in OH, verificata aici). Orele vin in UTC adevarat — FARA decalajul de 3 ore al AWBprint,
    deci la aplicare `decalaj=None`. Orice pagina esuata = EXCEPTIE (nu o lista partiala: rezervari lipsa =
    supra-vanzare). Intoarce (linii, ultima_comanda UTC naiv)."""
    oh_url, oh_tok = oh_acces(oh_url, oh_tok)
    linii, ultima, dupa = [], None, None
    for _ in range(OH_PAGINI_MAX):
        p = {"zile": zile, "limita": OH_PAGINA}
        if dupa is not None:
            p["dupa"] = dupa
        d = _oh_get(oh_url, oh_tok, "/api/cs/comenzi-deschise", p)
        if not d.get("ok"):
            raise RuntimeError("Order Hub: %s %s" % (d.get("rezultat"), str(d.get("mesaj"))[:120]))
        if dupa is None:
            ultima = _utc_naiv(d.get("ultima_comanda"))
            verifica_fara_rezervare(d)
        for ln in d.get("linii") or []:
            if ln.get("comanda") and ln.get("sku") and (ln.get("status") or "") != "cancelled":
                linii.append({"comanda": ln["comanda"], "status": ln.get("status") or "",
                              "creata": _utc_naiv(ln.get("creata")), "fulfilled": _utc_naiv(ln.get("fulfilled")),
                              "sku": str(ln["sku"]).lower(), "buc": int(ln.get("buc") or 0)})
        dupa = d.get("urmator")
        if dupa is None:
            return linii, ultima
    raise RuntimeError("Order Hub: peste %d pagini de comenzi — opresc" % OH_PAGINI_MAX)


def ultima_comanda_oh(oh_url=None, oh_tok=None):
    """Cea mai noua comanda din Order Hub (UTC naiv) — prima pagina, fereastra de o zi (raspuns mic)."""
    oh_url, oh_tok = oh_acces(oh_url, oh_tok)
    d = _oh_get(oh_url, oh_tok, "/api/cs/comenzi-deschise", {"zile": 1, "limita": 1})
    return _utc_naiv(d.get("ultima_comanda"))


def alege_sursa(stare, consumator):
    """Partea pura a lui `sursa_comenzi`: raspunsul /api/cs/stoc-sursa -> "awbprint" | "oh".
    Valoare necunoscuta = awbprint; „oh" sarit peste ordine (`incalcari` il numeste) = awbprint."""
    v = ((stare or {}).get("comutator") or {}).get(consumator)
    if v not in SURSE:
        return "awbprint"
    if v == "oh" and any(str(x).startswith(consumator + " ") for x in (stare or {}).get("incalcari") or []):
        print("  ⚠ stoc-sursa: %s e pe oh peste ordinea comutarii — raman pe awbprint" % consumator)
        return "awbprint"
    return v


def sursa_comenzi(consumator, oh_url=None, oh_tok=None):
    """Comutatorul S3 din baza OH (hub_settings.stoc_sursa, GET /api/cs/stoc-sursa): "awbprint" | "oh".
    Orice eroare, valoare necunoscuta sau ordine incalcata = "awbprint" (nimic nu se comuta din greseala)."""
    try:
        u, t = oh_acces(oh_url, oh_tok)
        return alege_sursa(_oh_get(u, t, "/api/cs/stoc-sursa", incercari=1, timeout=30), consumator)
    except Exception:  # noqa: BLE001
        return "awbprint"


def decalaj_aplicabil(sursa):
    """False pe „oh": orele vin in UTC adevarat, decalajul masurat pe AWBprint nu le priveste."""
    return sursa != "oh"


def ultima_comanda(pg_url):
    """Cea mai noua comanda din AWBprint (UTC naiv) — ca sa stim daca importul e la zi."""
    import psycopg2
    con = psycopg2.connect(pg_url)
    try:
        cur = con.cursor()
        cur.execute("SELECT max(frisbo_created_at) FROM orders")
        return cur.fetchone()[0]
    finally:
        con.close()


def calculeaza(linii, depozit, granita_pe_sku, acum, stoc_la_zi=None, magazine_pe_sku=None):
    """{sku_raft: {"rezervat": n, "inapoi": {raft: n}, "comenzi": [(comanda, n)]}}.

    `granita_pe_sku`: sku_raft -> datetime UTC naiv (ultima livrare a masterului) sau None
    (master nelivrat: totul e „inainte de granita"). Un sku lipsa = nu-l administram, sarim.
    `stoc_la_zi`: {"until": "YYYY-MM-DD", "skus": set()} — lista lui ship_deduct (nu scade acolo).
    `magazine_pe_sku`: vezi e_vazuta().
    """
    sinonime = depozit["sinonime"]
    pickuri = depozit["pickuri"]
    expediate = depozit["expediate"]
    skip = {s.lower() for s in (stoc_la_zi or {}).get("skus", [])}
    pana = (stoc_la_zi or {}).get("until", "")
    prag_cron = acum - timedelta(days=FEREASTRA_CRON_ZILE)
    out = {}
    # o comanda poate avea acelasi sku pe mai multe linii: se aduna pe (comanda, sku)
    # KITURI: linia kitului devine produsele lui (compoziția cu care a intrat comanda), marcate `din_kit`.
    # Vânzarea unui kit coboară în Shopify produsul KITULUI (fără barcode, stock-sync nu-l vede), nu
    # listingul componentei — deci reconcilierea nu o scade niciodată: se rezervă MEREU, până se culege.
    kit, kfix = depozit.get("kituri") or {}, depozit.get("kituri_fix") or {}
    if (kit or kfix) and _KIT is not None:
        linii = [dict(ln, sku=s, buc=n, din_kit=k) if k else ln
                 for ln in linii for s, n, k in _KIT.desface(ln["sku"], ln["buc"], kit, ln["comanda"], kfix)]
    pe_linie = collections.OrderedDict()
    for ln in linii:
        if (ln["status"] or "") in ANULATE:
            continue
        sku = sinonime.get(ln["sku"], ln["sku"])
        if sku not in granita_pe_sku:
            continue
        cheie = (ln["comanda"], sku)
        if cheie in pe_linie:
            pe_linie[cheie]["buc"] += ln["buc"]
            if ln.get("din_kit"):
                pe_linie[cheie]["din_kit"] = ln["din_kit"]   # amestec kit + produs separat: rezervă tot
        else:
            pe_linie[cheie] = dict(ln, sku=sku)
    for (onum, sku), ln in pe_linie.items():
        rec = out.setdefault(sku, {"rezervat": 0, "inapoi": collections.Counter(), "comenzi": []})
        scoase = pickuri.get((onum, sku), [])
        deja = max(0, sum(d for _, d in scoase))
        granita = granita_pe_sku[sku]
        vazut = e_vazuta(onum, sku, magazine_pe_sku) and not ln.get("din_kit")
        dupa = vazut and granita is not None and ln["creata"] is not None and ln["creata"] > granita
        if dupa:
            # stock-sync o scade singur (magazinul a coborat). Ce s-a scos deja de pe raft pentru
            # ea s-ar scadea de doua ori -> se pune inapoi pe raftul de unde a plecat.
            pe_raft = collections.Counter()
            for code, d in scoase:
                if code:
                    pe_raft[code] += d
            for code, d in pe_raft.items():
                if d > 0:
                    rec["inapoi"][code] += d
            continue
        if onum in expediate:
            continue                                   # cronul a terminat cu ea
        f = ln["fulfilled"]
        if f is not None and sku in skip and str(f)[:10] <= pana:
            continue                                   # ship_deduct n-o va scadea (stoc la zi)
        if f is not None and f < prag_cron:
            continue                                   # in afara ferestrei ship_deduct
        ramas = ln["buc"] - deja
        if ramas > 0:
            rec["rezervat"] += ramas
            rec["comenzi"].append((onum, ramas))
    return out


def _locuri(sku, depozit):
    """Rafturile unui sku in ordinea in care le goleste ship_deduct: [(bucati, raft, zona)].

    Intai stocul „fara raft" (rafturi virtuale / LOC-*, fara REBUT), cele mai pline primele; la HA
    nu de la Bartolomeu (acolo scade scannerul). Apoi rafturile reale, tot cele mai pline primele
    (pick cu scannerul).
    """
    statii, virtual = depozit["statii"], depozit["virtual"]
    loose, reale = [], []
    for code, s, qty in depozit["rafturi"]:
        if s != sku or qty <= 0 or code.startswith("LOC-REBUT"):
            continue
        z = zona(statii.get(code))
        if z is None:
            continue
        e_loose = virtual.get(code, 0) == 1 or code.startswith("LOC-")
        if e_loose and not (e_ha(sku) and z == "bart"):
            loose.append((qty, code, z))
        else:
            reale.append((qty, code, z))
    return sorted(loose, key=lambda x: (-x[0], x[1])) + sorted(reale, key=lambda x: (-x[0], x[1]))


def atribuie(sku, rezervat, depozit, folosit=None):
    """({zona: bucati}, nealocat) — de pe ce depozit va pleca fiecare bucata rezervata.

    `folosit`: Counter (raft, sku) -> bucati deja atribuite in rularea asta; se actualizeaza, ca un
    al doilea pas pe acelasi master sa nu ia aceeasi bucata de doua ori. `nealocat` > 0 = nu mai
    e loc pe rafturile sku-ului.
    """
    folosit = folosit if folosit is not None else collections.Counter()
    pe_zona = collections.Counter()
    ramas = rezervat
    for qty, code, z in _locuri(sku, depozit):
        if ramas <= 0:
            break
        liber = qty - folosit[(code, sku)]
        if liber <= 0:
            continue
        ia = min(liber, ramas)
        folosit[(code, sku)] += ia
        pe_zona[z] += ia
        ramas -= ia
    return dict(pe_zona), max(ramas, 0)


def zona_raftului(code, depozit):
    return zona(depozit["statii"].get(code))


def masoara_decalaj(perechi, toleranta=timedelta(minutes=2), minim=3):
    """Cu cat e `frisbo_created_at` (AWBprint) in urma lui Shopify `createdAt` (UTC adevarat).

    Masurat 01-oct-2026 pe 4 comenzi din 2 magazine: exact 3 ore (AWBprint pare sa converteasca
    ora Romaniei in UTC de doua ori). Nu-l scriem fix in cod: iarna ar fi 2 ore, iar daca cineva
    repara importul ar deveni 0 — si un decalaj aplicat gresit muta comenzi peste granita in
    directia PERICULOASA. Se masoara la fiecare rulare; `perechi` = [(frisbo_naiv, shopify_utc_naiv)].
    Intoarce decalajul (timedelta) daca macar `minim` perechi sunt de acord in `toleranta`, altfel
    None — iar apelantul foloseste 0: comenzile par MAI VECHI decat sunt, deci se rezerva in plus
    (sub-vanzare pana la rularea urmatoare), niciodata invers.
    """
    dif = sorted((s - f) for f, s in perechi if f is not None and s is not None)
    if len(dif) < minim:
        return None
    mediana = dif[len(dif) // 2]
    if any(abs(d - mediana) > toleranta for d in dif):
        return None
    return mediana


def parse_utc(text):
    """ISO din stock-sync („2026-10-01T04:41:00.123Z") -> datetime UTC naiv."""
    if not text:
        return None
    t = text.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def aplica_pe_master(pe_master, G, depozit, linii, acum, decalaj=None, stoc_la_zi=None,
                     marja=timedelta(minutes=2)):
    """Scade rezervarile din numaratoarea fiecarui master (mutand `rec`), pe depozit.

    pe_master: {master_id: rec} cu rec["skus"], rec["bart"/"uz2"/"teliu"], rec["teliu_istoric"].
    G: {master_id: {"deliveredAt": iso|None, "magazine": [store_id]|None, ...}} din /v1/stock.
    `decalaj`: cat e AWBprint in urma lui Shopify (masoara_decalaj); None = 0.
    `marja`: granita se muta putin MAI TARZIU — o comanda din primele minute de dupa livrare se
    rezerva (si o scade si reconcilierea): eroare mica, in directia sigura.

    Pune in rec: "fizic" (numaratoarea bruta pe zone), "rezervat", "inapoi", "supravandut",
    "comenzi_rezervate". Intoarce lista supra-vandutelor [(master, nume, skus, rezervat, raft)].
    """
    granita_pe_sku, magazine_pe_sku = {}, {}
    for mid, rec in pe_master.items():
        g = (G or {}).get(mid) or {}
        livrat = parse_utc(g.get("deliveredAt"))
        granita = (livrat + marja) if livrat is not None else None
        magazine = g.get("magazine")
        for sku in rec["skus"]:
            granita_pe_sku[sku] = granita
            if magazine is not None:
                magazine_pe_sku[sku] = frozenset(magazine)
    linii_corectate = linii
    if decalaj:
        linii_corectate = [dict(ln, creata=(ln["creata"] + decalaj) if ln["creata"] is not None else None)
                           for ln in linii]
    rez = calculeaza(linii_corectate, depozit, granita_pe_sku, acum, stoc_la_zi,
                     magazine_pe_sku or None)
    supra = []
    for mid, rec in pe_master.items():
        fizic = {z: rec[z] for z in ZONE}
        scade = collections.Counter()
        adauga = collections.Counter()
        rezervat = 0
        nealocat = 0
        comenzi = []
        folosit = collections.Counter()
        for sku in rec["skus"]:
            r = rez.get(sku)
            if not r:
                continue
            pe_zona, rest = atribuie(sku, r["rezervat"], depozit, folosit)
            scade.update(pe_zona)
            rezervat += r["rezervat"]
            nealocat += rest
            comenzi += r["comenzi"]
            for code, d in r["inapoi"].items():
                z = zona_raftului(code, depozit)
                if z is not None:
                    adauga[z] += d
        # Un sku al masterului fara (destule) bucati pe raft: comanda se onoreaza fizic din raftul
        # altui sku al ACELUIASI master (acelasi barcode = acelasi produs; ex. negru-4xl vandut,
        # pe raft doar negru-5xl). Se cauta acolo inainte de a declara produsul supra-vandut —
        # altfel bucata nu se scade de nicaieri si se vinde a doua oara.
        for sku in rec["skus"]:
            if nealocat <= 0:
                break
            pe_zona, nealocat = atribuie(sku, nealocat, depozit, folosit)
            scade.update(pe_zona)
        teliu_numarat = rec["teliu"] > 0 or rec.get("teliu_istoric")
        if not teliu_numarat:
            # Teliu nenumarat = gramada stock-sync (tinte_pe_depozit „pastrat"); un rand pe Teliu
            # aici ar transforma regula in „numarat". Fara rafturi Teliu nu exista nici rezervari acolo.
            scade.pop("teliu", None)
            adauga.pop("teliu", None)
        for z in ZONE:
            rec[z] = max(0, rec[z] + adauga[z] - scade[z])
        rec["tinta"] = sum(rec[z] for z in ZONE)
        rec["fizic"] = fizic
        rec["rezervat"] = rezervat
        rec["inapoi"] = sum(adauga.values())
        rec["supravandut"] = nealocat
        rec["comenzi_rezervate"] = comenzi
        if nealocat > 0:
            supra.append((mid, rec.get("nume", ""), list(rec["skus"]), rezervat,
                          sum(fizic.values()) + sum(adauga.values())))
    return supra
