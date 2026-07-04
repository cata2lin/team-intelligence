# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Citeste / compara / seteaza ratele de TRANSPORT (Shopify delivery profiles) pe magazinele ARONA
de pe app-ul OAuth ARONA (Esteban/GT/Nubra/LabNoir + orice brand cu SHOPIFY_ARONA_<BRAND>_DOMAIN).

  read  --brand LABNOIR                     # arata ratele (RO + international)
  read  --all                               # toate brandurile ARONA-app, comparativ
  set-ro --brand LABNOIR --flat 20 --free-over 150 [--name "Livrare prin DPD"]   # DRY
  set-ro --brand LABNOIR --flat 20 --free-over 150 --apply                       # scrie

set-ro = pattern-ul canonic COD RO: rata plata (0..free-over-0.01) + rata GRATUIT (>= free-over).
STERGE metodele existente de pe zona Domestic si creeaza cele 2. International ramane neatins.
Dry-run by default. Vezi si `gigi:shopify-stores` (acces generic Admin API).
"""
import os, sys, json, argparse, subprocess
import requests
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KB = os.environ.get("KB_PY") or os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
def kb(key):
    v = os.environ.get(key)
    if v: return v
    try: return subprocess.run(["uv","run",KB,"secret-get",key], capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception: return ""

ARONA_BRANDS = ["ESTEBAN","GT","NUBRA","LABNOIR"]  # au SHOPIFY_ARONA_<BRAND>_DOMAIN

def token(brand):
    dom = kb(f"SHOPIFY_ARONA_{brand}_DOMAIN")
    if not dom: sys.exit(f"nu am SHOPIFY_ARONA_{brand}_DOMAIN in KB")
    cid, cs = kb("SHOPIFY_ARONA_CLIENT_ID"), kb("SHOPIFY_ARONA_CLIENT_SECRET")
    tok = requests.post(f"https://{dom}/admin/oauth/access_token",
                        json={"client_id":cid,"client_secret":cs,"grant_type":"client_credentials"}, timeout=30).json().get("access_token")
    if not tok: sys.exit(f"nu pot obtine token pt {brand} ({dom})")
    return dom, tok, kb("SHOPIFY_ARONA_API_VERSION") or "2026-04"

def gql(dom, tok, ver, q, v=None):
    return requests.post(f"https://{dom}/admin/api/{ver}/graphql.json",
                         headers={"X-Shopify-Access-Token":tok,"Content-Type":"application/json"},
                         json={"query":q,"variables":v or {}}, timeout=60).json()

READ_Q = """query {
  deliveryProfiles(first: 1) { edges { node { id
    profileLocationGroups {
      locationGroup { id }
      locationGroupZones(first: 20) { edges { node {
        zone { id name }
        methodDefinitions(first: 20) { edges { node { id name active
          rateProvider { ... on DeliveryRateDefinition { price { amount currencyCode } } }
          methodConditions { operator conditionCriteria { ... on MoneyV2 { amount } ... on Weight { value unit } } }
        } } }
      } } }
    }
  } } }
}"""

def profile(dom, tok, ver):
    r = gql(dom, tok, ver, READ_Q)
    if r.get("errors"): sys.exit(f"read err: {r['errors']}")
    return r["data"]["deliveryProfiles"]["edges"][0]["node"]

def cmd_read(brand):
    dom, tok, ver = token(brand)
    n = profile(dom, tok, ver)
    print(f"### {brand} ({dom})")
    for plg in n["profileLocationGroups"]:
        for ze in plg["locationGroupZones"]["edges"]:
            z = ze["node"]
            for me in z["methodDefinitions"]["edges"]:
                m = me["node"]; pr = (m.get("rateProvider") or {}).get("price") or {}
                conds = [(c["operator"], (c.get("conditionCriteria") or {}).get("amount") or (c.get("conditionCriteria") or {}).get("value")) for c in (m.get("methodConditions") or [])]
                print(f"  [{z['zone']['name']}] {m['name']} = {pr.get('amount')} {pr.get('currencyCode','')} | {conds or '-'}")

def domestic_zone(n):
    """Return (location_group_id, zone_id, [method_ids], currency) for the Domestic zone."""
    for plg in n["profileLocationGroups"]:
        for ze in plg["locationGroupZones"]["edges"]:
            z = ze["node"]
            if z["zone"]["name"].lower() in ("domestic", "romania", "românia"):
                mids = [e["node"]["id"] for e in z["methodDefinitions"]["edges"]]
                cur = "RON"
                for e in z["methodDefinitions"]["edges"]:
                    p = (e["node"].get("rateProvider") or {}).get("price") or {}
                    if p.get("currencyCode"): cur = p["currencyCode"]
                return plg["locationGroup"]["id"], z["zone"]["id"], mids, cur
    return None, None, None, "RON"

MUT = """mutation upd($id: ID!, $p: DeliveryProfileInput!) {
  deliveryProfileUpdate(id: $id, profile: $p) { profile { id } userErrors { field message } } }"""

def cmd_set_ro(brand, flat, free_over, name, apply):
    dom, tok, ver = token(brand)
    n = profile(dom, tok, ver)
    lg, zid, mids, cur = domestic_zone(n)
    if not zid: sys.exit("nu gasesc zona Domestic")
    below = f"{free_over - 0.01:.2f}"
    print(f"{brand}: RO Domestic -> STERG {len(mids)} metode existente, creez:")
    print(f"   '{name}' = {flat} {cur}  (0 .. {below})")
    print(f"   '{name}' = 0 {cur}  (>= {free_over})")
    if not apply:
        print("DRY-RUN (adauga --apply ca sa scrii)."); return
    def meth(price, op, amt):
        return {"name":name,"active":True,"rateDefinition":{"price":{"amount":str(price),"currencyCode":cur}},
                "priceConditionsToCreate":[{"criteria":{"amount":str(amt),"currencyCode":cur},"operator":op}]}
    variables = {"id": n["id"], "p": {
        "methodDefinitionsToDelete": mids,
        "locationGroupsToUpdate": [{"id": lg, "zonesToUpdate": [{"id": zid,
            "methodDefinitionsToCreate": [
                meth(f"{flat:.1f}", "LESS_THAN_OR_EQUAL_TO", below),
                meth("0", "GREATER_THAN_OR_EQUAL_TO", f"{free_over:.1f}")]}]}]}}
    r = gql(dom, tok, ver, MUT, variables)
    ue = ((r.get("data") or {}).get("deliveryProfileUpdate") or {}).get("userErrors")
    print("REZULTAT:", "OK" if ue == [] else (ue or r.get("errors")))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read"); r.add_argument("--brand"); r.add_argument("--all", action="store_true")
    s = sub.add_parser("set-ro"); s.add_argument("--brand", required=True); s.add_argument("--flat", type=float, required=True)
    s.add_argument("--free-over", type=float, required=True); s.add_argument("--name", default="Livrare prin DPD"); s.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    if a.cmd == "read":
        for b in (ARONA_BRANDS if a.all else [a.brand or sys.exit("--brand sau --all")]):
            try: cmd_read(b)
            except SystemExit as e: print(f"### {b}: {e}")
    elif a.cmd == "set-ro":
        cmd_set_ro(a.brand, a.flat, a.free_over, a.name, a.apply)
