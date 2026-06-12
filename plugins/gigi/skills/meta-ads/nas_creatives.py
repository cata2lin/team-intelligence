# /// script
# requires-python = ">=3.10"
# dependencies = ["psycopg2-binary>=2.9", "requests>=2.31"]
# ///
"""Bridge: top-performing Meta ads → the actual creative FILES on the team NAS.
Finds which video/image files (by ad-name ↔ filename matching) correspond to a brand's
best Meta ads, then lets you copy them somewhere, print their paths/links, or stage them
for Google Ads (YouTube upload via gigi:google-ads-mcc yt_upload.py).

  uv run nas_creatives.py find belasil --root ~/nas --range last_90d --top 10
  uv run nas_creatives.py find belasil --root ~/nas --copy-to "$NAS_ROOT/exports/winners"
The NAS mounts at ~/nas/<share> (core:nas / nas_connect.py). --root can be ANY folder
(a local dir works too). Matching = same normalization as meta.py creatives.
"""
import os, sys, re, json, shutil, argparse, unicodedata
from pathlib import Path

VID_EXT = (".mp4", ".mov", ".m4v", ".webm", ".avi")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp")

def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFD", str(s or "")) if unicodedata.category(c) != "Mn")
    s = re.sub(r"\.(mp4|mov|m4v|webm|avi|jpg|jpeg|png|webp)$", "", s.lower())
    return re.sub(r"[^a-z0-9]", "", s)

def index_files(root, with_images=False):
    """Walk root and index media files by normalized basename. Skips hidden/system dirs."""
    exts = VID_EXT + (IMG_EXT if with_images else ())
    idx = {}
    root = Path(os.path.expanduser(root))
    if not root.exists():
        sys.exit(f"--root nu există / nu e montat: {root}  (rulează nas_connect.py / verifică rețeaua)")
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith((".", "@", "#"))]
        for f in filenames:
            if f.lower().endswith(exts):
                idx.setdefault(norm(f), []).append(os.path.join(dirpath, f))
    return idx

def top_ads(brand, rng, top):
    """Top ads by ROAS (with volume) via the meta.py machinery (RON, canonical mapping)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import meta as M
    start, end = M.daterange(rng)
    accts, rows = M.insights(brand, "ad", rng)
    idx = M.fx_index([x["cur"] for x in accts], start, end)
    agg = {}
    for r in rows:
        nm = r.get("ad_name", "?"); m = M.metricize(r); dd = M._pdate(r.get("date_start"))
        g = agg.setdefault(nm, dict(spend=0, purch=0, rev=0))
        g["spend"] += M.conv(m["spend"], r["_cur"], dd, idx)
        g["rev"]   += M.conv(m["rev"],   r["_cur"], dd, idx)
        g["purch"] += m["purch"]
    out = [dict(name=n, **g, roas=(g["rev"]/g["spend"] if g["spend"] else 0)) for n, g in agg.items()]
    out = [o for o in out if o["spend"] >= 150 and o["purch"] >= 3]
    out.sort(key=lambda x: -x["roas"])
    return out[:top]

def match(ad_name, idx):
    n = norm(ad_name)
    if len(n) >= 5:
        for k, paths in idx.items():
            if n in k or k in n:
                return paths
    toks = set(re.findall(r"[a-z]{4,}", (ad_name or "").lower())) - {"test", "video", "belasil", "lavete"}
    best, score = [], 0
    for k, paths in idx.items():
        s = sum(1 for t in toks if t in k)
        if s > score: best, score = paths, s
    return best if score >= 2 else []

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="top Meta ads → files under --root (NAS or any folder)")
    f.add_argument("brand"); f.add_argument("--root", default=os.path.expanduser("~/nas"))
    f.add_argument("--range", default="last_90d"); f.add_argument("--top", type=int, default=10)
    f.add_argument("--images", action="store_true", help="include image files too")
    f.add_argument("--copy-to", default="", help="copy matched files here (created if missing)")
    a = ap.parse_args()

    print(f"» indexez fișierele media sub {a.root} …")
    idx = index_files(a.root, with_images=a.images)
    nfiles = sum(len(v) for v in idx.values())
    print(f"  {nfiles} fișiere media indexate")
    print(f"» top reclame Meta '{a.brand}' ({a.range}) …")
    ads = top_ads(a.brand, a.range, a.top)

    copied = 0
    dest = Path(os.path.expanduser(a.copy_to)) if a.copy_to else None
    if dest: dest.mkdir(parents=True, exist_ok=True)
    print(f"\n{'ROAS':>5} {'achiz':>6} {'spend':>9}  reclamă → fișier(e) pe NAS")
    for o in ads:
        paths = match(o["name"], idx)
        tag = paths[0] if paths else "— negăsit —"
        print(f"{o['roas']:>5.2f} {o['purch']:>6.0f} {o['spend']:>9.0f}  {o['name'][:36]:36} → {tag}")
        for extra in paths[1:3]:
            print(f"{'':28}↳ {extra}")
        if dest and paths:
            tgt = dest / Path(paths[0]).name
            if not tgt.exists():
                shutil.copy2(paths[0], tgt); copied += 1
                print(f"{'':28}✓ copiat → {tgt}")
    if dest:
        print(f"\n{copied} fișiere copiate în {dest}")
        print("Pentru Google Ads: uv run ../google-ads-mcc/yt_upload.py --dir \"%s\"  (apoi atașare la PMax)" % dest)

if __name__ == "__main__":
    main()
