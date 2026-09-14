# /// script
# dependencies = ["requests"]
# ///
"""upload_to_files.py — upload transparent PNGs to Shopify Files and repoint a product metafield.

Input JSON: { "<key>": {"id": "gid://shopify/Product/…", ...}, ... }   (the product owner ids)
PNGs:       <pngs>/<key>.png  (from cutout.py)

    uv run scripts/upload_to_files.py --in images.json --pngs /tmp/cut \
        --domain <shop>.myshopify.com --namespace custom --key inspired_by_photo        # dry-run
    uv run scripts/upload_to_files.py … --apply

Auth: ARONA Assistant client_credentials (SHOPIFY_ARONA_* KB secrets). Never prints the token.
For static/OAuth-token stores, swap token() for core.stores.get_store(prefix).
"""
import argparse, json, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path.home() / "Downloads/Scripturi/team-intelligence/plugins/core/scripts"))
from kb_env import load_secrets_into_env  # noqa: E402
load_secrets_into_env()
import requests  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--pngs", default="/tmp/cut")
    ap.add_argument("--domain", help="shop.myshopify.com (else SHOPIFY_ARONA_DUPPO_DOMAIN)")
    ap.add_argument("--namespace", default="custom")
    ap.add_argument("--key", required=True, help="image metafield key, e.g. inspired_by_photo")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    D = a.domain or os.environ["SHOPIFY_ARONA_DUPPO_DOMAIN"]
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
                raise SystemExit(str(r["errors"])[:300])
            return r["data"]
        raise SystemExit("network")

    data = json.load(open(a.inp))
    keys = [k for k in sorted(data) if os.path.exists(os.path.join(a.pngs, f"{k}.png"))]
    ptype = {d["key"]: d["type"]["name"] for d in gql(
        f'{{ metafieldDefinitions(first:60, ownerType:PRODUCT, namespace:"{a.namespace}"){{ nodes{{ key type{{ name }} }} }} }}'
    )["metafieldDefinitions"]["nodes"]}.get(a.key, "single_line_text_field")
    print(f"{len(keys)} PNGs · metafield {a.namespace}.{a.key} ({ptype})")
    if not a.apply:
        print("DRY RUN — add --apply"); return

    def upload(path, fn):
        st = gql('mutation($i:[StagedUploadInput!]!){ stagedUploadsCreate(input:$i){ '
                 'stagedTargets{ url resourceUrl parameters{name value} } userErrors{field message} } }',
                 {"i": [{"filename": fn, "mimeType": "image/png", "resource": "IMAGE", "httpMethod": "POST"}]}
                 )["stagedUploadsCreate"]["stagedTargets"][0]
        r = requests.post(st["url"], data={p["name"]: p["value"] for p in st["parameters"]},
                          files={"file": (fn, open(path, "rb"), "image/png")}, timeout=90)
        if r.status_code not in (200, 201, 204):
            raise Exception(f"put {r.status_code}")
        return st["resourceUrl"]

    results = {}
    for k in keys:
        try:
            ru = upload(os.path.join(a.pngs, f"{k}.png"), f"cut-{k}.png")
            fc = gql('mutation($f:[FileCreateInput!]!){ fileCreate(files:$f){ files{ id } userErrors{field message} } }',
                     {"f": [{"contentType": "IMAGE", "originalSource": ru, "alt": f"cut-{k}"}]})["fileCreate"]
            if fc["userErrors"]:
                print("fc x", k, fc["userErrors"]); continue
            results[k] = fc["files"][0]["id"]
        except Exception as e:  # noqa: BLE001
            print("up x", k, str(e)[:90])
        time.sleep(0.15)
    print("files created:", len(results), "· polling CDN urls…")

    id2k = {v: k for k, v in results.items()}
    ids, urls = list(results.values()), {}
    for _ in range(40):
        q = gql("query($ids:[ID!]!){ nodes(ids:$ids){ ... on MediaImage{ id image{ url } } } }", {"ids": ids})
        pend = []
        for n in q["nodes"]:
            if n and n.get("image") and n["image"] and n["image"].get("url"):
                urls[id2k[n["id"]]] = n["image"]["url"]
            elif n:
                pend.append(n["id"])
        if not pend:
            break
        ids = pend; time.sleep(3)
    print("cdn urls:", len(urls))

    mfs = [{"ownerId": data[k]["id"], "namespace": a.namespace, "key": a.key, "type": ptype, "value": u}
           for k, u in urls.items()]
    done = 0
    for i in range(0, len(mfs), 25):
        r = gql('mutation($m:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$m){ '
                'metafields{key} userErrors{field message} } }', {"m": mfs[i:i + 25]})["metafieldsSet"]
        if r["userErrors"]:
            print("mf x", r["userErrors"][0])
        else:
            done += len(mfs[i:i + 25])
        time.sleep(0.25)
    print("DONE metafields updated:", done)


if __name__ == "__main__":
    main()
