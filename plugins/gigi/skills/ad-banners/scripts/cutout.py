#!/usr/bin/env python3
"""Remove the background from a product photo -> tight-cropped transparent PNG.

Uses rembg (isnet-general-use, a high-quality general matting model). Run
`bash setup_env.sh <venv>` ONCE to install rembg in your venv, then:

    <venv>/bin/python cutout.py --src bottle.jpg --out bottle_cut.png
    <venv>/bin/python cutout.py --dir nas_src/ --out-dir cuts/

Notes
- Works best on product shots on a light/uniform background.
- A DARK product (e.g. a matte-black bottle) becomes invisible on a dark banner —
  prefer a coloured/amber/glass source, or add a glow behind it (see banners.html).
- Glass is translucent: the silhouette is cut cleanly but the interior keeps the
  original look; amber/opaque liquids read best on a new background.
- This is NOT remove_watermark.py — that only erases the Gemini ✦ corner mark.
"""
import argparse, os, glob
from PIL import Image
import numpy as np
from rembg import remove, new_session

def cutout(src, out, sess, margin=8, alpha_thresh=12):
    im = Image.open(src).convert("RGBA")
    cut = remove(im, session=sess)            # hard alpha (no pymatting needed)
    a = np.array(cut)[:, :, 3]
    ys, xs = np.where(a > alpha_thresh)
    if len(ys):
        y1, y2, x1, x2 = ys.min(), ys.max(), xs.min(), xs.max()
        cut = cut.crop((max(0, x1 - margin), max(0, y1 - margin),
                        min(im.width, x2 + margin), min(im.height, y2 + margin)))
    cut.save(out)
    print(f"✓ {out}  {cut.size}  aspect={cut.width/cut.height:.3f}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src"); ap.add_argument("--out")
    ap.add_argument("--dir"); ap.add_argument("--out-dir", default="cuts")
    ap.add_argument("--model", default="isnet-general-use")
    ap.add_argument("--margin", type=int, default=8)
    a = ap.parse_args()
    sess = new_session(a.model)
    if a.src:
        cutout(a.src, a.out or os.path.splitext(a.src)[0] + "_cut.png", sess, a.margin)
    elif a.dir:
        os.makedirs(a.out_dir, exist_ok=True)
        for f in sorted(glob.glob(os.path.join(a.dir, "*"))):
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                stem = os.path.splitext(os.path.basename(f))[0].replace(" ", "_")
                cutout(f, os.path.join(a.out_dir, f"cut_{stem}.png"), sess, a.margin)
    else:
        ap.error("pass --src FILE or --dir FOLDER")

if __name__ == "__main__":
    main()
