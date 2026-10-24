#!/usr/bin/env python3
"""Scade din stock-sync vânzările PLECATE ale produselor care NU stau pe raft în scaner (ROSSI de la Parfumuri & co).

De ce (24-oct-2026): până pe 28-iul, stock-sync deducea vânzările din scăderile văzute în Shopify (`SALE` /
`INFERRED_SALE`). După cutover (masterul scrie el în Shopify) deducerea s-a oprit, iar de atunci scăderea vine DOAR
din scaner (ship_deduct.py scade „Fără raft", awb_to_stocksync împinge raft − rezervat). Produsele care nu sunt pe
niciun raft — ROSSI, culese la stația Parfumuri — nu le mai scade nimeni: ~350 buc/lună umflă stocul pe eMAG/Trendyol.

Regula:
  · plecată = Order Hub o știe la curier (oglinda `oh_mirror.db`, aceleași statusuri ca ship_deduct: OH_PLECAT);
  · linia se leagă de master prin listarea stock-sync a magazinului (magazin + SKU exact);
  · intră DOAR masterele fără niciun SKU în scaner (`shelf_items` / `product_locations`), fără parfumuri (regula
    ownerului: stocul de parfumuri are sistemul lui) și fără magazinele MD / Duppo / Artevita (stocul lor nu e la noi);
  · kiturile ROSSI se desfac în componente (`KITURI`, confirmate de owner din poze / descriere); alte kituri scad
    masterul liniei, exact ce făcea stock-sync înainte de 28-iul, și apar separat în raport;
  · marketplace (eMAG / Trendyol) nu e în oglindă: vine din `GET /api/cs/vanzari-marketplace` (OH, agregat pe
    marketplace + SKU + zi), doar zilele ÎNCHEIATE de după bază; SKU-ul se leagă de master doar dacă e unic.
Fără recuperare: prima rulare marchează tot ce e deja plecat ca `baza` (nu se scade). O comandă se scade O SINGURĂ dată:
registrul local (cheie comandă + SKU) și cheia de idempotență a ledger-ului (`sfr:<comandă>:<sku>`).

  scadere_fara_raft.py --dry-run            # raport, nu scrie în stock-sync (rândurile noi rămân de aplicat)
  scadere_fara_raft.py --auto               # aplică DOAR dacă ultimele 3 rulări dry-run au fost curate
  scadere_fara_raft.py --apply              # aplică (manual)
"""
import argparse
import collections
import json
import os
import re
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, "/root/Scripturi")
try:
    from core import stocksync as SS  # VPS-ul de scripturi; testele importă doar `calculeaza`
except ImportError:  # pragma: no cover
    SS = None

OH_DB = "/root/Scripturi/data/oh_mirror.db"
LOC_DB = "/root/Scripturi/data/locations.db"
DB = "/root/Scripturi/data/scadere_fara_raft.db"
RAPOARTE = "/root/Scripturi/logs/scadere_fara_raft"
OH_PLECAT = ("in_transit", "shipped", "delivered", "refused", "pickup_office", "delivery_issues")   # = ship_deduct
PARFUM = re.compile(r"^(\d{1,4}|gt-.*|zn-.*|nub.*|surpriza-.*|est-.*)$")                         # = stoc_unic_umbra
MAGAZINE_EXCLUSE = re.compile(r"esteban|parfum|nubra|lab noir|duppo|artevita|moldova", re.I)
KIT = re.compile(r"(^kit|kit$|-kit-|bottle)", re.I)
RULARI_CURATE = 3
OH_URL = os.environ.get("OH_URL", "https://orderhub.arona.ro")
# Compoziția kiturilor ROSSI (24-oct-2026). Pudră: pozele celor 3 starter kit-uri + descrierea R203-3bottle arată
# Base Coat + Activator + Top Coat (culorile sunt la alegere și vin pe linii proprii). Polygel: „CE INCLUDE” din
# descrierea kitpolygel-3, fără Top Coat Polygel (R196-top) până răspunde ownerul (288 numărate vs 324 kituri vândute);
# forma reutilizabilă, clema și pila n-au SKU ROSSI separat.
_ESENTIALE = (("r203-basecoat", 1), ("r203-activator", 1), ("r203-topcoat", 1))
KITURI = {
    "r203-3bottle": _ESENTIALE, "kit-culoare": _ESENTIALE, "kit-3culori": _ESENTIALE, "kit-6culori": _ESENTIALE,
    "kitpolygel-3": (("r196-1", 1), ("r196-2", 1), ("r196-7", 1), ("r196-brush", 1), ("r196-slip", 1),
                     ("r196-base", 1), ("lampa-6w", 1)),
}


def _db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS linie (comanda TEXT, sku TEXT, master TEXT, buc INTEGER, magazin TEXT,
        status TEXT, vazut TEXT, stare TEXT, aplicat_la TEXT, eroare TEXT, PRIMARY KEY (comanda, sku))""")
    c.execute("CREATE TABLE IF NOT EXISTS rulare (la TEXT PRIMARY KEY, mod TEXT, curata INTEGER, rezumat TEXT)")
    # o comandă văzută o dată nu mai produce rânduri, oricum s-ar schimba regula (kituri, mapare): fără recuperări
    c.execute("CREATE TABLE IF NOT EXISTS vazuta (comanda TEXT PRIMARY KEY, la TEXT)")
    return c


def _listari():
    """{(magazin_jos, sku_jos): master} și {master: {sku}} din toate listările stock-sync (paginat)."""
    pe, skus = {}, collections.defaultdict(set)
    for st in (SS.get("/v1/stores") or {}).get("stores") or []:
        cursor = None
        while True:
            q = "/v1/listings?storeId=%s&limit=200" % st["id"] + ("&cursor=%s" % urllib.parse.quote(cursor) if cursor else "")
            j = SS.get(q)
            if j is None:
                raise SystemExit("stock-sync nu răspunde la %s — opresc (nu ghicesc masterele)" % q)
            for l in j.get("listings") or []:
                m, s = l.get("masterProductId"), str(l.get("sku") or "").strip().lower()
                if m and s:
                    pe[(st["name"].strip().lower(), s)] = m
                    skus[m].add(s)
            cursor = j.get("nextCursor")
            if not cursor:
                break
    return pe, skus


def _in_scaner():
    c = sqlite3.connect("file:%s?mode=ro" % LOC_DB, uri=True)
    try:
        return {r[0] for r in c.execute("SELECT DISTINCT lower(sku) FROM shelf_items UNION "
                                        "SELECT DISTINCT lower(sku) FROM product_locations") if r[0]}
    finally:
        c.close()


def _plecate():
    """{comanda: (magazin, status, [(sku, buc)])} — comenzile cu o etichetă plecată după Order Hub."""
    c = sqlite3.connect("file:%s?mode=ro" % OH_DB, uri=True, timeout=30)
    try:
        st = {}
        for onum, s in c.execute("SELECT order_name, lower(COALESCE(status,'')) FROM oh_awbs WHERE lower(COALESCE(status,'')) "
                                 "IN (%s)" % ",".join("?" * len(OH_PLECAT)), OH_PLECAT):
            if onum:
                st.setdefault(onum, s)
        out = {}
        for onum, mag, canc, lj in c.execute("SELECT order_name, store, cancelled, lines_json FROM oh_orders"):
            if onum not in st or canc:
                continue
            linii = collections.Counter()
            for l in json.loads(lj or "[]"):
                s = str(l.get("sku") or "").strip().lower()
                if s:
                    linii[s] += int(l.get("qty") or 0)
            out[onum] = (mag or "", st[onum], [(s, q) for s, q in linii.items() if q > 0])
        return out
    finally:
        c.close()


def _vanzari_marketplace(de_la, pana_la):
    """{"MKT:<marketplace>:<zi>": ("marketplace:<mkt>", "vandut", [(sku, buc)])} pe zilele [de_la, pana_la]."""
    import urllib.request
    tok = (os.environ.get("OH_PROFIT_CITIRE_TOKEN") or os.environ.get("OH_CS_SB_TOKEN") or "").strip()
    if not tok:
        raise SystemExit("lipsește cheia de citire Order Hub (OH_PROFIT_CITIRE_TOKEN) — nu scad marketplace-ul pe ghicite")
    url = OH_URL + "/api/cs/vanzari-marketplace?" + urllib.parse.urlencode({"de_la": de_la, "pana_la": pana_la})
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    if not d.get("ok"):
        raise SystemExit("Order Hub a refuzat vânzările de marketplace: %s" % str(d)[:200])
    out = collections.defaultdict(collections.Counter)
    for v in d.get("vanzari") or []:
        s, zi, mk = str(v.get("sku") or "").strip().lower(), str(v.get("zi") or ""), str(v.get("marketplace") or "?")
        if s and zi:
            out["MKT:%s:%s" % (mk, zi)][s] += int(round(float(v.get("bucati") or 0)))
    return {k: ("marketplace:" + k.split(":")[1], "vandut", [(s, q) for s, q in c.items() if q > 0])
            for k, c in out.items()}


def _desface(linii):
    """[(sku, buc)] cu kiturile ROSSI desfăcute în componente; (sku_kit, buc) pentru raport."""
    out, kit = collections.Counter(), collections.Counter()
    for sku, q in linii:
        if sku in KITURI:
            kit[sku] += q
            for c, n in KITURI[sku]:
                out[c] += q * n
        else:
            out[sku] += q
    return list(out.items()), kit


def calculeaza(plecate, listari, skus_master, in_scaner):
    """[(comanda, sku, master, buc, magazin, status)] eligibile + contoare pentru raport. Funcție pură (testată).
    Masterul: listarea magazinului comenzii; pe marketplace (fără magazin în stock-sync) doar un SKU cu UN master."""
    eligibile, cnt, sarite = [], collections.Counter(), collections.Counter()
    unic = collections.defaultdict(set)
    for (_mag, s), m in listari.items():
        unic[s].add(m)
    for onum, (mag, status, linii) in plecate.items():
        if MAGAZINE_EXCLUSE.search(mag or ""):
            cnt["magazin_exclus"] += 1
            continue
        linii, kit = _desface(linii)
        cnt["kit_desfacut"] += sum(kit.values())
        for sku, q in linii:
            m = listari.get(((mag or "").strip().lower(), sku))
            if not m and (onum.startswith("MKT:") or kit):
                m = next(iter(unic[sku])) if len(unic.get(sku, ())) == 1 else None
            if not m:
                sarite["fara_master"] += 1
                continue
            toate = skus_master.get(m) or {sku}
            if toate & in_scaner:
                cnt["pe_raft"] += 1           # scaner-ul îl scade deja (ship_deduct / awb_to_stocksync)
                continue
            if any(PARFUM.match(s) for s in toate):
                sarite["parfum"] += 1
                continue
            eligibile.append((onum, sku, m, q, mag, status))
    return eligibile, cnt, sarite


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--auto", action="store_true", help="aplică doar după %d rulări dry-run curate" % RULARI_CURATE)
    a = ap.parse_args()
    acum = datetime.now().isoformat(timespec="seconds")
    con = _db()
    baza = con.execute("SELECT count(*) FROM vazuta").fetchone()[0] == 0

    listari, skus_master = _listari()
    in_scaner = _in_scaner()
    plecate = _plecate()
    if not baza:
        zi_baza = (con.execute("SELECT min(la) FROM rulare WHERE mod='baza'").fetchone()[0] or acum)[:10]
        de_la = (datetime.fromisoformat(zi_baza) + timedelta(days=1)).date().isoformat()
        ieri = (datetime.fromisoformat(acum) - timedelta(days=1)).date().isoformat()
        if de_la <= ieri:
            plecate.update(_vanzari_marketplace(de_la, ieri))      # doar zilele încheiate de după bază
    vazute = {r[0] for r in con.execute("SELECT comanda FROM vazuta")}
    noi_comenzi = {k: v for k, v in plecate.items() if k not in vazute}
    con.executemany("INSERT OR IGNORE INTO vazuta VALUES (?,?)", [(k, acum) for k in noi_comenzi])
    eligibile, cnt, sarite = calculeaza(noi_comenzi, listari, skus_master, in_scaner)
    noi = 0
    for onum, sku, m, q, mag, status in eligibile:
        cur = con.execute("INSERT OR IGNORE INTO linie VALUES (?,?,?,?,?,?,?,?,NULL,NULL)",
                          (onum, sku, m, q, mag, status, acum, "baza" if baza else "de_aplicat"))
        noi += cur.rowcount
    con.commit()

    de_aplicat = con.execute("SELECT comanda, sku, master, buc, magazin FROM linie WHERE stare='de_aplicat'").fetchall()
    # control: niciun master de aplicat nu are între timp un SKU în scaner; nicio linie de două ori (PK o garantează)
    prinse_gresit = sorted({m for _, _, m, _, _ in de_aplicat if (skus_master.get(m) or set()) & in_scaner})
    dubluri = con.execute("SELECT count(*) FROM (SELECT comanda, sku FROM linie GROUP BY 1,2 HAVING count(*)>1)").fetchone()[0]
    curata = not prinse_gresit and not dubluri

    mod = "dry-run" if a.dry_run else "apply"
    if a.auto:
        ult = [r[0] for r in con.execute("SELECT curata FROM rulare WHERE mod='dry-run' ORDER BY la DESC LIMIT ?",
                                         (RULARI_CURATE,))]
        mod = "apply" if (len(ult) == RULARI_CURATE and all(ult) and curata) else "dry-run"
    aplicate, erori = 0, []
    if mod == "apply" and not baza:
        for onum, sku, m, q, mag in de_aplicat:
            if m in prinse_gresit:
                continue
            _, err = SS.post("/v1/master-products/%s/stock-adjustments" % m,
                             {"delta": -int(q), "reasonCode": "SALE", "idempotencyKey": "sfr:%s:%s" % (onum, sku),
                              "reason": "vânzare plecată %s (%s), produs fără raft în scaner" % (onum, mag),
                              "reference": onum, "requestedBy": "scadere_fara_raft"},
                             idem="sfr:%s:%s" % (onum, sku))
            if err:
                erori.append("%s %s: %s" % (onum, sku, err))
                con.execute("UPDATE linie SET eroare=? WHERE comanda=? AND sku=?", (str(err)[:200], onum, sku))
            else:
                aplicate += 1
                con.execute("UPDATE linie SET stare='aplicat', aplicat_la=?, eroare=NULL WHERE comanda=? AND sku=?",
                            (acum, onum, sku))
        con.commit()
        if aplicate:
            SS.trigger_sync()

    pe_master = collections.Counter()
    kit = collections.Counter()
    for _, sku, m, q, _ in de_aplicat:
        pe_master[(m, sku)] += q
        if KIT.search(sku):
            kit[sku] += q
    rez = {"la": acum, "mod": mod, "baza": baza, "eligibile_azi": len(eligibile), "noi": noi,
           "de_aplicat": len(de_aplicat), "bucati_de_aplicat": sum(r[3] for r in de_aplicat),
           "aplicate": aplicate, "erori": erori[:20], "mastere_cu_raft_prinse": prinse_gresit, "dubluri": dubluri,
           "curata": curata, "sarite": dict(sarite), "contoare": dict(cnt), "kituri": dict(kit.most_common(20)),
           "top": [{"sku": s, "master": m, "buc": q} for (m, s), q in pe_master.most_common(25)]}
    # rularea de bază nu se numără printre cele 3 nopți de dry-run
    con.execute("INSERT OR REPLACE INTO rulare VALUES (?,?,?,?)",
                (acum, "baza" if baza else mod, int(curata), json.dumps(rez, ensure_ascii=False)))
    con.commit()
    con.close()
    os.makedirs(RAPOARTE, exist_ok=True)
    zi = acum[:10]
    linii = ["## Scădere fără raft (ROSSI & co) — %s, %s" % (zi, mod),
             "- %s: %d linii eligibile azi, %d noi; de aplicat %d linii / %d buc; aplicate %d%s"
             % ("BAZĂ (prima rulare, nimic de scăzut)" if baza else "rulare", len(eligibile), noi, len(de_aplicat),
                rez["bucati_de_aplicat"], aplicate, (" · %d erori" % len(erori)) if erori else ""),
             "- control: %d mastere cu raft prinse, %d dubluri → %s" % (len(prinse_gresit), dubluri,
                                                                       "CURAT" if curata else "NU e curat, nu aplic"),
             "- kituri (se scade masterul kitului, nu componentele): %s" % (", ".join("%s %d" % kv for kv in kit.most_common(8)) or "—")]
    open(os.path.join(RAPOARTE, zi + ".md"), "w").write("\n".join(linii) + "\n")
    json.dump(rez, open(os.path.join(RAPOARTE, zi + ".json"), "w"), ensure_ascii=False, indent=1)
    try:
        import briefing_alert
        briefing_alert.scrie("Scădere fără raft %s (%s): %d linii / %d buc de aplicat, %s"
                             % (zi, mod, len(de_aplicat), rez["bucati_de_aplicat"], "curat" if curata else "NECURAT"),
                             "\n".join(linii))
    except Exception:
        pass
    print("\n".join(linii))
    return 0 if curata and not erori else 1


if __name__ == "__main__":
    sys.exit(main())
