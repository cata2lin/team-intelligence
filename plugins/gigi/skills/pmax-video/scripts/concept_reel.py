# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pillow>=10.0", "numpy>=1.24", "yt-dlp>=2024.0"]
# ///
"""
concept_reel.py — concept "REEL" (beat-cut REAL demo footage) pentru generatorul de
video-ad-uri "Ofertele Zilei" (ofertelezilei.ro).

PROBLEMA cu stilurile existente (classic/kinetic/bento): fiecare arată UN card-produs
static (poză) cu zoom + tranziție = SLIDESHOW. „Card după card." Mereu același ritm.

ACEST CONCEPT e STRUCTURAL DIFERIT — un REEL montat real, NU carduri:
  • MIȘCARE REALĂ CONTINUĂ: footage video de demonstrație (oameni care folosesc produsul),
    nu poze statice. Mișcarea în cadru = antidotul la slideshow.
  • MONTAJUL E POANTA: tăieturi PE BEAT (muzica = 124 BPM → un beat la 60/124 ≈ 0.484s).
    LUNGIMI VARIATE de shot: rafale rapide (~0.25-0.4s) amestecate cu HOLD-uri (~1.0-1.5s)
    pe momentul-cheie al acțiunii. NU fiecare shot la fel.
  • PUNCH-IN (zoom rapid) pe momentul de acțiune al fiecărui clip; speed-ramp ocazional.
  • DESCHIDE pe cel mai DINAMIC cadru de acțiune (mișcare în primul cadru), NU pe un card-titlu.
  • CAPTIONS KINETICE: beneficiu RO scurt + preț (vechi tăiat / nou / -X%) care ANIMEAZĂ
    IN (pop/slide) pe un beat — NU etichetă statică pe tot shot-ul. PNG-uri PIL (FFmpeg n-are drawtext).
  • ~12-15s, 9:16. Termină cu CTA rapid.

REUTILIZEAZĂ pipeline-ul de footage din concept_ugc.py (import direct):
  gemini_queries / find_clean_footage (= yt-dlp search+download → crop anti-watermark → OCR
  filtru text străin EN/CJK/Cyrillic), plus pmax_video (make_music_deals 124 BPM, FONT, FMT, _hex).

FALLBACK ONEST: dacă footage-ul nu poate fi sursat/curățat pt un produs, cade pe Ken-Burns pe
POZA produsului — DAR tot beat-cut + text kinetic animat + lungimi variate (NU slideshow uniform).
Se LOGează „fallback poză".

Usage:
  uv run --with yt-dlp --with pillow --with numpy --with requests concept_reel.py \
      --storefront ofertelezilei.ro --brand "Ofertele Zilei" --fmt 9:16 --n 4
  uv run ... concept_reel.py --manifest ofer_manifest.json --n 4 --no-network   # forțează fallback poză
"""
from __future__ import annotations
import argparse, json, math, os, re, subprocess, sys, tempfile, random
from pathlib import Path

PMAX_DIR = Path("/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/pmax-video/scripts")
sys.path.insert(0, str(PMAX_DIR))
import pmax_video as pv          # noqa: E402  (FONT, FMT, _hex, make_music_deals, fetch_storefront, _gemini, _fit_font)
import concept_ugc as ugc        # noqa: E402  (gemini_queries, find_clean_footage, resolve_products, _ffdur, _run)

FPS = 30
BPM = 124
BEAT = 60.0 / BPM               # ≈ 0.4839 s — UN beat
HALF = BEAT / 2.0               # ≈ 0.2419 s — half-beat (rafale)
DEFAULT_BRAND = "Ofertele Zilei"

# font negru pt impact kinetic (Poppins-Black dacă există, altfel FONT din pv)
_BLACK = "/Users/gheorghebeschea/Library/Fonts/Poppins-Black.ttf"
FONT_HEAVY = _BLACK if os.path.exists(_BLACK) else pv.FONT
ACCENT = "#E63329"              # roșu de reduceri

_run = ugc._run
_ffdur = ugc._ffdur
_lei = ugc._lei


# ════════════════════════════ 1. RITM: grila de beat-uri ════════════════════════════
# Construim o secvență de DURATE de shot care alternează rafale (half-beat) cu hold-uri
# (1-3 beat-uri) — exact „varied shot lengths", aliniate pe grila muzicală de 124 BPM.

def beat_pattern(target_dur):
    """Întoarce o listă de durate de shot (sec) care însumează ~target_dur, alternând
    RAFALE rapide (half-beat) cu HOLD-uri (1-3 beat-uri), aliniate pe beat. NU uniform."""
    # tipare de „grupuri" exprimate în multipli de half-beat (0.5 = half, 1 = beat, 2 = 2 beats...)
    # fiecare grup = o frază ritmică: rafală-rafală-rafală-HOLD etc.
    groups = [
        [0.5, 0.5, 1.0],            # două rafale + un beat
        [0.5, 0.5, 0.5, 0.5, 2.0],  # patru rafale „machine-gun" + hold de 2 beats
        [1.0, 1.0],                 # două beat-uri medii
        [0.5, 0.5, 3.0],            # două rafale + HOLD lung (momentul-cheie)
        [0.5, 0.5, 0.5, 1.5],       # trei rafale + hold de 1.5 beats
    ]
    durs = []
    total = 0.0
    gi = 0
    rnd = random.Random(7)
    while total < target_dur - BEAT:
        g = groups[gi % len(groups)]
        gi += 1
        for mult in g:
            d = mult * BEAT
            # mic jitter ca să nu fie metronomic-robotic, dar stă pe sub/aproape de beat
            d = max(HALF * 0.85, d + rnd.uniform(-0.02, 0.02))
            durs.append(round(d, 3))
            total += d
            if total >= target_dur - HALF:
                break
    return durs


# ════════════════════════════ 1b. RE-VALIDARE DENSĂ text străin ════════════════════════════

def dense_foreign_scan(clip, tmp, n_frames=18, cjk_th=20, cyr_th=20):
    """Re-scanare DENSĂ a unui clip DEJA acceptat de filtrul din concept_ugc (care ia doar 8 cadre).
    Clipul e ținut pe ecran multe shot-uri (hook+corp+CTA), deci text străin INTERMITENT (ex.
    „4 1/4 inches" ars doar pe câteva cadre) trebuie prins. Refolosim helperele OCR din concept_ugc
    (_ocr_frame/_classify/_english_hits) dar cu n_frames cadre uniform distribuite pe TOT clipul.

    POLITICĂ: STRICT pe ENGLEZĂ (text instrucțional ars = adevăratul semnal, ex. „inches", „storage box"),
    dar TOLERANT pe CJK/Cyr (prag înalt 20) fiindcă OCR-ul HALUCINEAZĂ caractere CJK/Cyr pe LOGO-uri de
    brand (ex. „Newhai") și texturi — un logo de produs e on-screen text ACCEPTABIL, nu text străin.
    Rejectăm pe CJK/Cyr DOAR la volum mare (≥20 = chiar un clip în limbă străină, nu zgomot pe logo)."""
    dur = _ffdur(clip)
    if dur <= 0:
        return {"ok": False, "reason": "durată 0", "en": [], "cjk": 0, "cyr": 0}
    ts = [dur * (i + 0.5) / n_frames for i in range(n_frames)]
    tot_cjk = tot_cyr = 0
    stop_words, strong_units = set(), set()
    ro_diac = False
    scanned = 0
    for i, t in enumerate(ts):
        fp = tmp / f"reocr_{Path(clip).stem}_{i}.png"
        clean, aggressive = ugc._ocr_frame(clip, t, fp)
        if clean is None:
            continue
        scanned += 1
        c, y = ugc._classify(clean)
        tot_cjk += c
        tot_cyr += y
        s, st = ugc._english_hits(aggressive)
        stop_words |= s
        strong_units |= st
        if any(ch in ugc._RO_DIACRITICS for ch in clean):
            ro_diac = True
    english_foreign = (not ro_diac) and (len(strong_units) >= 1 or len(stop_words) >= 2)
    en_report = sorted(strong_units | stop_words)
    ok = (scanned > 0) and (tot_cjk < cjk_th) and (tot_cyr < cyr_th) and not english_foreign
    reason = ""
    if tot_cjk >= cjk_th:
        reason = f"CJK {tot_cjk}"
    elif tot_cyr >= cyr_th:
        reason = f"Cyrillic {tot_cyr}"
    elif english_foreign:
        reason = "EN ars (" + ", ".join(en_report[:6]) + ")"
    return {"ok": ok, "reason": reason, "en": en_report, "cjk": tot_cjk, "cyr": tot_cyr}


# ════════════════════ 1c. footage finder ENGLEZĂ-STRICT, SCRIPT-TOLERANT ════════════════════

def find_footage_english_strict(products, queries, W, H, tmp, log):
    """Sursare footage cu politica corectă pt un REEL de oferte RO: refolosește download+crop din
    concept_ugc, dar ACCEPTAREA o decidem cu dense_foreign_scan (ENGLEZĂ-strict, CJK/Cyr-tolerant).
    Motiv: filtrul original respinge pe CJK/Cyr≥6, dar OCR-ul halucinează CJK/Cyr pe LOGO-uri de brand
    (Newhai) și texturi → arunca footage REAL utilizabil. Aici rejectăm pe ENGLEZĂ ARSĂ (semnalul real)
    sau CJK/Cyr foarte mare (≥20 = clip chiar în limbă străină). Întoarce (footage[], stats)."""
    results = []
    stats = {"en": 0, "cjkcyr": 0}
    for pi, (p, q) in enumerate(zip(products, queries)):
        log.append(f"\n── Produs {pi}: {p['title'][:50]}")
        log.append(f"   query: \"{q}\"")
        clean = None
        try:
            cands = ugc.yt_search(q, n=6)
        except Exception as e:
            log.append(f"   ! yt-dlp search eșuat: {e}")
            cands = []
        short = [c for c in cands if c.get("duration") and 4 < c["duration"] < 60]
        short.sort(key=lambda c: (abs(c["duration"] - 25), -c["views"]))
        log.append(f"   {len(cands)} rezultate, {len(short)} scurte")
        for c in short[:4]:
            dl = tmp / f"dl_{pi}_{c['id']}.mp4"
            log.append(f"   ↓ {c['id']} ({c['duration']}s, {c['views']} views): {c['title'][:46]}")
            got = None
            try:
                got = ugc.yt_download(c["id"], str(dl))
            except Exception as e:
                log.append(f"     ! download eșuat: {e}")
            if not got:
                continue
            cropped = tmp / f"crop_{pi}_{c['id']}.mp4"
            cp = ugc.crop_reframe(got, W, H, str(cropped))
            if not cp:
                continue
            v = dense_foreign_scan(cp, tmp, n_frames=18, cjk_th=20, cyr_th=20)
            if v["ok"]:
                log.append(f"     ✓ CURAT (EN-strict/script-tolerant: CJK={v['cjk']}, Cyr={v['cyr']}, EN={v['en']})")
                clean = cp
                break
            else:
                if v.get("en"):
                    stats["en"] += 1
                else:
                    stats["cjkcyr"] += 1
                log.append(f"     ✗ RESPINS: {v['reason']}")
        log.append(f"   → produs {pi}: " + ("footage curat" if clean else "FĂRĂ footage → fallback poză"))
        results.append(clean)
    return results, stats


# ════════════════════════════ 2. SURSE: segmente de footage „de acțiune" ════════════════════════════

def _scene_scores(clip, n_probe=12):
    """Returnează (timestamps, scoruri-mișcare) sortabile: cât de multă MIȘCARE e în jurul fiecărui
    moment al clipului (diferență medie între cadre). Folosit ca să deschidem pe cel mai dinamic moment
    și să punem HOLD-urile pe momentele de acțiune."""
    dur = _ffdur(clip)
    if dur <= 0.4:
        return [], []
    # signalstats via scene-detection: extragem scoruri de „scene change" cu select+metadata
    # mai simplu și robust: eșantionăm n_probe perechi de cadre și măsurăm diferența medie cu ffmpeg
    ts = [dur * (i + 0.5) / n_probe for i in range(n_probe)]
    scores = []
    for t in ts:
        # diferența între cadrul la t și la t+0.12 → proxy de mișcare
        r = _run(["ffmpeg", "-v", "error", "-ss", f"{max(0,t-0.06):.2f}", "-i", clip,
                  "-frames:v", "12", "-vf",
                  "select='gte(scene\\,0)',metadata=print:file=-",
                  "-an", "-f", "null", "-"], timeout=40)
        # parse scene scores din metadata
        vals = [float(m) for m in re.findall(r"scene_score=([0-9.]+)", r.stderr + r.stdout)]
        scores.append(sum(vals) / len(vals) if vals else 0.0)
    return ts, scores


def _motion_windows(clip, n=10):
    """Sortează momentele clipului după MIȘCARE (cel mai dinamic primul). Întoarce listă de
    timestamps de start (sec). Fallback la eșantionare uniformă dacă scene-detect dă 0."""
    ts, sc = _scene_scores(clip, n_probe=max(8, n))
    if ts and any(s > 0 for s in sc):
        order = sorted(range(len(ts)), key=lambda i: -sc[i])
        return [ts[i] for i in order]
    dur = _ffdur(clip)
    if dur <= 0:
        return [0.0]
    # uniform, dar începem din interiorul clipului (evităm intro/outro statice)
    return [dur * f for f in (0.25, 0.5, 0.7, 0.15, 0.4, 0.6, 0.8, 0.35, 0.55, 0.1)]


# ════════════════════════════ 3. SHOT din FOOTAGE: trim + PUNCH-IN zoom ════════════════════════════

def shot_from_footage(clip, start, dur, W, H, path, punch=True, speedramp=False, seed=0,
                      ovmov=None, src_dur=None, flash_in=False):
    """Decupează un SEGMENT scurt din footage la `start`, durată `dur`, cu PUNCH-IN (zoom rapid
    de la 1.0→~1.14 pe durata shot-ului) ca să dea senzație de „intrare în acțiune". `speedramp`=
    accelerează la 1.4× (momentul-cheie). Footage e DEJA decupat la W×H (crop_reframe din ugc).
    Dacă `ovmov` e dat, SUPRAPUNE overlay-ul kinetic în ACEEAȘI trecere (1 ffmpeg/shot, rapid).
    `flash_in` = mic flash alb pe primele 2 cadre (accent de tăietură pe beat)."""
    frames = max(2, int(round(dur * FPS)))
    rnd = random.Random(seed)
    z_to = 1.10 + rnd.uniform(0.0, 0.08)
    if src_dur and src_dur > 0:
        start = max(0.0, min(start, src_dur - dur - 0.05))
    setpts = "0.71*PTS" if speedramp else "PTS-STARTPTS"   # 1/1.4 ≈ 0.71 → speed-up
    take = dur * (1.4 if speedramp else 1.0) + 0.05         # consumăm mai mult brut dacă accelerăm
    if punch:
        SW, SH = int(W * 1.16), int(H * 1.16)
        base = (f"trim=0:{take:.3f},setpts={setpts},"
                f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},setsar=1,"
                f"zoompan=z='min(1.0+{(z_to-1.0):.3f}*on/{frames},{z_to:.3f})':"
                f"d={frames}:s={W}x{H}:fps={FPS},fps={FPS}")
    else:
        base = (f"trim=0:{take:.3f},setpts={setpts},"
                f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}")
    if flash_in:
        base += f",fade=t=in:st=0:d=0.07:color=white"
    return _encode_shot(["-ss", f"{start:.2f}", "-i", clip], base, ovmov, dur, W, H, path)


def shot_from_photo(img, dur, W, H, path, direction=0, seed=0, ovmov=None, flash_in=False):
    """Fallback: Ken-Burns pe POZĂ — pan+zoom DIRECȚIONAT (nu același mereu), ca să dea mișcare.
    direction alege colțul spre care derivăm (varietate între shot-uri). Suprapune overlay-ul
    kinetic în aceeași trecere dacă `ovmov` e dat."""
    frames = max(2, int(round(dur * FPS)))
    rnd = random.Random(seed)
    SW, SH = int(W * 1.30), int(H * 1.30)
    z0 = 1.0 + rnd.uniform(0.0, 0.04)
    z1 = z0 + 0.16 + rnd.uniform(0.0, 0.06)
    dx = [0.0, 1.0, 1.0, 0.0][direction % 4]
    dy = [0.0, 0.0, 1.0, 1.0][direction % 4]
    base = (f"scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},setsar=1,"
            f"zoompan=z='min({z0:.3f}+{(z1-z0):.3f}*on/{frames},{z1:.3f})':"
            f"x='(iw-iw/zoom)*({dx:.2f}*on/{frames})':"
            f"y='(ih-ih/zoom)*({dy:.2f}*on/{frames})':"
            f"d={frames}:s={W}x{H}:fps={FPS},fps={FPS}")
    if flash_in:
        base += f",fade=t=in:st=0:d=0.07:color=white"
    return _encode_shot(["-loop", "1", "-i", img], base, ovmov, dur, W, H, path)


def _encode_shot(in_args, base_vf, ovmov, dur, W, H, path):
    """Encode UN shot final într-o singură trecere ffmpeg: sursă → `base_vf` → (opțional) overlay
    kinetic `ovmov`. Asta înlocuiește 2 treceri (render shot + composite) cu 1 → de ~2× mai rapid."""
    if ovmov:
        cmd = (["ffmpeg", "-y"] + in_args + ["-i", ovmov,
               "-filter_complex", f"[0:v]{base_vf}[b];[b][1:v]overlay=0:0:format=auto:shortest=1[v]",
               "-map", "[v]", "-t", f"{dur:.3f}", "-an", "-r", str(FPS),
               "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", path])
    else:
        cmd = (["ffmpeg", "-y"] + in_args + ["-vf", base_vf, "-t", f"{dur:.3f}",
               "-an", "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264",
               "-preset", "veryfast", path])
    r = _run(cmd, timeout=120)
    if os.path.exists(path) and os.path.getsize(path) > 6000:
        return path
    sys.stderr.write(f"[shot] fail: {r.stderr[-260:]}\n")
    return None


# ════════════════════════════ 4. TEXT KINETIC: PNG-uri pe sub-segmente ════════════════════════════
# FFmpeg n-are drawtext → randăm PNG-uri PIL. Pt animația IN (pop/slide) randăm CÂTEVA stări (in→settle)
# și le suprapunem secvențial peste shot, fiecare câteva cadre → textul „intră" pe beat, NU stă static.

def _draw_pill_text(d, text, cx, cy, font, fill_bg, fill_txt, padx, pady, radius):
    from PIL import ImageDraw
    w = d.textlength(text, font=font)
    asc, desc = font.getmetrics()
    h = asc + desc
    x0, y0 = cx - w / 2 - padx, cy - h / 2 - pady
    x1, y1 = cx + w / 2 + padx, cy + h / 2 + pady
    if fill_bg is not None:
        d.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill_bg)
    d.text((cx - w / 2, cy - h / 2), text, font=font, fill=fill_txt,
           stroke_width=max(2, font.size // 16), stroke_fill=(0, 0, 0, 230))
    return (x1 - x0)


def kinetic_caption_png(caption, prod, role, W, H, brand, prog, path):
    """Randează UN cadru al overlay-ului kinetic la progresul `prog`∈[0,1] (0=intrare, 1=așezat).
    - caption beneficiu sus, intră cu SLIDE-UP + fade (pe beat).
    - badge preț jos (vechi tăiat / nou pe accent) intră cu POP (scale) ușor decalat.
    - burst -X% colț, intră cu pop.
    Toate pe pastile solide (lizibilitate peste footage, nu re-expun watermark rezidual)."""
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np
    ac = pv._hex(ACCENT)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    # ușoare benzi gradient (doar cât să prindă textul), NU un card plin → footage-ul respiră
    if role != "burstonly":
        top = np.zeros((int(H * 0.22), W, 4), np.uint8)
        top[:, :, 3] = np.linspace(165, 0, top.shape[0]).astype(np.uint8)[:, None]
        ov.alpha_composite(Image.fromarray(top, "RGBA"), (0, 0))
        bot = np.zeros((int(H * 0.30), W, 4), np.uint8)
        bot[:, :, 3] = np.linspace(0, 185, bot.shape[0]).astype(np.uint8)[:, None]
        ov.alpha_composite(Image.fromarray(bot, "RGBA"), (0, H - bot.shape[0]))

    d = ImageDraw.Draw(ov)
    e = max(0.0, min(1.0, prog))
    ease = 1 - (1 - e) * (1 - e)        # ease-out quad

    # ── caption sus: SLIDE-UP + fade ──
    if caption:
        from PIL import ImageFont as IF
        base_fs = int(H * (0.066 if role in ("hook", "cta") else 0.056))
        # fit pe Poppins-Black
        def _fit(txt, maxw, bfs, minfs):
            fs = bfs
            while fs > minfs:
                f = IF.truetype(FONT_HEAVY, fs)
                ls = pv._wrap_caption(txt, d, f, maxw)
                if len(ls) <= 2:
                    return f, ls
                fs -= 2
            f = IF.truetype(FONT_HEAVY, minfs)
            return f, pv._wrap_caption(txt, d, f, maxw)[:2]
        f_top, lines = _fit(caption.upper(), W * 0.90, base_fs, int(H * 0.040))
        lh = int(f_top.size * 1.14)
        slide = int((1 - ease) * H * 0.05)         # intră de jos în sus
        ty = int(H * 0.105) + slide
        alpha = int(255 * ease)
        cap_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        cd = ImageDraw.Draw(cap_layer)
        for ln in lines:
            lw = cd.textlength(ln, font=f_top)
            cd.text(((W - lw) / 2, ty), ln, font=f_top, fill=(255, 255, 255, 255),
                    stroke_width=max(3, f_top.size // 13), stroke_fill=(0, 0, 0, 245))
            ty += lh
        if alpha < 255:
            a = cap_layer.split()[3].point(lambda v: int(v * ease))
            cap_layer.putalpha(a)
        ov.alpha_composite(cap_layer)

    # ── badge preț jos: POP (scale) ──
    if prod is not None and prod.get("price") is not None:
        from PIL import ImageFont as IF
        p, old, pct = prod.get("price"), prod.get("old"), prod.get("pct")
        pop = 0.6 + 0.4 * ease                      # scale-in 0.6→1.0
        fs_new = int(H * 0.070 * pop)
        fs_old = int(H * 0.034 * pop)
        fnew = IF.truetype(FONT_HEAVY, max(10, fs_new))
        fold = IF.truetype(pv.FONT, max(8, fs_old))
        newtxt = f"{_lei(p)} lei"
        oldtxt = f"{_lei(old)} lei" if old else ""
        new_w = d.textlength(newtxt, font=fnew)
        old_w = d.textlength(oldtxt, font=fold) if oldtxt else 0
        gap = int(W * 0.028)
        total_w = new_w + (old_w + gap if oldtxt else 0)
        by = int(H * 0.855)
        bx = (W - total_w) / 2
        pad = int(fs_new * 0.40)
        pl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pl)
        pd.rounded_rectangle([bx - pad, by - pad, bx + total_w + pad, by + fs_new + pad],
                             radius=int(fs_new * 0.42), fill=(0, 0, 0, 205))
        xcur = bx
        if oldtxt:
            oy = by + (fs_new - fs_old)
            pd.text((xcur, oy), oldtxt, font=fold, fill=(208, 208, 208, 255))
            pd.line([xcur, oy + fs_old * 0.55, xcur + old_w, oy + fs_old * 0.55],
                    fill=(245, 70, 70, 255), width=max(3, fs_old // 9))
            xcur += old_w + gap
        pd.rounded_rectangle([xcur - pad // 2, by - pad // 2, xcur + new_w + pad // 2, by + fs_new + pad // 2],
                             radius=int(fs_new * 0.30), fill=(ac[0], ac[1], ac[2], 255))
        pd.text((xcur, by), newtxt, font=fnew, fill=(255, 255, 255, 255),
                stroke_width=max(2, fs_new // 22), stroke_fill=(0, 0, 0, 235))
        a = pl.split()[3].point(lambda v: int(v * (0.25 + 0.75 * ease)))
        pl.putalpha(a)
        ov.alpha_composite(pl)

        # burst -X% colț dreapta-sus, POP
        if pct:
            bs = int(H * 0.078 * (0.5 + 0.5 * ease))
            bxc, byc = int(W * 0.80), int(H * 0.30)
            burst = Image.new("RGBA", (max(4, bs * 2), max(4, bs * 2)), (0, 0, 0, 0))
            bd = ImageDraw.Draw(burst)
            pts = []
            for i in range(20):
                ang = math.pi * i / 10 - 0.15 + (1 - ease) * 0.5   # mică rotație la intrare
                rr = bs if i % 2 == 0 else bs * 0.74
                pts.append((bs + rr * math.cos(ang), bs + rr * math.sin(ang)))
            bd.polygon(pts, fill=(235, 30, 35, 255), outline=(255, 255, 255, 255))
            fbu = IF.truetype(FONT_HEAVY, max(8, int(bs * 0.5)))
            btx = f"-{pct}%"
            bw2 = bd.textlength(btx, font=fbu)
            bd.text((bs - bw2 / 2, bs - bs * 0.30), btx, font=fbu, fill="white",
                    stroke_width=2, stroke_fill=(0, 0, 0, 220))
            ov.alpha_composite(burst, (bxc - bs, byc - bs))

    # ── etichetă brand (doar hook/cta) ──
    if role in ("hook", "cta"):
        from PIL import ImageFont as IF
        fb = IF.truetype(FONT_HEAVY, int(H * 0.030))
        bt = brand.upper()
        bw = d.textlength(bt, font=fb)
        pad = int(H * 0.013)
        cy = int(H * 0.30)
        _draw_pill_text(d, bt, W / 2, cy, fb, (ac[0], ac[1], ac[2], int(235 * ease)),
                        (255, 255, 255, int(255 * ease)), pad, pad // 2, int(H * 0.016))

    ov.save(path)
    return path


def overlay_seq_for_shot(caption, prod, role, W, H, brand, dur, tmp, tag):
    """Construiește un MIC video-overlay (RGBA) pentru un shot: textul ANIMEAZĂ IN în primele ~0.18s
    (pe beat), apoi stă AȘEZAT pe restul shot-ului. Întoarce path la un .mov cu alfa (qtrle) sau None.

    OPTIM: randăm DOAR ~n_in cadre de intrare (prog 0→1) cu PIL, le encodăm ca .mov alfa, apoi HOLD-ăm
    ultimul cadru până la durata shot-ului cu tpad=clone (zero PIL în plus). Asta taie de la N cadre/shot
    la ~6 cadre/shot → de câteva ori mai rapid, fără să schimbe rezultatul vizual."""
    if not caption and (prod is None or prod.get("price") is None) and role not in ("hook", "cta"):
        return None  # nimic de afișat pe acest shot (rafală fără text)
    n = max(2, int(round(dur * FPS)))
    n_in = min(n, max(4, int(round(0.18 * FPS))))     # ~0.18s de animație de intrare
    d = tmp / f"ovseq_{tag}"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n_in):
        prog = i / max(1, n_in - 1)
        kinetic_caption_png(caption, prod, role, W, H, brand, prog, str(d / f"{i:04d}.png"))
    movp = tmp / f"ovseq_{tag}.mov"
    # encodăm cadrele de intrare + HOLD pe ultimul cadru până la durata shot-ului (tpad clone)
    hold = max(0.0, dur - n_in / FPS) + 0.10
    r = _run(["ffmpeg", "-y", "-framerate", str(FPS), "-i", str(d / "%04d.png"),
              "-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f}",
              "-c:v", "qtrle", "-pix_fmt", "argb", str(movp)], timeout=120)
    if movp.exists() and movp.stat().st_size > 3000:
        return str(movp)
    sys.stderr.write(f"[ovseq] fail {tag}: {r.stderr[-200:]}\n")
    return None


# ════════════════════════════ 5. COPY (RO) ════════════════════════════

def reel_copy(products, brand):
    """hook + caption-beneficiu per produs + CTA (RO corect, NU inventează prețuri). Refolosește
    formularea din concept_ugc (captions scurte care se înțeleg pe mut)."""
    return ugc.gemini_copy(products, brand)


# ════════════════════════════ 6. TIMELINE: montajul de tip REEL ════════════════════════════

def build_reel(products, copy, footage, W, H, brand, out_path, tmp, log):
    """Construiește timeline-ul REEL: deschide pe cel mai dinamic cadru de acțiune, apoi beat-cut
    cu lungimi variate (rafale + hold-uri), punch-in pe fiecare shot, text kinetic animat per shot,
    CTA rapid la final. Întoarce (out_path, used_real, n_fallback, DUR, n_shots)."""
    caps = copy["captions"]
    hook, cta = copy["hook"], copy["cta"]
    TARGET = 13.5                                  # ~12-15s
    pat = beat_pattern(TARGET)
    log.append(f"\n● grila de beat (124 BPM): {len(pat)} shot-uri, durate(s)={['%.2f'%x for x in pat]}")

    # alocăm shot-urile la produse: distribuim pat-ul pe produse + rezervăm 1 shot hook + 1 CTA
    n_prod = len(products)
    # blocuri: HOOK(1) | per-produs(restul) | CTA(1)
    body = pat[1:-1] if len(pat) >= 3 else pat
    # împărțim corpul pe produse, dând fiecărui produs un grup de shot-uri consecutive
    per = max(1, len(body) // max(1, n_prod))
    assign = []                                    # listă de (prod_idx, dur, is_hold)
    bi = 0
    for pidx in range(n_prod):
        k = per if pidx < n_prod - 1 else (len(body) - bi)
        for j in range(k):
            if bi >= len(body):
                break
            dur = body[bi]
            assign.append((pidx, dur, dur >= 1.2))   # hold = shot lung
            bi += 1
    # ── pregătim ferestrele de mișcare + durata sursă per footage (cel mai dinamic moment primul) ──
    motion = {}
    src_durs = {}
    for i, f in enumerate(footage):
        if f:
            motion[i] = _motion_windows(f, n=12)
            src_durs[i] = _ffdur(f)

    beats = []                                     # path-uri de shot-uri finale
    used_real = 0
    n_fallback_prod = set()
    shot_seed = 0

    def emit(prod_idx, caption, role, dur, speed=False, flash=False):
        """Randează UN shot final într-o singură trecere ffmpeg (sursă→punch/kenburns→overlay kinetic)."""
        nonlocal used_real, shot_seed
        shot_seed += 1
        f = footage[prod_idx] if prod_idx is not None and prod_idx < len(footage) else None
        prod = products[prod_idx] if prod_idx is not None else None
        fin = str(tmp / f"fin_{shot_seed:03d}.mp4")
        # 1) overlay kinetic (mov alfa) — animă IN pe ~0.18s, hold după
        ov = overlay_seq_for_shot(caption, prod, role, W, H, brand, dur, tmp, f"{shot_seed:03d}")
        # 2) shot + overlay în ACEEAȘI trecere
        sp = None
        if f:
            wins = motion.get(prod_idx) or [0.0]
            start = wins[0] if dur >= 1.2 else wins[shot_seed % len(wins)]  # HOLD = cel mai dinamic moment
            sp = shot_from_footage(f, start, dur, W, H, fin, punch=True, speedramp=speed,
                                   seed=shot_seed, ovmov=ov, src_dur=src_durs.get(prod_idx), flash_in=flash)
            if sp:
                used_real += 1
        if sp is None:
            if f and prod_idx is not None:
                n_fallback_prod.add(prod_idx)   # footage exista dar render-ul a picat
            if prod_idx is not None and not f:
                n_fallback_prod.add(prod_idx)
            img = (prod or products[0])["img"]
            sp = shot_from_photo(img, dur, W, H, fin, direction=shot_seed, seed=shot_seed,
                                 ovmov=ov, flash_in=flash)
        return sp

    # ── HOOK: deschide pe cel mai dinamic cadru de acțiune (footage) — mișcare în frame 1 ──
    hook_src = next((i for i, f in enumerate(footage) if f), None)
    hook_dur = pat[0]
    fb = emit(hook_src if hook_src is not None else 0, hook, "hook", max(hook_dur, BEAT))
    if fb:
        beats.append(fb)
        log.append(f"   HOOK ({max(hook_dur,BEAT):.2f}s) {'footage' if hook_src is not None else 'poză'} — '{hook[:30]}'")

    # ── CORP: shot-uri beat-cut per produs; caption apare DOAR pe primul shot al produsului + pe hold-uri ──
    prev_prod = None
    for k, (pidx, dur, is_hold) in enumerate(assign):
        first_of_prod = (pidx != prev_prod)
        prev_prod = pidx
        cap = caps[pidx] if (first_of_prod or is_hold) else ""   # text doar la intrare/hold → animă pe beat
        role = "product"
        # speed-ramp ocazional pe o rafală scurtă (nu pe hold) → varietate de ritm
        speed = (not is_hold) and (k % 5 == 2)
        # flash alb scurt pe tăietura HARD spre un produs NOU → accent de montaj pe beat
        c = emit(pidx, cap, role, dur, speed=speed, flash=first_of_prod)
        if c:
            beats.append(c)

    # ── CTA: rapid, footage final dacă există ──
    cta_src = next((i for i in range(len(footage) - 1, -1, -1) if footage[i]), None)
    cta_dur = max(pat[-1], BEAT * 2)
    cc = emit(cta_src if cta_src is not None else len(products) - 1, cta, "cta", cta_dur)
    if cc:
        beats.append(cc)
        log.append(f"   CTA ({cta_dur:.2f}s) — '{cta[:30]}'")

    if not beats:
        sys.exit("niciun shot randat")

    # ── concatenăm shot-urile (re-encode pt siguranță pe timestamp) ──
    DUR = 0.0
    for b in beats:
        DUR += _ffdur(b)
    lst = tmp / "list.txt"
    lst.write_text("\n".join(f"file '{b}'" for b in beats))
    concat = str(tmp / "concat.mp4")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
          "-r", str(FPS), concat], timeout=240)

    # ── muzică 124 BPM + fade-out + mux ──
    music = pv.make_music_deals(DUR, str(tmp / "music.wav"))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-y", "-i", concat, "-i", music, "-map", "0:v", "-map", "1:a",
          "-af", f"afade=t=out:st={max(DUR-0.5,0):.2f}:d=0.5", "-shortest",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
          "-movflags", "+faststart", str(out_path)], timeout=240)

    n_fallback = len(n_fallback_prod)
    return out_path, used_real, n_fallback, DUR, len(beats)


# ════════════════════════════ main ════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Concept REEL beat-cut real-footage video ad (Ofertele Zilei)")
    ap.add_argument("--storefront", default="", help="domeniu storefront public (ex ofertelezilei.ro)")
    ap.add_argument("--manifest", default="", help="JSON cu produse [{title,price,old,pct,img}]")
    ap.add_argument("--brand", default=DEFAULT_BRAND)
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop/pmax-video"))
    ap.add_argument("--fmt", default="9:16", choices=list(pv.FMT))
    ap.add_argument("--n", type=int, default=4, help="câte produse (3-5 ideal)")
    ap.add_argument("--no-network", action="store_true", help="sare peste yt-dlp → forțează fallback Ken-Burns pe poze")
    ap.add_argument("--footage-cache", default="", help="director cache: refolosește clipurile curate "
                    "deja descărcate (per produs) ca să NU re-descarci la fiecare iterație a montajului")
    a = ap.parse_args()

    W, H = pv.FMT[a.fmt]
    brand = a.brand
    brandslug = re.sub(r"[^A-Za-z0-9]", "", brand)
    tmp = Path(tempfile.mkdtemp(prefix="reel_"))
    log = []

    # 0. produse (reutilizăm resolver-ul din concept_ugc: storefront/manifest + normalizare JPEG)
    products = ugc.resolve_products(a, tmp)
    if len(products) < 1:
        sys.exit(f"prea puține produse ({len(products)})")
    print(f"● {brand}: {len(products)} produse ({a.fmt}, {W}×{H}) →")
    for p in products:
        print(f"   - {p['title'][:55]}  ({_lei(p['price'])} lei, -{p.get('pct')}%)")

    # 1. copy RO + query-uri de footage
    print("● Gemini: query-uri footage + copy RO (hook/captions/CTA)...")
    queries = ugc.gemini_queries(products, brand)
    copy = reel_copy(products, brand)
    for p, q in zip(products, queries):
        print(f"   query[{p['title'][:26]}] = \"{q}\"")
    print(f"   hook: „{copy['hook']}”  · CTA: „{copy['cta']}”")
    for i, c in enumerate(copy["captions"]):
        print(f"   caption {i}: „{c}”")

    # 2-4. footage real → crop anti-watermark → filtru text străin (reutilizat din concept_ugc)
    if a.no_network:
        print("● --no-network: forțez fallback Ken-Burns pe poze (beat-cut + text kinetic).")
        footage = [None] * len(products)
        stats = {"en": 0, "cjkcyr": 0}
        log.append("MOD --no-network: niciun footage descărcat (test fallback poză).")
    else:
        cache = Path(a.footage_cache) if a.footage_cache else None
        cached = [None] * len(products)
        if cache and cache.exists():
            for i in range(len(products)):
                cp = cache / f"footage_{i}.mp4"
                if cp.exists() and cp.stat().st_size > 30000:
                    cached[i] = str(cp)
        if cache and any(cached):
            print(f"● refolosesc footage din cache: {sum(1 for c in cached if c)}/{len(products)} produse")
            footage, stats = cached, {"en": 0, "cjkcyr": 0}
            log.append(f"CACHE: refolosit {sum(1 for c in cached if c)} clipuri din {cache}")
            # chiar și din cache, re-validăm ENGLEZĂ-strict pe 18 cadre (garantăm: fără text englez ars)
            for i, f in enumerate(footage):
                if not f:
                    continue
                v2 = dense_foreign_scan(f, tmp, n_frames=18, cjk_th=20, cyr_th=20)
                if not v2.get("ok"):
                    log.append(f"   ⚠ cache produs {i} are text străin ({v2.get('reason')}) → fallback poză")
                    footage[i] = None
                    stats["en" if v2.get("en") else "cjkcyr"] += 1
        else:
            print("● caut footage real (yt-dlp) → CROP anti-watermark → filtru ENGLEZĂ-strict (OCR dens)...")
            # finder ENGLEZĂ-strict / SCRIPT-tolerant: respinge text englez ars (semnal real),
            # NU aruncă footage real din cauza halucinațiilor OCR de CJK/Cyr pe logo-uri de brand.
            footage, stats = find_footage_english_strict(products, queries, W, H, tmp, log)
            if cache:                                # salvăm clipurile curate pt iterații viitoare
                import shutil
                cache.mkdir(parents=True, exist_ok=True)
                for i, f in enumerate(footage):
                    if f:
                        shutil.copy(f, cache / f"footage_{i}.mp4")

    n_real = sum(1 for f in footage if f)
    print(f"● footage curat (decupat): {n_real}/{len(products)} produse  "
          f"(respinse: {stats['en']} EN, {stats['cjkcyr']} CJK/Cyr)")

    # 5. montaj REEL
    out_path = str(Path(a.out) / f"{brandslug}_REEL_{a.fmt.replace(':', 'x')}.mp4")
    print(f"● montez REEL beat-cut ({a.fmt}, 124 BPM, text kinetic + preț + muzică)...")
    out_path, used_real, n_fallback, dur, n_shots = build_reel(
        products, copy, footage, W, H, brand, out_path, tmp, log)
    print(f"✓ render: {out_path}  (~{dur:.1f}s, {n_shots} shot-uri, {used_real} shot-uri footage real, "
          f"{n_fallback} produse pe fallback poză)")

    logp = Path(a.out) / f"{brandslug}_REEL_log.txt"
    logp.write_text("\n".join(log))
    print(f"  log: {logp}")
    print(f"\nQUERIES={json.dumps(queries, ensure_ascii=False)}")
    print(f"SHOTS={n_shots} REAL_FOOTAGE_SHOTS={used_real} FALLBACK_PHOTO_PRODUCTS={n_fallback}")
    print(f"REJECTED_EN={stats['en']} REJECTED_CJK_CYR={stats['cjkcyr']}")
    print(f"OUTPUT={out_path}")


if __name__ == "__main__":
    main()
