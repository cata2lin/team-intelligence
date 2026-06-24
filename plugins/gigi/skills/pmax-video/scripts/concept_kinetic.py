# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pillow>=10.0", "numpy>=1.24"]
# ///
"""
concept_kinetic.py — concept „KINETIC PRICE-DROP" pentru generatorul de video-ad-uri
short-form al magazinului de oferte „Ofertele Zilei" (ofertelezilei.ro).

PROBLEMA cu generatorul curent (pmax_video.render_montage): produce un SLIDESHOW de
carduri de produs — fiecare beat = o poză cu zoompan lent + un text STATIC overlay,
lipite cu concat. Se simte ca un PowerPoint, nu ca o reclamă de reduceri.

ACEST CONCEPT scapă de senzația de slideshow prin TIPOGRAFIE ANIMATĂ + tranziții
dinamice, 100% FFmpeg + PIL (gratis, fără Veo):

  HOOK (0-2.5s): intro animat — cuvinte/elemente POP-in, ștampila „-50%" intră cu
    SCALE + impact/shake, fundal accent care pulsează.
  PER PRODUS (~1.7s): produsul INTRĂ cu whip/slide + ușor motion-blur (slide-in via
    overlay x variabil în timp), apoi PREȚUL CADE — întâi prețul vechi, apoi prețul
    nou e „trântit" (scale-in cu impact) + burst „-50%/REDUS". Produsul mai face un
    bob/zoom subtil. Tăieturi pe beat + tranziție whip ocazională.
  CTA (ultimele ~2.5s): „Comandă acum la Ofertele Zilei" animat.

IMPLEMENTARE animație: pre-randăm MAI MULTE stări PNG cu PIL (RGBA) pentru fiecare
element și le compunem pe ferestre de timp cu overlay enable='between(t,a,b)' și/sau
expresii x/y variabile în timp. Așa fiecare element are propria mișcare — NU o poză
care doar se zoom-ează.

Copy RO (hook / label per produs / CTA) = Gemini (pv.direct_deals), corect gramatical,
fără emoji/URL. Prețurile vin DOAR din storefront/manifest — niciodată inventate.

Usage (CLI standard, apelabil uniform de tool-ul principal):
  # din storefront-ul public Shopify (recomandat)
  uv run --with pillow --with numpy --with requests concept_kinetic.py \
     --storefront ofertelezilei.ro --brand "Ofertele Zilei" --fmt 9:16 --n 6
  # sau dintr-un manifest JSON [{title,price,old,pct,img}, ...]
  uv run --with pillow --with numpy --with requests concept_kinetic.py \
     --manifest ofer_manifest.json --brand "Ofertele Zilei" --fmt 1:1

Numele fișierului de ieșire = {brandslug}_KINETIC_{fmt_with_x}.mp4
(ex. OferteleZilei_KINETIC_9x16.mp4).
"""
from __future__ import annotations
import argparse, json, math, os, re, subprocess, sys, tempfile
from pathlib import Path

# reutilizăm helperele din pmax_video (Gemini, muzică deals, trim produs, font, hex…)
PV_DIR = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/pmax-video/scripts"
sys.path.insert(0, PV_DIR)
import pmax_video as pv  # noqa: E402

from PIL import Image, ImageDraw, ImageFont, ImageFilter  # noqa: E402
import numpy as np  # noqa: E402

FONT = pv.FONT
FPS = 30


# ───────────────────────────── helpers de desen ─────────────────────────────
def _font(px):
    return ImageFont.truetype(FONT, max(8, int(px)))


def _lei(p):
    return pv._lei(p)


def _text_centered(d, cx, y, text, font, fill="white", stroke=4, stroke_fill=(0, 0, 0, 235)):
    w = d.textlength(text, font=font)
    d.text((cx - w / 2, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
    return w


def _star_burst(size, pct_text, fill=(232, 28, 32, 255)):
    """Ștampilă-stea roșie cu „-50%" (sau „REDUS"). RGBA, transparentă în jur."""
    s = size
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
    # inel interior
    d.ellipse([cx - rI * 0.92, cy - rI * 0.92, cx + rI * 0.92, cy + rI * 0.92],
              outline=(255, 255, 255, 230), width=max(2, int(s * 0.012)))
    f = _font(s * (0.30 if len(pct_text) <= 4 else 0.22))
    bbox = d.textbbox((0, 0), pct_text, font=f)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]), pct_text, font=f, fill="white",
           stroke_width=max(2, int(s * 0.02)), stroke_fill=(120, 0, 0, 230))
    return img


def _make_scale_states(src_rgba, W, H, center_xy, scales, prefix):
    """Pre-randează ACELAȘI element la mai multe scale-uri, fiecare SPRITE MIC (nu full-frame,
    ca să fie ieftin la compositing), centrat pe center_xy. Întoarce listă de
    (cale_png, x_topleft, y_topleft) → le compunem secvențial pt SCALE-in real."""
    out = []
    cx, cy = center_xy
    smax = max(scales)
    canvas_w = int(src_rgba.width * smax) + 4
    canvas_h = int(src_rgba.height * smax) + 4
    for k, s in enumerate(scales):
        nw, nh = max(1, int(src_rgba.width * s)), max(1, int(src_rgba.height * s))
        resized = src_rgba.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        canvas.alpha_composite(resized, ((canvas_w - nw) // 2, (canvas_h - nh) // 2))
        p = f"{prefix}_s{k}.png"
        canvas.save(p)
        x = int(cx - canvas_w / 2)
        y = int(cy - canvas_h / 2)
        out.append((p, x, y))
    return out


def _accent_bg(W, H, accent, t_pulse=0.0):
    """Fundal dinamic: gradient radial accent care PULSEAZĂ (luminozitate ~ t_pulse 0..1)."""
    ac = pv._hex(accent)
    yy, xx = np.ogrid[:H, :W]
    cx, cy = W / 2, H * 0.42
    rad = max(W, H) * 0.78
    glow = np.clip(1 - np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rad, 0, 1) ** 1.5
    base = 0.16 + 0.20 * t_pulse        # cât de „aprins" e centrul
    arr = np.zeros((H, W, 3), np.uint8)
    for k in range(3):
        lvl = ac[k] * (0.10 + base * glow)
        arr[:, :, k] = np.clip(lvl + 6, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB").convert("RGBA")


def _product_layer(prod, W, H, frame_color):
    """Produsul în card animabil: poză trim-uită, încadrată într-un card rotunjit cu chenar accent
    + umbră. Transparent în jur → poate fi slide-uit/whip-uit ca un overlay separat."""
    pr = pv._trim_product(prod["img"]).convert("RGBA")
    box_w, box_h = int(W * 0.78), int(H * 0.46)
    sc = min(box_w / pr.width, box_h / pr.height)
    pr = pr.resize((max(1, int(pr.width * sc)), max(1, int(pr.height * sc))), Image.LANCZOS)
    pad = int(W * 0.022)
    cw, ch = pr.width + pad * 2, pr.height + pad * 2
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    card = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    rad = int(min(cw, ch) * 0.07)
    # card alb cu chenar accent
    cd.rounded_rectangle([0, 0, cw - 1, ch - 1], radius=rad, fill=(255, 255, 255, 255),
                         outline=pv._hex(frame_color), width=max(5, int(W * 0.010)))
    # poză rotunjită în interior
    mask = Image.new("L", pr.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pr.width - 1, pr.height - 1],
                                           radius=int(rad * 0.7), fill=255)
    card.paste(pr, (pad, pad), mask)
    cx, cy = (W - cw) // 2, int(H * 0.28)
    # umbră
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([cx + 14, cy + 18, cx + cw + 14, cy + ch + 18],
                                         radius=rad, fill=(0, 0, 0, 150))
    canvas = Image.alpha_composite(canvas, sh.filter(ImageFilter.GaussianBlur(26)))
    canvas.alpha_composite(card, (cx, cy))
    return canvas, (cx, cy, cw, ch)


def _label_layer(W, H, text, accent):
    """Banda de label/beneficiu de sus (text mare alb, bară accent dedesubt)."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    base_fs = int(H * 0.052)
    f, lines = pv._fit_font(text, d, W * 0.90, base_fs, int(H * 0.034), 2)
    lh = int(f.size * 1.14)
    y = int(H * 0.085)
    maxw = 0
    for ln in lines:
        w = _text_centered(d, W / 2, y, ln, f, stroke=max(3, f.size // 14))
        maxw = max(maxw, w)
        y += lh
    # bară accent
    bw = min(W * 0.5, maxw * 0.6)
    d.rounded_rectangle([(W - bw) / 2, y + 6, (W + bw) / 2, y + 6 + int(H * 0.008)],
                        radius=6, fill=pv._hex(accent))
    return ov


def _price_layers(W, H, prod, accent, card_box=None, burst_overshoot=1.14):
    """Întoarce 3 PNG-uri pt animația de cădere a prețului:
       (old) preț vechi tăiat,  (new) badge preț nou (slam),  (burst) ștampila -%.
    `card_box` = (cx,cy,cw,ch) al cardului de produs → ancorăm burst-ul sus-dreapta pe card.
    `burst_overshoot` = scale-ul MAXIM din pop-in (vezi _make_scale_states) → folosit ca să
    garantăm că sprite-ul, chiar și la overshoot, încape COMPLET în cadru (fix bug clipping)."""
    old = prod.get("old")
    p = prod.get("price")
    pct = prod.get("pct")
    by = int(H * 0.80)

    # old price (tăiat) — apare primul
    old_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if old:
        d = ImageDraw.Draw(old_img)
        fo = _font(H * 0.040)
        ot = f"{_lei(old)} lei"
        ow = d.textlength(ot, font=fo)
        ox = (W - ow) / 2
        oy = by - int(H * 0.075)
        d.text((ox, oy), ot, font=fo, fill=(225, 225, 225, 255),
               stroke_width=3, stroke_fill=(0, 0, 0, 220))
        d.line([ox - 6, oy + fo.size * 0.55, ox + ow + 6, oy + fo.size * 0.55],
               fill=(245, 60, 60, 255), width=max(4, int(fo.size / 7)))

    # new price badge — „slam"-uit
    new_img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(new_img)
    fn = _font(H * 0.085)
    nt = f"{_lei(p)} lei"
    nw = d.textlength(nt, font=fn)
    pad = int(fn.size * 0.40)
    bx0, bx1 = (W - nw) / 2 - pad, (W + nw) / 2 + pad
    ac = pv._hex(accent)
    d.rounded_rectangle([bx0, by - pad, bx1, by + fn.size + pad], radius=int(fn.size * 0.30),
                        fill=(ac[0], ac[1], ac[2], 255), outline=(255, 255, 255, 255),
                        width=max(3, int(W * 0.006)))
    _text_centered(d, W / 2, by, nt, fn, stroke=max(3, fn.size // 18))

    # burst -% — întoarcem DOAR sprite-ul (transparent) + centrul lui, ca să-l putem SCALE-in.
    # FIX clipping: îl ancorăm în colțul SUS-DREAPTA al cardului de produs (cu padding) și
    # CLAMP-uim centrul așa încât sprite-ul — chiar și la scale-ul de overshoot din pop-in —
    # să rămână COMPLET în cadru, cu o margine de siguranță. Înainte era pus la dreapta
    # badge-ului de preț (bx1 + 0.30*bs) și ieșea din ramă pe dreapta.
    bs = int(H * 0.16)
    burst = _star_burst(bs, f"-{pct}%" if pct else "REDUS")
    margin = int(W * 0.03)
    # jumătatea sprite-ului la cel mai mare scale (canvas-ul din _make_scale_states),
    # ca să clamp-uim corect față de margini.
    half = (bs * burst_overshoot) / 2.0
    if card_box:
        cx0, cy0, cw, ch = card_box
        # colțul sus-dreapta al cardului, cu padding spre interior
        burst_cx = int(cx0 + cw - bs * 0.30)
        burst_cy = int(cy0 + bs * 0.34)
    else:
        burst_cx = int(W * 0.78)
        burst_cy = int(H * 0.30)
    # clamp în [margin+half, W-margin-half] × [margin+half, H-margin-half]
    burst_cx = int(min(W - margin - half, max(margin + half, burst_cx)))
    burst_cy = int(min(H - margin - half, max(margin + half, burst_cy)))
    return old_img, new_img, burst, (burst_cx, burst_cy), by


# ───────────────────────────── pre-render PNG-uri ─────────────────────────────
def render_concept(script, products, out_path, W=1080, H=1920, brand="Ofertele Zilei"):
    accent = script.get("palette", "#FF4500")
    hook = script.get("hook", "50% reducere la TOT")
    cta = script.get("cta", f"Comandă acum la {brand}")
    labels = script["labels"]
    tmp = Path(tempfile.mkdtemp())

    # ── timeline (beat-synced, ~124 BPM → 0.4839s/beat; folosim multipli) ──
    HOOK = 2.5
    PROD = 1.75
    CTA = 2.6
    n = len(products)
    DUR = HOOK + n * PROD + CTA

    # ── pre-render statice (PNG) ──
    bg_dark = _accent_bg(W, H, accent, 0.10)
    bg_dark.convert("RGB").save(tmp / "bg.jpg", quality=92)

    # HOOK assets: cuvinte mari + ștampilă -50%
    hook_top = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(hook_top)
    f, lines = pv._fit_font(hook.upper(), d, W * 0.92, int(H * 0.085), int(H * 0.05), 2)
    lh = int(f.size * 1.12)
    y = int(H * 0.16)
    for ln in lines:
        _text_centered(d, W / 2, y, ln, f, stroke=max(4, f.size // 12))
        y += lh
    hook_top.save(tmp / "hook_top.png")

    # ștampila hook = SCALE-in real (stări multiple, overshoot pop)
    POP = [0.25, 0.70, 1.14, 1.0]   # mic → mare (overshoot) → settle
    hook_stamp_sprite = _star_burst(int(H * 0.30), "-50%")
    hook_stamp_states = _make_scale_states(hook_stamp_sprite, W, H,
                                           (W / 2, int(H * 0.50) + hook_stamp_sprite.height / 2),
                                           POP, str(tmp / "hookstamp"))

    hook_sub = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(hook_sub)
    fs = _font(H * 0.040)
    _text_centered(d, W / 2, int(H * 0.84), f"DOAR AZI · {brand}", fs, stroke=3)
    hook_sub.save(tmp / "hook_sub.png")

    # PER PRODUS assets
    prod_assets = []
    prod_burst_states = {}
    for i, p in enumerate(products):
        plyr, box = _product_layer(p, W, H, accent)
        plyr.save(tmp / f"prod{i}.png")
        lab = _label_layer(W, H, labels[i], accent)
        lab.save(tmp / f"lab{i}.png")
        old_i, new_i, burst_sprite, burst_center, _by = _price_layers(
            W, H, p, accent, card_box=box, burst_overshoot=max(POP))
        old_i.save(tmp / f"old{i}.png")
        new_i.save(tmp / f"new{i}.png")
        prod_burst_states[i] = _make_scale_states(burst_sprite, W, H, burst_center, POP,
                                                  str(tmp / f"burst{i}"))
        prod_assets.append(i)

    # CTA assets
    cta_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(cta_layer)
    # buton mare
    f, lines = pv._fit_font(cta, d, W * 0.86, int(H * 0.060), int(H * 0.040), 2)
    lh = int(f.size * 1.16)
    block_h = lh * len(lines)
    btn_y = int(H * 0.40)
    btn_pad = int(H * 0.045)
    ac = pv._hex(accent)
    d.rounded_rectangle([W * 0.07, btn_y - btn_pad, W * 0.93, btn_y + block_h + btn_pad],
                        radius=int(H * 0.04), fill=(ac[0], ac[1], ac[2], 255),
                        outline=(255, 255, 255, 255), width=max(4, int(W * 0.007)))
    yy = btn_y
    for ln in lines:
        _text_centered(d, W / 2, yy, ln, f, stroke=max(3, f.size // 16))
        yy += lh
    cta_layer.save(tmp / "cta.png")

    cta_stamp_sprite = _star_burst(int(H * 0.20), "-50%")
    cta_stamp_states = _make_scale_states(cta_stamp_sprite, W, H,
                                          (W / 2, int(H * 0.66) + cta_stamp_sprite.height / 2),
                                          POP, str(tmp / "ctastamp"))

    # ── muzică energică ──
    music = pv.make_music_deals(DUR, str(tmp / "m.wav"))

    # ════════════ construiește filtergraph ════════════
    # input 0 = fundal (loop static jpg). Pe el compunem totul cu overlay timed/animat.
    inputs = ["-loop", "1", "-t", f"{DUR:.2f}", "-i", str(tmp / "bg.jpg")]

    # listă de overlay-uri PNG (în ordinea de adăugare ca input)
    png_inputs = []

    def add_png(name):
        png_inputs.append(str(tmp / name))
        return len(png_inputs)  # index real în inputs ffmpeg (bg=0, png-urile de la 1)

    def add_path(path):
        png_inputs.append(str(path))
        return len(png_inputs)

    def add_states(states):
        # states = listă de (path, x, y) → întoarce listă de (idx, x, y)
        return [(add_path(p), x, y) for (p, x, y) in states]

    # HOOK
    i_hook_top = add_png("hook_top.png")
    i_hook_stamp_states = add_states(hook_stamp_states)   # SCALE-in real
    i_hook_sub = add_png("hook_sub.png")
    # PRODUSE
    prod_idx = []
    for i in prod_assets:
        prod_idx.append({
            "prod": add_png(f"prod{i}.png"),
            "lab": add_png(f"lab{i}.png"),
            "old": add_png(f"old{i}.png"),
            "new": add_png(f"new{i}.png"),
            "burst_states": add_states(prod_burst_states[i]),
        })
    # CTA
    i_cta = add_png("cta.png")
    i_cta_stamp_states = add_states(cta_stamp_states)

    for p in png_inputs:
        inputs += ["-loop", "1", "-t", f"{DUR:.2f}", "-i", p]
    inputs += ["-i", music]
    music_idx = 1 + len(png_inputs)

    # ── fundal cu PULS de luminozitate pe beat (lift/eq pe tot) + shake fin ──
    # puls: la fiecare beat fundalul se „aprinde" puțin (sinusoidal rapid).
    beat = 60.0 / 124.0
    fc = (
        f"[0:v]format=rgba,"
        f"eq=brightness='0.06*abs(sin(PI*t/{beat:.4f}))':saturation=1.25,"
        f"setsar=1[base]"
    )
    prev = "base"
    o = 0  # counter pt etichete unice

    def ov(prev, idx, expr_x, expr_y, enable):
        """Adaugă un overlay animat. idx = index real în lista de inputs ffmpeg (bg=0, png-urile de la 1)."""
        nonlocal o, fc
        lab = f"v{o}"
        o += 1
        fc += f";[{prev}][{idx}:v]overlay=x='{expr_x}':y='{expr_y}':enable='{enable}'[{lab}]"
        return lab

    def pop_in(prev, states, t0, t_end, step=0.05):
        """SCALE-in REAL: arată stările (mici→mare→settle) în ferestre succesive de `step`,
        ultima stare (settle) rămâne până la t_end. states = listă de (idx, x, y)."""
        n = len(states)
        t = t0
        for j, (idx, x, y) in enumerate(states):
            if j < n - 1:
                a, b = t, t + step
                t = b
            else:
                a, b = t, t_end   # ultima stare (settle) ține restul
            prev = ov(prev, idx, str(x), str(y), f"between(t,{a:.3f},{b:.3f})")
        return prev

    # ───────── HOOK (0..HOOK) ─────────
    h0 = 0.0
    h1 = HOOK
    # cuvinte hook: slide-in de sus cu overshoot (pop) primele 0.5s, apoi fix
    # y = target - amplitudine*exp(-k*(t)) ... aproximăm cu min(0, ...) prin expresii ffmpeg
    # folosim: y începe sus (-H*0.1) și aterizează; impact = mic recul
    yexpr_top = f"if(lt(t,{h0+0.40}), -260+260*((t-{h0})/0.40), 6*sin(22*(t-{h0+0.40}))*exp(-5*(t-{h0+0.40})))"
    prev = ov(prev, i_hook_top, "0", yexpr_top, f"between(t,{h0},{h1})")
    # ștampila -50%: SCALE-in REAL (stări multiple, overshoot pop) la 0.40s
    prev = pop_in(prev, i_hook_stamp_states, h0 + 0.40, h1)
    # subtitlu: apare la 0.9s
    prev = ov(prev, i_hook_sub, "0", "0", f"between(t,{h0+0.9},{h1})")

    # ───────── PRODUSE ─────────
    t0 = HOOK
    for k, p in enumerate(prod_idx):
        a = t0
        b = t0 + PROD
        # tranziție whip ocazională: la produsele pare, produsul intră din DREAPTA cu
        # slide rapid (whip) + motion-blur fake; la impare din STÂNGA.
        from_right = (k % 2 == 0)
        slide_t = 0.22  # intrare WHIP rapidă (decelerează spre 0 = ease-out)
        sign = 1 if from_right else -1
        # ease-out (1-(1-u)^2): pleacă rapid, frânează la final → senzație de „whip"
        u = f"((t-{a})/{slide_t})"
        ease = f"(1-(1-{u})*(1-{u}))"
        px = f"if(lt(t,{a+slide_t}), {sign}*{W}*(1-{ease}), {sign}*7*sin(26*(t-{a+slide_t}))*exp(-6*(t-{a+slide_t})))"
        # bob subtil + mic zoom-bob pe restul beat-ului
        py = f"if(lt(t,{a+slide_t}), 0, 10*sin(3.4*(t-{a+slide_t})))"
        prev = ov(prev, p["prod"], px, py, f"between(t,{a},{b})")
        # label intră ÎMPREUNĂ cu produsul (din aceeași parte) → fără frame gol
        prev = ov(prev, p["lab"], px, "0", f"between(t,{a},{b})")
        # PREȚUL CADE: old price apare la +0.30; new price „slam" la +0.70; burst SCALE-in cu el
        old_a = a + 0.30
        prev = ov(prev, p["old"], "0", "0", f"between(t,{old_a},{old_a+0.55})")  # dispare când vine new
        new_a = a + 0.72
        slam_y = f"if(lt(t,{new_a+0.16}), -100*(1-((t-{new_a})/0.16)), 5*sin(34*(t-{new_a+0.16}))*exp(-6*(t-{new_a+0.16})))"
        prev = ov(prev, p["new"], "0", slam_y, f"between(t,{new_a},{b})")
        # burst -% = SCALE-in real, odată cu prețul nou
        prev = pop_in(prev, p["burst_states"], new_a, b, step=0.038)
        t0 = b

    # ───────── CTA ─────────
    c0 = t0
    c1 = DUR
    # butonul intră cu „pop" de jos (ease-out + mic recul)
    cy = f"if(lt(t,{c0+0.38}), 200*(1-((t-{c0})/0.38))*(1-((t-{c0})/0.38)), 7*sin(18*(t-{c0+0.38}))*exp(-3*(t-{c0+0.38})))"
    prev = ov(prev, i_cta, "0", cy, f"between(t,{c0},{c1})")
    # ștampila -50% = SCALE-in real
    prev = pop_in(prev, i_cta_stamp_states, c0 + 0.32, c1)

    out = Path(out_path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", f"[{prev}]",
           "-map", f"{music_idx}:a", "-r", str(FPS), "-t", f"{DUR:.2f}",
           "-af", f"afade=t=out:st={max(DUR-0.6,0):.2f}:d=0.6", "-shortest",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        sys.stderr.write("FFMPEG FAIL:\n" + r.stderr[-2500:] + "\n")
        sys.exit(1)
    return out, DUR


def main():
    ap = argparse.ArgumentParser(
        description="Concept KINETIC PRICE-DROP — video ad short-form pt magazinele de oferte ARONA")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--storefront", help="domeniu storefront Shopify public, ex ofertelezilei.ro "
                     "(trage produse + poze via pv.fetch_storefront)")
    src.add_argument("--manifest", help="JSON alternativ: listă de {title,price,old,pct,img}")
    ap.add_argument("--brand", default="Ofertele Zilei")
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop/pmax-video"),
                    help="director de ieșire (numele fișierului e standardizat)")
    ap.add_argument("--fmt", default="9:16", choices=list(pv.FMT.keys()),
                    help="raport de aspect (9:16 | 1:1 | 16:9)")
    ap.add_argument("--n", type=int, default=6, help="câte produse (max 6)")
    ap.add_argument("--pick", default="", help="indici produse din manifest, ex '1,7,11,13,3'")
    ap.add_argument("--offer", default="-50% la TOT")
    a = ap.parse_args()

    W, H = pv.FMT[a.fmt]

    # ── director de ieșire + nume fișier STANDARD: {brandslug}_KINETIC_{fmt_with_x}.mp4 ──
    out_dir = Path(a.out).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    brandslug = re.sub(r"[^A-Za-z0-9]", "", a.brand) or "Brand"
    fmt_with_x = a.fmt.replace(":", "x")
    out_path = out_dir / f"{brandslug}_KINETIC_{fmt_with_x}.mp4"

    # ── sursă produse: storefront (poze absolute) sau manifest ──
    if a.storefront:
        img_dir = out_dir / f"_src_{brandslug}"
        print(f"● {a.brand}: trag produse din {a.storefront} (max {a.n})…")
        products = pv.fetch_storefront(a.storefront, a.n, str(img_dir))
        if not products:
            sys.stderr.write(f"Niciun produs cu poză din {a.storefront}\n")
            sys.exit(1)
    else:
        allp = json.load(open(a.manifest))
        if a.pick:
            idx = [int(x) for x in a.pick.split(",")]
            products = [allp[i] for i in idx]
        else:
            products = allp[:a.n]
    products = products[:min(a.n, 6)]
    # poze → căi ABSOLUTE (manifest poate avea căi relative la cwd)
    for p in products:
        if p.get("img"):
            p["img"] = str(Path(p["img"]).expanduser().resolve())

    print(f"● {a.brand}: {len(products)} produse [{a.fmt} {W}x{H}] → Gemini scrie copy-ul…")
    script = pv.direct_deals(a.brand, products, a.offer)
    print(f"  hook: „{script.get('hook')}”  CTA: „{script.get('cta')}”  paletă {script.get('palette')}")
    for i, l in enumerate(script["labels"]):
        pr = products[i].get("price")
        print(f"    [{i}] {int(pr) if pr else '?'} lei — „{l}”")
    out, dur = render_concept(script, products, out_path, W=W, H=H, brand=a.brand)
    print(f"✓ {dur:.1f}s [{a.fmt}] → {out}")


if __name__ == "__main__":
    main()
