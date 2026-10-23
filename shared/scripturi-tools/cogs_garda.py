# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Garda de COGS aberant — semnalul e CONTRAZICEREA costului de achiziție, nu procentul.

De ce nu pe procent: la noi marjele mari sunt REALE (lavetele 2,5% COGS, parfumurile 14,2% cu cost 9,00
lei pe rețetă, HA-urile ieftine la 3–7%). Un prag „COGS% < 15%" a dat 40 de „probleme" dintre care
niciuna reală (măsurat 22-oct-2026). Ce prinde greșelile adevărate e compararea cu **costul plătit**:
`produs_cost` din Order Hub (lotul valabil la data comenzii, curs BNR de la recepție) vs costul cu care
motorul a contabilizat marfa. Așa au ieșit HA-0204 (12,58 vs 26,30 ⇒ ~29.400 lei profit raportat în plus)
și GD-BR-30482/30926/30642 (0,17 lei pe un set de 16,70).

⚠️ `magazin` e OBLIGATORIU la citire: un cod NUMERIC înseamnă produse diferite în magazine diferite
(codul „30" = parfum pe Esteban, lustră de 75,75 lei pe George Talent).
⚠️ Costul din OH e EX-TVA; `line_cogs` din motor e CU TVA ⇒ se compară după împărțirea la 1,21.

    .venv/bin/python ops/cogs_garda.py            # raport + scrie data/garda_cogs.json
    .venv/bin/python ops/cogs_garda.py --zile 7   # altă fereastră
Briefingul ia DOAR o linie, prin `linie()`. Fără mail, fără alertă separată.
"""
import argparse, json, os, sqlite3, sys, urllib.parse, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
DB = os.environ.get("PROFITABILITY_DB", str(HERE / "data" / "profitability.db"))
OUT = HERE / "data" / "garda_cogs.json"
OH = os.environ.get("OH_URL", "https://orderhub.arona.ro")
TVA_IMPLICIT = 1.21
PRAG_PCT = 0.25          # peste 25% diferență față de costul plătit
PRAG_LEI = 200.0         # și cel puțin 200 lei pe fereastra analizată — altfel e zgomot
TOP = 400                # câte SKU-uri (după venit) verificăm, ca să nu batem OH-ul degeaba


def _tok():
    for v in ("OH_COST_TOKEN", "OH_CS_SB_TOKEN", "OH_GARDA_TOKEN"):
        if os.environ.get(v):
            return os.environ[v]
    return ""


def _cost_oh(sku, magazin, tok):
    url = OH + "/api/produs/cost/" + urllib.parse.quote(str(sku))
    if magazin:
        url += "?magazin=" + urllib.parse.quote(str(magazin))
    try:
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode())
    except Exception:
        return None
    c = j.get("cost") or {}
    if c.get("cost_lei_buc") is None:
        return None
    return {"cost": float(c["cost_lei_buc"]), "transport_inclus": bool(c.get("transport_inclus")),
            "lot": c.get("lot"), "sursa": c.get("sursa")}


def aduna(zile=30):
    tok = _tok()
    if not tok:
        return {"ok": False, "motiv": "fără token OH", "semnale": []}
    cx = sqlite3.connect(DB)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA query_only = 1")
    rows = cx.execute("""
        -- ⚠️ `line_revenue` e in MONEDA magazinului, iar `line_cogs` in LEI (unitati amestecate in
        -- aceeasi tabela). Fara cursul din `profit_exchange_rates`, ordonarea pe venit punea produsele
        -- din CZ (305 CZK) deasupra celor din RO (50 lei) pentru acelasi produs, iar HUF cu ~70x —
        -- deci garda inspecta alte 50 de produse decit cele mai mari ca bani. Nicio cifra raportata
        -- nu era greșita, doar selectia.
        SELECT pol.sku, po.shop, SUM(pol.qty) buc, SUM(pol.line_cogs) cogs,
               SUM(pol.line_revenue * COALESCE(fx.rate_to_ron, 1.0)) venit
        FROM profit_order_lines pol
        JOIN profit_orders po ON po.month=pol.month AND po.prefix=pol.prefix AND po.order_name=pol.order_name
        LEFT JOIN profit_exchange_rates fx
               ON fx.month = po.month AND fx.currency = COALESCE(NULLIF(po.currency,''),'RON')
        WHERE po.created_at >= date('now', ?) AND po.status_category='Livrata' AND pol.qty > 0
        GROUP BY pol.sku, po.shop ORDER BY venit DESC LIMIT ?""", ("-%d days" % zile, TOP)).fetchall()
    cx.close()
    semnale = []
    for r in rows:
        buc = r["buc"] or 0
        if buc <= 0:
            continue
        info = _cost_oh(r["sku"], r["shop"], tok)
        if info is None:
            continue                               # fără cost de achiziție nu avem cu ce contrazice
        platit = info["cost"]
        folosit = (r["cogs"] or 0) / buc / TVA_IMPLICIT        # ex-TVA, ca să fie comparabil
        if platit <= 0:
            continue
        dif_buc = folosit - platit
        if abs(dif_buc) / platit < PRAG_PCT:
            continue
        lei = dif_buc * buc
        if abs(lei) < PRAG_LEI:
            continue
        # ⚠️ Clasificarea contează mai mult decât diferența. „Costul folosit e MAI MARE decât cel plătit"
        # NU e automat o eroare: dacă lotul are `transport_inclus=false` (TOM n-are navlu, iar produsul nu
        # e în sheet-ul cu Shipping Cost), referința noastră e INCOMPLETĂ — nu Shopify e greșit. Altfel am
        # raporta ca „greșeală" exact golul propriu. Măsurat 22-oct-2026: toate cele 11 semnale din prima
        # rulare aveau transport_inclus=false, iar 9 din ele erau în direcția asta.
        if folosit == 0:
            fel, grav = "cost LIPSĂ în motor (0,00) deși marfa e plătită", True
        elif dif_buc < 0:
            fel, grav = "costul folosit e SUB cel plătit ⇒ profit raportat în PLUS", True
        elif not info["transport_inclus"]:
            fel, grav = "referință INCOMPLETĂ (transport lipsă pe lot) — de verificat, nu neapărat greșit", False
        else:
            fel, grav = "costul folosit e PESTE cel plătit ⇒ profit raportat în MINUS", True
        semnale.append({"sku": r["sku"], "magazin": r["shop"], "buc": buc,
                        "cost_folosit": round(folosit, 2), "cost_platit": round(platit, 2),
                        "transport_inclus": info["transport_inclus"], "lot": info["lot"],
                        "diferenta_pe_buc": round(dif_buc, 2), "lei_pe_fereastra": round(lei, 0),
                        "grav": grav, "fel": fel})
    semnale.sort(key=lambda x: (not x["grav"], -abs(x["lei_pe_fereastra"])))
    return {"ok": True, "zile": zile, "verificate": len(rows), "semnale": semnale,
            "grave": sum(1 for x in semnale if x["grav"]),
            "de_verificat": sum(1 for x in semnale if not x["grav"])}


def linie(zile=30):
    """O singură linie pentru briefing. Niciodată excepție — briefingul nu cade din cauza gărzii."""
    try:
        d = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else aduna(zile)
    except Exception:
        return "- Gardă COGS: n-am putut verifica (vezi data/garda_cogs.json)"
    s = d.get("semnale") or []
    if not d.get("ok"):
        return "- Gardă COGS: n-am putut verifica (%s)" % d.get("motiv", "necunoscut")
    grave = [x for x in s if x.get("grav")]
    dev = [x for x in s if not x.get("grav")]
    if not grave:
        return ("- Gardă COGS: niciun cost care să contrazică achiziția (din %d verificate)%s"
                % (d.get("verificate", 0),
                   ("; %d de verificat — referință incompletă, transport lipsă pe lot" % len(dev)) if dev else ""))
    plus = sum(x["lei_pe_fereastra"] for x in grave if x["diferenta_pe_buc"] < 0)
    top = grave[0]
    return ("- 🔴 Gardă COGS: **%d produse** cu cost greșit (profit raportat în plus: %s lei); cel mai mare "
            "**%s** — folosit %.2f vs plătit %.2f lei/buc, %s lei pe %d zile%s."
            % (len(grave), format(int(abs(plus)), ","), top["sku"], top["cost_folosit"], top["cost_platit"],
               format(int(top["lei_pe_fereastra"]), ","), d.get("zile", 30),
               ("; plus %d de verificat (transport lipsă pe lot)" % len(dev)) if dev else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--zile", type=int, default=30)
    a = ap.parse_args()
    d = aduna(a.zile)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print("verificate: %s · semnale: %d" % (d.get("verificate"), len(d.get("semnale") or [])))
    print("  grave: %s · de verificat: %s" % (d.get("grave"), d.get("de_verificat")))
    for x in (d.get("semnale") or [])[:15]:
        print("  %-9s %-20s %-24s %5d buc · folosit %8.2f vs plătit %8.2f ⇒ %+8.0f lei · %s"
              % ("GRAV" if x.get("grav") else "de verif", x["sku"][:20], (x["magazin"] or "")[:24],
                 x["buc"], x["cost_folosit"], x["cost_platit"], x["lei_pe_fereastra"], x["fel"][:52]))
    print("\n" + linie(a.zile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
