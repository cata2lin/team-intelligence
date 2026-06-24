# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pillow>=10.0", "numpy>=1.24"]
# ///
"""
concept_bento.py — concept "BENTO / GRID DEALS-DUMP" pentru video-ad short-form al magazinului
de oferte „Ofertele Zilei" (ofertelezilei.ro).

DIFERENȚIATORUL față de slideshow-ul mono-produs din `pmax_video.py montage`:
  - HOOK (0-3s): un GRID animat 3x3 de thumbnail-uri care POP-uie rapid pe rând (scale+fade),
    cu copy mare „Peste 200 de oferte / Toate -50% azi" — energie de „deals-dump".
  - BODY: PUNCH-IN pe 3-4 produse HERO, fiecare un TILE bento curat (border accent, shadow moale)
    care SE SCALEAZĂ ca să umple, cu preț (vechi tăiat / nou / burst -50%) + beneficiu RO scurt.
  - CTA (~2.5s): grid-ul se REFORMEAZĂ + „Comandă acum pe Ofertele Zilei".

Motion = PRE-RENDER de cadre PIL per fază (full control pe pop-in / scale / slide), apoi fiecare
fază e encodată ca secvență de cadre. Așa ocolesc limita ffmpeg zoompan (crop dims fixe) și nu mă
bazez pe drawtext. Muzică energică via pv.make_music_deals. H.264 yuv420p +faststart. 100% gratis.

Usage (CLI standard, ca restul fabricii — main-tool îl cheamă uniform):
  uv run --with pillow --with numpy --with requests concept_bento.py \
      --storefront ofertelezilei.ro --brand "Ofertele Zilei" --fmt 9:16 --n 6
  uv run --with pillow --with numpy --with requests concept_bento.py \
      --manifest ofer_manifest.json --brand "Ofertele Zilei" --fmt 1:1

Output: {brandslug}_BENTO_{fmt_cu_x}.mp4 în --out (ex. OferteleZilei_BENTO_9x16.mp4).
"""
from __future__ import annotations
import argparse, json, math, os, re, subprocess, sys, tempfile
from pathlib import Path

# ── reutilizăm tot ce putem din pmax_video.py ──
sys.path.insert(0, '/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/pmax-video/scripts')
import pmax_video as pv
from pmax_video import (_trim_product, _gemini, make_music_deals, _wrap_caption,
                       _fit_font, fetch_storefront, FONT, FMT, _hex)

from PIL import Image, ImageDraw, ImageFont, ImageFilter
import numpy as np

FPS = 30


# ════════════════════════ helpers ════════════════════════
def _lei(p):
    if p is None:
        return ""
    s = f"{float(p):.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def ease_out(t):
    """ease-out cubic — pop rapid apoi se așază."""
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    return 0.5 * (1 - math.cos(math.pi * max(0.0, min(1.0, t))))


def back_out(t, s=1.7):
    """ease cu un mic overshoot — dă „pop"-ul de tile."""
    t -= 1
    return t * t * ((s + 1) * t + s) + 1


def load_tile(path, size):
    """Produs trimmed (fără margini albe) așezat centrat pe un pătrat alb curat → thumbnail de grid."""
    pr = _trim_product(path)  # RGBA
    box = int(size * 0.86)
    sc = min(box / pr.width, box / pr.height)
    pr = pr.resize((max(1, int(pr.width * sc)), max(1, int(pr.height * sc))), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    canvas.alpha_composite(pr, ((size - pr.width) // 2, (size - pr.height) // 2))
    return canvas


def rounded_mask(w, h, radius):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    return m


def rounded(img, radius):
    """Aplică colțuri rotunjite (returnează RGBA)."""
    img = img.convert("RGBA")
    img.putalpha(rounded_mask(img.width, img.height, radius))
    return img


def drop_shadow(w, h, radius, blur=26, alpha=150, grow=10):
    """Umbră moale pentru un tile rotunjit de dimensiune w×h (canvas mai mare cu blur+grow margine)."""
    pad = blur + grow + 6
    s = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(s).rounded_rectangle(
        [pad - grow, pad - grow + 6, pad + w + grow, pad + h + grow + 6],
        radius=radius + grow, fill=(0, 0, 0, alpha))
    return s.filter(ImageFilter.GaussianBlur(blur)), pad


def make_background(W, H, accent):
    """Fundal energic: gradient diagonal accent-închis → aproape negru + glow radial cald sus."""
    ac = _hex(accent)
    yy, xx = np.ogrid[:H, :W]
    diag = (xx / W * 0.45 + yy / H * 0.55)
    cx, cy, rad = W * 0.5, H * 0.30, max(W, H) * 0.7
    glow = np.clip(1 - np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rad, 0, 1) ** 1.8
    bg = np.zeros((H, W, 3), np.uint8)
    for k in range(3):
        base = ac[k] * (0.22 - 0.20 * diag)          # accent care se închide pe diagonală
        bg[:, :, k] = np.clip(base + ac[k] * 0.30 * glow + 8, 0, 255).astype(np.uint8)
    return Image.fromarray(bg, "RGB").convert("RGBA")


def fit_line(text, max_w, base_fs, min_fs):
    """Cel mai mare font (≤ base_fs) la care `text` încape pe UN rând în max_w. Single-line, fără wrap."""
    d = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    fs = base_fs
    while fs > min_fs:
        f = ImageFont.truetype(FONT, fs)
        if d.textlength(text, font=f) <= max_w:
            return f
        fs -= 2
    return ImageFont.truetype(FONT, min_fs)


def draw_top_copy(draw, lines_specs, W, top_y):
    """Desenează linii de copy centrate (fiecare: text, font, fill). Returnează y-ul de jos."""
    y = top_y
    for txt, font, fill in lines_specs:
        lw = draw.textlength(txt, font=font)
        draw.text(((W - lw) / 2, y), txt, font=font, fill=fill,
                  stroke_width=max(3, font.size // 14), stroke_fill=(0, 0, 0, 240))
        y += int(font.size * 1.12)
    return y


# ════════════════════════ HOOK: grid 3x3 care pop-uie ════════════════════════
def render_hook(products, hook_top, hook_sub, W, H, accent, frames_dir, n_frames, start_idx):
    """Grid 3x3 de thumbnail-uri care apar pe rând (scale+fade, back-out overshoot) + copy mare sus.
    La final toate 9 sunt afișate → senzație de «tone de oferte»."""
    cols, rows = 3, 3
    ac = _hex(accent)
    margin = int(W * 0.045)
    gap = int(W * 0.028)
    grid_w = W - 2 * margin
    cell = (grid_w - (cols - 1) * gap) // cols
    radius = int(cell * 0.16)
    grid_top = int(H * 0.345)

    # pre-construim tile-urile (produs pe pătrat alb, rounded) — 9 produse
    tiles = []
    for i in range(cols * rows):
        p = products[i % len(products)]
        t = load_tile(p["img"], cell)
        tiles.append(rounded(t, radius))
    # umbră comună pentru un tile
    shadow, spad = drop_shadow(cell, cell, radius, blur=18, alpha=120, grow=4)

    # fonturi copy (fit pe lățime → NU se taie la marginile cadrului)
    f_top = fit_line(hook_top, W * 0.90, int(H * 0.060), int(H * 0.034))
    f_sub = fit_line(hook_sub, W * 0.82, int(H * 0.072), int(H * 0.040))

    # ordinea de apariție: pe diagonale → mai dinamic decât rând cu rând
    order = sorted(range(cols * rows), key=lambda k: ((k % cols) + (k // cols), k % cols))
    per_tile = n_frames * 0.62 / len(order)   # toate apar în primele ~62% din hook
    pop_len = per_tile * 2.6                    # fiecare pop durează ceva mai mult → overlap plăcut

    bg0 = make_background(W, H, accent)         # fundal construit O SINGURĂ DATĂ (perf)
    for fi in range(n_frames):
        frame = bg0.copy()
        d = ImageDraw.Draw(frame)
        for slot, idx in enumerate(order):
            appear = slot * per_tile
            local = (fi - appear) / pop_len
            if local <= 0:
                continue
            if local >= 1:
                sc, al = 1.0, 1.0
            else:
                sc = back_out(local)           # overshoot pop
                al = min(1.0, local * 1.8)
            r, c = idx // cols, idx % cols
            cx = margin + c * (cell + gap) + cell // 2
            cy = grid_top + r * (cell + gap) + cell // 2
            tw = max(1, int(cell * sc))
            tile = tiles[idx]
            if tw != cell:
                tile = tile.resize((tw, tw), Image.LANCZOS)
            # umbră (doar când aproape așezat, ca să nu „murdărească" pop-ul)
            if al > 0.85 and sc >= 0.98:
                frame.alpha_composite(shadow, (cx - cell // 2 - spad, cy - cell // 2 - spad))
            if al < 1.0:
                tmp = tile.copy()
                a = tmp.getchannel("A").point(lambda v: int(v * al))
                tmp.putalpha(a)
                tile = tmp
            frame.alpha_composite(tile, (cx - tw // 2, cy - tw // 2))
        # copy mare sus (intră instant — hook trebuie citit imediat)
        y = draw_top_copy(d, [(hook_top, f_top, (255, 255, 255, 255))], W, int(H * 0.11))
        # sub-linie pe badge accent
        sw = d.textlength(hook_sub, font=f_sub)
        pad = int(f_sub.size * 0.40)
        bx0, bx1 = (W - sw) / 2 - pad, (W + sw) / 2 + pad
        by0 = y + int(H * 0.012)
        d.rounded_rectangle([bx0, by0, bx1, by0 + f_sub.size + 2 * pad],
                            radius=int(f_sub.size * 0.45), fill=(ac[0], ac[1], ac[2], 255))
        d.text(((W - sw) / 2, by0 + pad), hook_sub, font=f_sub, fill="white",
               stroke_width=max(2, f_sub.size // 18), stroke_fill=(0, 0, 0, 200))
        frame.convert("RGB").save(frames_dir / f"f{start_idx + fi:05d}.jpg", "JPEG", quality=90)
    return start_idx + n_frames


# ════════════════════════ BODY: hero bento tile ════════════════════════
def render_hero(prod, label, W, H, accent, frames_dir, n_frames, start_idx, slide_dir=1):
    """Un produs HERO ca tile BENTO mare: intră scale+slide (overshoot), border accent, shadow moale,
    titlu scurt sus, burst -% și badge preț (vechi tăiat / nou) jos. Punch-in energic."""
    ac = _hex(accent)
    tile_w = int(W * 0.86)
    tile_h = int(H * 0.60)
    tx = (W - tile_w) // 2
    ty = int(H * 0.255)
    radius = int(tile_w * 0.075)
    border = max(6, int(tile_w * 0.014))

    # ── construim CONȚINUTUL tile-ului o singură dată (produs pe fundal alb cald, rounded, border) ──
    inner = Image.new("RGBA", (tile_w, tile_h), (250, 249, 247, 255))
    pr = _trim_product(prod["img"])
    box_w, box_h = int(tile_w * 0.92), int(tile_h * 0.74)
    sc = min(box_w / pr.width, box_h / pr.height)
    pr = pr.resize((max(1, int(pr.width * sc)), max(1, int(pr.height * sc))), Image.LANCZOS)
    px, py = (tile_w - pr.width) // 2, int(tile_h * 0.085)
    # umbră moale sub produs (grounded)
    sh = Image.new("RGBA", (tile_w, tile_h), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([px + pr.width * 0.12, py + pr.height - 16,
                                px + pr.width * 0.88, py + pr.height + 34], fill=(0, 0, 0, 70))
    inner = Image.alpha_composite(inner, sh.filter(ImageFilter.GaussianBlur(20)))
    inner.alpha_composite(pr, (px, py))
    inner = rounded(inner, radius)
    # border accent
    bd = ImageDraw.Draw(inner)
    bd.rounded_rectangle([border // 2, border // 2, tile_w - border // 2 - 1, tile_h - border // 2 - 1],
                         radius=radius, outline=(ac[0], ac[1], ac[2], 255), width=border)

    # burst „-X%" colț dreapta-sus al tile-ului
    pct = prod.get("pct")
    if pct:
        bs = int(tile_w * 0.13)
        burst = Image.new("RGBA", (bs * 2, bs * 2), (0, 0, 0, 0))
        bdr = ImageDraw.Draw(burst)
        pts = []
        for i in range(24):
            ang = math.pi * i / 12 - 0.1
            r = bs if i % 2 == 0 else bs * 0.74
            pts.append((bs + r * math.cos(ang), bs + r * math.sin(ang)))
        bdr.polygon(pts, fill=(233, 32, 38, 255), outline=(255, 255, 255, 255))
        fb = ImageFont.truetype(FONT, int(bs * 0.46))
        bt = f"-{pct}%"
        bw = bdr.textlength(bt, font=fb)
        bdr.text((bs - bw / 2, bs - bs * 0.30), bt, font=fb, fill="white",
                 stroke_width=2, stroke_fill=(0, 0, 0, 200))
        inner.alpha_composite(burst, (tile_w - int(bs * 1.55), -int(bs * 0.45)))

    # umbră tile (drop shadow) — canvas separat
    shadow, spad = drop_shadow(tile_w, tile_h, radius, blur=30, alpha=160, grow=12)

    # ── overlay STATIC (titlu sus + badge preț jos) la dimensiune full-frame ──
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)
    f_lab, lines = _fit_font(label, od, W * 0.86, int(H * 0.058), int(H * 0.038), 2)
    ly = int(H * 0.085)
    for ln in lines:
        lw = od.textlength(ln, font=f_lab)
        od.text(((W - lw) / 2, ly), ln, font=f_lab, fill="white",
                stroke_width=max(3, f_lab.size // 13), stroke_fill=(0, 0, 0, 240))
        ly += int(f_lab.size * 1.12)
    # badge preț jos
    p, old = prod.get("price"), prod.get("old")
    by = int(H * 0.875)
    newtxt = f"{_lei(p)} lei"
    fs_new = int(H * 0.072)
    fnew = ImageFont.truetype(FONT, fs_new)
    fs_old = int(H * 0.034)
    fold = ImageFont.truetype(FONT, fs_old)
    oldtxt = f"{_lei(old)} lei" if old else ""
    new_w = od.textlength(newtxt, font=fnew)
    old_w = od.textlength(oldtxt, font=fold) if oldtxt else 0
    gap = int(W * 0.03)
    total_w = new_w + (old_w + gap if oldtxt else 0)
    bx = (W - total_w) / 2
    pad = int(fs_new * 0.42)
    od.rounded_rectangle([bx - pad, by - pad, bx + total_w + pad, by + fs_new + pad],
                         radius=int(fs_new * 0.42), fill=(0, 0, 0, 185))
    xcur = bx
    if oldtxt:
        oy = by + (fs_new - fs_old)
        od.text((xcur, oy), oldtxt, font=fold, fill=(210, 210, 210, 255))
        od.line([xcur, oy + fs_old * 0.55, xcur + old_w, oy + fs_old * 0.55],
                fill=(245, 70, 70, 255), width=max(3, fs_old // 9))
        xcur += old_w + gap
    od.rounded_rectangle([xcur - pad // 2, by - pad // 2, xcur + new_w + pad // 2, by + fs_new + pad // 2],
                         radius=int(fs_new * 0.3), fill=(ac[0], ac[1], ac[2], 255))
    od.text((xcur, by), newtxt, font=fnew, fill="white",
            stroke_width=max(2, fs_new // 22), stroke_fill=(0, 0, 0, 235))

    bg = make_background(W, H, accent)
    for fi in range(n_frames):
        t = fi / max(1, n_frames - 1)
        # intrare: primele ~38% scale+slide cu overshoot, apoi ușor drift (push-in) până la final
        if t < 0.38:
            lt = t / 0.38
            sc = 0.62 + (back_out(lt) * 0.38)         # 0.62 → ~1.0 cu overshoot
            sc = min(sc, 1.06)
            al = min(1.0, lt * 2.2)
            off = int((1 - ease_out(lt)) * W * 0.16 * slide_dir)
        else:
            lt = (t - 0.38) / 0.62
            sc = 1.0 + 0.045 * ease_in_out(lt)        # push-in lent → viu, nu static
            al = 1.0
            off = 0
        frame = bg.copy()
        cw, ch = max(1, int(tile_w * sc)), max(1, int(tile_h * sc))
        cx = tx + tile_w // 2 + off
        cy = ty + tile_h // 2
        # umbră (scalată aproximativ prin reposition)
        if al > 0.6:
            sc_shadow = shadow
            sw, shh = shadow.size
            if abs(sc - 1.0) > 0.01:
                sc_shadow = shadow.resize((max(1, int(sw * sc)), max(1, int(shh * sc))), Image.LANCZOS)
            frame.alpha_composite(sc_shadow, (cx - sc_shadow.width // 2, cy - sc_shadow.height // 2))
        tile = inner if (cw == tile_w and ch == tile_h) else inner.resize((cw, ch), Image.LANCZOS)
        if al < 1.0:
            tmp = tile.copy()
            tmp.putalpha(tile.getchannel("A").point(lambda v: int(v * al)))
            tile = tmp
        frame.alpha_composite(tile, (cx - cw // 2, cy - ch // 2))
        # overlay text apare după ce tile-ul s-a așezat (la ~30%)
        if t > 0.22:
            ot = min(1.0, (t - 0.22) / 0.18)
            if ot < 1.0:
                tmp = over.copy()
                tmp.putalpha(over.getchannel("A").point(lambda v: int(v * ot)))
                frame.alpha_composite(tmp)
            else:
                frame.alpha_composite(over)
        frame.convert("RGB").save(frames_dir / f"f{start_idx + fi:05d}.jpg", "JPEG", quality=90)
    return start_idx + n_frames


# ════════════════════════ CTA: grid se reformează ════════════════════════
def render_cta(products, cta_text, brand_line, W, H, accent, frames_dir, n_frames, start_idx):
    """Grid 3x3 se reformează (tile-urile zboară spre poziție din afara cadrului, ease-out) și se
    întunecă sub un panou cu CTA: „Comandă acum pe Ofertele Zilei"."""
    cols, rows = 3, 3
    ac = _hex(accent)
    margin = int(W * 0.045)
    gap = int(W * 0.028)
    grid_w = W - 2 * margin
    cell = (grid_w - (cols - 1) * gap) // cols
    radius = int(cell * 0.16)
    grid_top = int(H * 0.155)

    tiles = []
    for i in range(cols * rows):
        p = products[i % len(products)]
        tiles.append(rounded(load_tile(p["img"], cell), radius))
    shadow, spad = drop_shadow(cell, cell, radius, blur=16, alpha=110, grow=4)

    # ținta fiecărui tile + direcția din care „zboară" (din marginea cea mai apropiată)
    targets = []
    for idx in range(cols * rows):
        r, c = idx // cols, idx % cols
        cx = margin + c * (cell + gap) + cell // 2
        cy = grid_top + r * (cell + gap) + cell // 2
        # offset de pornire: împins în afară pe diagonală
        dx = (c - 1) * W * 0.5
        dy = (r - 1) * H * 0.35
        targets.append((cx, cy, dx, dy, idx * 0.05))   # mic stagger

    f_cta, cta_lines = _fit_font(cta_text, ImageDraw.Draw(Image.new("RGB", (W, H))),
                                 W * 0.84, int(H * 0.066), int(H * 0.044), 2)
    f_brand = fit_line(brand_line, W * 0.82, int(H * 0.052), int(H * 0.032))  # fit → nu se taie

    bg0 = make_background(W, H, accent)         # fundal construit O SINGURĂ DATĂ (perf)
    for fi in range(n_frames):
        t = fi / max(1, n_frames - 1)
        frame = bg0.copy()
        for idx, (cx, cy, dx, dy, stagger) in enumerate(targets):
            lt = max(0.0, min(1.0, (t - stagger) / max(0.01, (0.55 - stagger))))
            e = ease_out(lt)
            ox = int(cx + dx * (1 - e))
            oy = int(cy + dy * (1 - e))
            al = min(1.0, lt * 1.6)
            tile = tiles[idx]
            if al > 0.9:
                frame.alpha_composite(shadow, (ox - cell // 2 - spad, oy - cell // 2 - spad))
            if al < 1.0:
                tmp = tile.copy()
                tmp.putalpha(tile.getchannel("A").point(lambda v: int(v * al)))
                tile = tmp
            frame.alpha_composite(tile, (ox - cell // 2, oy - cell // 2))
        # panou CTA întunecat care urcă din jos după ~45%
        if t > 0.40:
            pt = min(1.0, (t - 0.40) / 0.35)
            panel_h = int(H * 0.40)
            panel = Image.new("RGBA", (W, panel_h), (0, 0, 0, 0))
            grad = np.zeros((panel_h, W, 4), np.uint8)
            grad[:, :, 3] = np.clip(np.linspace(0, 235, panel_h), 0, 255).astype(np.uint8)[:, None]
            panel.alpha_composite(Image.fromarray(grad, "RGBA"))
            py = int(H - panel_h * pt)
            frame.alpha_composite(panel, (0, py + panel_h - int(panel_h * pt)) if False else (0, H - int(panel_h * pt)))
            d = ImageDraw.Draw(frame)
            if pt > 0.3:
                ty = H - int(H * 0.26)
                for ln in cta_lines:
                    lw = d.textlength(ln, font=f_cta)
                    d.text(((W - lw) / 2, ty), ln, font=f_cta, fill="white",
                           stroke_width=max(3, f_cta.size // 13), stroke_fill=(0, 0, 0, 240))
                    ty += int(f_cta.size * 1.14)
                # linie brand pe badge accent
                bw = d.textlength(brand_line, font=f_brand)
                pad = int(f_brand.size * 0.42)
                bx0, bx1 = (W - bw) / 2 - pad, (W + bw) / 2 + pad
                by0 = ty + int(H * 0.012)
                d.rounded_rectangle([bx0, by0, bx1, by0 + f_brand.size + 2 * pad],
                                    radius=int(f_brand.size * 0.5), fill=(ac[0], ac[1], ac[2], 255))
                d.text(((W - bw) / 2, by0 + pad), brand_line, font=f_brand, fill="white",
                       stroke_width=max(2, f_brand.size // 18), stroke_fill=(0, 0, 0, 200))
        frame.convert("RGB").save(frames_dir / f"f{start_idx + fi:05d}.jpg", "JPEG", quality=90)
    return start_idx + n_frames


# ════════════════════════ Gemini copy ════════════════════════
def get_copy(brand, products, heroes):
    """Gemini scrie: hook_top, hook_sub, cta + label scurt per hero. Prețuri NU inventează (din manifest)."""
    items = "\n".join(f"{i}. {p['title']} — {_lei(p.get('price'))} lei (vechi {_lei(p.get('old'))}, -{p.get('pct')}%)"
                      for i, p in enumerate(heroes))
    prompt = f"""Ești copywriter de reclame video short-form (TikTok/Reels) pentru magazinul de OFERTE „{brand}" (e-commerce RO, plata ramburs).
Conceptul reclamei: un GRID de zeci de oferte care apar rapid (energie de „magazin plin de reduceri"),
apoi 3-4 produse-vedetă mari, apoi îndemn final.

Produse-vedetă (cu prețuri REALE — NU le schimba, NU inventa altele):
{items}

Răspunde STRICT JSON:
{{
 "hook_top": "<3-5 cuvinte, linia mare de sus a hook-ului; energie de oferte; RO corect; ex: «Azi toate la jumătate»>",
 "hook_sub": "<2-4 cuvinte, badge sub hook; ex: «Peste 200 de oferte» sau «Toate -50% azi»>",
 "labels": ["<beneficiu/utilizare scurtă 2-4 cuvinte per produs-vedetă, în ordine — NU titlul lung>", ...],
 "cta": "<3-5 cuvinte, îndemn + urgență; ex: «Comandă acum, profită azi»>",
 "brand_line": "Comandă pe {brand}",
 "palette": "#hex-accent-energic (roșu/portocaliu de reduceri)"
}}
„labels" trebuie să aibă EXACT {len(heroes)} elemente, în ordine.
GRAMATICĂ RO corectă (diacritice), fără emoji, fără URL, fără ghilimele în texte."""
    import re as _re
    raw = _gemini([{"text": prompt}], want_json=True)
    try:
        j = json.loads(raw)
    except Exception:
        j = json.loads(_re.search(r"\{.*\}", raw, _re.S).group(0))
    labels = j.get("labels", [])
    while len(labels) < len(heroes):
        labels.append(heroes[len(labels)]["title"][:22])
    j["labels"] = labels[:len(heroes)]
    j.setdefault("hook_top", "Azi toate la jumătate")
    j.setdefault("hook_sub", "Peste 200 de oferte")
    j.setdefault("cta", "Comandă acum, profită azi")
    j.setdefault("brand_line", f"Comandă pe {brand}")
    j.setdefault("palette", "#E8392B")
    return j


# ════════════════════════ orchestrare ════════════════════════
def build(prods, brand, out_dir, fmt="9:16", grid_n=9, hero_n=6):
    """`prods` = listă de {title,price,old,pct,img} cu img = cale ABSOLUTĂ.
    Numele fișierului e impus aici: {brandslug}_BENTO_{fmt_cu_x}.mp4."""
    W, H = FMT[fmt]
    brandslug = re.sub(r"[^A-Za-z0-9]", "", brand) or "Brand"
    out_path = Path(out_dir) / f"{brandslug}_BENTO_{fmt.replace(':', 'x')}.mp4"

    grid_prods = prods[:grid_n] if len(prods) >= grid_n else prods
    # heroes = produse cu cea mai mare reducere absolută (old-price) → cele mai „wow"
    heroes = sorted(prods, key=lambda p: (p.get("old") or 0), reverse=True)[:hero_n]

    print(f"● {brand}: {len(prods)} produse → Gemini scrie copy-ul (hook/labels/CTA)...")
    copy = get_copy(brand, prods, heroes)
    accent = copy.get("palette", "#E8392B")
    print(f"  hook: „{copy['hook_top']}» / «{copy['hook_sub']}»  · CTA: „{copy['cta']}»  · paletă {accent}")
    for h, l in zip(heroes, copy["labels"]):
        print(f"    hero: {l}  ({_lei(h['price'])} lei, -{h.get('pct')}%)")

    tmp = Path(tempfile.mkdtemp())
    frames_dir = tmp / "frames"
    frames_dir.mkdir()

    # timeline (~13.5s la 30fps)
    HOOK_S, HERO_S, CTA_S = 3.0, 2.45, 2.6
    idx = 0
    print("  🎬 randez HOOK (grid 3x3 pop-in)...")
    idx = render_hook(grid_prods, copy["hook_top"], copy["hook_sub"], W, H, accent,
                      frames_dir, int(HOOK_S * FPS), idx)
    for hi, (h, lab) in enumerate(zip(heroes, copy["labels"])):
        print(f"  🎬 randez HERO {hi+1}/{len(heroes)} (bento punch-in)...")
        idx = render_hero(h, lab, W, H, accent, frames_dir, int(HERO_S * FPS), idx,
                          slide_dir=(1 if hi % 2 == 0 else -1))
    print("  🎬 randez CTA (grid reformat)...")
    idx = render_cta(grid_prods, copy["cta"], copy["brand_line"], W, H, accent,
                     frames_dir, int(CTA_S * FPS), idx)

    total_dur = idx / FPS
    print(f"  ⏱  {idx} cadre = {total_dur:.1f}s · muzică energică (make_music_deals)...")
    music = make_music_deals(total_dur, str(tmp / "m.wav"))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-framerate", str(FPS), "-i", str(frames_dir / "f%05d.jpg"),
           "-i", music, "-map", "0:v", "-map", "1:a",
           "-af", f"afade=t=out:st={max(total_dur-0.6,0):.2f}:d=0.6", "-shortest",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not out.exists() or out.stat().st_size == 0:
        sys.stderr.write(r.stderr[-1200:] + "\n")
        sys.exit("ffmpeg a eșuat")
    print(f"  ✓ {fmt} → {out}  ({out.stat().st_size//1024} KB, {total_dur:.1f}s)")
    return out


def main():
    ap = argparse.ArgumentParser(description="Concept BENTO/GRID DEALS-DUMP (Ofertele Zilei)")
    ap.add_argument("--storefront", default="", help="domeniu storefront public (ex ofertelezilei.ro) → trage produse + preț")
    ap.add_argument("--manifest", default="", help="JSON cu produse [{title,price,old,pct,img}] (alternativă la --storefront)")
    ap.add_argument("--brand", default="Ofertele Zilei")
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop/pmax-video"))
    ap.add_argument("--fmt", default="9:16", choices=list(FMT))
    ap.add_argument("--n", type=int, default=6, help="câte produse HERO (bento punch-in)")
    a = ap.parse_args()

    GRID_N = 9   # grid-ul e mereu 3x3
    if a.storefront:
        print(f"● trag produse din storefront public: {a.storefront} ...")
        # suficiente pt grid (9) + heroes; ia max(n,9) ca să umpli grid-ul
        outdir = f"/tmp/bento_{re.sub(r'[^a-z0-9]', '', a.storefront.lower())}"
        prods = fetch_storefront(a.storefront, max(a.n, GRID_N), outdir)  # img = căi ABSOLUTE
    elif a.manifest:
        prods = json.load(open(a.manifest))
        base = Path(a.manifest).resolve().parent       # rezolvă img relativ la manifest → absolut
        for p in prods:
            if not Path(p["img"]).is_absolute():
                p["img"] = str(base / p["img"])
    else:
        sys.exit("dă --storefront <domeniu> sau --manifest <json>")

    if len(prods) < 3:
        sys.exit(f"prea puține produse ({len(prods)}) — am nevoie de cel puțin 3")

    build(prods, a.brand, a.out, a.fmt, GRID_N, a.n)


if __name__ == "__main__":
    main()
