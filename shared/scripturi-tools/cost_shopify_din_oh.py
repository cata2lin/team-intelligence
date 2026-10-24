#!/usr/bin/env python3
"""Scrie costPerItem (inventoryItem.unitCost) în magazinele Shopify DIN Order Hub (S2, 24-oct-2026).

De ce: costul unui produs trăia în cinci locuri (Shopify pe fiecare magazin, sku_costs, foaia „COGS 2026”, override-ul
motorului, TOM) și dădea costuri diferite pe același produs. Sursa e acum `produs_cost` din Order Hub (pe lot); Shopify
doar primește cifra, ca rapoartele Shopify și aplicațiile care citesc costPerItem să vadă același cost. Foaia „COGS
2026” rămâne referință, nu mai intră în flux.

Regula (aceeași ca vederea `sku_costs` din AWB): lotul valabil cu cost transport maritim, altfel cel mai nou lot
(„plasa”), × 1,21 — costPerItem e ținut CU TVA (formula COGS 2026 se termină în × 1,21).
Plasa scrie DOAR unde Shopify n-are cost (nu e o dovadă mai bună decât costul existent). Doar magazinele în RON; fără parfumuri (au sistemul lor) și fără MD / Duppo / Artevita. Fără lot în OH = nu se atinge
(nu ștergem un cost). Gardă: o schimbare de peste ×3 sau sub ÷3 nu se aplică, apare în raport pentru verificare.

  cost_shopify_din_oh.py                    # probă: raport, nu scrie
  cost_shopify_din_oh.py --aplica           # scrie (backup JSON cu valorile vechi înainte)
  cost_shopify_din_oh.py --magazin GRAN     # doar un magazin
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
GQL = "/root/Scripturi/team-intelligence/plugins/gigi/skills/shopify-stores/scripts/shopify_gql.py"
OH_URL = os.environ.get("OH_URL", "https://orderhub.arona.ro")
OUT = "/root/Scripturi/logs/cost_shopify_din_oh"
TVA = 1.21
GARDA = 3.0
MAGAZINE = ["GRAN", "MAG", "OFER", "RED", "BON", "APR", "ORC", "GEN", "BELA", "CARP", "ROSSI", "BUC", "LUX", "NOC"]
PARFUM = re.compile(r"^(\d{1,4}|gt-.*|zn-.*|nub.*|surpriza-.*|est-.*)$", re.I)
Q = """query($c:String){ shop{ currencyCode myshopifyDomain }
  productVariants(first:200, after:$c){ pageInfo{hasNextPage endCursor}
    nodes{ sku barcode inventoryItem{ id unitCost{ amount } } product{ status } } } }"""
M = """mutation($id:ID!,$c:Decimal!){ inventoryItemUpdate(id:$id, input:{cost:$c}){
  inventoryItem{ id unitCost{ amount } } userErrors{ field message } } }"""


def _g():
    spec = importlib.util.spec_from_file_location("g", GQL)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def alege(loturi):
    """(cost CU TVA, „lot”|„plasa”) — aceeași regulă ca AWB `sku_costs_vedere.alege_cost` / `cost_la`."""
    if not loturi:
        return None, None
    loturi = sorted(loturi, key=lambda l: (l.get("data_lot") or ""), reverse=True)
    azi = date.today().isoformat()
    valabil = [l for l in loturi if (l.get("data_lot") or "") <= azi and l.get("cost_lei_buc") is not None]
    if not valabil:
        return None, None
    l0 = valabil[0]
    sursa = "lot" if l0.get("transport_inclus") else "plasa"
    return round(float(l0["cost_lei_buc"]) * TVA, 2), sursa


def loturi_oh(perechi, tok):
    import time
    out = {}
    for i in range(0, len(perechi), 300):
        corp = {"istoric": True, "perechi": [{"cod": c, "magazin": m} for c, m in perechi[i:i + 300]]}
        for incercare in range(4):   # un 502 de la proxy în timpul unui deploy OH nu trebuie să oprească tot
            req = urllib.request.Request(OH_URL + "/api/produs/cost/lot", data=json.dumps(corp).encode(), method="POST",
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
            try:
                with urllib.request.urlopen(req, timeout=240) as r:
                    d = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code < 500 or incercare == 3:
                    raise
                time.sleep(15 * (incercare + 1))
        if not d.get("ok"):
            raise SystemExit("Order Hub a refuzat costurile: %s" % str(d)[:200])
        for rand in d.get("rezultate") or []:
            out[(str(rand.get("cod") or "").lower(), str(rand.get("magazin") or "").lower())] = rand.get("loturi") or []
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplica", action="store_true")
    ap.add_argument("--magazin")
    a = ap.parse_args()
    tok = (os.environ.get("OH_PROFIT_CITIRE_TOKEN") or "").strip()
    if not tok:
        raise SystemExit("lipsește OH_PROFIT_CITIRE_TOKEN")
    g = _g()
    os.makedirs(OUT, exist_ok=True)
    acum = datetime.now().strftime("%Y%m%d_%H%M")
    raport, backup = [], []
    for pfx in ([a.magazin] if a.magazin else MAGAZINE):
        try:
            shop, token = g.resolve_store(pfx)
        except SystemExit as e:
            raport.append({"magazin": pfx, "sarit": str(e)}); continue
        var, c, moneda, dom = [], None, None, None
        while True:
            try:
                d = g.gql(shop, token, Q, {"c": c})
            except Exception as e:  # noqa: BLE001
                raport.append({"magazin": pfx, "sarit": "Shopify: %s" % e}); var = None; break
            moneda, dom = d["data"]["shop"]["currencyCode"], d["data"]["shop"]["myshopifyDomain"]
            pv = d["data"]["productVariants"]
            var += [n for n in pv["nodes"] if (n.get("sku") or "").strip() and n.get("inventoryItem")]
            if not pv["pageInfo"]["hasNextPage"]:
                break
            c = pv["pageInfo"]["endCursor"]
        if var is None:
            continue
        if moneda != "RON":
            raport.append({"magazin": pfx, "sarit": "monedă %s" % moneda}); continue
        var = [v for v in var if not PARFUM.match(v["sku"].strip())]
        lot = loturi_oh([(v["sku"].strip(), dom) for v in var], tok)
        schimb, garda, fara, egal, plasa = [], [], 0, 0, 0
        for v in var:
            nou, sursa = alege(lot.get((v["sku"].strip().lower(), dom.lower())))
            if nou is None:
                fara += 1; continue
            vechi = float(((v["inventoryItem"].get("unitCost") or {}).get("amount")) or 0)
            if abs(nou - vechi) < 0.01:
                egal += 1; continue
            if sursa == "plasa" and vechi > 0:
                # plasa (lot manual / cost vechi, fără cost transport maritim) nu e o dovadă mai bună decât costul
                # din Shopify: completează doar unde Shopify n-are cost
                plasa += 1; continue
            rand = {"sku": v["sku"], "item": v["inventoryItem"]["id"], "vechi": vechi, "nou": nou, "sursa": sursa,
                    "status": (v.get("product") or {}).get("status")}
            if vechi and (nou / vechi > GARDA or vechi / nou > GARDA):
                garda.append(rand)
            else:
                schimb.append(rand)
        aplicate, erori = 0, []
        if a.aplica:
            backup += [dict(r, magazin=pfx) for r in schimb]
            json.dump(backup, open(os.path.join(OUT, "backup_%s.json" % acum), "w"), ensure_ascii=False)
            for r in schimb:
                try:
                    d = g.gql(shop, token, M, {"id": r["item"], "c": "%.2f" % r["nou"]})
                    ue = d["data"]["inventoryItemUpdate"]["userErrors"]
                    if ue:
                        erori.append("%s: %s" % (r["sku"], ue[0]["message"]))
                    else:
                        aplicate += 1
                except Exception as e:  # noqa: BLE001
                    erori.append("%s: %s" % (r["sku"], e))
        raport.append({"magazin": pfx, "variante": len(var), "fara_lot_oh": fara, "egal": egal,
                       "de_schimbat": len(schimb), "garda": len(garda), "plasa_pastrat_shopify": plasa, "aplicate": aplicate, "erori": erori[:10],
                       "delta_lei": round(sum(r["nou"] - r["vechi"] for r in schimb), 2),
                       "din_zero": sum(1 for r in schimb if not r["vechi"]),
                       "exemple_garda": garda[:8], "exemple": sorted(schimb, key=lambda r: -abs(r["nou"] - r["vechi"]))[:5]})
        print("%-6s variante %4d · fără lot %4d · egal %4d · plasa (rămâne Shopify) %4d · de schimbat %4d (din 0: %d, Δ %+.0f lei) · gardă %d · aplicate %d%s"
              % (pfx, len(var), fara, egal, plasa, len(schimb), raport[-1]["din_zero"], raport[-1]["delta_lei"], len(garda),
                 aplicate, (" · %d erori" % len(erori)) if erori else ""))
    json.dump(raport, open(os.path.join(OUT, "raport_%s.json" % acum), "w"), ensure_ascii=False, indent=1)
    for r in raport:
        if r.get("sarit"):
            print("%-6s sărit: %s" % (r["magazin"], r["sarit"]))


if __name__ == "__main__":
    main()
