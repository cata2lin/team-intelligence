#!/usr/bin/env python3
"""Scanul UNIC al magazinelor pentru pipeline-ul de colete (pagina /colete).

DE CE EXISTA: lista de magazine era scrisa de TREI ori (parcel_products_build.py, parcel_density_push.py,
sku_box_map_build.py) si s-a desincronizat in tacere — Grandia era in lista de PUSH dar NU si in lista din
care se construieste pagina, asa ca 484 de produse Grandia (toate cu `custom.nr_cutii` deja pus in Shopify)
nu au aparut niciodata in /colete. Aici stau, intr-un singur loc: lista de magazine, scanul, si indexul
SKU -> valoare din Shopify + product id per magazin (data/parcel_shop_values.json).

Indexul e folosit de:
  • pagina /colete   — ca sa ARATE ce e deja pus in Shopify (nu doar ce a completat depozitul)
  • pagina /colete   — ca sa scrie metafield-ul DIRECT la salvare (fara sa astepte cronul de 6:15)
  • parcel_density_push.py — propagarea in masa

UNITATEA canonica peste tot e `custom.nr_cutii` = cate CUTII ocupa O BUCATA (1 = 1 cutie/buc,
0.1 = 10 buc/cutie, 3 = produs voluminos, 3 colete pentru o bucata). Pagina afiseaza inversul
(bucati/colet) fiindca asa vorbeste depozitul.
"""
import os, sys, json, time

DATA = os.environ.get("PARCEL_DATA_DIR", "/root/Scripturi/data")
INDEX = os.path.join(DATA, "parcel_shop_values.json")
XCONN = "/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector"

# SINGURA lista de magazine a pipeline-ului de colete. Adaugi un magazin AICI, nu in scripturi.
STORES = {
    "ofertelezilei.myshopify.com": "Ofertele",
    "audusp-rf.myshopify.com": "Reduceri",
    "bonhaus.myshopify.com": "CasaOfertelor",
    "covoareauto-ro.myshopify.com": "MagDeal",
    "oriceredus.myshopify.com": "OriceRedus",
    "ux1x6n-n2.myshopify.com": "BonhausBG",
    "vthuzq-7j.myshopify.com": "BonhausCZ",
    "63e901-2f.myshopify.com": "BonhausHU",
    "16w7xv-0w.myshopify.com": "BonhausSK",
    "n12w89-yy.myshopify.com": "Grandia",      # catalog PROPRIU, nr_cutii pe tot catalogul
    "dvk4hu-dq.myshopify.com": "Belasil",       # catalog propriu (kit detergent = 1 colet, lavetele merg cu el)
    "nxfer1-n4.myshopify.com": "Carpetto",      # catalog propriu
}

Q = ('{ products(first:200%s){ pageInfo{ hasNextPage endCursor } edges{ node{ id title status tags '
     'featuredMedia{ preview{ image{ url } } } '
     'variants(first:1){ edges{ node{ sku } } } '
     'c: metafield(namespace:"custom", key:"nr_cutii"){ value } '
     'p: metafield(namespace:"custom", key:"nr_produse"){ value } } } } }')
SET = ('mutation($m:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$m){ '
       'metafields{ value } userErrors{ code message } } }')
DEL = ('mutation($m:[MetafieldIdentifierInput!]!){ metafieldsDelete(metafields:$m){ '
       'deletedMetafields{ key } userErrors{ message } } }')
DEF = ('mutation($d:MetafieldDefinitionInput!){ metafieldDefinitionCreate(definition:$d){ '
       'createdDefinition{ key } userErrors{ code } } }')
BY_SKU = ('{ products(first:10, query:"sku:\\"%s\\""){ edges{ node{ id '
          'variants(first:20){ edges{ node{ sku } } } } } } }')


def xconn():
    """Modulul xconnector (tokenuri Shopify + GraphQL cu backoff). Import lenes: e mare."""
    if XCONN not in sys.path:
        sys.path.insert(0, XCONN)
    import xconnector as X
    return X


_TOK = {"at": 0.0, "map": {}}


def tokens(max_age=900):
    """{shopDomain: adminToken} pt magazinele din STORES, cache pe 15 min (tokenurile se pot minta)."""
    now = time.time()
    if _TOK["map"] and now - _TOK["at"] < max_age:
        return _TOK["map"]
    X = xconn()
    m = {t["shopDomain"]: t["adminToken"] for t in X.load_shopify_tokens()
         if t.get("shopDomain") in STORES and t.get("adminToken")}
    if m:
        _TOK["map"], _TOK["at"] = m, now
    return _TOK["map"]


def fnum(v):
    """0 e o valoare VALIDA, nu lipsa: produsul nu-si cere colet propriu, calatoreste in coletul altuia
    (lavetele Belasil in cutia detergentului — regula owner 24-aug-2026). Doar negativul e zgomot."""
    try:
        f = float(v)
        return f if f >= 0 else None
    except Exception:
        return None


def scan(stores=None, log=print):
    """Scaneaza magazinele si intoarce randuri: dict(dom,label,gid,sku,title,img,status,tags,box,src)."""
    X = xconn()
    tok = tokens()
    rows = []
    for dom in (stores or STORES):
        t = tok.get(dom)
        if not t:
            log("%-16s FARA TOKEN — sarit" % STORES.get(dom, dom))
            continue
        cur, n = None, 0
        for _ in range(40):
            after = (', after:"%s"' % cur) if cur else ""
            d = X.shopify_gql(dom, t, Q % after)
            conn = ((d.get("data") or {}).get("products")) or {}
            if not conn and d.get("errors"):
                log("%-16s EROARE: %s" % (STORES.get(dom, dom), str(d["errors"])[:120]))
                break
            for e in (conn.get("edges") or []):
                p = e["node"]
                tags = [x.lower() for x in (p.get("tags") or [])]
                vs = ((p.get("variants") or {}).get("edges") or [])
                sku = (vs[0]["node"].get("sku") or "").strip() if vs else ""
                if not sku:
                    continue
                c, pr = (p.get("c") or {}).get("value"), (p.get("p") or {}).get("value")
                box, src = fnum(c), "nr_cutii"
                if box is None:
                    box, src = fnum(pr), "nr_produse"
                n += 1
                rows.append({"dom": dom, "label": STORES.get(dom, dom), "gid": p["id"], "sku": sku,
                             "title": p.get("title") or "", "status": p.get("status") or "",
                             "img": (((p.get("featuredMedia") or {}).get("preview") or {}).get("image") or {}).get("url"),
                             "tags": tags, "box": box, "src": (src if box is not None else None)})
            pi = conn.get("pageInfo") or {}
            if not pi.get("hasNextPage"):
                break
            cur = pi.get("endCursor")
        log("  %-16s %d produse cu SKU" % (STORES.get(dom, dom), n))
    return rows


def index_from_rows(rows):
    """{sku: {"box": float|None, "src": str|None, "st": {dom: gid}}} — valoarea din Shopify + unde e produsul."""
    idx = {}
    for r in rows:
        e = idx.setdefault(r["sku"], {"box": None, "src": None, "st": {}})
        e["st"][r["dom"]] = r["gid"]
        if e["box"] is None and r["box"] is not None:
            e["box"], e["src"] = r["box"], r["src"]
    return idx


def write_index(idx, path=INDEX):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    os.replace(tmp, path)
    return len(idx)


_IDX = {"mtime": None, "data": {}}


def load_index(path=INDEX):
    """Indexul, recitit doar cand fisierul s-a schimbat (cronul il rescrie zilnic)."""
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        return _IDX["data"]
    if _IDX["mtime"] != mt:
        try:
            with open(path, encoding="utf-8") as f:
                _IDX["data"] = json.load(f)
            _IDX["mtime"] = mt
        except Exception:
            pass
    return _IDX["data"]


def find_products(sku):
    """{dom: gid} cautand LIVE dupa SKU — fallback cand indexul n-are inca produsul (produs nou)."""
    X = xconn()
    out = {}
    for dom, t in tokens().items():
        try:
            d = X.shopify_gql(dom, t, BY_SKU % sku.replace('"', ""))
            for e in ((((d.get("data") or {}).get("products")) or {}).get("edges") or []):
                n = e["node"]
                skus = [(v["node"].get("sku") or "").strip()
                        for v in ((n.get("variants") or {}).get("edges") or [])]
                if sku in skus:
                    out[dom] = n["id"]
                    break
        except Exception:
            pass
    return out


_DEF_OK = set()


def ensure_definition(dom, token):
    """Definitia `custom.nr_cutii` — fara ea valoarea nu e vizibila in adminul Shopify.
    O data per proces: mutatia e idempotenta, dar ar fi un apel in plus la FIECARE salvare din pagina."""
    if dom in _DEF_OK:
        return
    _DEF_OK.add(dom)
    X = xconn()
    X.shopify_gql(dom, token, DEF, {"d": {
        "name": "nr_cutii", "namespace": "custom", "key": "nr_cutii", "type": "number_decimal",
        "ownerType": "PRODUCT",
        "description": "Cate CUTII ocupa o bucata. 1 = 1/cutie; 0.5 = 2/cutie; 0.1 = 10/cutie. "
                       "Sursa: scripts.arona.ro/colete (depozitul o corecteaza acolo)."}})


def set_metafield(dom, token, gids, box):
    """Scrie (sau STERGE, daca box e None) custom.nr_cutii pe produsele date. -> (scrise, eroare|None)"""
    X = xconn()
    gids = list(gids)
    if not gids:
        return 0, None
    done, err = 0, None
    for i in range(0, len(gids), 25):
        chunk = gids[i:i + 25]
        if box is None:
            m = [{"ownerId": g, "namespace": "custom", "key": "nr_cutii"} for g in chunk]
            r = X.shopify_gql(dom, token, DEL, {"m": m})
            res = ((r.get("data") or {}).get("metafieldsDelete") or {})
            done += len(res.get("deletedMetafields") or [])
        else:
            m = [{"ownerId": g, "namespace": "custom", "key": "nr_cutii",
                  "type": "number_decimal", "value": ("%g" % box)} for g in chunk]
            r = X.shopify_gql(dom, token, SET, {"m": m})
            res = ((r.get("data") or {}).get("metafieldsSet") or {})
            done += len(res.get("metafields") or [])
        ue = res.get("userErrors") or r.get("errors")
        if ue:
            err = str(ue)[:150]
    return done, err


def push_sku(sku, box, gids_by_dom=None):
    """Propaga o valoare pt UN sku pe toate magazinele unde exista produsul (in paralel, ca pagina sa nu astepte).
    box=None => STERGE metafield-ul (altfel `order_parcel_count` ar folosi mai departe valoarea veche de pe produs,
    care are prioritate in fata hartii centrale — deci stersul local n-ar repara nimic).
    -> {"ok":[labels], "err":[(label,msg)], "stores":n}"""
    from concurrent.futures import ThreadPoolExecutor
    idx = load_index()
    targets = dict(gids_by_dom or ((idx.get(sku) or {}).get("st") or {}))
    if not targets:
        targets = find_products(sku)
    tok = tokens()
    out = {"ok": [], "err": [], "stores": len(targets)}

    def one(item):
        dom, gid = item
        label = STORES.get(dom, dom)
        t = tok.get(dom)
        if not t:
            return ("err", label, "fara token")
        try:
            if box is not None:
                ensure_definition(dom, t)
            n, err = set_metafield(dom, t, [gid], box)
            if err:
                return ("err", label, err)
            return ("ok", label, n)
        except Exception as e:
            return ("err", label, str(e)[:120])

    if targets:
        with ThreadPoolExecutor(max_workers=6) as ex:
            for kind, label, info in ex.map(one, list(targets.items())):
                if kind == "err":
                    out["err"].append((label, info))
                elif info:
                    out["ok"].append(label)
        # tine indexul din memorie corect pana la urmatorul scan al cronului
        e = idx.setdefault(sku, {"box": None, "src": None, "st": {}})
        e["st"].update(targets)
        e["box"], e["src"] = box, ("nr_cutii" if box is not None else None)
    return out
