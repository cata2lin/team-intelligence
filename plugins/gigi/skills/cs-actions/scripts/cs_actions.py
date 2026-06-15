#!/usr/bin/env python3
"""
cs_actions.py — operațiunile CS de tip ACȚIUNE, declanșate de agent din chat, pe ORICE magazin ARONA.

Operațiuni:
  cancel    --order GT44004 [--reason customer|inventory|declined|fraud|other] [--refund] [--no-restock]
  place     --store GT --name "Ion Pop" --phone 0750... --address "Str X 1" --city Ploiesti --zip 100294
            --items "term:qty;term:qty"                         # comandă nouă COD (adresa din chat)
  swap      --from-order GT44004 --items "term:qty"             # înlocuire (copiază adresa) + tag swap
  resend    --from-order GT44004 [--items "term:qty"]           # retrimitere gratis (100% discount) + tag resend
  modify    --order GT44004 [--address "Str Y 2" --city .. --zip ..] [--phone ..]   # schimbă adresa (pre-fulfillment)
  invoice   --order GT44004                                     # link/factură (GT via xConnector)

Comun: COD = draftOrderComplete(paymentPending). Tag AGENT mereu; --swap/resend/etc adaugă tag-ul lor.
Adresa pt swap/resend: xConnector (GT) / Frisbo (restul) / --address (override). DRY-RUN implicit; scrie cu --apply.

Token Shopify (write_orders): SHOPIFY_STORES_CSV (env/cwd/KB). xConnector: XCONNECTOR_SHOPS. Frisbo: FRISBO_ORG_TOKENS.
Agent: --agent NAME (Raluca/Oana/Andra/Anna/OanaO), altfel env CS_AGENT. Nicio cheie nu se printează.
"""
import argparse, csv, io, json, os, re, subprocess, sys, time, urllib.request, urllib.error, urllib.parse

SHOP_API = "2026-01"
XBASE = "https://xconnector.app"
CS_AGENTS = {"raluca": "Raluca", "oana": "Oana", "andra": "Andra", "anna": "Anna", "oanao": "OanaO"}


# ───────────────────────── secrete / KB ─────────────────────────
def _kb_path():
    d = os.getcwd()
    for _ in range(8):
        c = os.path.join(d, "team-intelligence", "plugins", "core", "scripts", "kb.py")
        if os.path.exists(c):
            return c
        d = os.path.dirname(d)
    here = os.path.dirname(os.path.abspath(__file__))
    c = os.path.normpath(os.path.join(here, "..", "..", "..", "core", "scripts", "kb.py"))
    return c if os.path.exists(c) else None


def _kb_secret(name):
    kb = _kb_path()
    if not kb:
        return ""
    try:
        return subprocess.run(["uv", "run", kb, "secret-get", name], capture_output=True, text=True, timeout=30).stdout.strip()
    except Exception:
        return ""


def _stores_csv():
    env = os.getenv("SHOPIFY_STORES_CSV")
    if env:
        return env if "\n" in env else open(env, encoding="utf-8-sig").read()
    if os.path.exists("stores.csv"):
        return open("stores.csv", encoding="utf-8-sig").read()
    return _kb_secret("SHOPIFY_STORES_CSV")


_STORES = None


def stores():
    global _STORES
    if _STORES is None:
        _STORES = {}
        for r in csv.DictReader(io.StringIO(_stores_csv())):
            p = (r.get("prefix") or "").strip().lstrip("﻿").upper()
            if p:
                _STORES[p] = ((r.get("shop") or "").strip().replace("https://", "").strip("/"), (r.get("token") or "").strip())
    return _STORES


def store_of(prefix):
    s = stores().get(prefix.upper())
    if not s:
        sys.exit("prefix %r negăsit în stores.csv (--store)" % prefix)
    return s


def prefix_of_order(name):
    m = re.match(r"^([A-Za-z]+)", name or "")
    return m.group(1).upper() if m else None


# ───────────────────────── HTTP ─────────────────────────
def _http(method, url, headers, body=None, timeout=40):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {}
    except Exception as e:
        return "ERR", {"_err": str(e)[:160]}


def sgql(prefix, query, variables=None):
    shop, token = store_of(prefix)
    s, d = _http("POST", "https://%s/admin/api/%s/graphql.json" % (shop, SHOP_API),
                 {"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                 {"query": query, "variables": variables or {}})
    if isinstance(d, dict) and d.get("errors"):
        sys.exit("GraphQL: %s" % json.dumps(d["errors"], ensure_ascii=False)[:300])
    return (d or {}).get("data", {})


def srest(prefix, method, path, body=None):
    shop, token = store_of(prefix)
    return _http(method, "https://%s/admin/api/%s/%s" % (shop, SHOP_API, path.lstrip("/")),
                 {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}, body)


# ───────────────────────── adresă pt swap/resend ─────────────────────────
def addr_from_xconnector(order_name):
    """GT: adresa din xConnector (by_id după ce găsesc orderId în fereastră)."""
    raw = _kb_secret("XCONNECTOR_SHOPS")
    try:
        shops = json.loads(raw) if raw.startswith("[") else []
    except Exception:
        shops = []
    if not shops:
        return None
    h = {"Authorization": "Bearer " + shops[0]["apiKey"], "Content-Type": "application/json"}
    import datetime
    dto = datetime.date.today().isoformat()
    dfrom = (datetime.date.today() - datetime.timedelta(days=120)).isoformat()
    oid = None
    for page in range(0, 12):
        s, d = _http("GET", XBASE + "/api/orders?fromOrderDate=%s&toOrderDate=%s&page=%d&size=200" % (dfrom, dto, page), h)
        arr = d if isinstance(d, list) else (d.get("content") or d.get("orders") or []) if isinstance(d, dict) else []
        if not arr:
            break
        for o in arr:
            if o.get("orderName") == order_name:
                oid = o.get("orderId"); break
        if oid or len(arr) < 200:
            break
    if not oid:
        return None
    s, d = _http("GET", XBASE + "/api/orders/by-id?orderId=%s" % oid, h)
    a = (d or {}).get("shippingAddress") or {}
    if not a.get("address1"):
        return None
    return {"firstName": a.get("firstName"), "lastName": a.get("lastName"), "address1": a.get("address1"),
            "address2": a.get("address2"), "city": a.get("city"), "zip": a.get("zip"),
            "provinceCode": a.get("provinceCode"), "countryCode": "RO", "phone": a.get("phone")}


FRISBO_BASE = "https://ingest.apis.store-view.frisbo.dev"
# prefix magazin (stores.csv) → org Frisbo (FRISBO_ORG_TOKENS name)
FRISBO_BY_PREFIX = {
    "EST": "esteban.ro", "ROSSI": "rossinails.ro", "NOC": "nocturna.ro", "LUX": "nocturnalux.ro",
    "GT": "georgetalent.ro", "BELA": "belasil.ro", "APR": "apreciat.ro", "GEN": "gento.ro",
    "CARP": "carpetto.ro", "RED": "reduceribune.ro", "COV": "covoria.ro", "BON": "casaofertelor.ro",
    "PAT": "cepatai.ro", "OFER": "ofertelezilei.ro", "GRAN": "grandia.ro", "MAG": "magdeal.ro",
    "PL": "bonhaus.pl", "CZ": "bonhaus.cz", "BONBG": "bonhaus.bg", "BG": "nocturna.bg",
}


def _frisbo_token(prefix):
    org = FRISBO_BY_PREFIX.get(prefix.upper())
    if not org:
        return None
    try:
        for o in json.loads(_kb_secret("FRISBO_ORG_TOKENS")):
            if (o.get("name") or "").lower() == org:
                return o.get("token")
    except Exception:
        pass
    return None


def addr_from_frisbo(prefix, order_name=None, phone=None):
    """Adresa de livrare din Frisbo (org token per magazin), după nr comandă (reference) sau telefon."""
    tok = _frisbo_token(prefix)
    if not tok:
        return None
    q = ("reference=" + urllib.parse.quote(order_name)) if order_name else ("phone_number=" + urllib.parse.quote(phone or ""))
    s, d = _http("GET", FRISBO_BASE + "/orders/search?limit=1&" + q, {"Authorization": "Bearer " + tok})
    items = ((d.get("data") or {}).get("orders") or []) if isinstance(d, dict) else []
    if not isinstance(items, list) or not items:
        return None
    a = items[0].get("shipping_address") or {}
    if not a.get("address1"):
        return None
    return {"firstName": a.get("first_name"), "lastName": a.get("last_name"), "address1": a.get("address1"),
            "address2": a.get("address2"), "city": a.get("city"), "zip": a.get("zip"),
            "provinceCode": a.get("province_code"), "countryCode": a.get("country_code") or "RO",
            "phone": a.get("phone"), "email": a.get("email")}


def resolve_address(order_name):
    pref = prefix_of_order(order_name)
    if pref == "GT":
        return addr_from_xconnector(order_name) or addr_from_frisbo(pref, order_name)
    return addr_from_frisbo(pref, order_name) or (addr_from_xconnector(order_name) if pref == "GT" else None)


# ───────────────────────── lookups Shopify ─────────────────────────
def get_order(prefix, name):
    q = ('query($q:String!){ orders(first:1, query:$q){ edges{ node{ id name displayFinancialStatus '
         'displayFulfillmentStatus cancelledAt lineItems(first:50){ edges{ node{ id title sku quantity } } } } } } }')
    e = sgql(prefix, q, {"q": "name:%s" % name})["orders"]["edges"]
    return e[0]["node"] if e else None


def _items_list(spec, default_qty=1):
    out = []
    for part in [p for p in re.split(r"[;,]", spec or "") if p.strip()]:
        term, _, qty = part.rpartition(":")
        term = (term or part).strip()
        out.append((term, int(qty) if qty.strip().isdigit() else default_qty))
    return out


def find_variant(prefix, term):
    q = ('query($q:String!){ products(first:6, query:$q){ edges{ node{ title variants(first:10){ edges{ node{ id sku title price } } } } } } }')
    d = sgql(prefix, q, {"q": term})
    hits = []
    for pe in d["products"]["edges"]:
        for ve in pe["node"]["variants"]["edges"]:
            v = ve["node"]
            lbl = pe["node"]["title"] + ("" if v["title"] in (None, "Default Title") else " / " + v["title"])
            hits.append((v["id"], lbl, v["price"], (v.get("sku") or "")))
    exact = [h for h in hits if h[3].lower() == term.lower()]
    pool = exact or hits
    if not pool:
        sys.exit("Niciun produs pt %r în %s." % (term, prefix))
    if len(pool) > 1 and not exact:
        sys.exit("Ambiguu pt %r în %s — dă SKU exact:\n%s" % (term, prefix, "\n".join("  - %s (%s) [%s]" % (h[1], h[2], h[3]) for h in pool[:8])))
    return pool[0][:3]  # (vid, label, price)


def parse_items(prefix, spec):
    out = []
    for part in [p for p in re.split(r"[;,]", spec or "") if p.strip()]:
        term, _, qty = part.rpartition(":")
        term = (term or part).strip(); q = int(qty) if qty.strip().isdigit() else 1
        vid, lbl, price = find_variant(prefix, term)
        out.append((vid, lbl, price, q))
    return out


def clean_addr(a):
    return {k: a[k] for k in ("firstName", "lastName", "address1", "address2", "city", "zip", "provinceCode", "countryCode", "phone")
            if a.get(k)}


# ───────────────────────── plasare COD (place/swap/resend) ─────────────────────────
def place_cod(prefix, addr, items, tags, note, apply, free=False):
    total = sum(float(p) * q for _, _, p, q in items)
    print("─" * 64)
    print("  %s  magazin %s  COD%s" % ("PLASEZ" if apply else "DRY-RUN", prefix, "  (GRATIS 100%)" if free else ""))
    print("  Adresă: %s %s, %s %s, %s" % (addr.get("firstName") or "", addr.get("lastName") or "",
                                          addr.get("address1"), addr.get("city") or "", addr.get("zip") or ""))
    for _, lbl, price, q in items:
        print("    %d × %s @ %s" % (q, lbl, price))
    print("  Total ~%.2f RON  Tag-uri: %s" % (0 if free else total, ", ".join(tags)))
    if not apply:
        print("  → --apply ca să plasezi."); return
    di = {"lineItems": [{"variantId": v, "quantity": q} for v, _, _, q in items],
          "shippingAddress": clean_addr(addr), "billingAddress": clean_addr(addr), "tags": tags, "note": note}
    if free:
        di["appliedDiscount"] = {"value": 100.0, "valueType": "PERCENTAGE", "title": "Resend/garanție"}
    if addr.get("email"):
        di["email"] = addr["email"]
    dc = sgql(prefix, "mutation($i:DraftOrderInput!){ draftOrderCreate(input:$i){ draftOrder{ id } userErrors{ field message } } }", {"i": di})["draftOrderCreate"]
    if dc["userErrors"]:
        sys.exit("  draftOrderCreate: %s" % json.dumps(dc["userErrors"], ensure_ascii=False))
    cp = sgql(prefix, "mutation($id:ID!){ draftOrderComplete(id:$id, paymentPending:true){ draftOrder{ order{ id name } } userErrors{ message } } }", {"id": dc["draftOrder"]["id"]})["draftOrderComplete"]
    if cp["userErrors"]:
        sys.exit("  draftOrderComplete: %s" % json.dumps(cp["userErrors"], ensure_ascii=False))
    o = cp["draftOrder"]["order"]
    sgql(prefix, "mutation($id:ID!,$t:[String!]!){ tagsAdd(id:$id, tags:$t){ userErrors{ message } } }", {"id": o["id"], "t": tags})
    print("  ✅ PLASAT %s (COD)  tag-uri: %s" % (o["name"], ", ".join(tags)))


# ───────────────────────── operațiuni ─────────────────────────
def op_cancel(a, agent):
    pref = (a.store or prefix_of_order(a.order)).upper()
    o = get_order(pref, a.order)
    if not o:
        sys.exit("Nu găsesc %s în %s." % (a.order, pref))
    if o.get("cancelledAt"):
        print("  %s e deja anulată." % a.order); return
    print("─" * 64)
    print("  %s ANULEZ %s (%s, %s)" % ("" if a.apply else "[DRY-RUN]", a.order, o["displayFinancialStatus"], o["displayFulfillmentStatus"]))
    print("  motiv=%s refund=%s restock=%s" % (a.reason, bool(a.refund), not a.no_restock))
    if not a.apply:
        print("  → --apply ca să anulezi."); return
    m = ('mutation($id:ID!,$reason:OrderCancelReason!,$refund:Boolean!,$restock:Boolean!){ '
         'orderCancel(orderId:$id, reason:$reason, refund:$refund, restock:$restock, notifyCustomer:false){ userErrors{ message } } }')
    r = sgql(pref, m, {"id": o["id"], "reason": a.reason.upper(), "refund": bool(a.refund), "restock": not a.no_restock})["orderCancel"]
    if r["userErrors"]:
        sys.exit("  orderCancel: %s" % json.dumps(r["userErrors"], ensure_ascii=False))
    sgql(pref, "mutation($id:ID!,$t:[String!]!){ tagsAdd(id:$id, tags:$t){ userErrors{ message } } }", {"id": o["id"], "t": [agent, "anulat-cs"]})
    print("  ✅ ANULAT %s (tag %s, anulat-cs)" % (a.order, agent))


def op_place(a, agent):
    pref = a.store.upper()
    if not (a.address and a.city):
        sys.exit("place: dă --address + --city (+ --zip --phone --name).")
    nm = (a.name or "").split()
    addr = {"firstName": nm[0] if nm else None, "lastName": " ".join(nm[1:]) or None,
            "address1": a.address, "city": a.city, "zip": a.zip, "countryCode": "RO", "phone": a.phone, "email": a.email}
    items = parse_items(pref, a.items)
    if not items:
        sys.exit("place: dă --items.")
    place_cod(pref, addr, items, [agent], "comandă nouă CS | agent %s" % agent + (" | %s" % a.note if a.note else ""), a.apply)


def op_swap(a, agent):
    pref = (a.store or prefix_of_order(a.from_order)).upper()
    addr = ({"address1": a.address, "city": a.city, "zip": a.zip, "countryCode": "RO", "phone": a.phone, "firstName": a.name}
            if a.address else resolve_address(a.from_order))
    if not addr or not addr.get("address1"):
        sys.exit("swap: n-am adresă din %s (xConnector/Frisbo). Dă --address/--city/--zip." % a.from_order)
    items = parse_items(pref, a.items)
    if not items:
        sys.exit("swap: dă --items (produsul corect).")
    place_cod(pref, addr, items, [agent, "swap"], "SWAP după %s | agent %s" % (a.from_order, agent), a.apply)


def op_resend(a, agent):
    pref = (a.store or prefix_of_order(a.from_order)).upper()
    addr = ({"address1": a.address, "city": a.city, "zip": a.zip, "countryCode": "RO", "phone": a.phone, "firstName": a.name}
            if a.address else resolve_address(a.from_order))
    if not addr or not addr.get("address1"):
        sys.exit("resend: n-am adresă din %s. Dă --address/--city/--zip." % a.from_order)
    items = parse_items(pref, a.items) if a.items else None
    if not items:
        sys.exit("resend: dă --items (ce retrimitem gratis).")
    place_cod(pref, addr, items, [agent, "resend", "garantie"], "RESEND gratis după %s | agent %s" % (a.from_order, agent), a.apply, free=True)


def op_modify(a, agent):
    """Modifică adresa (REST) și/sau produsele (orderEdit add/remove/set), pre-fulfillment."""
    pref = (a.store or prefix_of_order(a.order)).upper()
    o = get_order(pref, a.order)
    if not o:
        sys.exit("Nu găsesc %s." % a.order)
    ful = o["displayFulfillmentStatus"]
    if ful not in ("UNFULFILLED", "PARTIALLY_FULFILLED", "ON_HOLD", "OPEN", "SCHEDULED"):
        print("  ⚠ %s e %s — modificarea poate să nu mai conteze (deja expediată)." % (a.order, ful))
    existing = [(le["node"]["id"], le["node"].get("title", ""), (le["node"].get("sku") or "")) for le in o["lineItems"]["edges"]]
    adds = _items_list(a.add) if a.add else []
    rems = _items_list(a.remove, default_qty=0) if a.remove else []
    sets = _items_list(a.set_qty) if a.set_qty else []
    new_addr = {k: v for k, v in (("address1", a.address), ("city", a.city), ("zip", a.zip), ("phone", a.phone)) if v} if a.address else None
    if not (adds or rems or sets or new_addr):
        sys.exit("modify: dă --address și/sau --add/--remove/--set.")
    print("─" * 64)
    print("  %s MODIFIC %s (%s)" % ("" if a.apply else "[DRY-RUN]", a.order, ful))
    plan_add = []
    for term, q in adds:
        vid, lbl, _ = find_variant(pref, term)
        plan_add.append((vid, lbl, q)); print("    + %d × %s" % (q, lbl))
    plan_set = []
    for term, q in rems + sets:
        e = next((x for x in existing if term.lower() in (x[1].lower() + " " + x[2].lower())), None)
        if not e:
            print("    ⚠ negăsit în comandă: %s" % term); continue
        plan_set.append((term, q)); print("    %s %s" % ("− scot" if q == 0 else "→ qty=%d" % q, e[1][:40]))
    if new_addr:
        print("    adresă → %s" % json.dumps(new_addr, ensure_ascii=False))
    if not a.apply:
        print("  → --apply ca să modifici."); return
    if new_addr:
        num = o["id"].rsplit("/", 1)[-1]
        s, d = srest(pref, "PUT", "orders/%s.json" % num, {"order": {"id": int(num), "shipping_address": new_addr}})
        if s != 200:
            print("  ⚠ adresă REST %s: %s" % (s, json.dumps(d, ensure_ascii=False)[:160]))
    if plan_add or plan_set:
        beg = sgql(pref, "mutation($id:ID!){ orderEditBegin(id:$id){ calculatedOrder{ id lineItems(first:100){ edges{ node{ id title sku } } } } userErrors{ message } } }", {"id": o["id"]})["orderEditBegin"]
        if beg["userErrors"]:
            sys.exit("  orderEditBegin: %s" % json.dumps(beg["userErrors"], ensure_ascii=False))
        cid = beg["calculatedOrder"]["id"]
        calc = [(le["node"]["id"], le["node"].get("title", ""), (le["node"].get("sku") or "")) for le in beg["calculatedOrder"]["lineItems"]["edges"]]
        for vid, lbl, q in plan_add:
            r = sgql(pref, "mutation($id:ID!,$v:ID!,$q:Int!){ orderEditAddVariant(id:$id, variantId:$v, quantity:$q){ userErrors{ message } } }", {"id": cid, "v": vid, "q": q})["orderEditAddVariant"]
            if r["userErrors"]: print("  ⚠ add %s: %s" % (lbl, json.dumps(r["userErrors"], ensure_ascii=False)))
        for term, q in plan_set:
            cl = next((c for c in calc if term.lower() in (c[1].lower() + " " + c[2].lower())), None)
            if not cl: continue
            r = sgql(pref, "mutation($id:ID!,$li:ID!,$q:Int!){ orderEditSetQuantity(id:$id, lineItemId:$li, quantity:$q){ userErrors{ message } } }", {"id": cid, "li": cl[0], "q": q})["orderEditSetQuantity"]
            if r["userErrors"]: print("  ⚠ set %s: %s" % (term, json.dumps(r["userErrors"], ensure_ascii=False)))
        cm = sgql(pref, "mutation($id:ID!){ orderEditCommit(id:$id, notifyCustomer:false){ order{ name } userErrors{ message } } }", {"id": cid})["orderEditCommit"]
        if cm["userErrors"]: sys.exit("  orderEditCommit: %s" % json.dumps(cm["userErrors"], ensure_ascii=False))
    sgql(pref, "mutation($id:ID!,$t:[String!]!){ tagsAdd(id:$id, tags:$t){ userErrors{ message } } }", {"id": o["id"], "t": [agent, "modificata-cs"]})
    print("  ✅ MODIFICAT %s (tag %s, modificata-cs)" % (a.order, agent))


def op_invoice(a, agent):
    """Factură fiscală prin SmartBill (per magazin). KB SMARTBILL_STORES=[{prefix,email,token,cif,series}]."""
    import base64, datetime
    pref = (a.store or prefix_of_order(a.order)).upper()
    raw = _kb_secret("SMARTBILL_STORES")
    try:
        creds = {c["prefix"].upper(): c for c in json.loads(raw)} if raw.startswith("[") else {}
    except Exception:
        creds = {}
    c = creds.get(pref)
    if not c:
        print("  Factura SmartBill nu e activă pt %s." % pref)
        print('  Activare: KB secret SMARTBILL_STORES = [{"prefix","email","token","cif","series"}] (creds există pe server).')
        return
    q = ('query($q:String!){ orders(first:1, query:$q){ edges{ node{ name email '
         'lineItems(first:50){ edges{ node{ title quantity originalUnitPriceSet{ shopMoney{ amount } } } } } } } } }')
    e = sgql(pref, q, {"q": "name:%s" % a.order})["orders"]["edges"]
    if not e:
        sys.exit("Nu găsesc %s." % a.order)
    od = e[0]["node"]
    cust = resolve_address(a.order) or {}
    cname = " ".join(filter(None, [cust.get("firstName"), cust.get("lastName")])) or od.get("email") or "Client"
    products = []
    for li in od["lineItems"]["edges"]:
        n = li["node"]
        products.append({"name": n["title"][:200], "isService": False, "measuringUnitName": "buc", "currency": "RON",
                         "quantity": n["quantity"], "price": float(n["originalUnitPriceSet"]["shopMoney"]["amount"]),
                         "isTaxIncluded": True, "taxName": "Normala", "taxPercentage": 19, "saveToDb": False})
    body = {"companyVatCode": c["cif"], "seriesName": c.get("series", ""), "isDraft": False,
            "issueDate": datetime.date.today().isoformat(),
            "client": {"name": cname, "vatCode": "", "isTaxPayer": False, "country": "Romania",
                       "city": cust.get("city") or "", "address": cust.get("address1") or "",
                       "email": od.get("email") or cust.get("email") or ""},
            "products": products}
    print("─" * 64)
    print("  %s FACTURĂ SmartBill %s — %s, %d produse" % ("" if a.apply else "[DRY-RUN]", a.order, cname, len(products)))
    if not a.apply:
        print("  → --apply (ATENȚIE: emite factură fiscală REALĂ)."); return
    auth = base64.b64encode(("%s:%s" % (c["email"], c["token"])).encode()).decode()
    hb = {"Authorization": "Basic " + auth, "Content-Type": "application/json", "Accept": "application/json"}
    s, d = _http("POST", "https://ws.smartbill.ro/SBORO/api/invoice", hb, body)
    if s != 200 or (isinstance(d, dict) and d.get("errorText")):
        sys.exit("  SmartBill %s: %s" % (s, json.dumps(d, ensure_ascii=False)[:220]))
    ser, num = d.get("series") or c.get("series"), d.get("number")
    if od.get("email"):
        _http("POST", "https://ws.smartbill.ro/SBORO/api/document/send", hb,
              {"companyVatCode": c["cif"], "seriesName": ser, "number": num, "type": "factura", "to": od["email"]})
    print("  ✅ FACTURĂ %s%s emisă%s (agent %s)" % (ser or "", num or "", " + trimisă pe email" if od.get("email") else "", agent))


# ───────────────────────── main ─────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Operațiuni CS de tip acțiune (agent-driven), multi-magazin.")
    ap.add_argument("op", choices=["cancel", "place", "swap", "resend", "modify", "invoice"])
    ap.add_argument("--agent", help="Raluca/Oana/Andra/Anna/OanaO (sau env CS_AGENT)")
    ap.add_argument("--order"); ap.add_argument("--from-order"); ap.add_argument("--store")
    ap.add_argument("--items"); ap.add_argument("--name"); ap.add_argument("--phone"); ap.add_argument("--email")
    ap.add_argument("--address"); ap.add_argument("--city"); ap.add_argument("--zip")
    ap.add_argument("--reason", default="customer"); ap.add_argument("--refund", action="store_true"); ap.add_argument("--no-restock", action="store_true")
    ap.add_argument("--add", help='modify: adaugă produse "term:qty;..."'); ap.add_argument("--remove", help='modify: scoate produse "term;..."')
    ap.add_argument("--set", dest="set_qty", help='modify: schimbă cantități "term:qty;..."')
    ap.add_argument("--note"); ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    agent = CS_AGENTS.get((a.agent or os.getenv("CS_AGENT", "")).strip().lower())
    if not agent:
        sys.exit("Dă --agent (Raluca/Oana/Andra/Anna/OanaO) sau setează CS_AGENT.")
    {"cancel": op_cancel, "place": op_place, "swap": op_swap,
     "resend": op_resend, "modify": op_modify, "invoice": op_invoice}[a.op](a, agent)


if __name__ == "__main__":
    main()
