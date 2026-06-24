# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pillow>=10.0", "numpy>=1.24"]
# ///
"""
concept_mograph.py — concept „MOGRAPH" (kinetic motion-graphics cu DECUPAJE de produs)
pentru generatorul de video-ad-uri short-form al magazinului de oferte „Ofertele Zilei"
(ofertelezilei.ro) și ale celorlalte magazine de deals ARONA.

DE CE ALT CONCEPT: stilurile existente (classic / kinetic / bento) arată UN card de
produs static centrat, cu zoom + tranziție — adică un SLIDESHOW (card-după-card). Acest
concept este STRUCTURAL DIFERIT: o reclamă motion-graphics ca-n After-Effects, în care
TOTUL se mișcă continuu și se SUPRAPUNE.

CUM SCAPĂ DE SENZAȚIA DE SLIDESHOW:
  • Fundal viu care NU stă NICIODATĂ pe loc: gradient care plutește/se rotește lent +
    particule (bokeh) care driftează + un „light sweep" diagonal care trece peste cadru.
  • Produsele = DECUPAJE rembg (PNG transparente, fără fundal) care ZBOARĂ / SE
    SCALEAZĂ / SE ROTESC în și din cadru, cu MOTION BLUR (copii-fantomă „trailing" în
    spate, pe direcția de mișcare). NU un card centrat, ci obiecte care swoosh-uiesc.
  • MAI MULTE produse pe ecran simultan, suprapuse, cu „handoff" pe mișcare: un produs
    iese în swoosh în timp ce următorul intră — niciodată „un card static centrat".
  • TIPOGRAFIE KINETICĂ mare: cuvintele și prețul intră pe beat (scale-pop / slide), iar
    prețul „se trântește" de la vechi → nou. Nu etichete statice.
  • BEAT-SYNCED pe pv.make_music_deals (124 BPM → un beat la 60/124 ≈ 0.484s). Intrările,
    tăieturile și pop-urile cad pe beat.

IMPLEMENTARE (control total pe mișcare): randăm O SECVENȚĂ de cadre PIL full-frame
(~30fps), câte unul per frame video, cu pozițiile / scale / rotație / blur calculate per
timp, apoi le codăm cu `ffmpeg -framerate 30 -i frame_%05d.png`. Așa fiecare element are
mișcarea lui coregrafiată + motion blur real (compunere de copii-fantomă) — opusul unor
slide-uri discrete. Audio = pv.make_music_deals (același bed energic ca la deals).

Copy RO (hook / label per produs / CTA) = Gemini (pv.direct_deals), corect gramatical,
fără emoji/URL. Prețurile vin DOAR din storefront/manifest — NICIODATĂ inventate; afișăm
preț vechi tăiat / preț nou / „-50%".

Usage (CLI standard, apelabil uniform de tool-ul principal):
  uv run --with pillow --with numpy --with requests concept_mograph.py \
     --storefront ofertelezilei.ro --brand "Ofertele Zilei" --fmt 9:16 --n 5
  # sau dintr-un manifest JSON [{title,price,old,pct,img}, ...]
  uv run --with pillow --with numpy --with requests concept_mograph.py \
     --manifest ofer_manifest.json --brand "Ofertele Zilei" --fmt 9:16

Numele fișierului de ieșire = {brandslug}_MOGRAPH_{fmt_with_x}.mp4
(ex. OferteleZilei_MOGRAPH_9x16.mp4).
"""
from __future__ import annotations
import argparse, glob, json, math, os, re, shutil, subprocess, sys, tempfile, urllib.request
from pathlib import Path

# reutilizăm helperele din pmax_video (Gemini copy, muzică deals, trim, font, hex, cutout…)
PV_DIR = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/pmax-video/scripts"
sys.path.insert(0, PV_DIR)
import pmax_video as pv  # noqa: E402

from PIL import Image, ImageDraw, ImageFont, ImageFilter  # noqa: E402
import numpy as np  # noqa: E402

FONT = pv.FONT
FPS = 30
BEAT = 60.0 / 124.0  # 0.4839 s — muzica e la 124 BPM (pv.make_music_deals)

# rembg batch (model încărcat O DATĂ pe un --dir) — mult mai ieftin decât per-imagine
RV = str(Path(PV_DIR) / ".rembg" / "bin" / "python")
CUTPY = str(Path(PV_DIR).parents[1] / "ad-banners" / "scripts" / "cutout.py")

# Praguri de calitate a decupajului (din _score_cutout):
#  GOOD  = minim acceptabil ca să PĂSTRĂM produsul în selecție (altfel îl aruncăm).
#  CRISP = peste asta = decupaj curat → îl lăsăm să ZBOARE (cutout fără fundal).
#          sub CRISP (dar peste GOOD) = soft → îl prezentăm ca TILE încadrat (nu halo soft).
GOOD = 0.75
CRISP = 1.05


# ════════════════════════════ helpers de desen ════════════════════════════
def _font(px):
    return ImageFont.truetype(FONT, max(8, int(px)))


def _lei(p):
    return pv._lei(p)


def _text_centered(d, cx, y, text, font, fill="white", stroke=4, stroke_fill=(0, 0, 0, 235)):
    w = d.textlength(text, font=font)
    d.text((cx - w / 2, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return w


def _ease_out(u):
    """ease-out cubic: pleacă rapid, frânează (pentru swoosh care „aterizează")."""
    u = max(0.0, min(1.0, u))
    return 1 - (1 - u) ** 3


def _ease_in(u):
    u = max(0.0, min(1.0, u))
    return u ** 3


def _smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def _star_burst(size, pct_text, fill=(232, 28, 32, 255)):
    """Ștampilă-stea roșie cu „-50%". RGBA, transparentă în jur."""
    s = int(size)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = s / 2
    pts = []
    spikes = 14
    rO, rI = s * 0.48, s * 0.36
    for i in range(spikes * 2):
        ang = math.pi * i / spikes - math.pi / 2
        r = rO if i % 2 == 0 else rI
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    d.polygon(pts, fill=fill, outline=(255, 255, 255, 255))
    d.ellipse([cx - rI * 0.92, cy - rI * 0.92, cx + rI * 0.92, cy + rI * 0.92],
              outline=(255, 255, 255, 230), width=max(2, int(s * 0.012)))
    f = _font(s * (0.30 if len(pct_text) <= 4 else 0.22))
    bbox = d.textbbox((0, 0), pct_text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), pct_text, font=f, fill="white",
           stroke_width=max(2, int(s * 0.02)), stroke_fill=(120, 0, 0, 230))
    return img


# ════════════════════════ surse: produse + DECUPAJE ════════════════════════
def _dl(u, fn):
    try:
        req = urllib.request.Request(u.split("?")[0], headers={"User-Agent": "Mozilla/5.0"})
        b = urllib.request.urlopen(req, timeout=25).read()
        if len(b) < 8000:
            return None
        open(fn, "wb").write(b)
        return fn
    except Exception:
        return None


def fetch_products_multi(domain, n, outdir, cand=2):
    """Ca pv.fetch_storefront, dar reține și până la `cand` poze CANDIDATE per produs
    (nu doar featured), ca să putem alege poza care se DECUPEAZĂ cel mai curat."""
    dom = domain.replace("https://", "").replace("http://", "").strip("/")
    url = f"https://{dom}/products.json?limit=250"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read())
    Path(outdir).mkdir(parents=True, exist_ok=True)
    out = []
    for x in data.get("products", []):
        imgs = [i.get("src") for i in x.get("images", []) if i.get("src")]
        if not imgs:
            continue
        v = (x.get("variants") or [{}])[0]
        p, c = v.get("price"), v.get("compare_at_price")
        old = pct = None
        try:
            if c and float(c) > float(p):
                old = float(c); pct = round((1 - float(p) / float(c)) * 100)
        except Exception:
            pass
        cand_paths = []
        for ci, u in enumerate(imgs[:cand]):
            fn = os.path.join(outdir, f"p{len(out):02d}_{ci}.jpg")
            if _dl(u, fn):
                cand_paths.append(fn)
        if not cand_paths:
            continue
        out.append({"title": (x.get("title") or "").strip(),
                    "price": float(p) if p else None, "old": old, "pct": pct,
                    "img": cand_paths[0], "candidates": cand_paths, "handle": x.get("handle")})
        if len(out) >= n:
            break
    return out


def _largest_blob_frac(opaque):
    """Fracția din pixelii opaci care aparțin celei mai mari componente conexe.
    ~1 = un singur obiect (packshot); mic = colaj/infografic cu mai multe poze.
    Lucrăm pe o versiune mică (rapid, fără scipy)."""
    from collections import deque
    H, W = opaque.shape
    scale = max(1, int(max(H, W) / 160))
    small = opaque[::scale, ::scale]
    h, w = small.shape
    seen = np.zeros_like(small, dtype=bool)
    best = 0
    total = int(small.sum())
    if total == 0:
        return 0.0
    for sy in range(h):
        for sx in range(w):
            if small[sy, sx] and not seen[sy, sx]:
                cnt = 0
                dq = deque([(sy, sx)])
                seen[sy, sx] = True
                while dq:
                    y, x = dq.popleft()
                    cnt += 1
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w and small[ny, nx] and not seen[ny, nx]:
                            seen[ny, nx] = True
                            dq.append((ny, nx))
                best = max(best, cnt)
    return best / total


def _score_cutout(cut_png, src_jpg):
    """Cât de curat e un decupaj de produs UNIC (packshot) vs colaj/infografic/lifestyle.
    Mai mare = mai bun. Penalizează: alpha hazy, alpha care atinge marginile (colaj umple
    cadrul), multe componente conexe (mai multe poze), muchii de text dese în sursă."""
    try:
        im = Image.open(cut_png).convert("RGBA")
    except Exception:
        return -9
    a = np.asarray(im)
    alpha = a[:, :, 3].astype(float) / 255.0
    cov = float(alpha.mean())
    if cov < 0.05 or cov > 0.92:
        return -5
    opaque = alpha > 0.85
    op = float(opaque.mean())
    # hazy = alpha intermediar; un matte PROST (sticlă translucidă, mână pe fundal alb)
    # are MULT alpha intermediar și o margine moale, lată → penalizare mare.
    hazy = float(((alpha > 0.05) & (alpha < 0.85)).mean())
    # raportul hazy/opac: pe un packshot curat marginea e subțire (hazy ≪ opac)
    haze_ratio = hazy / max(op, 1e-3)
    b = 2
    border = np.concatenate([alpha[:b].ravel(), alpha[-b:].ravel(),
                             alpha[:, :b].ravel(), alpha[:, -b:].ravel()])
    edgetouch = float((border > 0.5).mean())
    big = _largest_blob_frac(opaque)
    edge = 0.0
    white_corners = 0.0
    try:
        src = Image.open(src_jpg).convert("RGB")
        g = np.asarray(src.convert("L")).astype(float)
        edge = float((np.abs(np.diff(g, axis=1)) > 40).mean())  # densitate muchii (text/infografic)
        # packshot curat = colțuri aproape ALBE și uniforme; infografic = colțuri colorate
        sa = np.asarray(src).astype(int)
        sh, sw = sa.shape[:2]
        k = max(8, sh // 8)
        corners = np.concatenate([sa[:k, :k].reshape(-1, 3), sa[:k, -k:].reshape(-1, 3),
                                  sa[-k:, :k].reshape(-1, 3), sa[-k:, -k:].reshape(-1, 3)])
        white_corners = float((corners.min(1) > 200).mean())
    except Exception:
        pass
    # Un decupaj BUN are fundal ELIMINAT → cov bine sub 1. Dacă cov e mare (infografic/
    # poster pe fundal închis pe care rembg l-a păstrat întreg), penalizăm puternic.
    over_cov = max(0.0, cov - 0.62)
    # `edge` penalizat TARE (infograficele cu text pică), bonus pt colțuri albe (packshot)
    return (0.8 * op / max(cov, 1e-3)) - 1.2 * hazy - 0.9 * haze_ratio \
        - 1.6 * edgetouch + 0.7 * big - 2.4 * edge + 0.5 * white_corners - 3.0 * over_cov


def _crop_caption_strip(src_jpg):
    """Multe poze de deals au o BANDĂ de text/caption jos (sau sus). Dacă banda de jos
    are aspect de „bară de text" (uniformă, lată), o tăiem ÎNAINTE de decupaj, ca rembg
    să nu o includă. Întoarce calea (eventual cropată)."""
    try:
        im = Image.open(src_jpg).convert("RGB")
    except Exception:
        return src_jpg
    arr = np.asarray(im).astype(int)
    H, W = arr.shape[:2]
    band = int(H * 0.16)
    bottom = arr[-band:]
    # bandă „caption" = rânduri cu varianță mică pe verticală dar text (muchii orizontale)
    row_mean = bottom.mean((1, 2))
    flatish = (row_mean.std() < 18)
    if flatish and band < H * 0.4:
        out = src_jpg.replace(".jpg", "_c.jpg")
        im.crop((0, 0, W, H - band)).save(out, "JPEG", quality=92)
        return out
    return src_jpg


# Strategie de modele rembg: birefnet-general (margini foarte curate, incl. sticlă/translucid;
# model mare ~970MB) ca PASUL 1 pe toate; isnet-general-use (rapid) ca a doua opinie DOAR pe
# produsele unde birefnet n-a dat decupaj CRISP. Vezi cutout_best.


def _batch_cut(src_dir, out_dir, model):
    """Decupează tot directorul src_dir cu un model, într-o singură sesiune (model încărcat
    o dată). Întoarce True dacă a produs ceva."""
    od = Path(out_dir)
    od.mkdir(parents=True, exist_ok=True)
    if Path(RV).exists() and Path(CUTPY).exists():
        subprocess.run([RV, CUTPY, "--dir", str(src_dir), "--out-dir", str(od),
                        "--model", model], capture_output=True, text=True)
        return any(od.glob("cut_*.png"))
    return False


def cutout_best(products, workdir):
    """Pentru fiecare produs alege poza-candidat care dă decupajul cel mai curat.
    EFICIENT: isnet-general-use (rapid) pe TOATE candidatele → cea mai bună poză per produs.
    OPȚIONAL (MOGRAPH_BIREFNET=1): a doua opinie birefnet-general pe câștigători (margini mai
    curate pe sticlă, dar lent pe CPU — implicit oprit). Setează prod['cut'], prod['cut_src'],
    prod['cut_score'] și prod['soft']=True dacă scorul e sub CRISP → produsul se prezintă ca
    TILE încadrat (poză într-un card cu chenar + umbră), NU ca decupaj zburător cu halo soft."""
    src_dir = Path(workdir) / "cut_src"
    src_dir.mkdir(parents=True, exist_ok=True)
    index = []  # (prod_i, cand_i, src_path_in_src_dir)
    for pi, p in enumerate(products):
        for ci, cpath in enumerate(p.get("candidates", [p["img"]])):
            cropped = _crop_caption_strip(cpath)
            dst = src_dir / f"p{pi:02d}_{ci}.jpg"
            try:
                im = Image.open(cropped).convert("RGB")
                # DOWNSCALE înainte de matting: rembg (mai ales birefnet) e foarte lent pe
                # poze full-res; decupajul oricum se afișează la ~850px. Cap la 1100px.
                if max(im.size) > 1100:
                    im.thumbnail((1100, 1100), Image.LANCZOS)
                im.save(dst, "JPEG", quality=92)
                index.append((pi, ci, str(dst)))
            except Exception:
                pass
    from collections import defaultdict

    def score_dir(od, best, only_srcs=None):
        for pi, ci, src in index:
            if only_srcs is not None and src not in only_srcs:
                continue
            cut = Path(od) / f"cut_{Path(src).stem}.png"
            if cut.exists():
                s = _score_cutout(str(cut), src)
                if s > best[pi][0]:
                    best[pi] = (s, str(cut), src)

    # PASUL 1 (rapid): isnet pe TOATE candidatele → cea mai bună poză per produs
    best = defaultdict(lambda: (-99, None, None))
    d_isnet = Path(workdir) / "cut_isnet"
    if _batch_cut(src_dir, d_isnet, "isnet-general-use"):
        score_dir(d_isnet, best)
    if all(best[pi][1] is None for pi in range(len(products))):  # nimic → floodfill
        ff = Path(workdir) / "cut_ff"; ff.mkdir(parents=True, exist_ok=True)
        for pi, ci, src in index:
            pv._cutout_floodfill(src, str(ff / f"cut_{Path(src).stem}.png"))
        score_dir(ff, best)

    # PASUL 2 (OPȚIONAL, calitate): birefnet-general pe poza câștigătoare per produs — margini
    # mai curate pe sticlă/translucid, dar modelul (973MB, CPU) e LENT și pe multe mașini nu
    # merită. Implicit OPRIT; activează cu MOGRAPH_BIREFNET=1 dacă ai GPU/CPU rapid. Produsele
    # rămase „soft" oricum se prezintă ca TILE încadrat (nu halo murdar), deci isnet+tile e
    # suficient ca default. Păstrăm rezultatul birefnet doar dacă scorează mai bine.
    if os.environ.get("MOGRAPH_BIREFNET") == "1":
        winners = {best[pi][2] for pi in range(len(products)) if best[pi][2]}
        if winners:
            bsrc = Path(workdir) / "cut_birefnet_src"; bsrc.mkdir(parents=True, exist_ok=True)
            for f in src_dir.glob("p*.jpg"):
                if str(f) in winners:
                    shutil.copy(f, bsrc / f.name)
            d_bire = Path(workdir) / "cut_birefnet"
            if _batch_cut(bsrc, d_bire, "birefnet-general"):
                score_dir(d_bire, best, only_srcs=winners)

    for pi, p in enumerate(products):
        sc, cut, src = best.get(pi, (-99, None, None))
        if cut is None:  # ultim fallback: decupaj direct pe featured
            cut = str(Path(workdir) / f"forced_{pi}.png")
            pv.cutout_product(p["img"], cut)
            sc = _score_cutout(cut, p["img"]); src = p["img"]
        p["cut"] = cut
        p["cut_src"] = src or p["img"]   # sursa care a dat cel mai bun decupaj (pt tile)
        p["cut_score"] = round(float(sc), 3)
        # marcăm produsele cu decupaj SOFT → vor fi prezentate ca TILE încadrat, nu zburător
        p["soft"] = float(sc) < CRISP
    return products


# ════════════════════════ sprite-uri de produs (decupaj prep) ════════════════════════
def _prep_cut(cut_path, target_h):
    """Încarcă decupajul, trim la bbox, redimensionează la înălțimea țintă (cu cap pe lățime),
    adaugă un contur alb subtil + umbră proprie → „pop" pe orice fundal. Întoarce RGBA."""
    im = Image.open(cut_path).convert("RGBA")
    bb = im.getbbox()
    if bb:
        im = im.crop(bb)
    if im.height < 1:
        return im
    sc = target_h / im.height
    nw, nh = max(1, int(im.width * sc)), max(1, int(im.height * sc))
    # cap pe lățime (produse foarte late nu trebuie să umple tot ecranul)
    if nw > target_h * 1.45:
        sc2 = (target_h * 1.45) / nw
        nw, nh = max(1, int(nw * sc2)), max(1, int(nh * sc2))
    im = im.resize((nw, nh), Image.LANCZOS)
    # contur alb subtil din alpha dilatat
    alpha = im.split()[3]
    halo = Image.new("RGBA", (im.width + 16, im.height + 16), (0, 0, 0, 0))
    am = alpha.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(2))
    white = Image.new("RGBA", am.size, (255, 255, 255, 255))
    halo.paste(white, (8, 8), am)
    halo.alpha_composite(im, (8, 8))
    return halo


def _prep_tile(src_path, target_h, accent):
    """Pentru produse cu decupaj SOFT (sticlă translucidă, lifestyle, infografic): NU zburăm
    un decupaj cu halo murdar — punem poza ORIGINALĂ (trim-uită) într-un CARD rotunjit alb,
    cu chenar accent + umbră. Arată INTENȚIONAT (ca o poză de produs încadrată), nu rupt.
    Întoarce un sprite RGBA gata de animat (slide/scale ca orice alt element)."""
    src = pv._trim_product(src_path).convert("RGBA")  # taie marginile uniforme dacă există
    # umple cardul: poza ocupă interiorul, păstrând raportul
    inner_h = int(target_h * 0.86)
    sc = inner_h / src.height
    iw = max(1, int(src.width * sc)); ih = max(1, int(src.height * sc))
    if iw > target_h * 1.5:
        sc2 = (target_h * 1.5) / iw
        iw = max(1, int(iw * sc2)); ih = max(1, int(ih * sc2))
    src = src.resize((iw, ih), Image.LANCZOS).convert("RGB")
    pad = max(10, int(target_h * 0.045))
    cw, ch = iw + pad * 2, ih + pad * 2
    rad = int(min(cw, ch) * 0.075)
    ac = pv._hex(accent)
    # card alb cu colțuri rotunjite + chenar accent
    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([0, 0, cw - 1, ch - 1], radius=rad, fill=(255, 255, 255, 255),
                         outline=(ac[0], ac[1], ac[2], 255), width=max(5, int(target_h * 0.018)))
    # poza rotunjită în interior (mască)
    mask = Image.new("L", (iw, ih), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, iw - 1, ih - 1], radius=int(rad * 0.7), fill=255)
    card.paste(src, (pad, pad), mask)
    # umbră proprie sub card (sprite final puțin mai mare)
    sprite = Image.new("RGBA", (cw + 40, ch + 48), (0, 0, 0, 0))
    sh = Image.new("RGBA", sprite.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([24, 30, 24 + cw, 30 + ch], radius=rad, fill=(0, 0, 0, 150))
    sprite = Image.alpha_composite(sprite, sh.filter(ImageFilter.GaussianBlur(20)))
    sprite.alpha_composite(card, (20, 18))
    return sprite


# ════════════════════════ fundal animat (NU stă pe loc) ════════════════════════
class Background:
    """Fundal motion-graphics continuu: gradient care plutește + particule bokeh care
    driftează + un light-sweep diagonal. Optimizat: câmpurile numpy se calculează la
    REZOLUȚIE MICĂ (sw×sh) și se upscale-uiesc (bilinear) la W×H — soft, perfect pt bokeh
    și gradient, dar de ~16× mai ieftin. Glow-ul radial e PRE-CALCULAT ca textură mare și
    doar se decupează translatat per frame; bokeh-ul e desenat pe canvas-ul mic."""

    def __init__(self, W, H, accent):
        self.W, self.H = W, H
        self.ac = pv._hex(accent)[:3]
        self.div = 4  # randăm fundalul la 1/4 din rezoluție (270x480) → upscale
        self.sw, self.sh = W // self.div, H // self.div
        sw, sh = self.sw, self.sh
        rng = np.random.default_rng(7)
        self.npart = 40
        self.px = rng.uniform(0, sw, self.npart)
        self.py = rng.uniform(0, sh, self.npart)
        self.pr = rng.uniform(sw * 0.012, sw * 0.07, self.npart)
        self.pv_ = rng.uniform(-3.0, 3.0, (self.npart, 2))  # px(mic) / s drift
        self.pa = rng.uniform(45, 130, self.npart)
        yy, xx = np.ogrid[:sh, :sw]
        self.xx = xx.astype(np.float32)
        self.yy = yy.astype(np.float32)
        self.diag = (self.xx / sw + self.yy / sh) / 2.0  # 0..1 pe diagonală (precalc)

    def frame(self, t, beat_pulse=0.0):
        sw, sh, ac, div = self.sw, self.sh, self.ac, self.div
        # gradient radial care PLUTEȘTE: centrul se rotește lent pe o elipsă (la rez mică)
        cx = sw * (0.5 + 0.16 * math.sin(0.5 * t))
        cy = sh * (0.42 + 0.10 * math.cos(0.38 * t))
        rad = max(sw, sh) * (0.80 + 0.05 * math.sin(0.9 * t))
        dist = np.sqrt((self.xx - cx) ** 2 + (self.yy - cy) ** 2) / rad
        glow = np.clip(1 - dist, 0, 1) ** 1.5
        base = 0.13 + 0.16 * beat_pulse
        # light sweep diagonal care TRAVERSEAZĂ cadrul
        sweep_pos = ((t * 0.33) % 1.6) - 0.3
        sweep = np.exp(-((self.diag - sweep_pos) ** 2) / (2 * 0.045 ** 2)) * 42
        arr = np.empty((sh, sw, 3), np.float32)
        for k in range(3):
            arr[:, :, k] = np.clip(ac[k] * (0.08 + base * glow) + 5 + sweep, 0, 255)
        small = Image.fromarray(arr.astype(np.uint8), "RGB")
        # particule bokeh care driftează (pe canvas mic, blur ieftin)
        bok = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        bd = ImageDraw.Draw(bok)
        col0 = (min(255, ac[0] + 80), min(255, ac[1] + 80), min(255, ac[2] + 90))
        for i in range(self.npart):
            x = (self.px[i] + self.pv_[i, 0] * t) % (sw + 30) - 15
            y = (self.py[i] + self.pv_[i, 1] * t) % (sh + 30) - 15
            a = int(self.pa[i] * (0.5 + 0.5 * math.sin(0.7 * t + i)))
            r = self.pr[i]
            bd.ellipse([x - r, y - r, x + r, y + r], fill=col0 + (max(0, a),))
        bok = bok.filter(ImageFilter.GaussianBlur(2))
        small = small.convert("RGBA")
        small.alpha_composite(bok)
        # upscale bilinear la rezoluția finală (soft = bun pt fundal)
        return small.resize((self.W, self.H), Image.BILINEAR)


# ════════════════════════ blit cu motion-blur (trailing) ════════════════════════
# Cache de sprite-uri transformate: redimensionarea/rotația LANCZOS pe decupaje mari e
# scumpă și se repetă (scale/rot variază lent). Cuantizăm scale & rot și reutilizăm.
_XFORM_CACHE = {}


def _xform(sprite, scale, rot):
    """Întoarce sprite-ul redimensionat (+ rotit), cu rezultat memoizat pe (scale,rot)
    cuantizate. Scade dramatic costul per-frame față de un resize LANCZOS de fiecare dată."""
    sq = round(scale, 2)
    rq = round(rot, 0)
    key = (id(sprite), sq, rq)
    cached = _XFORM_CACHE.get(key)
    if cached is not None:
        return cached
    nw = max(1, int(sprite.width * sq))
    nh = max(1, int(sprite.height * sq))
    s = sprite.resize((nw, nh), Image.LANCZOS)
    if abs(rq) > 0.5:
        s = s.rotate(rq, resample=Image.BICUBIC, expand=True)
    if len(_XFORM_CACHE) > 4000:
        _XFORM_CACHE.clear()
    _XFORM_CACHE[key] = s
    return s


def _paste_rot_scale(canvas, sprite, cx, cy, scale, rot=0.0, alpha_mul=1.0):
    """Lipește un sprite RGBA centrat pe (cx,cy) cu scale + rotație + alpha global."""
    if scale <= 0.01 or alpha_mul <= 0.01:
        return
    s = _xform(sprite, scale, rot)
    if alpha_mul < 0.985:
        al = s.split()[3].point(lambda v: int(v * alpha_mul))
        s = s.copy()
        s.putalpha(al)
    canvas.alpha_composite(s, (int(cx - s.width / 2), int(cy - s.height / 2)))


def _blit_motion(canvas, sprite, x0, y0, x1, y1, scale, rot=0.0, trails=4, alpha=1.0):
    """Lipește sprite-ul la (x1,y1) + COPII-FANTOMĂ pe segmentul (x0,y0)->(x1,y1) cu alpha
    descrescător = motion blur direcțional. Mai mult delta = mai multe fantome vizibile."""
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    ntr = trails if dist > 12 else 0
    for k in range(ntr, 0, -1):
        f = k / (ntr + 1)
        gx = x1 - dx * f
        gy = y1 - dy * f
        _paste_rot_scale(canvas, sprite, gx, gy, scale, rot, alpha * 0.16 * (1 - f))
    _paste_rot_scale(canvas, sprite, x1, y1, scale, rot, alpha)


# ════════════════════════ overlay-uri de text (kinetic) ════════════════════════
def _wordmark(brand, W, H, accent):
    """Plăcuță wordmark mică sus (brand) — prezentă subtil tot timpul."""
    ov = Image.new("RGBA", (int(W * 0.62), int(H * 0.052)), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    ac = pv._hex(accent)
    d.rounded_rectangle([0, 0, ov.width - 1, ov.height - 1], radius=int(ov.height * 0.5),
                        fill=(0, 0, 0, 150), outline=(ac[0], ac[1], ac[2], 255), width=3)
    f = _font(ov.height * 0.5)
    _text_centered(d, ov.width / 2, ov.height * 0.22, brand.upper(), f, stroke=3)
    return ov


def _hook_words(hook, W, H):
    """Cuvintele hook-ului, fiecare ca SPRITE separat (ca să intre pe rând, pe beat)."""
    words = hook.upper().split()
    d0 = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    fs = int(H * 0.085)
    # auto-shrink ca cea mai lată linie posibilă să încapă
    while fs > int(H * 0.05):
        f = _font(fs)
        # grupăm în rânduri de ≤ W*0.9
        lines, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if d0.textlength(t, font=f) <= W * 0.9 or not cur:
                cur = t
            else:
                lines.append(cur); cur = w
        if cur:
            lines.append(cur)
        if max(d0.textlength(l, font=f) for l in lines) <= W * 0.9 and len(lines) <= 3:
            break
        fs -= 3
    f = _font(fs)
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d0.textlength(t, font=f) <= W * 0.9 or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    lh = int(fs * 1.12)
    block_h = lh * len(lines)
    # centrăm blocul de hook pe verticală (ocupă tot ecranul curat, fără produs dedesubt)
    y0 = int(H * 0.30 - block_h * 0.15)
    sprites = []  # (img, cx, cy)  cu cx,cy = poziția finală a cuvântului
    for li, line in enumerate(lines):
        lw = d0.textlength(line, font=f)
        x = (W - lw) / 2
        y = y0 + li * lh
        for w in line.split():
            ww = d0.textlength(w + " ", font=f)
            spr = Image.new("RGBA", (int(d0.textlength(w, font=f)) + fs, int(fs * 1.5)), (0, 0, 0, 0))
            sd = ImageDraw.Draw(spr)
            sd.text((fs * 0.5, 0), w, font=f, fill="white",
                    stroke_width=max(4, fs // 12), stroke_fill=(0, 0, 0, 240))
            sprites.append((spr, x + ww / 2, y + fs * 0.7))
            x += ww
    return sprites


def _price_sprites(prod, W, H, accent):
    """Sprite-uri de preț: (old) preț vechi tăiat, (new) badge preț nou, ambele transparente,
    centrate jos. Întoarce (old_sprite, new_sprite, anchor_y). `by` = TOP-ul badge-ului nou;
    îl ținem sus de marginea de jos ca badge-ul (înalt) să NU iasă din cadru."""
    by = int(H * 0.77)
    old = prod.get("old")
    p = prod.get("price")
    # old
    fo = _font(H * 0.040)
    ot = f"{_lei(old)} lei" if old else ""
    d0 = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    ow = d0.textlength(ot, font=fo) if ot else 0
    old_s = Image.new("RGBA", (int(ow) + 40, int(fo.size * 1.6)), (0, 0, 0, 0))
    if ot:
        od = ImageDraw.Draw(old_s)
        od.text((20, 6), ot, font=fo, fill=(230, 230, 230, 255), stroke_width=3, stroke_fill=(0, 0, 0, 220))
        od.line([14, 6 + fo.size * 0.55, 26 + ow, 6 + fo.size * 0.55], fill=(245, 60, 60, 255),
                width=max(4, int(fo.size / 7)))
    # new badge
    fn = _font(H * 0.090)
    nt = f"{_lei(p)} lei"
    nw = d0.textlength(nt, font=fn)
    pad = int(fn.size * 0.42)
    new_s = Image.new("RGBA", (int(nw + pad * 2) + 12, int(fn.size + pad * 2) + 12), (0, 0, 0, 0))
    nd = ImageDraw.Draw(new_s)
    ac = pv._hex(accent)
    nd.rounded_rectangle([6, 6, new_s.width - 6, new_s.height - 6], radius=int(fn.size * 0.30),
                         fill=(ac[0], ac[1], ac[2], 255), outline=(255, 255, 255, 255),
                         width=max(3, int(W * 0.006)))
    _text_centered(nd, new_s.width / 2, pad + 6, nt, fn, stroke=max(3, fn.size // 18))
    return old_s, new_s, by


def _label_sprite(text, W, H, accent):
    """Banda de label/beneficiu (un singur sprite centrat sus, sub hook-zone)."""
    d0 = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    fs = int(H * 0.050)
    f, lines = pv._fit_font(text, d0, W * 0.86, fs, int(H * 0.032), 2)
    lh = int(f.size * 1.14)
    block_h = lh * len(lines) + int(H * 0.02)
    spr = Image.new("RGBA", (W, block_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(spr)
    y = 0
    maxw = 0
    for ln in lines:
        w = _text_centered(d, W / 2, y, ln, f, stroke=max(3, f.size // 14))
        maxw = max(maxw, w)
        y += lh
    bw = min(W * 0.5, maxw * 0.6)
    d.rounded_rectangle([(W - bw) / 2, y + 4, (W + bw) / 2, y + 4 + int(H * 0.008)],
                        radius=6, fill=pv._hex(accent))
    return spr


def _cta_sprite(cta, W, H, accent):
    spr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(spr)
    f, lines = pv._fit_font(cta, d, W * 0.84, int(H * 0.060), int(H * 0.040), 2)
    lh = int(f.size * 1.16)
    block_h = lh * len(lines)
    btn_y = int(H * 0.46)
    pad = int(H * 0.045)
    ac = pv._hex(accent)
    d.rounded_rectangle([W * 0.07, btn_y - pad, W * 0.93, btn_y + block_h + pad],
                        radius=int(H * 0.04), fill=(ac[0], ac[1], ac[2], 255),
                        outline=(255, 255, 255, 255), width=max(4, int(W * 0.007)))
    yy = btn_y
    for ln in lines:
        _text_centered(d, W / 2, yy, ln, f, stroke=max(3, f.size // 16))
        yy += lh
    return spr, btn_y + block_h / 2


# ════════════════════════ regie: timeline de mișcare ════════════════════════
def render_concept(script, products, out_path, W=1080, H=1920, brand="Ofertele Zilei"):
    accent = script.get("palette", "#FF4500")
    hook = script.get("hook", "Cele mai tari oferte azi")
    cta = script.get("cta", f"Comandă acum la {brand}")
    labels = script["labels"]
    n = len(products)
    tmp = Path(tempfile.mkdtemp())
    frames_dir = tmp / "frames"
    frames_dir.mkdir()

    # ── TIMELINE (beat-synced, 124 BPM) — durata URMEAZĂ conținutul (fără padding mort) ──
    # Hook ~4 beats, fiecare produs ~4 beats (intră cu OVERLAP peste cel anterior pt
    # handoff pe mișcare), CTA ~5 beats. Lungimea per-produs e auto-ajustată ca totalul
    # să cadă în ~12-15s, oricâte produse ar fi (n=4..6).
    HOOK = 4 * BEAT          # ~1.94s — hook-ul se TERMINĂ înainte să intre primul produs
    CTA = 5 * BEAT           # ~2.42s
    target_body = max(12.0, min(15.0, 6.5 + n * 1.4)) - HOOK - CTA  # cât rămâne pt produse
    PROD = max(2.4 * BEAT, target_body / max(1, n) + 1.0 * BEAT)
    OVERLAP = min(1.1 * BEAT, PROD * 0.42)  # cât timp 2 produse coexistă
    prod_start = [HOOK + i * (PROD - OVERLAP) for i in range(n)]
    cta_start = prod_start[-1] + PROD
    DUR = cta_start + CTA
    total_frames = int(DUR * FPS)
    ENTER = 0.42 * BEAT  # durata intrării swoosh a unui produs (folosită și de timeline)
    EXIT = 0.5 * BEAT
    # Momentul de SWAP al TITLULUI: când noul produs a ajuns „la rest" (după ENTER). Titlul
    # produsului i ține de la title_start[i] până la title_start[i+1] (hard-cut, fără overlap).
    title_start = [ps + ENTER for ps in prod_start]
    title_start[0] = HOOK  # primul titlu poate apărea imediat ce începe zona de produse

    # ── sprite-uri pre-randate ──
    bg = Background(W, H, accent)
    wm = _wordmark(brand, W, H, accent)
    hook_words = _hook_words(hook, W, H)
    hook_stamp = _star_burst(int(H * 0.30), "-50%")

    prod_sprites = []
    for i, p in enumerate(products):
        # CRISP → decupaj zburător; SOFT → poză într-un card încadrat (arată intenționat)
        if p.get("soft"):
            cut = _prep_tile(p.get("cut_src", p["img"]), int(H * 0.46), accent)
        else:
            cut = _prep_cut(p["cut"], int(H * 0.44))
        lab = _label_sprite(labels[i], W, H, accent)
        old_s, new_s, by = _price_sprites(p, W, H, accent)
        burst = _star_burst(int(H * 0.15), f"-{p['pct']}%" if p.get("pct") else "REDUS")
        prod_sprites.append(dict(cut=cut, lab=lab, old=old_s, new=new_s, by=by, burst=burst,
                                 pct=p.get("pct"), soft=p.get("soft", False)))

    cta_spr, cta_cy = _cta_sprite(cta, W, H, accent)
    cta_stamp = _star_burst(int(H * 0.22), "-50%")

    # lanes: alternăm latura de intrare (stânga/dreapta/jos) ȘI poziția de rest puțin
    # lateral, ca în timpul OVERLAP-ului produsul care iese și cel care intră să stea pe
    # poziții DIFERITE → se citește „două produse pe ecran", nu unul stivuit central.
    lanes = [
        dict(side="R", cx=0.56, cy=0.45),
        dict(side="L", cx=0.44, cy=0.46),
        dict(side="B", cx=0.55, cy=0.46),
        dict(side="R", cx=0.45, cy=0.45),
        dict(side="L", cx=0.56, cy=0.46),
        dict(side="B", cx=0.44, cy=0.46),
    ]

    def beat_pulse(t):
        # 0..1, vârf pe fiecare beat
        ph = (t % BEAT) / BEAT
        return math.exp(-((ph) ** 2) / (2 * 0.18 ** 2)) + math.exp(-((ph - 1) ** 2) / (2 * 0.18 ** 2))

    # ── randăm fiecare frame ──
    for fi in range(total_frames):
        t = fi / FPS
        bp = beat_pulse(t)
        canvas = bg.frame(t, beat_pulse=bp).copy()

        # ░░ PRODUSE (pot fi mai multe simultan; intră în swoosh, ies în swoosh) ░░
        for i, ps in enumerate(prod_sprites):
            ts = prod_start[i]
            te = ts + PROD
            # fereastra vizibilă: include intrare + (overlap cu următorul) + ieșire
            vis_a = ts - 0.02
            vis_b = te + OVERLAP + 0.05
            if t < vis_a or t > vis_b:
                continue
            lane = lanes[i % len(lanes)]
            rest_x = W * lane["cx"]
            rest_y = H * lane["cy"]
            # punctul de intrare (în afara cadrului, pe latura lane-ului)
            if lane["side"] == "R":
                in_x, in_y = W * 1.4, rest_y
            elif lane["side"] == "L":
                in_x, in_y = -W * 0.4, rest_y
            else:  # B
                in_x, in_y = rest_x, H * 1.45
            # punctul de ieșire (latura opusă, în swoosh)
            out_x, out_y = (-W * 0.5 if lane["side"] != "L" else W * 1.5), rest_y - H * 0.04

            cx = cy = 0.0
            scale = 1.0
            rot = 0.0
            a_mul = 1.0
            prev_x = prev_y = None
            if t < ts + ENTER:
                u = _ease_out((t - ts) / ENTER)
                cx = in_x + (rest_x - in_x) * u
                cy = in_y + (rest_y - in_y) * u
                scale = 0.7 + 0.35 * u   # mic overshoot la intrare
                if u > 0.85:
                    scale = 1.05 - 0.05 * ((u - 0.85) / 0.15)
                rot = (1 - u) * (18 if lane["side"] == "R" else -18)
                # poziție cu un frame înainte → motion blur
                up = _ease_out((t - 1.0 / FPS - ts) / ENTER)
                prev_x = in_x + (rest_x - in_x) * up
                prev_y = in_y + (rest_y - in_y) * up
            elif t > te:  # IEȘIRE în swoosh (overlap cu următorul)
                u = _ease_in((t - te) / EXIT)
                if u >= 1.0:
                    continue
                cx = rest_x + (out_x - rest_x) * u
                cy = rest_y + (out_y - rest_y) * u
                scale = 1.0 - 0.25 * u
                rot = u * (-22 if lane["side"] != "L" else 22)
                a_mul = 1.0 - 0.35 * u
                up = _ease_in((t - 1.0 / FPS - te) / EXIT)
                prev_x = rest_x + (out_x - rest_x) * up
                prev_y = rest_y + (out_y - rest_y) * up
            else:  # REST: float + bob + breathing zoom pe beat
                ph = (t - (ts + ENTER))
                cx = rest_x + 10 * math.sin(1.6 * ph)
                cy = rest_y + 14 * math.sin(2.2 * ph + 1)
                scale = 1.0 + 0.018 * bp + 0.01 * math.sin(2.0 * ph)
                rot = 1.2 * math.sin(1.1 * ph)
                prev_x, prev_y = cx, cy

            # blit cu motion blur direcțional (mai multe fantome la intrare/ieșire)
            trails = 5 if (t < ts + ENTER or t > te) else 1
            _blit_motion(canvas, ps["cut"], prev_x, prev_y, cx, cy, scale, rot,
                         trails=trails, alpha=a_mul)

        # ░░ TITLU + PREȚ — EXACT UN SINGUR set per frame (NU se suprapun la handoff) ░░
        # Alegem produsul „activ" după fereastra titlului [title_a, title_b), unde title_b al
        # produsului i = title_a al produsului i+1 (HARD-CUT). Titlul ȘI prețul aparțin DOAR
        # produsului activ → niciodată 2 titluri sau 2 badge-uri de preț simultan.
        if HOOK <= t < cta_start:
            active = None
            for i in range(n):
                ta = title_start[i]
                tb = title_start[i + 1] if i + 1 < n else cta_start
                if ta <= t < tb:
                    active = i
                    break
            if active is not None:
                ps = prod_sprites[active]
                lane = lanes[active % len(lanes)]
                ta = title_start[active]
                ts = prod_start[active]
                # ── PREȚUL produsului activ (slot fix jos-centru). Old la +0.30*BEAT după
                #    intrarea produsului, new „slam" la +1.0*BEAT. ──
                old_a = ts + 0.30 * BEAT
                new_a = ts + 1.0 * BEAT
                if old_a <= t < new_a:
                    oa = min(1.0, (t - old_a) / (0.25 * BEAT))
                    _paste_rot_scale(canvas, ps["old"], W / 2,
                                     ps["by"] - int(H * 0.075) + ps["old"].height / 2, 1.0, 0.0, oa)
                if t >= new_a:
                    su = (t - new_a) / (0.22 * BEAT)
                    if su < 1.0:
                        sc_pop = 1.25 - 0.25 * _smooth(su)
                        dy = -90 * (1 - _ease_out(su))
                    else:
                        sc_pop = 1.0 + 0.02 * bp
                        dy = 4 * math.sin(20 * (t - new_a - 0.22 * BEAT)) * math.exp(-5 * (t - new_a - 0.22 * BEAT))
                    _paste_rot_scale(canvas, ps["new"], W / 2, ps["by"] + ps["new"].height / 2 + dy,
                                     sc_pop, 0.0, 1.0)
                    bu = (t - new_a) / (0.30 * BEAT)
                    bsc = 1.05 * _ease_out(bu) if bu < 1.0 else 1.0 + 0.05 * bp
                    bx = min(W / 2 + ps["new"].width / 2 + ps["burst"].width * 0.12,
                             W - ps["burst"].width * 0.52)
                    by_burst = ps["by"] - ps["burst"].height * 0.22
                    _paste_rot_scale(canvas, ps["burst"], bx, by_burst,
                                     bsc, -10 + 20 * math.sin(3 * t), 1.0)
                # ── TITLUL produsului activ (pop-in scurt la swap, apoi STATIC) ──
                lt = (t - ta) / (0.34 * BEAT)
                if lt < 1.0:
                    e = _ease_out(lt)
                    off = (1 - e) * (W * 0.42) * (1 if lane["side"] == "R" else -1)
                    sc = 0.82 + 0.18 * e
                    la = min(1.0, lt * 1.8)
                else:
                    off = 0.0
                    sc = 1.0
                    la = 1.0
                _paste_rot_scale(canvas, ps["lab"], W / 2 + off,
                                 int(H * 0.11) + ps["lab"].height // 2, sc, 0.0, la)

        # ░░ HOOK (0..HOOK) — cuvinte care intră pe beat + ștampilă -50% ░░
        # Hook-ul trebuie să DISPARĂ COMPLET până la HOOK, ca primul produs (care intră
        # exact la HOOK) să aibă cadru curat. Fade-out rapid în ultimele 0.35*BEAT.
        fade_start = HOOK - 0.40 * BEAT
        if t < HOOK:
            ha = 1.0 if t < fade_start else max(0.0, 1.0 - (t - fade_start) / (0.40 * BEAT))
            per = (HOOK - 1.4 * BEAT) / max(1, len(hook_words))  # toate cuvintele intră devreme
            for wi, (spr, cx, cy) in enumerate(hook_words):
                wt = wi * per
                if t < wt:
                    continue
                u = min(1.0, (t - wt) / (0.26 * BEAT))
                sc = 0.5 + 0.6 * _ease_out(u)
                if u > 0.8:
                    sc = 1.12 - 0.12 * ((u - 0.8) / 0.2)
                _paste_rot_scale(canvas, spr, cx, cy, sc, 0.0, ha * min(1.0, u * 1.5))
            # ștampila -50% scale-in, sub blocul de hook (zonă curată în timpul hook-ului)
            st = HOOK * 0.42
            if t >= st:
                u = (t - st) / (0.30 * BEAT)
                sc = 1.18 * _ease_out(u) if u < 1.0 else 1.0 + 0.04 * bp
                _paste_rot_scale(canvas, hook_stamp, W / 2, int(H * 0.66), sc,
                                 -8 + 16 * math.sin(2.5 * t), ha)

        # ░░ CTA (cta_start..DUR) ░░
        if t >= cta_start - 0.05:
            ct = t - cta_start
            u = min(1.0, ct / (0.42 * BEAT))
            dy = (1 - _ease_out(u)) * H * 0.16
            ca = min(1.0, ct / (0.25 * BEAT))
            _paste_rot_scale(canvas, cta_spr, W / 2, H / 2 + dy, 1.0, 0.0, ca)
            st = cta_start + 0.28 * BEAT
            if t >= st:
                su = (t - st) / (0.30 * BEAT)
                sc = 1.2 * _ease_out(su) if su < 1.0 else 1.0 + 0.05 * bp
                # ștampila SUB buton (înainte stătea peste rândul 2 al CTA-ului)
                _paste_rot_scale(canvas, cta_stamp, W / 2, int(H * 0.74), sc,
                                 -8 + 16 * math.sin(2.6 * t), 1.0)

        # ░░ wordmark mereu vizibil (sus stânga) ░░
        if t > HOOK - 0.2:
            canvas.alpha_composite(wm, (int(W * 0.04), int(H * 0.035)))

        canvas.convert("RGB").save(frames_dir / f"f{fi:05d}.jpg", "JPEG", quality=92)

    # ── muzică + encode ──
    music = pv.make_music_deals(DUR, str(tmp / "m.wav"))
    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-framerate", str(FPS), "-i", str(frames_dir / "f%05d.jpg"),
           "-i", music, "-map", "0:v", "-map", "1:a",
           "-af", f"afade=t=out:st={max(DUR - 0.6, 0):.2f}:d=0.6", "-shortest",
           "-r", str(FPS), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    nframes = len(list(frames_dir.glob("f*.jpg")))
    shutil.rmtree(tmp, ignore_errors=True)
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 50_000:
        sys.stderr.write(f"FFMPEG FAIL (rc={r.returncode}, frames={nframes}, "
                         f"size={out.stat().st_size if out.exists() else 0}):\n" + r.stderr[-2500:] + "\n")
        sys.exit(1)
    return out, DUR


def main():
    ap = argparse.ArgumentParser(
        description="Concept MOGRAPH — motion-graphics cu decupaje de produs (deals ARONA)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--storefront", help="domeniu storefront Shopify public, ex ofertelezilei.ro")
    src.add_argument("--manifest", help="JSON alternativ: listă de {title,price,old,pct,img}")
    ap.add_argument("--brand", default="Ofertele Zilei")
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop/pmax-video"),
                    help="director de ieșire (numele fișierului e standardizat)")
    ap.add_argument("--fmt", default="9:16", choices=list(pv.FMT.keys()))
    ap.add_argument("--n", type=int, default=5, help="câte produse (recomandat 4-5)")
    ap.add_argument("--pick", default="", help="indici produse din manifest, ex '1,7,11,3'")
    ap.add_argument("--offer", default="-50% la TOT")
    a = ap.parse_args()

    W, H = pv.FMT[a.fmt]
    out_dir = Path(a.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    brandslug = re.sub(r"[^A-Za-z0-9]", "", a.brand) or "Brand"
    out_path = out_dir / f"{brandslug}_MOGRAPH_{a.fmt.replace(':', 'x')}.mp4"
    work = out_dir / f"_mograph_{brandslug}"
    work.mkdir(parents=True, exist_ok=True)

    want = min(a.n, 6)
    # ── sursă produse ──
    if a.storefront:
        # OVER-FETCH AGRESIV: tragem MULT mai multe produse decât ne trebuie (catalogul de
        # deals e plin de poze lifestyle/infografic), ca să putem alege doar pe cele cu
        # decupaj CURAT și să le aruncăm pe restul.
        over = max(want + 6, 11)
        print(f"● {a.brand}: trag produse din {a.storefront} (cer {over}, păstrez {want} cu decupaj curat)…")
        products = fetch_products_multi(a.storefront, over, str(work / "src"))
        if not products:
            sys.stderr.write(f"Niciun produs cu poză din {a.storefront}\n"); sys.exit(1)
    else:
        allp = json.load(open(a.manifest))
        if a.pick:
            products = [allp[int(x)] for x in a.pick.split(",")]
        else:
            products = allp
        for p in products:
            if p.get("img"):
                p["img"] = str(Path(p["img"]).expanduser().resolve())
            p.setdefault("candidates", [p["img"]])

    # dacă manifestul aduce deja decupaje (cut), nu re-decupăm; altfel rembg batch + scor
    pre_cut = bool(products) and all(p.get("cut") for p in products)
    if pre_cut:
        for p in products:
            p["cut"] = str(Path(p["cut"]).expanduser().resolve())
            p.setdefault("cut_score", 1.0)
    else:
        print(f"● decupez produsele (rembg, batch — aleg poza care iese cel mai curat)…")
        products = cutout_best(products, str(work))

    # păstrează doar produse cu decupaj DECENT (≥ GOOD), apoi cele mai bune `want` pe scor.
    good = [p for p in products if p.get("cut_score", 1.0) >= GOOD]
    if len(good) < want:  # nu sunt destule curate → completăm cu cele mai bune disponibile
        good = sorted(products, key=lambda p: -p.get("cut_score", 1.0))
    good = sorted(good, key=lambda p: -p.get("cut_score", 1.0))[:want]
    products = good if good else products[:want]
    # recalculează flag-ul soft (manifest pre-cut nu trece prin cutout_best)
    for p in products:
        p.setdefault("soft", p.get("cut_score", 1.0) < CRISP)
    for i, p in enumerate(products):
        print(f"    [{i}] {int(p['price']) if p.get('price') else '?'} lei  cut_score={p.get('cut_score')}  {p['title'][:42]}")

    print(f"● {a.brand}: {len(products)} produse [{a.fmt} {W}x{H}] → Gemini scrie copy-ul…")
    script = pv.direct_deals(a.brand, products, a.offer)
    print(f"  hook: „{script.get('hook')}”  CTA: „{script.get('cta')}”  paletă {script.get('palette')}")
    for i, l in enumerate(script["labels"]):
        print(f"    [{i}] „{l}”")

    print(f"● randez cadre full-frame (motion-graphics, ~{FPS}fps)…")
    out, dur = render_concept(script, products, out_path, W=W, H=H, brand=a.brand)
    print(f"✓ {dur:.1f}s [{a.fmt}] → {out}")


if __name__ == "__main__":
    main()
