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

def _trim(im, thr=12, m=8):
    a = np.array(im)[:, :, 3]; ys, xs = np.where(a > thr)
    if not len(ys): return im
    y1, y2, x1, x2 = ys.min(), ys.max(), xs.min(), xs.max()
    return im.crop((max(0, x1 - m), max(0, y1 - m),
                    min(im.width, x2 + m), min(im.height, y2 + m)))

def _upright(im):
    """Rotate a SINGLE-object cutout so its long axis is vertical, narrow (cap) end up.
    Use only for one isolated product; multi-object cutouts won't deskew sensibly."""
    import cv2
    a = np.array(im)[:, :, 3]; ys, xs = np.where(a > 20)
    (cx, cy), (w, h), ang = cv2.minAreaRect(np.column_stack([xs, ys]).astype(np.float32))
    if w > h: ang += 90                                    # make the longer side vertical
    rot = _trim(im.rotate(ang, resample=Image.BICUBIC, expand=True))
    aa = np.array(rot)[:, :, 3] > 20; H = aa.shape[0]; k = max(1, int(H * 0.18))
    if aa[:k].sum(1).mean() > aa[-k:].sum(1).mean():       # wider at top => cap at bottom
        rot = _trim(rot.rotate(180, expand=True))          # flip so the cap is up
    return rot

def cutout(src, out, sess, margin=8, alpha_thresh=12, upright=False):
    im = Image.open(src).convert("RGBA")
    cut = remove(im, session=sess)            # hard alpha (no pymatting needed)
    cut = _trim(cut, alpha_thresh, margin)
    if upright:
        cut = _upright(cut)
    cut.save(out)
    print(f"✓ {out}  {cut.size}  aspect={cut.width/cut.height:.3f}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src"); ap.add_argument("--out")
    ap.add_argument("--dir"); ap.add_argument("--out-dir", default="cuts")
    ap.add_argument("--model", default="isnet-general-use")
    ap.add_argument("--margin", type=int, default=8)
    ap.add_argument("--upright", action="store_true", help="auto-rotate a single bottle to vertical, cap up")
    a = ap.parse_args()
    sess = new_session(a.model)
    if a.src:
        cutout(a.src, a.out or os.path.splitext(a.src)[0] + "_cut.png", sess, a.margin, upright=a.upright)
    elif a.dir:
        os.makedirs(a.out_dir, exist_ok=True)
        for f in sorted(glob.glob(os.path.join(a.dir, "*"))):
            if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                stem = os.path.splitext(os.path.basename(f))[0].replace(" ", "_")
                cutout(f, os.path.join(a.out_dir, f"cut_{stem}.png"), sess, a.margin, upright=a.upright)
    else:
        ap.error("pass --src FILE or --dir FOLDER")

if __name__ == "__main__":
    main()
