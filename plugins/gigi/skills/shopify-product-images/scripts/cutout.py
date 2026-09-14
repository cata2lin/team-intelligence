# /// script
# requires-python = ">=3.11,<3.12"
# dependencies = ["rembg[cpu]", "pillow", "requests", "onnxruntime", "numpy", "llvmlite==0.42.0", "numba==0.59.1"]
# ///
"""cutout.py — remove background from product photos to transparent PNGs.

Input JSON: { "<key>": {"url": "https://…source.jpg", ...}, ... }  (extra fields ignored)
Output:     <out>/<key>.png  (transparent, trimmed to bbox, long side <= 600px)

MUST run on Python 3.9 (rembg's numba/llvmlite have no 3.12 wheels):
    uv run --python 3.11 scripts/cutout.py --in images.json --out /tmp/cut
"""
import argparse, io, json, os
import requests
from rembg import remove, new_session
from PIL import Image

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36",
      "Referer": "https://www.google.com/", "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", default="/tmp/cut")
    ap.add_argument("--model", default="isnet-general-use")
    ap.add_argument("--max", type=int, default=600)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    data = json.load(open(a.inp))
    sess = new_session(a.model)
    ok, fail = 0, []
    for i, (key, info) in enumerate(sorted(data.items()), 1):
        outp = os.path.join(a.out, f"{key}.png")
        if os.path.exists(outp):
            ok += 1
            continue
        url = info["url"] if isinstance(info, dict) else info
        try:
            r = requests.get(url, headers=UA, timeout=40)
            r.raise_for_status()
            im = Image.open(io.BytesIO(remove(r.content, session=sess))).convert("RGBA")
            bb = im.getbbox()
            if bb:
                im = im.crop(bb)
            im.thumbnail((a.max, a.max), Image.LANCZOS)
            im.save(outp)
            ok += 1
        except Exception as e:  # noqa: BLE001 — a blocked/404 source shouldn't kill the batch
            fail.append((key, str(e)[:90]))
        if i % 15 == 0:
            print(f"  {i}/{len(data)}", flush=True)
    print(f"DONE ok={ok} fail={len(fail)}")
    for k, e in fail:
        print("  FAIL", k, e, "  (hotlink-blocked? fetch via the browser — see SKILL.md)")


if __name__ == "__main__":
    main()
