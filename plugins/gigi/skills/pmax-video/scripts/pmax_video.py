# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pillow>=10.0", "numpy>=1.24"]
# ///
"""
pmax_video.py — fabrică AUTOMATĂ de video-ad-uri pentru Google Ads Performance Max.

Pipeline (vezi ../METHODOLOGY.md = creierul): poze produs → GEMINI regizează scriptul (Hook-Body-CTA,
captions muted-first, voce brand RO) → FFmpeg montează (Ken Burns + captions mari + ritm pe beat) →
export în formatele PMax (9:16 / 1:1 / 16:9). Veo (image-to-video, „Omni"-style) = add-on `--ai`.

Necesită: ffmpeg, font Poppins/Arial, GEMINI_API_KEY/GOOGLE_AI_API_KEY (KB).

Usage:
  uv run pmax_video.py make --brand Esteban --images /path/la/poze --out /tmp/est --fmt 9:16
  uv run pmax_video.py make --brand Esteban --images ./esteban-creatives --all-formats
"""
from __future__ import annotations
import argparse, base64, glob, json, os, re, subprocess, sys, tempfile
from pathlib import Path
import requests

KB = os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py")
FONTS = ["/Users/gheorghebeschea/Library/Fonts/Poppins-Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
FONT = next((f for f in FONTS if os.path.exists(f)), FONTS[-1])
FMT = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
# oferta REALĂ per magazin (Gemini o folosește exact în CTA, nu inventează). --offer o suprascrie.
BRAND_OFFERS = {"esteban": "2+1 GRATIS", "gt": "2+1 GRATIS", "georgetalent": "2+1 GRATIS",
                "george-talent": "2+1 GRATIS", "nubra": "2+1 GRATIS"}
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-3-pro", "gemini-1.5-flash"]

def secret(k):
    v = os.environ.get(k)
    if v: return v
    try:
        return subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception:
        return ""

def _gemini(parts, want_json=False):
    key = secret("GEMINI_API_KEY") or secret("GOOGLE_AI_API_KEY")
    if not key: raise RuntimeError("lipsește GEMINI_API_KEY/GOOGLE_AI_API_KEY")
    body = {"contents": [{"parts": parts}]}
    if want_json:
        body["generationConfig"] = {"responseMimeType": "application/json"}
    last = ""
    for m in GEMINI_MODELS:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={key}",
                          json=body, timeout=120)
        if r.status_code == 200:
            try:
                return r.json()["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                last = str(r.json())[:160]
        else:
            last = f"{r.status_code}:{r.text[:120]}"
    raise RuntimeError(f"Gemini eșuat ({last})")

VEO_MODEL = "veo-3.0-fast-generate-001"

def veo_clip(image, prompt, aspect, outpath):
    """Veo video (cinematic, audio nativ). image=None → text-to-video (fundal). Async: start+poll+download."""
    import time
    key = secret("GEMINI_API_KEY") or secret("GOOGLE_AI_API_KEY")
    B = "https://generativelanguage.googleapis.com/v1beta"
    inst = {"prompt": prompt}
    if image:
        inst["image"] = {"bytesBase64Encoded": base64.b64encode(Path(image).read_bytes()).decode(), "mimeType": "image/jpeg"}
    r = requests.post(f"{B}/models/{VEO_MODEL}:predictLongRunning?key={key}",
                      json={"instances": [inst], "parameters": {"aspectRatio": aspect, "sampleCount": 1}}, timeout=60).json()
    op = r.get("name")
    if not op:
        raise RuntimeError(f"Veo start: {str(r)[:200]}")
    for _ in range(40):
        time.sleep(10)
        s = requests.get(f"{B}/{op}?key={key}", timeout=60).json()
        if s.get("done"):
            txt = json.dumps(s.get("response", {}))
            m = re.search(r'"uri"\s*:\s*"([^"]+)"', txt)
            if m:
                vu = m.group(1) + ("&" if "?" in m.group(1) else "?") + "key=" + key
                Path(outpath).write_bytes(requests.get(vu, timeout=180).content)
            else:
                bm = re.search(r'"bytesBase64Encoded"\s*:\s*"([^"]+)"', txt)
                if not bm:
                    raise RuntimeError("Veo: fără video în răspuns")
                Path(outpath).write_bytes(base64.b64decode(bm.group(1)))
            return outpath
    raise RuntimeError("Veo timeout")

def collect_images(path, limit=8):
    p = Path(path)
    imgs = sorted([str(f) for f in p.rglob("*") if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")])
    # filtrează imagini prea mici (icoane/logo)
    from PIL import Image
    good = []
    for f in imgs:
        try:
            w, h = Image.open(f).size
            if min(w, h) >= 500: good.append(f)
        except Exception:
            pass
    return good[:limit]

def fetch_shopify_images(store_key, n, outdir):
    """Trage poze CURATE de produs din Shopify (featuredImage, best-selling) — reutilizează shopify_lib.Store."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shopify-seo" / "scripts"))
    from shopify_lib import Store
    st = Store(store_key)
    q = """{ products(first: 40, sortKey: UPDATED_AT, reverse: true, query: "status:active") {
              edges { node { title featuredImage { url } } } } }"""
    data = st.gql(q)
    urls, seen = [], set()
    for e in data.get("products", {}).get("edges", []):
        fi = (e["node"] or {}).get("featuredImage") or {}
        u = fi.get("url")
        if u and u not in seen:
            seen.add(u); urls.append(u)
    out = Path(outdir); out.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    import io
    paths = []
    for i, u in enumerate(urls):
        if len(paths) >= n: break
        try:
            b = requests.get(u, timeout=30).content
            im = Image.open(io.BytesIO(b)).convert("RGB")
            if min(im.size) < 500: continue
            p = out / f"prod_{i}.jpg"; im.save(p, "JPEG", quality=92)
            paths.append(str(p))
        except Exception:
            continue
    return paths

def img_part(path, max_px=768):
    from PIL import Image
    import io
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_px, max_px))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=80)
    return {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}}

def direct(brand, images, offer=""):
    """Gemini = regizor: vede pozele + brandul → script JSON (Hook-Body-CTA, captions muted-first)."""
    meth = (Path(__file__).resolve().parents[1] / "METHODOLOGY.md")
    rules = meth.read_text()[:3500] if meth.exists() else ""
    offer_rule = (f"OFERTA REALĂ a brandului: „{offer}”. Folosește EXACT această ofertă în CTA - NU inventa altă reducere/ofertă."
                  if offer else "NU inventa o ofertă/reducere specifică (ex. procent, transport gratis) dacă nu o știi — CTA = doar acțiune + urgență.")
    prompt = f"""Ești director de creative pe performance ads pentru brandul „{brand}" (ecommerce RO).
Ai {len(images)} poze de produs (în ordine, index 0..{len(images)-1}). Fă scriptul unui video-ad de ~15s
pentru Google Ads Performance Max + YouTube Shorts, care CONVERTEȘTE, după metodologia:

{rules}

Răspunde STRICT JSON:
{{
 "scenes": [
   {{"image": <index poză>, "caption": "<text MARE pe ecran, 3-6 cuvinte, RO>", "dur": <secunde 2-3>, "role": "hook|body|cta"}}
 ],
 "hero": <index poza cu O SINGURĂ sticlă/produs, curată, etichetă corectă/lizibilă — pt decupaj>,
 "palette": "#hexculoare-accent-brand",
 "music_vibe": "<2-3 cuvinte, ex: upbeat modern>"
}}
Reguli: prima scenă = HOOK (oprește scroll-ul, claim/beneficiu tare). Ultima = CTA = acțiune + urgență (acum/azi). {offer_rule}
NU pune URL/domeniu în CTA (clientul ajunge pe site dacă dă click pe reclamă) — CTA = doar acțiune + ofertă.
Captions = BENEFICII / emoție / ofertă care VÂND. NU copia text de pe etichetă (ex. „Essential", numele sau codul produsului) — alea NU sunt captions bune.
FIECARE caption = O SINGURĂ idee / propoziție COMPLETĂ, care încape pe 1 rând (max 2). NU înghesui două propoziții în același caption (ex. „Îți dorești mai mult? E rândul tău!" e GREȘIT — alege UNA). NU tăia o propoziție între scene (scena 1 „Parfumul tău," + scena 2 „semnătura ta." = GREȘIT). Preferă fraze de 3-5 cuvinte care stau pe un singur rând.
5-7 scene, total ~15s. Captions scurte (3-6 cuvinte), înțelese pe MUT, fără emoji, fără URL. Alege pozele cele mai bune pt fiecare moment.
GRAMATICĂ: scrie CORECT românește, fără greșeli (ex. „cadoul", NU „cadouul"; diacritice corecte)."""
    parts = [{"text": prompt}] + [img_part(p) for p in images]
    raw = _gemini(parts, want_json=True)
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0))

def _hex(h, a=255):
    h = (h or "#FFFFFF").lstrip("#")
    try:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4)) + (a,)
    except Exception:
        return (255, 255, 255, a)

# conectori RO care NU trebuie să rămână la capăt de rând (ar „întrerupe" propoziția urât)
_CONNECTORS = {"e", "și", "si", "cu", "la", "de", "în", "in", "pe", "un", "o", "să", "sa", "ori",
               "sau", "dar", "iar", "ca", "că", "din", "prin", "spre", "ce", "îți", "iti", "ți",
               "ti", "te", "mi", "ne", "vă", "va", "al", "ai", "a", "îl", "il", "se", "mai", "fără", "fara"}

def _balance_clause(part, d, font, max_w):
    """Împarte o frază (care nu încape pe un rând) în 2 rânduri ECHILIBRATE, fără orfani/conectori la capăt."""
    words = part.split()
    if len(words) <= 1:
        return [part]
    best = None
    for i in range(1, len(words)):
        l1, l2 = " ".join(words[:i]), " ".join(words[i:])
        w1, w2 = d.textlength(l1, font=font), d.textlength(l2, font=font)
        if w1 > max_w or w2 > max_w:
            continue
        score = abs(w1 - w2)                                   # vrem rânduri egale
        if re.search(r"[,;:]$", words[i - 1]):                 # rupere după virgulă = naturală
            score -= max_w * 0.5
        if words[i - 1].strip(",.;:!?").lower() in _CONNECTORS: # conector orfan la capăt de rând = urât
            score += max_w * 0.7
        if len(words[i:]) == 1 and len(words[i].strip(",.;:!?")) <= 3:  # ultim rând = un cuvânt scurt
            score += max_w * 0.35
        if best is None or score < best[0]:
            best = (score, [l1, l2])
    if best:
        return best[1]
    # nimic nu încape în 2 → greedy multi-rând (fallback extrem)
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur); cur = w
    if cur:
        lines.append(cur)
    return lines

def _wrap_caption(text, d, font, max_w):
    """Wrap CONȘTIENT DE PROPOZIȚIE: întâi rupe pe frază (?/!/.), apoi echilibrează fraza lată. Nu taie propoziții."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?…])\s+", text) if p.strip()]
    lines = []
    for part in parts:
        if d.textlength(part, font=font) <= max_w:
            lines.append(part)
        else:
            lines.extend(_balance_clause(part, d, font, max_w))
    return lines

def make_caption_png(text, W, H, accent, path, fmt):
    """Caption muted-first: text mare alb cu contur, pe box semi-transparent, jos (margine safe pe 9:16)."""
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    max_w = W * 0.86
    fs = int(H * 0.040)
    # auto-shrink: scade fontul până captionul încape în ≤2 rânduri (fără să spargă propoziții)
    while fs > int(H * 0.030):
        font = ImageFont.truetype(FONT, fs)
        lines = _wrap_caption(text, d, font, max_w)
        if len(lines) <= 2:
            break
        fs -= 2
    font = ImageFont.truetype(FONT, fs)
    lines = _wrap_caption(text, d, font, max_w)
    line_h = int(fs * 1.22)
    block_h = line_h * len(lines)
    bottom_margin = int(H * (0.20 if fmt == "9:16" else 0.10))
    y0 = H - bottom_margin - block_h
    maxw = max((d.textlength(l, font=font) for l in lines), default=0)
    pad = int(fs * 0.45)
    bx0, bx1 = (W - maxw) / 2 - pad, (W + maxw) / 2 + pad
    d.rounded_rectangle([bx0, y0 - pad, bx1, y0 + block_h + pad // 2], radius=int(fs * 0.4), fill=(0, 0, 0, 120))
    # linie accent brand sub box
    d.rounded_rectangle([bx0, y0 + block_h + pad // 2 + 6, bx1, y0 + block_h + pad // 2 + 14], radius=4, fill=_hex(accent))
    for i, l in enumerate(lines):
        lw = d.textlength(l, font=font)
        d.text(((W - lw) / 2, y0 + i * line_h), l, font=font, fill="white",
               stroke_width=max(2, fs // 22), stroke_fill=(0, 0, 0, 235))
    img.save(path)

def render_format(script, images, fmt, outdir, music=None):
    W, H = FMT[fmt]
    fps = 30
    tmp = Path(tempfile.mkdtemp())
    accent = script.get("palette", "#FFFFFF")
    clips = []
    for i, sc in enumerate(script["scenes"]):
        img = images[min(sc.get("image", i), len(images) - 1)]
        dur = max(2, min(4, sc.get("dur", 3)))
        frames = int(dur * fps)
        cap_png = tmp / f"cap{i}.png"
        make_caption_png(sc.get("caption", ""), W, H, accent, str(cap_png), fmt)
        clip = tmp / f"c{i}.mp4"
        fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"zoompan=z='min(zoom+0.0012,1.18)':d={frames}:s={W}x{H}:fps={fps}[bg];"
              f"[bg][1:v]overlay=0:0:format=auto[v]")
        r = subprocess.run(["ffmpeg", "-y", "-loop", "1", "-i", img, "-loop", "1", "-i", str(cap_png),
                            "-t", str(dur), "-filter_complex", fc, "-map", "[v]",
                            "-r", str(fps), "-pix_fmt", "yuv420p", "-an", str(clip)], capture_output=True, text=True)
        if not clip.exists() or clip.stat().st_size == 0:
            sys.stderr.write(f"[scene {i}] ffmpeg fail: {r.stderr[-300:]}\n")
        clips.append(clip)
    # concat
    lst = tmp / "list.txt"; lst.write_text("\n".join(f"file '{c}'" for c in clips))
    out = Path(outdir) / f"{script.get('_brand','ad')}_{fmt.replace(':','x')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst)]
    if music and os.path.exists(music):
        cmd += ["-i", music, "-shortest", "-c:a", "aac", "-map", "0:v", "-map", "1:a"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, capture_output=True)
    return out

VEO_PROMPTS = [
    "Cinematic luxury product commercial. Keep the EXACT same product and its printed label/branding "
    "completely unchanged, faithful and legible — do NOT add extra products, do NOT alter any text on "
    "the bottle. Slow elegant camera push-in, soft premium studio lighting, glossy glass reflections, "
    "tiny golden particles floating, shallow depth of field, smooth high-end motion. Fill the entire "
    "vertical frame edge to edge, no borders.",
    "Cinematic luxury product commercial, SAME product and SAME exact label as the image (faithful, "
    "unchanged text). Slow graceful orbit/parallax around the single product, dramatic rim lighting, "
    "reflective surface, elegant dark background, subtle bokeh, premium motion. Fill the whole vertical frame.",
]

def pad_to_aspect(image, W, H, accent, path):
    """Umple poza la formatul țintă: fundal blur (din produs) întunecat + produsul clar centrat → fără bare negre."""
    from PIL import Image, ImageFilter, ImageEnhance
    src = Image.open(image).convert("RGB")
    sc = max(W / src.width, H / src.height)
    bg = src.resize((int(src.width * sc) + 2, int(src.height * sc) + 2))
    l, t = (bg.width - W) // 2, (bg.height - H) // 2
    bg = bg.crop((l, t, l + W, t + H)).filter(ImageFilter.GaussianBlur(46))
    bg = ImageEnhance.Brightness(bg).enhance(0.45)
    fg = src.copy(); fg.thumbnail((int(W * 0.92), int(H * 0.66)))
    bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
    bg.save(path, "JPEG", quality=92)
    return path

def cutout_product(image, outpath):
    """Decupaj produs cu rembg (model ML isnet — bun pe sticlă/glass). Fallback: flood-fill."""
    rv = Path(__file__).resolve().parent / ".rembg" / "bin" / "python"
    cut = Path(__file__).resolve().parents[2] / "ad-banners" / "scripts" / "cutout.py"
    if rv.exists() and cut.exists():
        subprocess.run([str(rv), str(cut), "--src", str(image), "--out", str(outpath), "--model", "isnet-general-use"],
                       capture_output=True)
        if Path(outpath).exists() and Path(outpath).stat().st_size > 1000:
            return outpath
    return _cutout_floodfill(image, outpath)

def _cutout_floodfill(image, outpath):
    """Fallback fără rembg: flood-fill din margini pe fundal deschis-uniform (slab pe glass)."""
    from PIL import Image
    from collections import deque
    import numpy as np
    a = np.array(Image.open(image).convert("RGBA"))
    r, g, b = a[:, :, 0].astype(int), a[:, :, 1].astype(int), a[:, :, 2].astype(int)
    mx = np.maximum(np.maximum(r, g), b); mn = np.minimum(np.minimum(r, g), b)
    bg = (mx > 188) & ((mx - mn) < 32)
    H, W = bg.shape; seen = np.zeros_like(bg); dq = deque()
    for x in range(W):
        for y in (0, H - 1):
            if bg[y, x] and not seen[y, x]: seen[y, x] = 1; dq.append((y, x))
    for y in range(H):
        for x in (0, W - 1):
            if bg[y, x] and not seen[y, x]: seen[y, x] = 1; dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W and not seen[ny, nx] and bg[ny, nx]:
                seen[ny, nx] = 1; dq.append((ny, nx))
    a[:, :, 3] = np.where(seen, 0, 255)
    im = Image.fromarray(a)
    im = im.crop(im.getbbox())  # taie marginile goale
    im.save(outpath)
    return outpath

VEO_BG_PROMPT = ("Abstract dark luxury backdrop, EMPTY scene, absolutely no objects, no bottle, no product, "
                 "no text, no people. Only slowly drifting golden light particles and soft dust, gentle "
                 "volumetric light rays through darkness, subtle warm bokeh, elegant black background, "
                 "smooth slow cinematic camera drift. Pure atmospheric background plate.")

def make_music(duration, path, vibe=""):
    """Bed muzical continuu sintetizat (pad cald + arpegiu) — fără cheie externă. WAV."""
    import numpy as np, wave
    sr = 44100; N = int(sr * duration); t = np.arange(N) / sr
    def note(f, t0, t1, amp):
        seg = (t >= t0) & (t < t1)
        env = np.where(seg, np.clip((t - t0) / 0.4, 0, 1) * np.clip((t1 - t) / 0.7, 0, 1), 0.0)
        w = np.sin(2*np.pi*f*t) + 0.5*np.sin(2*np.pi*f*1.005*t) + 0.35*np.sin(2*np.pi*f*0.5*t)
        return amp * env * w
    audio = np.zeros(N)
    chords = [[220.0, 261.63, 329.63], [174.61, 220.0, 261.63], [261.63, 329.63, 392.0], [196.0, 246.94, 293.66]]
    bar = duration / max(1, round(duration / 3.6))
    for i in range(int(np.ceil(duration / bar))):
        ch = chords[i % 4]; t0 = i*bar; t1 = min((i+1)*bar, duration)
        for f in ch: audio += note(f, t0, t1, 0.15)
        for k, f in enumerate([ch[0]*2, ch[2]*2, ch[1]*2, ch[2]*4]):
            ts = t0 + k*bar/4; audio += note(f, ts, ts + bar/4, 0.045)
    audio /= (np.max(np.abs(audio)) + 1e-6); audio *= 0.82
    fi, fo = int(sr*0.6), int(sr*0.9)
    audio[:fi] *= np.linspace(0, 1, fi); audio[-fo:] *= np.linspace(1, 0, fo)
    pcm = (audio * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())
    return path

def make_glow(W, H, accent, path):
    """Halou radial cald în spatele produsului → produsul „pop", fără spațiu mort."""
    from PIL import Image, ImageFilter
    import numpy as np
    cx, cy, rad = W / 2, H * 0.42, H * 0.38
    yy, xx = np.ogrid[:H, :W]
    al = (np.clip(1 - np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rad, 0, 1) ** 1.7) * 115
    ac = _hex(accent)
    arr = np.zeros((H, W, 4), np.uint8)
    arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = ac[0], ac[1], ac[2]
    arr[:, :, 3] = al.astype(np.uint8)
    Image.fromarray(arr).filter(ImageFilter.GaussianBlur(42)).save(path)
    return path

def make_shadow(W, H, cy_frac, path):
    """Umbră moale (elipsă blurată) sub produs → grounded, nu plutește."""
    from PIL import Image, ImageDraw, ImageFilter
    s = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cx, cy = W // 2, int(H * cy_frac)
    ew, eh = int(W * 0.46), int(H * 0.045)
    ImageDraw.Draw(s).ellipse([cx - ew // 2, cy - eh // 2, cx + ew // 2, cy + eh // 2], fill=(0, 0, 0, 170))
    s.filter(ImageFilter.GaussianBlur(32)).save(path)
    return path

def render_faithful(script, images, fmt, outdir):
    """FIDEL + cinematic: produs REAL decupat (etichetă păstrată) peste fundal Veo abstract (mișcare + audio) + captions."""
    W, H = FMT[fmt]
    aspect = fmt if fmt in ("9:16", "16:9") else "9:16"
    tmp = Path(tempfile.mkdtemp())
    accent = script.get("palette", "#FFFFFF")
    from PIL import Image as _PI
    hero = max(images, key=lambda p: (_PI.open(p).size[1] / max(_PI.open(p).size[0], 1)))  # cea mai portret = sticlă unică curată
    prod = cutout_product(hero, str(tmp / "prod.png"))
    print("   Veo fundal abstract (text-to-video, ~40s)...")
    bg = veo_clip(None, VEO_BG_PROMPT, aspect, str(tmp / "bg.mp4"))
    DUR = 15
    # captions timed
    scenes = script["scenes"]; tot = sum(max(2, min(4, s.get("dur", 3))) for s in scenes)
    caps = []; t = 0.0
    for i, sc in enumerate(scenes):
        d = max(2, min(4, sc.get("dur", 3))) / tot * DUR
        cp = tmp / f"cap{i}.png"; make_caption_png(sc.get("caption", ""), W, H, accent, str(cp), fmt)
        caps.append((str(cp), t, min(t + d, DUR))); t += d
    # muzică reală (bed continuu) în loc de ambient-ul slab Veo
    music = make_music(DUR, str(tmp / "music.wav"), script.get("music_vibe", ""))
    ph = int(H * 0.56)
    yoff = int(H * 0.10) if fmt == "9:16" else int(H * 0.02)
    base_frac = ((H - ph) / 2 - yoff + ph + H * 0.012) / H   # baza produsului → poziția umbrei
    glow = make_glow(W, H, accent, str(tmp / "glow.png"))
    shadow = make_shadow(W, H, base_frac, str(tmp / "shadow.png"))
    # compose: bg(pan) → glow → umbră → produs(plutire) → captions
    inputs = ["-stream_loop", "-1", "-i", bg, "-loop", "1", "-i", glow, "-loop", "1", "-i", shadow, "-loop", "1", "-i", prod]
    for c, _, _ in caps:
        inputs += ["-loop", "1", "-i", c]
    inputs += ["-i", music]
    music_idx = 4 + len(caps)
    SW, SH = int(W * 1.14), int(H * 1.14)
    fc = (f"[0:v]scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={W}:{H}:"
          f"x='(in_w-{W})/2+(in_w-{W})/2.5*sin(0.25*t)':y='(in_h-{H})/2+(in_h-{H})/2.5*sin(0.17*t)',setpts=PTS-STARTPTS[bg];"
          f"[bg][1:v]overlay=0:0[g];[g][2:v]overlay=0:0[s];"
          f"[3:v]scale=-1:{ph}[p];"
          f"[s][p]overlay=(W-w)/2:'(H-h)/2-{yoff}+30*sin(1.2*t)'[v0]")
    prev = "v0"
    for i, (_, s, e) in enumerate(caps):
        idx = 4 + i
        fc += f";[{prev}][{idx}:v]overlay=0:0:enable='between(t,{s:.2f},{e:.2f})'[v{i+1}]"
        prev = f"v{i+1}"
    out = Path(outdir) / f"{script.get('_brand','ad')}_FIDEL_{fmt.replace(':','x')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", f"[{prev}]", "-map", f"{music_idx}:a",
                    "-t", str(DUR), "-af", f"afade=t=in:st=0:d=0.6,afade=t=out:st={DUR-0.9}:d=0.9", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(out)],
                   capture_output=True)
    return out

def render_ai(script, images, fmt, outdir, n=2):
    """CINEMATIC: clipuri Veo (produs în mișcare + audio) pe baza ACELEIAȘI poze (coerență), pre-umplute la
    format (fără bare negre), + captions timed + bed muzical continuu."""
    W, H = FMT[fmt]
    aspect = fmt if fmt in ("9:16", "16:9") else "9:16"
    tmp = Path(tempfile.mkdtemp())
    accent = script.get("palette", "#FFFFFF")
    padded = pad_to_aspect(images[0], W, H, accent, str(tmp / "src.jpg"))   # aceeași sursă → coerență
    veos = []
    for i in range(n):
        print(f"   Veo clip {i+1}/{n} (image-to-video, ~40s)...")
        veos.append(veo_clip(padded, VEO_PROMPTS[i % len(VEO_PROMPTS)], aspect, str(tmp / f"veo{i}.mp4")))
    cropf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setpts=PTS-STARTPTS"
    base = tmp / "base.mp4"
    if len(veos) >= 2:
        fc = (f"[0:v]{cropf}[a];[1:v]{cropf}[b];[a][b]xfade=transition=fade:duration=0.7:offset=7.3[v]")
        subprocess.run(["ffmpeg", "-y", "-i", veos[0], "-i", veos[1], "-filter_complex", fc,
                        "-map", "[v]", "-an", "-pix_fmt", "yuv420p", str(base)], capture_output=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-i", veos[0], "-vf", cropf, "-an", "-pix_fmt", "yuv420p", str(base)], capture_output=True)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(base)],
                               capture_output=True, text=True).stdout.strip() or 15)
    # captions timed + bed muzical continuu (audio din clip0, în buclă, fade la final)
    scenes = script["scenes"]; tot = sum(max(2, min(4, s.get("dur", 3))) for s in scenes)
    inputs = ["-i", str(base)]; filt = ""; prev = "0:v"; t = 0.0; idx = 1
    for i, sc in enumerate(scenes):
        d = max(2, min(4, sc.get("dur", 3))) / tot * dur
        cp = tmp / f"cap{i}.png"; make_caption_png(sc.get("caption", ""), W, H, accent, str(cp), fmt)
        inputs += ["-i", str(cp)]
        filt += f"[{prev}][{idx}:v]overlay=0:0:enable='between(t,{t:.2f},{min(t+d,dur):.2f})'[o{i}];"
        prev = f"o{i}"; t += d; idx += 1
    filt = filt.rstrip(";")
    aud_idx = idx  # bed audio = input următor (clip0, loop)
    out = Path(outdir) / f"{script.get('_brand','ad')}_AI_{fmt.replace(':','x')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", *inputs, "-stream_loop", "-1", "-i", veos[0],
                    "-filter_complex", filt, "-map", f"[{prev}]", "-map", f"{aud_idx}:a",
                    "-af", f"afade=t=out:st={max(dur-0.9,0):.2f}:d=0.9", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(out)],
                   capture_output=True)
    return out

def cmd_make(a):
    if a.store and not a.images:
        print(f"● trag poze curate din Shopify (store={a.store})...")
        images = fetch_shopify_images(a.store, 8, f"/tmp/pmax_src_{a.store}")
    else:
        images = collect_images(a.images)
    if len(images) < 3:
        sys.exit(f"prea puține imagini bune ({len(images)}) — dă --images <dir> sau --store <key>")
    print(f"● {a.brand}: {len(images)} imagini → Gemini regizează...")
    offer = a.offer or BRAND_OFFERS.get((a.store or "").lower(), "")
    if offer:
        print(f"  ofertă: {offer}")
    script = direct(a.brand, images, offer)
    script["_brand"] = re.sub(r"[^A-Za-z0-9]", "", a.brand)
    print(f"  script: {len(script['scenes'])} scene · paletă {script.get('palette')} · muzică „{script.get('music_vibe')}\"")
    for s in script["scenes"]:
        print(f"    [{s.get('role','?'):4}] {s.get('dur')}s  „{s.get('caption')}\"")
    fmts = list(FMT) if a.all_formats else [a.fmt]
    outs = []
    for f in fmts:
        if a.ai:
            print(f"  🎬 {f}: produs real decupat + fundal Veo (fidel + cinematic)...")
            out = render_faithful(script, images, f, a.out)
        else:
            out = render_format(script, images, f, a.out, a.music)
        outs.append(out); print(f"  ✓ {f} → {out}")
    if a.open and outs:
        subprocess.run(["open", str(outs[0].parent)])   # deschide folderul cu videourile
        subprocess.run(["open", str(outs[0])])           # + redă primul

# ════════════════ MOD „DEALS" — montaj rapid multi-produs (Ofertele Zilei & co) ════════════════
# Ieftin: FFmpeg-first (poze reale storefront + carduri PIL + zoompan + muzică sintetizată). Veo = accent opțional.

def _lei(p):
    if p is None:
        return ""
    s = f"{float(p):.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")

def fetch_storefront(domain, n, outdir):
    """Trage produse din storefront-ul PUBLIC Shopify (/products.json, fără auth) → poze + preț + reducere."""
    import urllib.request
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
        fn = os.path.join(outdir, f"p{len(out):02d}.jpg")
        try:
            req2 = urllib.request.Request(imgs[0].split("?")[0], headers={"User-Agent": "Mozilla/5.0"})
            b = urllib.request.urlopen(req2, timeout=25).read()
            if len(b) < 8000:
                continue
            open(fn, "wb").write(b)
        except Exception:
            continue
        out.append({"title": x.get("title", "").strip(), "price": float(p) if p else None,
                    "old": old, "pct": pct, "img": fn, "handle": x.get("handle")})
        if len(out) >= n:
            break
    return out

def _trim_product(path):
    """Taie marginile uniforme (alb/fundal) → produsul umple cardul (pozele Shopify au mult spațiu gol)."""
    from PIL import Image
    import numpy as np
    im = Image.open(path).convert("RGB")
    arr = np.asarray(im).astype(int)
    H, W = arr.shape[:2]
    corners = [arr[0, 0], arr[0, W - 1], arr[H - 1, 0], arr[H - 1, W - 1]]
    bgc = np.median(np.array(corners), axis=0)
    dist = np.abs(arr - bgc).sum(2)
    mask = dist > 42
    frac = mask.mean()
    if frac < 0.02 or frac > 0.96:      # fundal ne-uniform (lifestyle) sau gol → nu tăia
        return im.convert("RGBA")
    ys, xs = np.where(mask)
    pad = int(min(H, W) * 0.02)
    x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + pad)
    y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + pad)
    return im.crop((x0, y0, x1, y1)).convert("RGBA")

def _fit_font(text, d, max_w, base_fs, min_fs, max_lines=2):
    """Returnează (font, lines) cu cel mai mare font la care textul încape în max_lines × max_w."""
    from PIL import ImageFont
    fs = base_fs
    while fs > min_fs:
        f = ImageFont.truetype(FONT, fs)
        ls = _wrap_caption(text, d, f, max_w)
        if len(ls) <= max_lines:
            return f, ls
        fs -= 2
    f = ImageFont.truetype(FONT, min_fs)
    return f, _wrap_caption(text, d, f, max_w)[:max_lines]

def make_deal_card(prod, top_text, role, W, H, accent, fmt, base):
    """Întoarce 2 straturi: (poză = fundal+produs, pt ZOOM) + (overlay text STATIC = titlu/burst/preț).
    Așa zoom-ul mișcă doar poza, textul rămâne fix."""
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    import numpy as np, math
    ac = _hex(accent)
    # fundal: gradient diagonal accent→închis + glow radial cald în spatele produsului (nu plat/mort)
    yy, xx = np.ogrid[:H, :W]
    diag = (xx / W * 0.5 + yy / H * 0.5)                       # 0 (stânga-sus) → 1 (dreapta-jos)
    cx, cy, rad = W / 2, H * 0.46, max(W, H) * 0.55
    glow = np.clip(1 - np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / rad, 0, 1) ** 1.6
    bg = np.zeros((H, W, 3), np.uint8)
    for k in range(3):
        lvl = ac[k] * (0.50 - 0.45 * diag)                    # accent care se închide adânc pe diagonală
        bg[:, :, k] = np.clip(lvl + ac[k] * 0.33 * glow + 5, 0, 255).astype(np.uint8)
    img = Image.fromarray(bg, "RGB").convert("RGBA")
    # produs MARE (trim margini albe → scale-to-fill, permite upscale), cu umbră moale grounded
    pr = _trim_product(prod["img"])
    box_w, box_h = int(W * 0.94), int(H * (0.62 if fmt != "16:9" else 0.55))
    sc = min(box_w / pr.width, box_h / pr.height)
    pr = pr.resize((max(1, int(pr.width * sc)), max(1, int(pr.height * sc))), Image.LANCZOS)
    px, py = (W - pr.width) // 2, int(H * (0.225 if fmt != "16:9" else 0.17))
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([px + pr.width * 0.10, py + pr.height - 18, px + pr.width * 0.90, py + pr.height + 40],
                               fill=(0, 0, 0, 165))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(28)))
    img.alpha_composite(pr, (px, py))
    # ── STRAT 1: poza (fundal+produs) — se va ZOOM-a ──
    bg_path = base + "_bg.jpg"
    img.convert("RGB").save(bg_path, "JPEG", quality=92)

    # ── STRAT 2: overlay text STATIC (titlu + burst + badge preț) — NU se mișcă ──
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bn = np.zeros((int(H * 0.20), W, 4), np.uint8)
    bn[:, :, 3] = np.linspace(160, 0, bn.shape[0]).astype(np.uint8)[:, None]
    ov.alpha_composite(Image.fromarray(bn, "RGBA"), (0, 0))
    bnb = np.zeros((int(H * 0.26), W, 4), np.uint8)              # bandă jos pt preț
    bnb[:, :, 3] = np.linspace(0, 150, bnb.shape[0]).astype(np.uint8)[:, None]
    ov.alpha_composite(Image.fromarray(bnb, "RGBA"), (0, H - bnb.shape[0]))
    d = ImageDraw.Draw(ov)
    # titlu/hook sus — auto-shrink, full width, max 2 rânduri
    base_fs = int(H * (0.056 if role != "product" else 0.046))
    ftop, lines = _fit_font(top_text, d, W * 0.90, base_fs, int(H * 0.034), 2)
    lh = int(ftop.size * 1.14)
    ty = int(H * 0.052)
    for ln in lines:
        lw = d.textlength(ln, font=ftop)
        d.text(((W - lw) / 2, ty), ln, font=ftop, fill="white",
               stroke_width=max(3, ftop.size // 15), stroke_fill=(0, 0, 0, 240))
        ty += lh
    # burst „-X%" colț stânga-sus al produsului (poziție fixă, sub titlu)
    pct = prod.get("pct")
    if pct:
        bs = int(H * 0.066)
        bxc, byc = int(px + bs * 0.8), int(max(py, H * 0.20) + bs * 0.8)
        burst = Image.new("RGBA", (bs * 2, bs * 2), (0, 0, 0, 0))
        bd = ImageDraw.Draw(burst)
        pts = []
        for i in range(20):
            ang = math.pi * i / 10 - 0.15
            r = bs if i % 2 == 0 else bs * 0.76
            pts.append((bs + r * math.cos(ang), bs + r * math.sin(ang)))
        bd.polygon(pts, fill=(235, 30, 35, 255), outline=(255, 255, 255, 255))
        fb = ImageFont.truetype(FONT, int(bs * 0.5))
        bt = f"-{pct}%"
        bw = bd.textlength(bt, font=fb)
        bd.text((bs - bw / 2, bs - bs * 0.28), bt, font=fb, fill="white", stroke_width=2, stroke_fill=(0, 0, 0, 220))
        ov.alpha_composite(burst, (bxc - bs, byc - bs))
    # badge preț jos: preț vechi tăiat + accent în spate la prețul nou
    p, old = prod.get("price"), prod.get("old")
    by = int(H * 0.85)
    newtxt = f"{_lei(p)} lei"
    fs_new = int(H * 0.064)
    fnew = ImageFont.truetype(FONT, fs_new)
    fs_old = int(H * 0.030)
    fold = ImageFont.truetype(FONT, fs_old)
    oldtxt = f"{_lei(old)} lei" if old else ""
    new_w = d.textlength(newtxt, font=fnew)
    old_w = d.textlength(oldtxt, font=fold) if oldtxt else 0
    gap = int(W * 0.028)
    total_w = new_w + (old_w + gap if oldtxt else 0)
    bx = (W - total_w) / 2
    pad = int(fs_new * 0.42)
    d.rounded_rectangle([bx - pad, by - pad, bx + total_w + pad, by + fs_new + pad], radius=int(fs_new * 0.42),
                        fill=(0, 0, 0, 180))
    xcur = bx
    if oldtxt:
        oy = by + (fs_new - fs_old)
        d.text((xcur, oy), oldtxt, font=fold, fill=(205, 205, 205, 255))
        d.line([xcur, oy + fs_old * 0.55, xcur + old_w, oy + fs_old * 0.55], fill=(245, 70, 70, 255),
               width=max(3, fs_old // 9))
        xcur += old_w + gap
    d.rounded_rectangle([xcur - pad // 2, by - pad // 2, xcur + new_w + pad // 2, by + fs_new + pad // 2],
                        radius=int(fs_new * 0.3), fill=(ac[0], ac[1], ac[2], 255))
    d.text((xcur, by), newtxt, font=fnew, fill="white", stroke_width=max(2, fs_new // 22), stroke_fill=(0, 0, 0, 235))
    ov_path = base + "_ov.png"
    ov.save(ov_path)
    return bg_path, ov_path

def make_music_deals(duration, path):
    """Bed energic sintetizat (kick four-on-the-floor + bas + arpegiu bright) — gratis, WAV."""
    import numpy as np, wave
    sr = 44100; N = int(sr * duration); t = np.arange(N) / sr
    audio = np.zeros(N)
    bpm = 124; spb = 60.0 / bpm
    # kick: thump sinus cu pitch-drop
    for i in range(int(duration / spb)):
        t0 = i * spb; seg = (t >= t0) & (t < t0 + 0.18); ts = t[seg] - t0
        f = 120 * np.exp(-ts * 28) + 45
        audio[seg] += 0.55 * np.sin(2 * np.pi * f * ts) * np.exp(-ts * 16)
    # bas + arpegiu pe acorduri
    chords = [[55.0, 110.0, 164.81], [49.0, 98.0, 146.83], [65.41, 130.81, 196.0], [73.42, 146.83, 220.0]]
    bar = spb * 4
    for i in range(int(np.ceil(duration / bar))):
        ch = chords[i % 4]; t0 = i * bar
        seg = (t >= t0) & (t < t0 + bar); ts = t[seg] - t0
        audio[seg] += 0.16 * np.sin(2 * np.pi * ch[0] * ts) * np.clip(1 - ts / bar, 0, 1)
        for k in range(8):
            f = ch[1 + (k % 2)] * 2; a0 = t0 + k * bar / 8
            s2 = (t >= a0) & (t < a0 + bar / 8); tt = t[s2] - a0
            audio[s2] += 0.07 * np.sin(2 * np.pi * f * tt) * np.exp(-tt * 7)
    audio /= (np.max(np.abs(audio)) + 1e-6); audio *= 0.85
    fi, fo = int(sr * 0.05), int(sr * 0.6)
    audio[:fi] *= np.linspace(0, 1, fi); audio[-fo:] *= np.linspace(1, 0, fo)
    pcm = (audio * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(pcm.tobytes())
    return path

def direct_deals(brand, products, offer=""):
    """Gemini-flash (ieftin, text-only) = copywriter montaj deals: hook + label scurt/produs + CTA. Prețurile NU le inventează (vin din manifest)."""
    items = "\n".join(f"{i}. {p['title']} — {_lei(p.get('price'))} lei"
                      + (f" (vechi {_lei(p.get('old'))}, -{p.get('pct')}%)" if p.get('pct') else "")
                      for i, p in enumerate(products))
    offer_line = f"Oferta brandului (folosește-o în hook/CTA): „{offer}”." if offer else "Reducerile reale sunt pe fiecare produs (vezi -%)."
    prompt = f"""Ești copywriter de reclame video short-form pentru magazinul de OFERTE „{brand}" (e-commerce RO, COD).
Produse (cu prețuri REALE — NU le schimba, NU inventa altele):
{items}

Scrie un script de montaj rapid „oferta zilei" (~15s, ritm de TikTok/Reels), care VINDE. {offer_line}

HOOK = cel mai important (primele 3s decid ~71% din vizionare). NU striga doar reducerea („-50% azi") — reducerea se vede oricum pe badge. Hook-ul TREBUIE să oprească scroll-ul prin una din formulele (alege cea mai potrivită pe produsele de mai sus):
- CURIOZITATE: „De ce cumpără toți asta?", „N-am crezut până am încercat"
- PROBLEMĂ/durere: „Te-ai săturat de {{durere}}?", „Gata cu {{bătaie de cap}}"
- ȘOC DE PREȚ concret: „Asta? Doar 59 lei.", „59 lei și gata problema"
- LISTĂ/FOMO: „5 chestii sub 70 lei care zboară azi", „Top oferte — se termină azi"
- ADRESARE DIRECTĂ: „Stai! Vezi asta înainte să pleci"
- CLAIM ÎNDRĂZNEȚ: „Cei mai bine dați 60 de lei"
Hook concret, specific produselor reale, 3-7 cuvinte, RO natural (nu robotic).

Răspunde STRICT JSON:
{{
 "hook": "<cel mai TARE hook scroll-stopping, după formulele de mai sus — NU repeta doar reducerea>",
 "hooks": ["<3 variante alternative de hook, formule diferite, pt A/B>"],
 "labels": ["<label scurt 2-4 cuvinte per produs, în ordinea de mai sus — BENEFICIU/utilizare, NU titlul lung>", ...],
 "cta": "<3-6 cuvinte, acțiune + urgență; poate numi magazinul {brand}; FĂRĂ http/www>",
 "palette": "#hex-accent-energic (roșu/portocaliu de reduceri dacă nu știi)",
 "music_vibe": "upbeat"
}}
„labels" trebuie să aibă EXACT {len(products)} elemente, câte unul per produs, în ordine.
GRAMATICĂ RO corectă, fără emoji, fără URL, fără ghilimele în texte."""
    raw = _gemini([{"text": prompt}], want_json=True)
    try:
        j = json.loads(raw)
    except Exception:
        j = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    labels = j.get("labels", [])
    while len(labels) < len(products):
        labels.append(products[len(labels)]["title"][:24])
    j["labels"] = labels[:len(products)]
    j.setdefault("hooks", [])
    return j

def render_montage(script, products, fmt, outdir, brand, veo_hook=False):
    """Montaj deals: hook → produse (tăieturi rapide, punch-in) → CTA, peste muzică energică. FFmpeg-first."""
    W, H = FMT[fmt]
    tmp = Path(tempfile.mkdtemp())
    accent = script.get("palette", "#E63329")
    hook, cta, labels = script.get("hook", "OFERTE -50% AZI"), script.get("cta", f"Comandă acum"), script["labels"]
    # construiește beat-urile: hook(prod0) + produse + cta(ultimul prod). Fiecare = 2 straturi (poză zoom + text static)
    beats = []   # (bg_path, ov_path, dur, role)
    bgp, ovp = make_deal_card(products[0], hook, "hook", W, H, accent, fmt, str(tmp / "c_hook"))
    beats.append((bgp, ovp, 2.4, "hook"))
    for i, p in enumerate(products):
        bgp, ovp = make_deal_card(p, labels[i], "product", W, H, accent, fmt, str(tmp / f"c{i}"))
        beats.append((bgp, ovp, 1.8, "product"))
    bgp, ovp = make_deal_card(products[-1], cta, "cta", W, H, accent, fmt, str(tmp / "c_cta"))
    beats.append((bgp, ovp, 2.6, "cta"))
    T = 0.35  # durata tranziției între beat-uri
    durs = [b[2] for b in beats]
    total = sum(durs) - T * (len(beats) - 1)
    music = make_music_deals(total, str(tmp / "m.wav"))
    inputs = []
    for bgp, ovp, _, _ in beats:
        inputs += ["-i", bgp, "-i", ovp]
    inputs += ["-i", music]
    music_idx = 2 * len(beats)
    # per beat: zoom DOAR pe poză (strat bg, alternez punch-in/punch-out) + overlay text STATIC → [vi]
    parts = []
    for i, (_, _, dur, _) in enumerate(beats):
        frames = max(12, int(dur * 30))
        bgi, ovi = 2 * i, 2 * i + 1
        zexpr = "min(zoom+0.0017,1.16)" if i % 2 == 0 else "if(eq(on,0),1.16,max(zoom-0.0017,1.0))"
        parts.append(f"[{bgi}:v]scale={2*W}:{2*H},zoompan=z='{zexpr}':"
                     f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps=30,setsar=1,"
                     f"format=yuv420p[z{i}];[z{i}][{ovi}:v]overlay=0:0,format=yuv420p[v{i}]")
    # xfade chain cu tranziții VARIATE (slide/zoom/wipe/circle) = dinamic, nu hard-cut de slideshow
    TR = ["slideleft", "wipeleft", "slideup", "smoothright", "circleopen", "slideright", "diagtl", "wipeup"]
    chain = []
    cur = "v0"; L = durs[0]
    for i in range(1, len(beats)):
        off = L - T
        lbl = f"x{i}"
        chain.append(f"[{cur}][v{i}]xfade=transition={TR[i % len(TR)]}:duration={T}:offset={off:.3f}[{lbl}]")
        cur = lbl; L += durs[i] - T
    fc = ";".join(parts + chain)
    out = Path(outdir) / f"{brand}_DEALS_{fmt.replace(':', 'x')}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", *inputs, "-filter_complex", fc, "-map", f"[{cur}]", "-map", f"{music_idx}:a",
                    "-r", "30", "-t", f"{total:.2f}",
                    "-af", f"afade=t=out:st={max(total-0.6,0):.2f}:d=0.6", "-shortest",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(out)],
                   capture_output=True)
    return out

def cmd_montage(a):
    # stiluri alternative (kinetic/bento/ugc/veo) = scripturi concept dedicate, dispecerizate aici
    if getattr(a, "style", "classic") != "classic":
        sf = Path(__file__).resolve().parent / f"concept_{a.style}.py"
        if not sf.exists():
            sys.exit(f"stilul „{a.style}” nu există ({sf.name})")
        deps = ["--with", "pillow", "--with", "numpy", "--with", "requests"]
        if a.style == "ugc":
            deps += ["--with", "yt-dlp"]
        fmts = list(FMT) if a.all_formats else [a.fmt]
        for f in fmts:
            cmd = ["uv", "run", *deps, str(sf), "--brand", a.brand, "--out", a.out, "--fmt", f, "--n", str(a.n)]
            if a.storefront:
                cmd += ["--storefront", a.storefront]
            elif a.manifest:
                cmd += ["--manifest", a.manifest]
            print(f"● stil „{a.style}” · {f} → {sf.name}")
            subprocess.run(cmd)
        if a.open:
            subprocess.run(["open", os.path.expanduser(a.out)])
        return
    if a.storefront:
        print(f"● trag produse din storefront public: {a.storefront} ...")
        products = fetch_storefront(a.storefront, a.n, f"/tmp/deals_{re.sub(r'[^a-z0-9]','',a.storefront.lower())}")
    elif a.manifest:
        products = json.load(open(a.manifest))[:a.n]
    else:
        sys.exit("dă --storefront <domeniu> sau --manifest <json>")
    if len(products) < 3:
        sys.exit(f"prea puține produse ({len(products)})")
    print(f"● {a.brand}: {len(products)} produse → Gemini scrie montajul (deals)...")
    script = direct_deals(a.brand, products, a.offer)
    print(f"  hook: „{script.get('hook')}”  · CTA: „{script.get('cta')}”  · paletă {script.get('palette')}")
    brandslug = re.sub(r"[^A-Za-z0-9]", "", a.brand)
    fmts = list(FMT) if a.all_formats else [a.fmt]
    outs = []
    for f in fmts:
        print(f"  🎬 montaj {f} ({len(products)} produse, tăieturi rapide)...")
        outs.append(render_montage(script, products, f, a.out, brandslug, a.veo_hook))
        print(f"  ✓ {f} → {outs[-1]}")
    if a.open and outs:
        subprocess.run(["open", str(outs[0].parent)])
        subprocess.run(["open", str(outs[0])])

def main():
    ap = argparse.ArgumentParser(description="Fabrică video-ad PMax (Gemini regizor + FFmpeg montaj)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--brand", required=True)
    m.add_argument("--images", default="", help="director cu poze (sau folosește --store pt Shopify)")
    m.add_argument("--store", default="", help="cheie magazin Shopify (ex esteban) → trage poze curate")
    m.add_argument("--out", default=os.path.expanduser("~/Desktop/pmax-video"))
    m.add_argument("--fmt", default="9:16", choices=list(FMT))
    m.add_argument("--all-formats", action="store_true"); m.add_argument("--music", default="")
    m.add_argument("--offer", default="", help="oferta reala pt CTA (ex 2+1 GRATIS); altfel din BRAND_OFFERS dupa --store")
    m.add_argument("--ai", action="store_true", help="CINEMATIC: clipuri Veo (produs în mișcare + audio) în loc de poze+zoom")
    m.add_argument("--open", action="store_true", help="deschide videoul după render")
    m.set_defaults(fn=cmd_make)

    # montaj „deals" multi-produs (Ofertele Zilei & co)
    mo = sub.add_parser("montage", help="montaj rapid deals: hook + produse multiple + preț/-% + CTA (FFmpeg, ieftin)")
    mo.add_argument("--brand", required=True)
    mo.add_argument("--style", default="classic",
                    choices=["classic", "kinetic", "bento", "mograph", "ugc", "reel"],
                    help="classic=carduri+tranziții · kinetic=typografie animată · bento=grid deals · "
                         "mograph=motion-graphics cu decupaje (produse care zboară/suprapun, NU slideshow) · "
                         "ugc=footage real (filtru text străin) · reel=beat-cut pe ritm")
    mo.add_argument("--storefront", default="", help="domeniu storefront public (ex ofertelezilei.ro) → trage produse + preț")
    mo.add_argument("--manifest", default="", help="JSON cu produse [{title,price,old,pct,img}] (alternativă la --storefront)")
    mo.add_argument("--n", type=int, default=6, help="câte produse în montaj")
    mo.add_argument("--out", default=os.path.expanduser("~/Desktop/pmax-video"))
    mo.add_argument("--fmt", default="9:16", choices=list(FMT))
    mo.add_argument("--all-formats", action="store_true")
    mo.add_argument("--offer", default="", help="oferta brandului pt hook/CTA (ex -50% la tot)")
    mo.add_argument("--veo-hook", action="store_true", help="hook dinamic Veo (cost ~$0.30-0.45); altfel 100%% gratis")
    mo.add_argument("--open", action="store_true")
    mo.set_defaults(fn=cmd_montage)

    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
