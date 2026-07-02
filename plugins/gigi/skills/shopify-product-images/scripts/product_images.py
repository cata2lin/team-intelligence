# /// script
# requires-python = "==3.11.*"
# dependencies = [
#   "requests", "pillow", "numpy", "scipy",
#   "rembg==2.0.76", "onnxruntime", "numba==0.59.1", "llvmlite==0.42.0",
# ]
# ///
"""Optimizator poze de produs Shopify: backup · alt+redenumire SKU (shared-aware) · re-incadrare prima poza.
Vezi SKILL.md pentru capcane. rembg cere Python 3.11 (PEP723 forteaza ==3.11.*).

  backup     descarca toate imaginile + manifest (mereu primul)
  alt-rename seteaza alt + filename dupa SKU (partajate=generic, unice=per-SKU); lossless. --apply scrie.
  reframe    normalizeaza prima poza (rembg). --sample = contact-sheet before/after; --apply = urca. --fix a,b,c doar unele.

Token: app OAuth ARONA (client_credentials) daca domeniul e ARONA-app, altfel token static (xconnector.load_shopify_tokens).
"""
import os, sys, re, io, json, time, argparse, subprocess, hashlib, collections
import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
KB = os.environ.get("KB_PY") or "/Users/gheorghebeschea/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"

def kb_secret(key):
    v = os.environ.get(key)
    if v:
        return v
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", key], capture_output=True, text=True, timeout=40).stdout.strip()
    except Exception:
        return ""

# ---------- auth ----------
ARONA_BRAND = {  # myshopify domain -> brand key for SHOPIFY_ARONA_<BRAND>_DOMAIN
    "31k0py-bi.myshopify.com": "LABNOIR",
}
def get_token(domain):
    """Return (domain, admin_token, api_version). Tries ARONA client_credentials, else static tokens."""
    ver = kb_secret("SHOPIFY_ARONA_API_VERSION") or "2026-04"
    if domain in ARONA_BRAND:
        cid, cs = kb_secret("SHOPIFY_ARONA_CLIENT_ID"), kb_secret("SHOPIFY_ARONA_CLIENT_SECRET")
        r = requests.post(f"https://{domain}/admin/oauth/access_token",
                          json={"client_id": cid, "client_secret": cs, "grant_type": "client_credentials"}, timeout=30).json()
        return domain, r["access_token"], ver
    # static tokens via xconnector
    for cand in ("/root/Scripturi/team-intelligence/plugins/gigi/skills/xconnector/xconnector.py",
                 os.path.join(os.path.dirname(__file__), "..", "..", "xconnector", "xconnector.py")):
        if os.path.exists(cand):
            import importlib.util
            spec = importlib.util.spec_from_file_location("xc", cand); xc = importlib.util.module_from_spec(spec); spec.loader.exec_module(xc)
            for t in xc.load_shopify_tokens():
                if t["shopDomain"] == domain or t["shopDomain"].startswith(domain):
                    return t["shopDomain"], t["adminToken"], ver
    sys.exit(f"nu am gasit token pentru {domain}")

def gql(domain, token, ver, q, v=None):
    for _ in range(8):
        try:
            r = requests.post(f"https://{domain}/admin/api/{ver}/graphql.json",
                              headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                              json={"query": q, "variables": v or {}}, timeout=90).json()
        except Exception:
            time.sleep(3); continue
        if "data" in r and r["data"] is not None:
            return r
        time.sleep(3)
    return r

# ---------- product fetch ----------
PQ = '''query($c:String){ products(first:100, after:$c, sortKey:TITLE){ pageInfo{hasNextPage endCursor}
 edges{ node{ id title handle
  variants(first:3){ edges{ node{ sku } } }
  media(first:20){ edges{ node{ ... on MediaImage { id alt status image{ url width height } } } } } } } } }'''

def all_products(domain, token, ver):
    out, c = [], None
    while True:
        d = gql(domain, token, ver, PQ, {"c": c}); pr = d["data"]["products"]
        out += [e["node"] for e in pr["edges"]]
        if not pr["pageInfo"]["hasNextPage"]:
            break
        c = pr["pageInfo"]["endCursor"]
    return out

def sku_base(p):
    """SKU-ul de baza al produsului: numarul din titlu 'Blend No. N' sau primul sku fara sufix marime."""
    m = re.search(r'Blend No\.\s*(\d+)', p["title"])
    if m:
        return m.group(1)
    skus = [e["node"]["sku"] for e in p["variants"]["edges"] if e["node"].get("sku")]
    if skus:
        return re.sub(r'[-_](50ml|100ml|\d+ml)$', '', skus[0], flags=re.I)
    return re.sub(r'[^a-z0-9]+', '-', (p["handle"] or "product").lower())

def imgs_of(p):
    return [e["node"] for e in p["media"]["edges"] if e["node"].get("image")]

# ---------- backup ----------
def cmd_backup(domain, token, ver, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    prods = all_products(domain, token, ver)
    manifest, ok, fail = [], 0, 0
    for p in prods:
        n = sku_base(p)
        for i, m in enumerate(imgs_of(p), 1):
            base = m["image"]["url"].split("?")[0]; ext = base.rsplit(".", 1)[-1]
            fn = f"{n}-{i}.{ext}"; rec = {"sku": n, "idx": i, "product": p["title"], "media_id": m["id"],
                                          "alt": m.get("alt"), "cdn_url": base, "backup_file": fn}
            try:
                open(os.path.join(out_dir, fn), "wb").write(requests.get(base, timeout=60).content); ok += 1
            except Exception as e:
                rec["error"] = str(e); fail += 1
            manifest.append(rec)
    json.dump(manifest, open(os.path.join(out_dir, "_manifest.json"), "w"), ensure_ascii=False, indent=1)
    print(f"BACKUP: {ok} descarcate, {fail} esuate ({len(prods)} produse) -> {out_dir}")

# ---------- alt + rename ----------
def cmd_alt_rename(domain, token, ver, prefix, apply):
    prods = all_products(domain, token, ver)
    occ = collections.Counter()
    for p in prods:
        for m in imgs_of(p):
            occ[m["id"]] += 1
    shared = sorted([mid for mid, c in occ.items() if c > 1])
    ext = {}
    for p in prods:
        for m in imgs_of(p):
            ext[m["id"]] = "." + m["image"]["url"].split("?")[0].rsplit(".", 1)[-1].lower()
    GEN_ALT = f"{prefix} – produs"
    targets = {}
    for k, mid in enumerate(shared, 1):
        targets[mid] = (GEN_ALT, f"{slug(prefix)}-{k}{ext.get(mid,'.jpg')}")
    for p in prods:
        n = sku_base(p); i = 0
        for m in imgs_of(p):
            i += 1
            if occ[m["id"]] > 1:
                continue
            alt = f"{prefix} {n}" + (f" ({i})" if i > 1 else "")
            targets[m["id"]] = (alt, f"{slug(prefix)}-{n}-{i}{ext.get(m['id'],'.jpg')}")
    print(f"media de actualizat: {len(targets)} (partajate={len(shared)}, unice={len(targets)-len(shared)}) | {'APPLY' if apply else 'DRY'}")
    if not apply:
        for mid, (a, f) in list(targets.items())[:6]:
            print(f"  {mid.split('/')[-1]} -> {f}  alt='{a}'")
        return
    items = [{"id": mid, "alt": a, "filename": f} for mid, (a, f) in targets.items()]
    M = 'mutation($f:[FileUpdateInput!]!){ fileUpdate(files:$f){ userErrors{message} } }'
    ok, errs = 0, []
    for j in range(0, len(items), 15):
        res = gql(domain, token, ver, M, {"f": items[j:j+15]})
        ue = ((res.get("data") or {}).get("fileUpdate") or {}).get("userErrors")
        if ue: errs += ue
        else: ok += len(items[j:j+15])
        time.sleep(0.4)
    print(f"actualizate: {ok} | erori: {errs or 'ZERO'}")

def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')

# ---------- reframe (rembg) ----------
TARGET_BH, BASELINE, TOPBUF = 0.86, 0.90, 0.035
def _rembg():
    import numpy as np
    from scipy import ndimage as ndi
    from rembg import remove, new_session
    sess = new_session("u2net")
    def bbox(im):
        a = np.array(remove(im, session=sess, alpha_matting=False))[:, :, 3]; mask = a > 50
        lab, n = ndi.label(mask); best = None
        for i in range(1, n+1):
            ys, xs = np.where(lab == i)
            if len(xs) < im.size[0]*im.size[1]*0.008: continue
            h = int(ys.max()-ys.min())
            if best is None or h > best[0]: best = (h, int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        return best[1:] if best else None
    def label_cx(im):
        a = np.asarray(im, dtype=np.int16); R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
        mn = np.minimum(np.minimum(R, G), B); cr = ((R-B) > 15) & (mn > 95) & (R > B) & (G >= B)
        cr = ndi.binary_closing(cr, iterations=4); cr = ndi.binary_fill_holes(cr); cr = ndi.binary_opening(cr, iterations=3)
        lab, n = ndi.label(cr); H, W = R.shape; best = None
        for i in range(1, n+1):
            ys, xs = np.where(lab == i); w = xs.max()-xs.min(); area = len(xs); cx = (xs.min()+xs.max())/2
            if area < W*H*0.008 or w < W*0.12 or abs(cx/W-0.5) > 0.30: continue
            if best is None or area > best[0]: best = (area, cx)
        return best[1]/W if best else None
    return bbox, label_cx

def normalize(im, bb, lcx):
    import numpy as np
    from PIL import Image
    W, H = im.size; x0, y0, x1, y1 = bb; y0 = max(0, y0 - int(TOPBUF*H))
    bhh = y1 - y0; cx = (lcx*W) if lcx else (x0+x1)/2; bottom = y1
    s = (TARGET_BH*H)/bhh; winW, winH = W/s, H/s
    wl, wt = cx - winW/2, bottom - BASELINE*H/s
    src = np.asarray(im); L, T = int(round(wl)), int(round(wt))
    padL, padT = max(0, -L), max(0, -T)
    padR, padB = max(0, int(round(wl+winW))-W), max(0, int(round(wt+winH))-H)
    arr = np.pad(src, ((padT, padB), (padL, padR), (0, 0)), mode='edge')
    crop = arr[T+padT:T+padT+int(round(winH)), L+padL:L+padL+int(round(winW))]
    return Image.fromarray(crop).resize((W, H), Image.LANCZOS)

def cmd_reframe(domain, token, ver, prefix, work_dir, sample, apply, fix):
    from PIL import Image, ImageDraw
    os.makedirs(f"{work_dir}/framed", exist_ok=True)
    bbox, label_cx = _rembg()
    prods = all_products(domain, token, ver)
    only = set(fix.split(",")) if fix else None
    todo = [p for p in prods if imgs_of(p) and (only is None or sku_base(p) in only)]
    print(f"produse de re-incadrat: {len(todo)}")
    stats = []
    for p in todo:
        n = sku_base(p); first = imgs_of(p)[0]
        src = Image.open(io.BytesIO(requests.get(first["image"]["url"].split("?")[0], timeout=60).content)).convert("RGB")
        bb = bbox(src)
        if not bb:
            print(f"  {n}: NO bbox (sar)"); continue
        out = normalize(src, bb, label_cx(src))
        out.save(f"{work_dir}/framed/{slug(prefix)}-{n}-1.jpg", quality=95, subsampling=0)
        stats.append((n, p, src, out, first["id"]))
    if sample or not apply:
        _sheet(stats, f"{work_dir}/_before_after.png")
        print(f"contact-sheet -> {work_dir}/_before_after.png · {'(dry-run, nu s-a urcat)' if not apply else ''}")
        if not apply:
            return
    ok = 0
    for n, p, src, out, oldid in stats:
        if _replace_first(domain, token, ver, p["id"], f"{work_dir}/framed/{slug(prefix)}-{n}-1.jpg",
                          f"{slug(prefix)}-{n}-1.jpg", f"{prefix} {n}", oldid):
            ok += 1
            print(f"  {n}: urcat", flush=True)
        time.sleep(0.4)
    print(f"re-incadrate live: {ok}/{len(stats)}")

def _sheet(stats, path):
    from PIL import Image, ImageDraw
    s = sorted(stats, key=lambda r: 0)  # keep order
    pick = s[:3] + s[-3:] if len(s) > 6 else s
    tw, th = 300, 240
    sheet = Image.new("RGB", (tw*2+24, len(pick)*(th+22)), (250, 250, 250)); dd = ImageDraw.Draw(sheet)
    for i, (n, p, src, out, oldid) in enumerate(pick):
        b = src.copy(); b.thumbnail((tw, th)); a = out.copy(); a.thumbnail((tw, th))
        y = i*(th+22); sheet.paste(b, (6, y+16)); sheet.paste(a, (tw+16, y+16))
        dd.text((6, y+2), f"SKU {n}:  ORIGINAL  |  NORMALIZAT", fill=(20, 20, 20))
    sheet.save(path)

def _replace_first(domain, token, ver, pid, filepath, filename, alt, oldid):
    su = gql(domain, token, ver, 'mutation($i:[StagedUploadInput!]!){ stagedUploadsCreate(input:$i){ stagedTargets{ url resourceUrl parameters{name value} } } }',
             {"i": [{"resource": "IMAGE", "filename": filename, "mimeType": "image/jpeg", "httpMethod": "POST"}]})
    tg = su["data"]["stagedUploadsCreate"]["stagedTargets"][0]
    params = {x["name"]: x["value"] for x in tg["parameters"]}
    requests.post(tg["url"], data=params, files={"file": (filename, open(filepath, "rb"), "image/jpeg")}, timeout=120)
    cm = gql(domain, token, ver, 'mutation($id:ID!,$m:[CreateMediaInput!]!){ productCreateMedia(productId:$id, media:$m){ media{ ... on MediaImage { id } } mediaUserErrors{message} } }',
             {"id": pid, "m": [{"originalSource": tg["resourceUrl"], "mediaContentType": "IMAGE", "alt": alt}]})
    med = cm["data"]["productCreateMedia"]["media"]
    if not med:
        return False
    newid = med[0]["id"]
    for _ in range(25):
        d = gql(domain, token, ver, 'query($id:ID!){ product(id:$id){ media(first:20){ edges{ node{ ... on MediaImage { id status } } } } } }', {"id": pid})
        nm = next((e["node"] for e in d["data"]["product"]["media"]["edges"] if e["node"].get("id") == newid), None)
        if nm and nm.get("status") == "READY":
            break
        time.sleep(2)
    gql(domain, token, ver, 'mutation($id:ID!,$mv:[MoveInput!]!){ productReorderMedia(id:$id, moves:$mv){ mediaUserErrors{message} } }', {"id": pid, "mv": [{"id": newid, "newPosition": "0"}]}); time.sleep(2)
    if oldid:
        gql(domain, token, ver, 'mutation($id:ID!,$ids:[ID!]!){ productDeleteMedia(productId:$id, mediaIds:$ids){ mediaUserErrors{message} } }', {"id": pid, "ids": [oldid]}); time.sleep(2)
    gql(domain, token, ver, 'mutation($f:[FileUpdateInput!]!){ fileUpdate(files:$f){ userErrors{message} } }', {"f": [{"id": newid, "filename": filename, "alt": alt}]})
    return True

# ---------- main ----------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backup", "alt-rename", "reframe"])
    ap.add_argument("--domain", required=True)
    ap.add_argument("--prefix", default=None, help="prefix alt/nume (ex 'Lab Noir Blend No.'); default = numele magazinului")
    ap.add_argument("--out", default=None, help="folder de lucru/backup")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--sample", action="store_true")
    ap.add_argument("--fix", default=None, help="doar aceste SKU-uri (CSV) la reframe")
    a = ap.parse_args()
    domain, token, ver = get_token(a.domain)
    shop_name = gql(domain, token, ver, '{ shop{ name } }')["data"]["shop"]["name"]
    prefix = a.prefix or shop_name
    out = a.out or os.path.expanduser(f"~/Downloads/{slug(shop_name)}_images")
    if a.cmd == "backup":
        cmd_backup(domain, token, ver, out)
    elif a.cmd == "alt-rename":
        cmd_alt_rename(domain, token, ver, prefix, a.apply)
    elif a.cmd == "reframe":
        cmd_reframe(domain, token, ver, prefix, out, a.sample, a.apply, a.fix)
