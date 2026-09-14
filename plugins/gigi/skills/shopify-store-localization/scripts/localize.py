# /// script
# dependencies = ["requests"]
# ///
"""localize.py — apply target-language content to a Shopify store's catalog + facets + locale.

Subcommands (each dry-runs unless --apply):
  collections  --in collections.json   { "<handle>": {"title": "...", "descriptionHtml": "..."} }
  menus        --in menus.json         { "<handle>": {"title":"...","items":[{title,type,url|resourceId,items?}]} }
  facets       --in facets.json        { "<mf_key>": {"name":"<facet label>","map":{"<EN value>":"<target value>"}} }
                                       renames each metafield definition + rewrites its VALUES on every
                                       product (coverage-asserts: aborts if a live value isn't mapped).
  notes        --in notes.json  --keys notes_top,notes_heart,notes_base   { "<EN note>":"<target>" }
  publish      --locale bg             shopLocaleUpdate published:true (does NOT set it primary — API can't)

Product TITLES + product DESCRIPTIONS are store-specific: translate/​regenerate them in the store's
own build repo (see the perfume-store worked example in the SKILL). This tool is the catalog/facet
API glue that IS store-agnostic.

Auth: ARONA Assistant client_credentials (SHOPIFY_ARONA_* KB secrets). Never prints the token.
    uv run scripts/localize.py facets --domain <shop> --in facets.json          # dry-run
    uv run scripts/localize.py facets --domain <shop> --in facets.json --apply
"""
import argparse, json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path.home() / "Downloads/Scripturi/team-intelligence/plugins/core/scripts"))
from kb_env import load_secrets_into_env  # noqa: E402
load_secrets_into_env()
import requests  # noqa: E402


def client(domain):
    D = domain or os.environ["SHOPIFY_ARONA_DUPPO_DOMAIN"]
    V = os.environ["SHOPIFY_ARONA_API_VERSION"]
    tok = requests.post(f"https://{D}/admin/oauth/access_token",
                        json={"client_id": os.environ["SHOPIFY_ARONA_CLIENT_ID"],
                              "client_secret": os.environ["SHOPIFY_ARONA_CLIENT_SECRET"],
                              "grant_type": "client_credentials"}, timeout=20).json()["access_token"]

    def gql(q, v=None):
        for _ in range(4):
            try:
                r = requests.post(f"https://{D}/admin/api/{V}/graphql.json",
                                  headers={"X-Shopify-Access-Token": tok, "Content-Type": "application/json"},
                                  json={"query": q, "variables": v or {}}, timeout=60).json()
            except requests.exceptions.RequestException:
                time.sleep(2); continue
            if "errors" in r:
                raise SystemExit(str(r["errors"])[:400])
            return r["data"]
        raise SystemExit("network")
    return gql


def all_products(gql):
    out, cur = {}, None
    q = ("query($c:String){ products(first:100, after:$c, query:\"status:active OR status:draft\"){ "
         "pageInfo{hasNextPage endCursor} nodes{ id handle } } }")
    while True:
        d = gql(q, {"c": cur})["products"]
        for n in d["nodes"]:
            out[n["handle"]] = n["id"]
        if not d["pageInfo"]["hasNextPage"]:
            break
        cur = d["pageInfo"]["endCursor"]
    return out


def cmd_collections(gql, data, apply):
    byh = {c["handle"]: c["id"] for c in gql("{ collections(first:60){ nodes{ id handle } } }")["collections"]["nodes"]}
    for h, tr in data.items():
        if h not in byh:
            print("skip (no collection)", h); continue
        print(("apply " if apply else "would ") + f"collection {h} → {tr.get('title')}")
        if not apply:
            continue
        r = gql("mutation($i:CollectionInput!){ collectionUpdate(input:$i){ collection{handle} userErrors{field message} } }",
                {"i": {"id": byh[h], **{k: v for k, v in tr.items() if k in ("title", "descriptionHtml")}}})["collectionUpdate"]
        print("  ", r["userErrors"] or "ok"); time.sleep(0.3)


def cmd_menus(gql, data, apply):
    menus = {m["handle"]: m["id"] for m in gql("{ menus(first:20){ nodes{ id handle } } }")["menus"]["nodes"]}

    def clean(it):
        d = {"title": it["title"], "type": it["type"]}
        if it.get("resourceId"):
            d["resourceId"] = it["resourceId"]
        else:
            d["url"] = it.get("url", "/")
        if it.get("items"):
            d["items"] = [clean(x) for x in it["items"]]
        return d
    M = ("mutation($id:ID!,$t:String!,$h:String!,$i:[MenuItemUpdateInput!]!){ "
         "menuUpdate(id:$id,title:$t,handle:$h,items:$i){ menu{handle items{title}} userErrors{field message} } }")
    for h, spec in data.items():
        if h not in menus:
            print("skip (no menu)", h); continue
        items = [clean(x) for x in spec["items"]]
        print(("apply " if apply else "would ") + f"menu {h}: {[x['title'] for x in items]}")
        if not apply:
            continue
        r = gql(M, {"id": menus[h], "t": spec.get("title", h), "h": h, "i": items})["menuUpdate"]
        print("  ", r["userErrors"] or "ok"); time.sleep(0.3)


def cmd_facets(gql, data, apply):
    prods = all_products(gql)
    # 1) coverage check across live values
    UPD = ("mutation($d:MetafieldDefinitionUpdateInput!){ metafieldDefinitionUpdate(definition:$d){ "
           "updatedDefinition{name} userErrors{code message} } }")
    SET = ("mutation($m:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$m){ metafields{key} "
           "userErrors{field message} } }")
    mfs = []
    for key, spec in data.items():
        vmap = spec.get("map", {})
        # fetch current values for this facet, per product
        q = ("query($c:String){ products(first:100, after:$c, query:\"status:active OR status:draft\"){ "
             "pageInfo{hasNextPage endCursor} nodes{ id v: metafield(namespace:\"custom\", key:\"%s\"){value} } } }" % key)
        cur, missing = None, set()
        while True:
            d = gql(q, {"c": cur})["products"]
            for n in d["nodes"]:
                if n["v"] and n["v"]["value"]:
                    try:
                        vals = json.loads(n["v"]["value"])
                    except Exception:
                        vals = [n["v"]["value"]]
                    tvals = []
                    for x in vals:
                        x = x.strip()
                        if x not in vmap:
                            missing.add(x)
                        else:
                            tvals.append(vmap[x])
                    mfs.append({"ownerId": n["id"], "namespace": "custom", "key": key,
                                "type": "list.single_line_text_field", "value": json.dumps(tvals, ensure_ascii=False)})
            if not d["pageInfo"]["hasNextPage"]:
                break
            cur = d["pageInfo"]["endCursor"]
        if missing:
            raise SystemExit(f"facet {key}: UNMAPPED values {sorted(missing)} — add them to facets.json")
        print(f"facet {key}: label='{spec.get('name')}', {len(vmap)} values mapped, coverage OK")
    if not apply:
        print("DRY RUN — add --apply"); return
    for key, spec in data.items():
        if spec.get("name"):
            gql(UPD, {"d": {"namespace": "custom", "key": key, "ownerType": "PRODUCT", "name": spec["name"]}})
    done = 0
    for i in range(0, len(mfs), 25):
        r = gql(SET, {"m": mfs[i:i + 25]})["metafieldsSet"]
        if r["userErrors"]:
            print("  x", r["userErrors"][0]["message"][:80])
        else:
            done += len(mfs[i:i + 25])
        time.sleep(0.25)
    print("facet values set:", done)


def cmd_notes(gql, data, keys, apply):
    prods_q = ("query($c:String){ products(first:100, after:$c, query:\"status:active OR status:draft\"){ "
               "pageInfo{hasNextPage endCursor} nodes{ id " +
               " ".join(f'{k}: metafield(namespace:"custom", key:"{k}"){{value}}' for k in keys) + " } } }")
    nodes, cur, missing = [], None, set()
    while True:
        d = gql(prods_q, {"c": cur})["products"]
        nodes += d["nodes"]
        if not d["pageInfo"]["hasNextPage"]:
            break
        cur = d["pageInfo"]["endCursor"]
    for n in nodes:
        for k in keys:
            if n.get(k) and n[k]["value"]:
                for x in json.loads(n[k]["value"]):
                    if x.strip() not in data:
                        missing.add(x.strip())
    if missing:
        raise SystemExit(f"notes: UNMAPPED {sorted(missing)} — extend the glossary")
    print(f"notes: {len(nodes)} products, glossary covers all values")
    if not apply:
        print("DRY RUN — add --apply"); return
    SET = ("mutation($m:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$m){ metafields{key} "
           "userErrors{field message} } }")
    mfs = []
    for n in nodes:
        for k in keys:
            if n.get(k) and n[k]["value"]:
                arr = [data[x.strip()] for x in json.loads(n[k]["value"])]
                mfs.append({"ownerId": n["id"], "namespace": "custom", "key": k,
                            "type": "list.single_line_text_field", "value": json.dumps(arr, ensure_ascii=False)})
    done = 0
    for i in range(0, len(mfs), 25):
        r = gql(SET, {"m": mfs[i:i + 25]})["metafieldsSet"]
        done += 0 if r["userErrors"] else len(mfs[i:i + 25])
        time.sleep(0.25)
    print("note metafields set:", done)


def cmd_publish(gql, locale, apply):
    if not apply:
        print(f"would publish locale {locale} (then set it Default by hand in admin — API can't)"); return
    r = gql("mutation($l:String!){ shopLocaleUpdate(locale:$l, shopLocale:{published:true}){ "
            "shopLocale{locale published primary} userErrors{field message} } }", {"l": locale})["shopLocaleUpdate"]
    print(r["userErrors"] or r["shopLocale"])
    print("→ NOW: Settings → Languages → set", locale, "as Default (API cannot do this).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["collections", "menus", "facets", "notes", "publish"])
    ap.add_argument("--domain")
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--keys", help="notes: comma list, e.g. notes_top,notes_heart,notes_base")
    ap.add_argument("--locale")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    gql = client(a.domain)
    if a.cmd == "publish":
        cmd_publish(gql, a.locale, a.apply); return
    data = json.load(open(a.inp))
    if a.cmd == "collections":
        cmd_collections(gql, data, a.apply)
    elif a.cmd == "menus":
        cmd_menus(gql, data, a.apply)
    elif a.cmd == "facets":
        cmd_facets(gql, data, a.apply)
    elif a.cmd == "notes":
        cmd_notes(gql, data, (a.keys or "notes_top,notes_heart,notes_base").split(","), a.apply)


if __name__ == "__main__":
    main()
