# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary"]
# ///
"""
bundle_app.py — Esteban (and sibling clone-perfume stores) Set Cadou gift-bundle
maintenance: find bundle components running low on stock, recommend high-stock
weak-selling replacements (same gender, scent-family match), and swap the
storefront `inspired_list` display cards to the replacement's ORIGINAL-bottle photo.

Read-only by default (prints a SCOATE/PUNE hand-off list). `--apply-display` performs
the inspired_list photo swap via the Admin API. The actual COMPONENT swap is APP-OWNED
and CANNOT be done via API — it stays a MANUAL step in the bundle app (this tool prepares
everything around it). See SKILL.md.

Usage:
  uv run bundle_app.py                         # EST report: low-stock comps + recommended swaps
  uv run bundle_app.py --low 100 --pool-stock 400 --months 12
  uv run bundle_app.py --apply-display         # also swap inspired_list photos (WRITES display)
  uv run bundle_app.py --prefix EST --verify   # re-read inspired_list and check images resolve

Data:
  - stock + bundle components + inspired_list: Shopify Admin API (store resolved from the
    KB secret SHOPIFY_STORES_CSV; token never printed).
  - units sold (weak-seller ranking): metrics Postgres order_line_items (via core arona_pg).
  - gender: data/gender.json (sku -> M/W/U). Unknown sku => U (compatible with any theme);
    add new perfumes there so a men's set never gets a women's perfume.
"""
import argparse, csv, io, json, os, sys, time, urllib.request, urllib.error, subprocess
from pathlib import Path

API_VERSION = "2026-01"
HERE = Path(__file__).resolve()
DATA = HERE.parent.parent / "data"


# ---------- shared core helpers (arona_pg for Postgres, kb.py for the CSV secret) ----------
def _import_arona_pg():
    for up in range(2, 9):
        cand = HERE.parents[up] / "core" / "scripts"
        if (cand / "arona_pg.py").exists():
            sys.path.insert(0, str(cand))
            import arona_pg  # type: ignore
            return arona_pg
    return None


def _find_kb():
    for up in range(2, 9):
        cand = HERE.parents[up] / "core" / "scripts" / "kb.py"
        if cand.exists():
            return str(cand)
    return None


def _stores_csv():
    env = os.getenv("SHOPIFY_STORES_CSV")
    if env:
        return env if "\n" in env else open(env, encoding="utf-8-sig").read()
    ap = _import_arona_pg()
    if ap:
        try:
            return ap.secret("SHOPIFY_STORES_CSV")
        except Exception:
            pass
    kb = _find_kb()
    if kb:
        out = subprocess.run(["uv", "run", kb, "secret-get", "SHOPIFY_STORES_CSV"],
                             capture_output=True, text=True)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout
    sys.exit("Could not resolve SHOPIFY_STORES_CSV (env / arona_pg / kb.py).")


def resolve_store(prefix):
    for row in csv.DictReader(io.StringIO(_stores_csv())):
        if (row.get("prefix") or "").strip().lstrip("﻿").upper() == prefix.upper():
            return (row.get("shop") or "").strip().replace("https://", "").strip("/"), (row.get("token") or "").strip()
    sys.exit(f"prefix {prefix!r} not found in stores.csv")


def _req(url, headers, data=None):
    r = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(r, timeout=45) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 2)) + attempt); continue
            sys.exit(f"HTTP {e.code}: {e.read().decode()[:300]}")
    sys.exit("gave up after repeated 429s")


def gql(shop, token, query, variables=None):
    res = _req(f"https://{shop}/admin/api/{API_VERSION}/graphql.json",
               {"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
               json.dumps({"query": query, "variables": variables or {}}).encode())
    ts = ((res.get("extensions") or {}).get("cost") or {}).get("throttleStatus") or {}
    if ts.get("currentlyAvailable", 999) < 200:
        time.sleep(1.0)
    if res.get("errors"):
        sys.exit("GraphQL errors: " + json.dumps(res["errors"])[:400])
    return res["data"]


# ---------- catalog ----------
CAT_Q = """
query($cursor:String){ products(first:50, after:$cursor){
  pageInfo{ hasNextPage endCursor }
  edges{ node{ id title tags
    variants(first:5){ edges{ node{ sku inventoryQuantity requiresComponents
      productVariantComponents(first:20){ edges{ node{ quantity
        productVariant{ sku inventoryQuantity product{ title } } } } } } } } } } } }"""


def fetch_catalog(shop, token):
    prods, cur = [], None
    while True:
        d = gql(shop, token, CAT_Q, {"cursor": cur})["products"]
        prods += [e["node"] for e in d["edges"]]
        if d["pageInfo"]["hasNextPage"]:
            cur = d["pageInfo"]["endCursor"]
        else:
            break
    return prods


def is_bundle(p):
    return any(v["node"].get("requiresComponents") or v["node"].get("productVariantComponents", {}).get("edges")
               for v in p["variants"]["edges"])


# ---------- sales (metrics) ----------
def fetch_sales(brand_slug, months):
    ap = _import_arona_pg()
    if not ap:
        print("! arona_pg not found — sales unavailable, ranking by stock only", file=sys.stderr)
        return {}
    with ap.connect("DATABASE_URL_METRICS") as conn:
        bid = ap.query(conn, "SELECT id FROM brands WHERE slug=%s", (brand_slug,))
        if not bid:
            return {}
        bid = bid[0]["id"]
        rows = ap.query(conn, """
            SELECT li.sku, SUM(li.quantity) u FROM order_line_items li
            JOIN orders o ON o.id=li."orderId"
            WHERE li."brandId"=%s AND o."shopifyCreatedAt" >= now() - (%s||' months')::interval
              AND o."cancelledAt" IS NULL AND li.sku ~ '^[0-9]+$'
            GROUP BY li.sku""", (bid, str(months)))
    return {r["sku"]: int(r["u"]) for r in rows}


# ---------- gender ----------
def load_gender():
    f = DATA / "gender.json"
    return json.load(open(f, encoding="utf-8")) if f.exists() else {}


# ---------- recommend ----------
def build(prefix, low, pool_stock, months, brand_slug):
    shop, token = resolve_store(prefix)
    prods = fetch_catalog(shop, token)
    sales = fetch_sales(brand_slug, months)
    G = load_gender()
    fam = {}
    stock = {}
    for p in prods:
        if is_bundle(p):
            continue
        v = p["variants"]["edges"][0]["node"]
        if v["sku"] and v["sku"].isdigit():
            stock[v["sku"]] = v["inventoryQuantity"] or 0
            fam[v["sku"]] = [t for t in p["tags"] if t not in ("second", "New", "nostock", "hidden", "slow")]

    pool = [s for s in stock if stock[s] > pool_stock and sales.get(s)]
    used = dict.fromkeys(pool, 0)

    def g(s):
        return G.get(s, "U")

    def pick(theme, present, want_fam):
        cands = [s for s in pool if s not in present and (g(s) == theme or g(s) == "U")]
        cands.sort(key=lambda s: sales[s] - 250 * len(set(fam.get(s, [])) & set(want_fam)) + 250 * used[s])
        return cands[0] if cands else None

    plan = []
    for p in prods:
        if not is_bundle(p):
            continue
        v = p["variants"]["edges"][0]["node"]
        comps = [e["node"]["productVariant"] for e in v.get("productVariantComponents", {}).get("edges", [])]
        perf = [c for c in comps if c["sku"] != "cutie-cadou"]
        lows = [c for c in perf if (c["inventoryQuantity"] or 0) < low]
        if not lows:
            continue
        genders = [g(c["sku"]) for c in perf if g(c["sku"]) != "U"]
        theme = max(set(genders), key=genders.count) if genders else "U"
        present = {c["sku"] for c in perf}
        swaps = []
        for c in lows:
            sku = c["sku"]
            rep = pick(theme, present | {sku}, fam.get(sku, []))
            if rep:
                used[rep] += 1; present.add(rep)
            swaps.append({"scoate": sku, "scoate_name": c["product"]["title"],
                          "scoate_stock": c["inventoryQuantity"] or 0,
                          "pune": rep, "pune_stock": stock.get(rep), "pune_units": sales.get(rep),
                          "pune_fam": fam.get(rep, [])})
        plan.append({"id": p["id"], "title": p["title"], "theme": theme,
                     "n_perf": len(perf), "swaps": swaps})
    return shop, token, plan


# ---------- inspired_list display swap ----------
INSP_Q = """query($id:ID!){ product(id:$id){ metafield(namespace:"custom",key:"inspired_list"){
  references(first:20){ nodes{ ... on Metaobject { id fields{ key value } } } } } } }"""
PHOTO_Q = """query($q:String!){ products(first:1, query:$q){ edges{ node{
  metafield(namespace:"custom",key:"inspired_by_photo"){ reference{ ... on MediaImage { id } } } } } } }"""
META_Q = """query($c:String){ metaobjects(type:"perfume", first:200, after:$c){
  pageInfo{ hasNextPage endCursor } nodes{ id fields{ key value } } } }"""
CREATE_M = """mutation($input:MetaobjectCreateInput!){ metaobjectCreate(metaobject:$input){
  metaobject{ id } userErrors{ field message } } }"""
SET_MF = """mutation($m:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$m){
  metafields{ id } userErrors{ field message } } }"""


def _norm(s):
    import unicodedata, re
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def existing_metaobjects(shop, token):
    out, cur = {}, None
    while True:
        d = gql(shop, token, META_Q, {"c": cur})["metaobjects"]
        for n in d["nodes"]:
            nm = next((f["value"] for f in n["fields"] if f["key"] == "name"), "")
            out[_norm(nm)] = n["id"]
        if d["pageInfo"]["hasNextPage"]:
            cur = d["pageInfo"]["endCursor"]
        else:
            break
    return out


def sku_photo(shop, token, sku):
    d = gql(shop, token, PHOTO_Q, {"q": f"sku:{sku}"})["products"]["edges"]
    if not d:
        return None
    mf = d[0]["node"].get("metafield") or {}
    return (mf.get("reference") or {}).get("id")


def perfume_name(title):
    # "L'Essence No. 20, inspirat din Allure Home Sport by Coco Chanel" -> "Allure Home Sport by Coco Chanel"
    for sep in [", inspirat din ", ", inspirat de ", ", Inspirat din ", ", Inspirat de "]:
        if sep in title:
            return title.split(sep, 1)[1].strip()
    return title


def apply_display(shop, token, plan, prods_by_id):
    metas = existing_metaobjects(shop, token)

    def ensure_meta(sku, name):
        key = _norm(name)
        if key in metas:
            return metas[key]
        photo = sku_photo(shop, token, sku)
        if not photo:
            print(f"  ! No.{sku}: no inspired_by_photo — cannot build card, skipping", file=sys.stderr)
            return None
        base = {"type": "perfume", "fields": [{"key": "name", "value": name}, {"key": "image", "value": photo}]}
        for inp in ({**base, "capabilities": {"publishable": {"status": "ACTIVE"}}}, base):
            r = gql(shop, token, CREATE_M, {"input": inp})["metaobjectCreate"]
            if r["metaobject"]:
                metas[key] = r["metaobject"]["id"]
                return metas[key]
            if not any("publish" in (e.get("message", "").lower()) or "capabilit" in (e.get("message", "").lower())
                       for e in r["userErrors"]):
                print(f"  ! metaobjectCreate {name}: {r['userErrors']}", file=sys.stderr); return None
        return None

    for b in plan:
        # read current cards
        mf = gql(shop, token, INSP_Q, {"id": b["id"]})["product"]["metafield"]
        cards = []
        if mf:
            for n in mf["references"]["nodes"]:
                nm = next((f["value"] for f in n["fields"] if f["key"] == "name"), "")
                cards.append({"gid": n["id"], "name": nm})
        gids = [c["gid"] for c in cards]
        title = b["title"]
        for sw in b["swaps"]:
            if not sw["pune"]:
                continue
            tok = _norm(sw["scoate_name"].split(", inspirat")[0].split(" by ")[0]
                        if ", inspirat" not in sw["scoate_name"] else perfume_name(sw["scoate_name"]).split(" by ")[0])
            idx = [i for i, c in enumerate(cards) if tok and tok in _norm(c["name"])]
            pune_meta = ensure_meta(sw["pune"], perfume_name(prods_by_id.get(sw["pune"], {}).get("title", f"No.{sw['pune']}")))
            if not pune_meta:
                continue
            if idx:
                gids[idx[0]] = pune_meta
            else:
                gids.append(pune_meta)  # card for the dead component was absent from display
        if len(set(gids)) != len(gids):
            print(f"  ! {title}: duplicate card ref — skipping"); continue
        r = gql(shop, token, SET_MF, {"m": [{"ownerId": b["id"], "namespace": "custom", "key": "inspired_list",
                                             "type": "list.metaobject_reference", "value": json.dumps(gids)}]})
        e = r["metafieldsSet"]["userErrors"]
        print(f"  {title}: {'OK' if not e else 'ERR ' + str(e)}")


def verify(shop, token, plan):
    Q = """query($id:ID!){ product(id:$id){ metafield(namespace:"custom",key:"inspired_list"){
      references(first:20){ nodes{ ... on Metaobject { fields{ key value }
        field(key:"image"){ reference{ ... on MediaImage { image{ url } } } } } } } } } }"""
    bad = 0
    for b in plan:
        nodes = gql(shop, token, Q, {"id": b["id"]})["product"]["metafield"]["references"]["nodes"]
        print(f"\n{b['title']}:")
        for n in nodes:
            nm = next((f["value"] for f in n["fields"] if f["key"] == "name"), "")
            img = (((n.get("field") or {}).get("reference") or {}).get("image") or {}).get("url")
            if not img:
                bad += 1
            print(f"   {'IMG-OK' if img else '!!NO-IMG'}  {nm}")
    print(f"\n>>> cards without image: {bad}")


def main():
    ap = argparse.ArgumentParser(description="Esteban Set Cadou bundle maintenance.")
    ap.add_argument("--prefix", default="EST")
    ap.add_argument("--brand-slug", default="esteban")
    ap.add_argument("--low", type=int, default=100, help="component stock threshold to swap OUT")
    ap.add_argument("--pool-stock", type=int, default=400, help="replacement must have stock >")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--apply-display", action="store_true", help="WRITE inspired_list photo swaps")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    shop, token, plan = build(a.prefix, a.low, a.pool_stock, a.months, a.brand_slug)
    prods_by_id = {}  # sku -> {title} for replacement naming
    for p in fetch_catalog(shop, token):
        if is_bundle(p):
            continue
        v = p["variants"]["edges"][0]["node"]
        if v["sku"] and v["sku"].isdigit():
            prods_by_id[v["sku"]] = {"title": p["title"]}

    print(f"# {a.prefix} bundles with a component < {a.low} in stock: {len(plan)}")
    print(f"# replacement pool: stock > {a.pool_stock}, weakest {a.months}mo units, gender-matched\n")
    for b in plan:
        theme = {"M": "barbatesc", "W": "dama", "U": "unisex"}[b["theme"]]
        print(f"### {b['title']}  ({theme}, set-{b['n_perf']})")
        for sw in b["swaps"]:
            rep = (f"No.{sw['pune']} (stoc {sw['pune_stock']}, {sw['pune_units']}buc/an, "
                   f"{','.join(sw['pune_fam']) or '-'})") if sw["pune"] else "(fara candidat)"
            print(f"  SCOATE No.{sw['scoate']} {sw['scoate_name'][:34]:<34} (stoc {sw['scoate_stock']})")
            print(f"   PUNE  {rep}")
        print()

    if a.apply_display:
        print("=== applying inspired_list photo swaps ===")
        apply_display(shop, token, plan, prods_by_id)
    if a.verify:
        verify(shop, token, plan)
    if not a.apply_display:
        print("(read-only. Re-run with --apply-display to swap the storefront photos. "
              "Component swap stays MANUAL in the bundle app — see SKILL.md.)")


if __name__ == "__main__":
    main()
