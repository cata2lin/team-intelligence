# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10", "numpy>=1.26", "scipy>=1.11"]
# ///
"""
label-swap — pune eticheta TA peste eticheta unui produs din poze de mockup/studio.

Detectează patrulaterul REAL al etichetei existente (inclusiv înclinare/perspectivă),
reașază designul pe el (perspective warp) și compune supersamplat (antialiasing),
extinzând ușor marginea ca să acopere muchia aliasată din poza originală.

    uv run label_swap.py --design eticheta.png --photos "poze/*.jpg" --out final --apply
"""
import argparse
import glob
import os
import sys

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

# Windows (consolă cp1252) — nu crăpa pe diacritice
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ---------------------------------------------------------------- designul

def detect_border(img, tol=12):
    """Grosimea chenarului uniform din jurul designului (0 dacă n-are)."""
    a = np.asarray(img).astype(int)
    h, w = a.shape[:2]
    c = a[0, 0]
    n = 0
    while n < min(h, w) // 4:
        ring = np.concatenate([a[n, :], a[h-1-n, :], a[:, n], a[:, w-1-n]])
        if np.abs(ring - c).max() > tol:
            break
        n += 1
    return n


class Design:
    """Designul etichetei + capacitatea de a se re-randa la orice dimensiune."""

    def __init__(self, path, border=None, text_tol=30):
        self.img = Image.open(path).convert("RGB")
        self.W, self.H = self.img.size
        self.border = detect_border(self.img) if border is None else border
        b = self.border
        self.border_rgb = tuple(np.asarray(self.img).astype(int)[0, 0]) if b else None
        self.inner = self.img.crop((b, b, self.W-b, self.H-b))
        self.iw, self.ih = self.inner.size
        ia = np.asarray(self.inner).astype(int)
        self.bg = tuple(int(v) for v in np.median(ia[0], axis=0))     # fundalul etichetei
        lum = ia.mean(axis=2)
        bglum = float(np.median(lum[0]))
        # benzi cu conținut (FIXE, se scalează natural) vs goluri (ÎNTINSIBILE)
        rows = (np.abs(lum - bglum) > text_tol).sum(axis=1)
        segs, cur, kind = [], None, None
        for y in range(self.ih):
            k = "fix" if rows[y] > 0 else "gap"
            if kind is None:
                cur, kind = [y, y], k
            elif k == kind:
                cur[1] = y
            else:
                segs.append((kind, cur[0], cur[1])); cur, kind = [y, y], k
        segs.append((kind, cur[0], cur[1]))
        merged = []                                   # goluri mici între linii = tot bloc de text
        for s in segs:
            if merged and s[0] == "gap" and (s[2]-s[1]+1) < 12 and merged[-1][0] == "fix":
                merged[-1] = ("fix", merged[-1][1], s[2])
            elif merged and s[0] == "fix" and merged[-1][0] == "fix":
                merged[-1] = ("fix", merged[-1][1], s[2])
            else:
                merged.append(s)
        self.segs = merged

    def blocks(self):
        return [(a, b) for k, a, b in self.segs if k == "fix"]

    def render(self, w, h, mode="seam"):
        """Designul la exact w x h. seam = literele la scara lor, golurile absorb restul."""
        t = max(1, int(round(self.border * w / self.W))) if self.border else 0
        Wt, Ht = w - 2*t, h - 2*t
        if mode == "seam":
            s = Wt / self.iw
            fixh = sum(int(round((b-a+1)*s)) for k, a, b in self.segs if k == "fix")
            gtot = sum((b-a+1) for k, a, b in self.segs if k == "gap") or 1
            extra = Ht - fixh
        if mode != "seam" or extra < 0:               # fallback: proporțional, centrat
            s = min(Wt/self.iw, Ht/self.ih)
            nw, nh = max(1, int(self.iw*s)), max(1, int(self.ih*s))
            canvas = Image.new("RGB", (Wt, Ht), self.bg)
            canvas.paste(self.inner.resize((nw, nh), Image.LANCZOS), ((Wt-nw)//2, (Ht-nh)//2))
        else:
            canvas = Image.new("RGB", (Wt, Ht), self.bg)
            y = 0
            for k, a, b in self.segs:
                if k == "fix":
                    nh = int(round((b-a+1)*s))
                    canvas.paste(self.inner.crop((0, a, self.iw, b+1)).resize((Wt, nh), Image.LANCZOS), (0, y))
                    y += nh
                else:
                    y += max(0, int(round(extra * ((b-a+1)/gtot))))
        if not t:
            return canvas
        p = Image.new("RGB", (w, h), self.border_rgb)
        p.paste(canvas, (t, t))
        return p


# ------------------------------------------------- eticheta din fotografie

def label_mask(im, thresh, erode, light=False):
    """Masca etichetei existente = cea mai mare zonă compactă întunecată (sau luminoasă)."""
    a = np.asarray(im).astype(float).mean(axis=2)
    filled = ndimage.binary_fill_holes(a > thresh if light else a < thresh)
    er = None
    for k in (erode, erode//2, 5, 3):                 # dacă eroziunea o mănâncă, slăbește-o
        e = ndimage.binary_erosion(filled, structure=np.ones((k, k)))
        if e.any():
            er = e; break
    if er is None:
        return None
    l, n = ndimage.label(er)
    g = (l == int(np.argmax(ndimage.sum(er, l, range(1, n+1)))) + 1)
    for _ in range(erode - 5):                        # crește la loc, dar numai în interiorul zonei
        g = ndimage.binary_dilation(g, structure=np.ones((3, 3))) & filled
    return g


def _fit(pts):
    v = np.array([p[0] for p in pts], float)
    u = np.array([p[1] for p in pts], float)
    return np.linalg.lstsq(np.vstack([v, np.ones_like(v)]).T, u, rcond=None)[0]


def quad_of(mask, grow=1.5):
    """Cele 4 colțuri ale etichetei: fit pe cele 4 laturi + offset spre exterior cu `grow` px."""
    ys, xs = np.where(mask)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    L, R, T, B = [], [], [], []
    for y in range(int(y0+.1*(y1-y0)), int(y1-.1*(y1-y0))):
        r = np.where(mask[y])[0]
        if len(r) > 10:
            L.append((y, r.min())); R.append((y, r.max()))
    for x in range(int(x0+.1*(x1-x0)), int(x1-.1*(x1-x0))):
        c = np.where(mask[:, x])[0]
        if len(c) > 10:
            T.append((x, c.min())); B.append((x, c.max()))
    if min(len(L), len(T)) < 5:
        return None, None, None
    ml, cl = _fit(L); mr, cr = _fit(R)                # x = m*y + c
    mt, ct = _fit(T); mb, cb = _fit(B)                # y = m*x + c
    res = max(float(np.abs(np.array([p[1] for p in P]) - (m*np.array([p[0] for p in P], float) + c)).max())
              for P, m, c in ((L, ml, cl), (R, mr, cr), (T, mt, ct), (B, mb, cb)))
    cl -= grow*np.hypot(1, ml); cr += grow*np.hypot(1, mr)
    ct -= grow*np.hypot(1, mt); cb += grow*np.hypot(1, mb)

    def inter(mx, cx, my, cy):
        y = (my*cx + cy) / (1 - my*mx)
        return (mx*y + cx, y)
    quad = [inter(ml, cl, mt, ct), inter(mr, cr, mt, ct),
            inter(mr, cr, mb, cb), inter(ml, cl, mb, cb)]
    return quad, float(np.degrees(np.arctan(mt))), res


def _coeffs(dst, src):
    """Coeficienți pt Image.PERSPECTIVE: mapare destinație -> sursă."""
    A, b = [], []
    for (X, Y), (x, y) in zip(dst, src):
        A.append([X, Y, 1, 0, 0, 0, -X*x, -Y*x]); b.append(x)
        A.append([0, 0, 0, X, Y, 1, -X*y, -Y*y]); b.append(y)
    return np.linalg.solve(np.array(A, float), np.array(b, float))


def apply_label(im, design, quad, ss=4, mode="seam"):
    """Compune designul pe patrulater, supersamplat (antialiasing corect pe muchii)."""
    TL, TR, BR, BL = quad
    W = int(round((np.hypot(*np.subtract(TR, TL)) + np.hypot(*np.subtract(BR, BL))) / 2))
    H = int(round((np.hypot(*np.subtract(BL, TL)) + np.hypot(*np.subtract(BR, TR))) / 2))
    px = [p[0] for p in quad]; py = [p[1] for p in quad]
    rx0, ry0 = max(0, int(min(px))-4), max(0, int(min(py))-4)
    rx1, ry1 = min(im.width, int(max(px))+5), min(im.height, int(max(py))+5)
    rw, rh = rx1-rx0, ry1-ry0

    art = design.render(W*ss, H*ss, mode=mode)
    qss = [((x-rx0)*ss, (y-ry0)*ss) for x, y in quad]
    cf = _coeffs(qss, [(0, 0), (W*ss, 0), (W*ss, H*ss), (0, H*ss)])
    warp = art.transform((rw*ss, rh*ss), Image.PERSPECTIVE, cf, Image.BICUBIC, fillcolor=(0, 0, 0))
    am = Image.new("L", (rw*ss, rh*ss), 0)
    ImageDraw.Draw(am).polygon(qss, fill=255)

    a = np.asarray(am).astype(np.float32) / 255.0
    c = np.asarray(warp).astype(np.float32)
    pre = c * a[..., None]                             # premultiplicat: fără scurgeri din afara etichetei

    def box(x):                                        # mediere pe blocuri ss x ss = acoperire reală
        return x.reshape(rh, ss, rw, ss, -1).mean(axis=(1, 3))
    A = box(a[..., None])[..., 0]
    C = box(pre)
    col = np.where(A[..., None] > 1e-6, C / np.maximum(A, 1e-6)[..., None], 0.0)

    reg = np.asarray(im.crop((rx0, ry0, rx1, ry1))).astype(np.float32)
    out = reg * (1 - A[..., None]) + col * A[..., None]
    im.paste(Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8)), (rx0, ry0))
    return im, (W, H), int(((A > 0) & (A < 1)).sum())


# --------------------------------------------------------------------- CLI

def expand(patterns):
    out = []
    for p in patterns:
        if os.path.isdir(p):
            for e in ("jpg", "jpeg", "png", "webp"):
                out += glob.glob(os.path.join(p, f"*.{e}")) + glob.glob(os.path.join(p, f"*.{e.upper()}"))
        else:
            out += glob.glob(p) or ([p] if os.path.exists(p) else [])
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser(description="Pune eticheta ta peste eticheta unui produs din poze.")
    ap.add_argument("--design", required=True, help="PNG/JPG cu designul etichetei")
    ap.add_argument("--photos", nargs="+", required=True, help="fișiere / glob / folder cu pozele produsului")
    ap.add_argument("--out", help="folderul de ieșire (implicit <folderul pozelor>/label-swap)")
    ap.add_argument("--prefix", help="prefix nume fișier ieșire (implicit numele designului)")
    ap.add_argument("--fit", choices=["seam", "contain"], default="seam",
                    help="seam = literele la scara lor + golurile se întind (implicit); contain = scalare proporțională")
    ap.add_argument("--grow", type=float, default=1.5, help="px de extindere a etichetei (acoperă muchia aliasată din original)")
    ap.add_argument("--ss", type=int, default=4, help="factor de supersampling (antialiasing)")
    ap.add_argument("--thresh", type=float, default=70, help="prag luminanță pt eticheta din poză")
    ap.add_argument("--light", action="store_true", help="eticheta din poză e DESCHISĂ la culoare, nu întunecată")
    ap.add_argument("--erode", type=int, default=21, help="eroziune la detecție (elimină muchiile subțiri de sticlă)")
    ap.add_argument("--border", type=int, help="grosime chenar design (implicit: auto)")
    ap.add_argument("--quad", help="colțuri manuale x1,y1,x2,y2,x3,y3,x4,y4 (TL,TR,BR,BL) — sare peste detecție")
    ap.add_argument("--quality", type=int, default=97)
    ap.add_argument("--probe", action="store_true", help="doar raportează ce a detectat (+ overlay), nu scrie rezultatul")
    ap.add_argument("--apply", action="store_true", help="scrie fișierele (fără el = dry-run)")
    args = ap.parse_args()

    d = Design(args.design, border=args.border)
    print(f"design: {os.path.basename(args.design)} {d.W}x{d.H} chenar={d.border}px fundal=rgb{d.bg} "
          f"blocuri={d.blocks()}")

    dpath = os.path.realpath(args.design)
    photos = [p for p in expand(args.photos) if os.path.realpath(p) != dpath]   # nu te lipi pe tine
    if not photos:
        sys.exit("Nicio poză găsită.")
    out = args.out or os.path.join(os.path.dirname(photos[0]) or ".", "label-swap")
    prefix = args.prefix or os.path.splitext(os.path.basename(args.design))[0].split()[0].lower()
    if (args.apply or args.probe):
        os.makedirs(out, exist_ok=True)

    manual = None
    if args.quad:
        v = [float(t) for t in args.quad.replace(";", ",").split(",")]
        manual = [(v[0], v[1]), (v[2], v[3]), (v[4], v[5]), (v[6], v[7])]

    for i, f in enumerate(photos, 1):
        im = Image.open(f).convert("RGB")
        if manual:
            quad, ang, res = manual, 0.0, 0.0
        else:
            m = label_mask(im, args.thresh, args.erode, light=args.light)
            if m is None or m.sum() < 500:
                print(f"  [{i}] {os.path.basename(f)}: eticheta NU a fost găsită (încearcă --thresh/--light/--quad)")
                continue
            quad, ang, res = quad_of(m, grow=args.grow)
            if quad is None:
                print(f"  [{i}] {os.path.basename(f)}: zonă prea mică pt fit pe 4 laturi")
                continue
        if args.probe:
            ov = im.copy()
            ImageDraw.Draw(ov).polygon([tuple(p) for p in quad], outline=(255, 0, 0))
            ov.save(os.path.join(out, f"probe_{i}.jpg"), quality=90)
            print(f"  [{i}] {os.path.basename(f)}: quad={[tuple(round(v) for v in p) for p in quad]} "
                  f"înclinare={ang:+.2f}° abatere_max={res:.1f}px -> probe_{i}.jpg")
            continue
        im, (W, H), aa = apply_label(im, d, quad, ss=args.ss, mode=args.fit)
        name = f"{prefix}_{i}.jpg"
        if args.apply:
            im.save(os.path.join(out, name), quality=args.quality, subsampling=0)
        print(f"  [{i}] {os.path.basename(f)} -> {name}: {W}x{H} înclinare={ang:+.2f}° "
              f"abatere_max={res:.1f}px margini_AA={aa}px{'' if args.apply else '  (dry-run)'}")

    if args.apply:
        print(f"\nScris în: {out}")
    elif not args.probe:
        print("\nDry-run — adaugă --apply ca să scrie fișierele.")


if __name__ == "__main__":
    main()
