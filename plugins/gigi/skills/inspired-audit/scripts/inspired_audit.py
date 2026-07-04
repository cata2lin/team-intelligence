# /// script
# requires-python = ">=3.10"
# dependencies = ["requests"]
# ///
"""Audit + fix pentru metafield-urile „inspired by" pe magazinele de parfum ARONA (dupe-uri de tip clonă).

Fiecare parfum-clonă are un metafield care spune ce parfum de lux imită, plus poza sursei:
  custom.inspired_by        (text — ex „Tom Ford — Fucking Fabulous")
  custom.inspired_by_photo  (url — poza parfumului original)
Bug tipic: DOUĂ produse diferite arată ACEEAȘI poză inspired-by (copy-paste), sau lipsește text/poză,
sau poza e HOTLINK fragrantica (fimgs.net) care se poate rupe → trebuie re-găzduită pe Shopify.

  audit  --brand LABNOIR                 # raport: lipsă text/poză, poze DUPLICATE, hotlink-uri fragrantica
  audit  --all                           # toate magazinele de parfum
  rehost --brand LABNOIR [--apply]       # descarcă pozele fragrantica + le urcă pe Shopify + update metafield

Read-only by default. rehost scrie pe Shopify DOAR cu --apply. Vezi si gigi:shopify-product-images.
"""
import os, sys, json, argparse, subprocess, hashlib, re, io
import requests
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KB = os.environ.get("KB_PY") or os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
def kb(key):
    v = os.environ.get(key)
    if v: return v
    try: return subprocess.run(["uv","run",KB,"secret-get",key], capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception: return ""

# Magazine de parfum-clonă pe app-ul OAuth ARONA (au inspired_by metafields)
PERFUME_BRANDS = ["LABNOIR", "ESTEBAN", "GT", "NUBRA"]
IB_NS, IB_TEXT, IB_PHOTO = "custom", "inspired_by", "inspired_by_photo"

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

PROD_Q = """query($cur:String){ products(first:100, after:$cur){ pageInfo{ hasNextPage endCursor }
  edges{ node{ id title handle
    ib_text: metafield(namespace:"%s", key:"%s"){ value }
    ib_photo: metafield(namespace:"%s", key:"%s"){ value }
  } } } }""" % (IB_NS, IB_TEXT, IB_NS, IB_PHOTO)

def all_products(dom, tok, ver):
    cur, out = None, []
    while True:
        r = gql(dom, tok, ver, PROD_Q, {"cur": cur})
        if r.get("errors"): sys.exit(f"products err: {r['errors']}")
        c = r["data"]["products"]
        for e in c["edges"]:
            n = e["node"]
            out.append({"id": n["id"], "title": n["title"], "handle": n["handle"],
                        "text": (n.get("ib_text") or {}).get("value"),
                        "photo": (n.get("ib_photo") or {}).get("value")})
        if not c["pageInfo"]["hasNextPage"]: break
        cur = c["pageInfo"]["endCursor"]
    return out

def is_fragrantica(u): return bool(u) and ("fimgs.net" in u or "fragrantica" in u)
def is_shopify_cdn(u): return bool(u) and "cdn.shopify.com" in u

def cmd_audit(brand):
    dom, tok, ver = token(brand)
    prods = all_products(dom, tok, ver)
    no_text  = [p for p in prods if not p["text"]]
    no_photo = [p for p in prods if not p["photo"]]
    hotlinks = [p for p in prods if is_fragrantica(p["photo"])]
    # poze DUPLICATE: acelasi URL pe >1 produs
    byphoto = {}
    for p in prods:
        if p["photo"]: byphoto.setdefault(p["photo"], []).append(p)
    dup_photo = {u: ps for u, ps in byphoto.items() if len(ps) > 1}
    # text DUPLICAT (acelasi inspired_by pe >1 produs — posibil clona corecta, dar merita ochi)
    bytext = {}
    for p in prods:
        if p["text"]: bytext.setdefault(p["text"].strip().lower(), []).append(p)
    dup_text = {t: ps for t, ps in bytext.items() if len(ps) > 1}

    print(f"### {brand} ({dom}) — {len(prods)} produse")
    print(f"  fara text inspired_by : {len(no_text)}")
    print(f"  fara poza inspired_by : {len(no_photo)}")
    print(f"  hotlink fragrantica   : {len(hotlinks)}  (rehost recomandat)")
    print(f"  POZE DUPLICATE        : {len(dup_photo)} url-uri pe >1 produs")
    for u, ps in list(dup_photo.items())[:20]:
        src = "fragrantica" if is_fragrantica(u) else ("shopify" if is_shopify_cdn(u) else "alt")
        print(f"     [{src}] {u[:70]}")
        for p in ps: print(f"        - {p['title']}  ({p['text'] or 'FARA TEXT'})")
    if dup_text:
        print(f"  text inspired_by duplicat: {len(dup_text)} (verifica daca-s clone reale sau copy-paste)")
        for t, ps in list(dup_text.items())[:10]:
            print(f"     '{t}' -> {', '.join(p['title'] for p in ps)}")
    if no_photo:
        print("  --- produse FARA poza:")
        for p in no_photo[:30]: print(f"     - {p['title']}  ({p['text'] or 'si fara text'})")
    return {"prods": prods, "hotlinks": hotlinks, "dup_photo": dup_photo}

# ---- rehost: fragrantica -> Shopify Files -> update metafield ----
STAGED = """mutation($input:[StagedUploadInput!]!){ stagedUploadsCreate(input:$input){
  stagedTargets{ url resourceUrl parameters{ name value } } userErrors{ field message } } }"""
FILECREATE = """mutation($files:[FileCreateInput!]!){ fileCreate(files:$files){
  files{ id fileStatus alt ... on MediaImage{ image{ url } } } userErrors{ field message } } }"""
MFSET = """mutation($mf:[MetafieldsSetInput!]!){ metafieldsSet(metafields:$mf){
  metafields{ id } userErrors{ field message } } }"""

def dl(url):
    h = {"User-Agent":"Mozilla/5.0","Referer":"https://www.fragrantica.com/"}
    r = requests.get(url, headers=h, timeout=40); r.raise_for_status()
    return r.content

def upload_file(dom, tok, ver, data, fname):
    inp = [{"filename": fname, "mimeType": "image/jpeg", "resource": "IMAGE", "httpMethod": "POST", "fileSize": str(len(data))}]
    r = gql(dom, tok, ver, STAGED, {"input": inp})
    t = r["data"]["stagedUploadsCreate"]["stagedTargets"][0]
    files = {p["name"]: (None, p["value"]) for p in t["parameters"]}
    files["file"] = (fname, io.BytesIO(data), "image/jpeg")
    up = requests.post(t["url"], files=files, timeout=90)
    if up.status_code not in (200,201,204): sys.exit(f"staged upload fail {up.status_code}: {up.text[:200]}")
    r2 = gql(dom, tok, ver, FILECREATE, {"files":[{"originalSource": t["resourceUrl"], "contentType":"IMAGE", "alt": fname}]})
    ue = r2["data"]["fileCreate"]["userErrors"]
    if ue: sys.exit(f"fileCreate err: {ue}")
    fid = r2["data"]["fileCreate"]["files"][0]["id"]
    # poll pana are URL public (fileStatus READY)
    Q = """query($id:ID!){ node(id:$id){ ... on MediaImage{ fileStatus image{ url } } } }"""
    for _ in range(20):
        n = gql(dom, tok, ver, Q, {"id": fid})["data"]["node"]
        if n and n.get("image") and n["image"].get("url"): return n["image"]["url"]
    return None

def cmd_rehost(brand, apply):
    dom, tok, ver = token(brand)
    prods = all_products(dom, tok, ver)
    todo = [p for p in prods if is_fragrantica(p["photo"])]
    print(f"### {brand}: {len(todo)} poze inspired_by pe hotlink fragrantica -> Shopify")
    if not todo:
        print("  nimic de re-gazduit (toate deja pe Shopify CDN)."); return
    for p in todo:
        blend = re.sub(r"[^a-z0-9]+","-", (p["text"] or p["handle"]).lower()).strip("-")[:50]
        fname = f"inspired-by-{blend}.jpg"
        print(f"  {'REHOST' if apply else 'DRY'} {p['title']}  <-  {p['photo'][:60]}  ->  {fname}")
        if not apply: continue
        try:
            data = dl(p["photo"])
            new_url = upload_file(dom, tok, ver, data, fname)
            if not new_url: print("     ! upload nu a devenit READY, sar"); continue
            mf = [{"ownerId": p["id"], "namespace": IB_NS, "key": IB_PHOTO, "type":"url", "value": new_url}]
            r = gql(dom, tok, ver, MFSET, {"mf": mf})
            ue = r["data"]["metafieldsSet"]["userErrors"]
            print("     OK ->", new_url if not ue else f"MFSET err: {ue}")
        except Exception as e:
            print(f"     ! esec: {e}")
    if not apply: print("DRY-RUN (adauga --apply ca sa re-gazduiesti + update metafield).")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("audit"); a.add_argument("--brand"); a.add_argument("--all", action="store_true")
    r = sub.add_parser("rehost"); r.add_argument("--brand", required=True); r.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if args.cmd == "audit":
        for b in (PERFUME_BRANDS if args.all else [args.brand or sys.exit("--brand sau --all")]):
            try: cmd_audit(b)
            except SystemExit as e: print(f"### {b}: {e}")
    elif args.cmd == "rehost":
        cmd_rehost(args.brand, args.apply)
