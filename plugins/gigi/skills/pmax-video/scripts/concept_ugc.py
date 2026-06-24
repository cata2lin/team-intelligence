# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "pillow>=10.0", "numpy>=1.24", "yt-dlp>=2024.0"]
# ///
"""
concept_ugc.py — concept "UGC DEMO FOOTAGE" pentru generatorul de video-ad-uri
"Ofertele Zilei" (ofertelezilei.ro).

PROBLEMA cu generatorul actual (pmax_video.py `montage`): produce un SLIDESHOW de
carduri-produs (poză + zoompan + preț). Arată a catalog, NU a reclamă reală.

ACEST CONCEPT: pentru 3-4 produse, găsește FOOTAGE REAL de demonstrație (oameni care
folosesc EXACT produsul) pe YouTube, îl filtrează de text STRĂIN (engleză, CJK, Cyrillic
via OCR), și montează o reclamă de tip UGC/demo: hook → demo produs 1 → ... → demo produs N
→ CTA, cu captions RO (beneficiu) + overlay de preț (vechi tăiat / nou / -50% burst) + muzică.

PIPELINE:
  1. Gemini transformă titlul RO al fiecărui produs într-un query EN/RO căutabil.
  2. yt-dlp `ytsearch6:<query>` → candidați scurți (<60s), descarcă 1-2 cei mai buni.
  3. CROP ANTI-WATERMARK (OBLIGATORIU, ÎNAINTE de orice): pentru fiecare clip descărcat
     taie ~8% sus + ~14% jos (unde stau watermark-urile/handle-urile TikTok/IG + captions
     arse) și re-încadrează la formatul țintă (9:16) DIN regiunea decupată. Asta scoate
     ieftin majoritatea watermark-urilor + a captions-urilor de jos.
  4. FILTRU TEXT STRĂIN (OBLIGATORIU, pe clipul DEJA decupat): eșantionează 4-6 cadre,
     OCR cu tesseract pentru EN + CJK + Cyrillic. REJECT dacă:
       - apar caractere CJK (　-鿿 / ぀-ヿ) sau Cyrillic (Ѐ-ӿ), SAU
       - OCR întoarce mai multe cuvinte ENGLEZEȘTI reale (the/and/for/with/your/buy/now/
         free/best/new/this/that/quality/perfect…) ȘI textul NU are diacritice RO (ăâîșț).
     Păstrăm DOAR footage RO sau fără text.
  5. Montaj: 2-3s footage curat per produs + captions RO (Gemini) + preț overlay (PIL PNG,
     pe pastile solide ca să NU re-expună watermark rezidual) + hook card + CTA card.
     Muzică via pv.make_music_deals.

FALLBACK ONEST: dacă un produs NU are niciun clip curat (rețea/yt-dlp/text străin), cade pe
POZA acelui produs cu fake-UGC: handheld shake (jitter) + punch zoom + captions + preț —
produsul NU e aruncat tăcut, se LOGează „fallback poză".

Usage:
  uv run --with yt-dlp --with pillow --with numpy --with requests concept_ugc.py \
      --storefront ofertelezilei.ro --brand "Ofertele Zilei" --fmt 9:16 --n 4
  uv run ... concept_ugc.py --manifest ofer_manifest.json --n 4
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile, random
from pathlib import Path

# ── reutilizăm motorul pmax_video.py (Gemini, muzică, caption wrap, fonturi, storefront) ──
PMAX_DIR = Path("/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/pmax-video/scripts")
sys.path.insert(0, str(PMAX_DIR))
import pmax_video as pv   # noqa: E402

FPS = 30
TESSERACT = "/usr/local/bin/tesseract" if os.path.exists("/usr/local/bin/tesseract") else "tesseract"
DEFAULT_BRAND = "Ofertele Zilei"

# crop anti-watermark: cât tăiem din footage ÎNAINTE de re-încadrare
CROP_TOP = 0.08      # ~8% sus (watermark/handle/logo)
CROP_BOTTOM = 0.14   # ~14% jos (captions arse / handle / progress bar)

# ════════════════════════════ utilitare ════════════════════════════

def _lei(p):
    if p is None:
        return ""
    s = f"{float(p):.2f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def _run(cmd, timeout=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def _ffdur(path):
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "csv=p=0", path], timeout=30)
    try:
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# ════════════════════════ 1. Gemini: query EN/RO + copy ════════════════════════

def gemini_queries(products, brand):
    """Titlu RO lung → query scurt EN căutabil pe YouTube (demo real)."""
    items = "\n".join(f"{i}. {p['title']}" for i, p in enumerate(products))
    prompt = f"""Ești un cercetător de creative ads. Pentru fiecare produs de mai jos (titlu românesc),
scrie un query SCURT de căutare pe YouTube care găsește un VIDEO REAL de DEMONSTRAȚIE / unboxing /
"how to use" al EXACT acestui produs (oameni care îl folosesc). Query-ul = engleză simplă de e-commerce
(termenii pe care un vânzător AliExpress/Amazon i-ar pune în titlu), 3-6 cuvinte, + cuvântul demo.

Produse:
{items}

Răspunde STRICT JSON: {{"queries": ["<query produs 0>", "<query produs 1>", ...]}}
EXACT {len(products)} elemente, în ordine. Fără ghilimele în interiorul query-urilor."""
    raw = pv._gemini([{"text": prompt}], want_json=True)
    try:
        j = json.loads(raw)
    except Exception:
        j = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    qs = j.get("queries", [])
    while len(qs) < len(products):
        qs.append(products[len(qs)]["title"][:40])
    return qs[:len(products)]


def gemini_copy(products, brand):
    """hook + caption beneficiu per produs + CTA, în RO corect (NU inventează prețuri)."""
    items = "\n".join(
        f"{i}. {p['title']} — {_lei(p.get('price'))} lei (vechi {_lei(p.get('old'))}, -{p.get('pct')}%)"
        for i, p in enumerate(products)
    )
    prompt = f"""Ești copywriter de reclame short-form (TikTok/Reels) pentru magazinul de OFERTE „{brand}" (RO, COD).
Reclama arată FOOTAGE REAL cu fiecare produs folosit. Scrie textele care apar pe ecran.
Produse (prețuri REALE — NU le schimba, NU inventa altele):
{items}

Răspunde STRICT JSON:
{{
 "hook": "<3-5 cuvinte, oprește scroll-ul, energie de reduceri; ex: Gadgeturile care fac diferența>",
 "captions": ["<caption BENEFICIU 2-4 cuvinte per produs, în ordine — ce face produsul pt tine, NU titlul>", ...],
 "cta": "<3-5 cuvinte, acțiune + urgență; poate numi {brand}; FĂRĂ http/www>"
}}
„captions" = EXACT {len(products)} elemente, în ordine. RO corect, diacritice, fără emoji, fără URL, fără ghilimele în texte.
Captions scurte care se înțeleg pe MUT (ex: „Rade legume în secunde", „Fără stropi în cuptor")."""
    raw = pv._gemini([{"text": prompt}], want_json=True)
    try:
        j = json.loads(raw)
    except Exception:
        j = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    caps = j.get("captions", [])
    while len(caps) < len(products):
        caps.append(products[len(caps)]["title"][:24])
    j["captions"] = caps[:len(products)]
    j.setdefault("hook", "Reduceri -50% azi")
    j.setdefault("cta", f"Comandă acum pe {brand}")
    return j


# ════════════════════════ 2. yt-dlp: search + download ════════════════════════

def yt_search(query, n=6):
    """Returnează listă de candidați [{id,title,duration,views}] din ytsearchN."""
    cmd = ["uv", "run", "--with", "yt-dlp", "yt-dlp", f"ytsearch{n}:{query}",
           "--dump-json", "--flat-playlist", "--no-warnings"]
    r = _run(cmd, timeout=120)
    out = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        out.append({
            "id": d.get("id"),
            "title": d.get("title") or "",
            "duration": d.get("duration"),
            "views": d.get("view_count") or 0,
        })
    return out


def yt_download(vid, outpath):
    """Descarcă un clip ≤1080p mp4. Întoarce path sau None."""
    cmd = ["uv", "run", "--with", "yt-dlp", "yt-dlp",
           "-f", "mp4[height<=1080]/best[height<=1080]/best",
           "--no-warnings", "--no-playlist",
           "-o", outpath, f"https://www.youtube.com/watch?v={vid}"]
    _run(cmd, timeout=240)
    return outpath if os.path.exists(outpath) and os.path.getsize(outpath) > 50000 else None


# ════════════ 2b. CROP ANTI-WATERMARK + re-încadrare la format țintă ════════════

def crop_reframe(src, W, H, outpath):
    """Taie ~CROP_TOP sus + ~CROP_BOTTOM jos din footage (unde stau watermark/handle/captions),
    apoi re-încadrează regiunea rămasă la W×H (scale-to-fill + crop centrat). Întoarce path sau None.

    Asta e PRIMUL pas pe orice clip descărcat: scoate ieftin majoritatea text-ului ars la margini
    înainte de OCR-ul de text străin (deci OCR-ul nu mai vede watermark-ul/captionul tăiat)."""
    # crop pe regiunea utilă: ih*(1-top-bottom) înălțime, începând de la ih*top
    keep = max(0.05, 1.0 - CROP_TOP - CROP_BOTTOM)
    vf = (f"crop=iw:ih*{keep:.4f}:0:ih*{CROP_TOP:.4f},"
          f"scale={W}:{H}:force_original_aspect_ratio=increase,"
          f"crop={W}:{H},fps={FPS},setsar=1")
    r = _run(["ffmpeg", "-y", "-i", src, "-vf", vf, "-an",
              "-pix_fmt", "yuv420p", outpath], timeout=180)
    if os.path.exists(outpath) and os.path.getsize(outpath) > 30000:
        return outpath
    sys.stderr.write(f"[crop_reframe] ffmpeg fail: {r.stderr[-300:]}\n")
    return None


# ════════════════════════ 3. FILTRU TEXT STRĂIN (OCR) ════════════════════════

_CJK_RANGES = [(0x3000, 0x303F), (0x3040, 0x309F), (0x30A0, 0x30FF),  # punct CJK, hiragana, katakana
               (0x4E00, 0x9FFF), (0x3400, 0x4DBF),                    # CJK ideographs (一-鿿 / 㐀-䶿)
               (0xAC00, 0xD7AF), (0x1100, 0x11FF),                    # hangul
               (0xFF00, 0xFFEF)]                                      # fullwidth
_CYR_RANGES = [(0x0400, 0x04FF), (0x0500, 0x052F)]                    # Cyrillic (Ѐ-ӿ) + supplement

# stoplist cuvinte ENGLEZEȘTI frecvente — dacă apar 2+ ȘI lipsesc diacriticele RO → text englez ars.
# include unități/cuvinte de e-commerce care apar des ars în footage AliExpress/Amazon (inches, size, color…).
_EN_STOP = {
    "the", "and", "for", "with", "your", "you", "buy", "now", "free", "best", "new",
    "this", "that", "quality", "perfect", "how", "use", "using", "product", "review",
    "amazing", "love", "click", "shop", "order", "get", "off", "sale", "price", "from",
    "ingredients", "before", "after", "easy", "great", "more", "all", "out", "our",
    "make", "made", "will", "have", "what", "just", "like", "want", "stainless", "steel",
    # unități / atribute de e-commerce (apar des arse pe demo-uri AliExpress/Amazon)
    "inches", "inch", "size", "color", "colour", "pack", "set", "piece", "pieces",
    "material", "weight", "length", "width", "height", "diameter", "capacity",
    "waterproof", "portable", "premium", "original", "official", "warranty", "shipping",
}
# unități care, chiar SINGURE, semnalează text englez ars (NU sunt cuvinte RO) → trigger puternic
_EN_UNIT_STRONG = {"inches", "inch", "waterproof", "stainless", "shipping", "warranty",
                   "quality", "premium", "colour", "ingredients"}
# cuvinte engleze care sunt ȘI românești sau prea ambigue → NU le numărăm
_EN_AMBIGUOUS = {"are", "all", "out", "can", "la", "no", "mai", "ce", "in", "on",
                 "set", "color", "material", "premium", "portable", "original"}
# mic allowlist de cuvinte RO frecvente (fără diacritice) ca să nu confundăm RO simplu cu engleză
_RO_WORDS = {"oferta", "oferte", "reducere", "pret", "lei", "comanda", "acum", "azi",
             "gratis", "transport", "produs", "produse", "magazin", "noua", "nou", "cel",
             "mai", "din", "pentru", "este", "are", "fara", "doar"}
_RO_DIACRITICS = set("ăâîșțĂÂÎȘȚşţŞŢ")


def _classify(text):
    """(n_cjk, n_cyr) — câte caractere CJK / Cyrillic apar în textul OCR."""
    n_cjk = n_cyr = 0
    for ch in text:
        o = ord(ch)
        if any(a <= o <= b for a, b in _CJK_RANGES):
            n_cjk += 1
        elif any(a <= o <= b for a, b in _CYR_RANGES):
            n_cyr += 1
    return n_cjk, n_cyr


def _english_hits(text):
    """(stop_words, strong_units) ENGLEZEȘTI găsite în OCR. Sunt cuvinte SPECIFIC englezești
    (the/and/quality/buy/inches/stainless…) pe care un text ROMÂNESC nu le conține — deci nu
    confundă RO-fără-diacritice cu engleza. NU folosim un trigger 'orice cuvânt latin' fiindcă
    acela ar respinge greșit footage RO legitim."""
    low = text.lower()
    words = re.findall(r"[a-z]{3,}", low)
    stop = {w for w in words if w in _EN_STOP and w not in _EN_AMBIGUOUS}
    strong = {w for w in words if w in _EN_UNIT_STRONG}
    return stop, strong


def _ocr_frame(clip, t, fp):
    """Extrage un cadru la timpul t și OCR-uiește în DOUĂ regimuri, întorcând (clean, aggressive):
      - clean      = o trecere conservatoare (grayscale ușor, psm 6 bloc) → fiabilă pt SCRIPT/ALFABET
                     (CJK/Cyrillic se numără DOAR aici, ca să nu confundăm zgomotul cu text străin);
      - aggressive = clean + treceri agresive (upscale + contrast + negate, psm 11 sparse) → prinde
                     text ENGLEZ fin/ars; e zgomotos pe alfabet, deci îl folosim DOAR pt cuvinte EN
                     (cuvintele reale din stoplist nu apar din zgomot, spre deosebire de caractere CJK).
    Limbi: eng+ron+chi_sim+rus (ron → citește diacriticele RO corect)."""
    raw = fp.with_suffix(".raw.png")
    _run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", clip, "-frames:v", "1",
          "-vf", "scale=1280:-1", str(raw)], timeout=60)
    if not raw.exists():
        return None, None
    # trecere CLEAN (conservatoare, fiabilă pt CJK/Cyrillic)
    pc = fp.with_suffix(".c.png")
    _run(["ffmpeg", "-y", "-i", str(raw), "-vf", "format=gray", str(pc)], timeout=30)
    clean = ""
    if pc.exists():
        r = _run([TESSERACT, str(pc), "stdout", "-l", "eng+ron+chi_sim+rus", "--psm", "6"], timeout=60)
        clean = r.stdout or ""
    # treceri AGRESIVE (contrast+negate, sparse) — doar pt EN
    aggressive = clean
    for neg in ("", "negate,"):
        pp = fp.with_suffix(f".{('n' if neg else 'p')}.png")
        _run(["ffmpeg", "-y", "-i", str(raw),
              "-vf", f"format=gray,{neg}eq=contrast=1.6", str(pp)], timeout=30)
        if pp.exists():
            r = _run([TESSERACT, str(pp), "stdout", "-l", "eng", "--psm", "11"], timeout=60)
            aggressive += " " + (r.stdout or "")
    return clean, aggressive


def ocr_foreign_scan(clip, tmp, n_frames=8):
    """Eșantionează n_frames din clip (DEJA decupat) și OCR-uiește. REJECT dacă:
      - SUMA CJK ≥ 6  sau  Cyrillic ≥ 6 din trecerea CLEAN (prag mai sus = anti-halucinare OCR), SAU
      - apare ≥1 unitate englezească 'tare' (inches/stainless/waterproof…), SAU
      - ≥2 cuvinte din stoplistul englez (the/buy/quality/free…).
    EN se caută în trecerea agresivă (prinde text fin), CJK/Cyrillic DOAR în cea clean (fără zgomot).
    Triggerele englezești se DEZARMEAZĂ dacă apar diacritice RO (clip RO). Footage fără text → trece.
    Astfel supraviețuiește DOAR footage RO sau fără text."""
    dur = _ffdur(clip)
    if dur <= 0:
        return {"ok": False, "reason": "durată 0 / clip ilizibil",
                "cjk": 0, "cyr": 0, "en": [], "frames": 0}
    fracs = [0.06, 0.18, 0.30, 0.42, 0.54, 0.66, 0.78, 0.90][:n_frames]
    ts = [dur * f for f in fracs]
    tot_cjk = tot_cyr = 0
    stop_words, strong_units = set(), set()
    ro_diac = False
    frames_scanned = 0
    for i, t in enumerate(ts):
        fp = tmp / f"ocr_{Path(clip).stem}_{i}.png"
        clean, aggressive = _ocr_frame(clip, t, fp)
        if clean is None:
            continue
        frames_scanned += 1
        c, y = _classify(clean)                 # CJK/Cyr DOAR din clean (fiabil)
        tot_cjk += c
        tot_cyr += y
        s, st = _english_hits(aggressive)       # EN din agresiv (prinde fin)
        stop_words |= s
        strong_units |= st
        if any(ch in _RO_DIACRITICS for ch in clean):
            ro_diac = True
    english_foreign = (not ro_diac) and (len(strong_units) >= 1 or len(stop_words) >= 2)
    en_report = sorted(strong_units | stop_words)
    CJK_TH = CYR_TH = 6                          # prag mai sus = robust la halucinație OCR pe textură
    ok = (frames_scanned > 0) and (tot_cjk < CJK_TH) and (tot_cyr < CYR_TH) and not english_foreign
    reason = ""
    if frames_scanned == 0:
        reason = "0 cadre extrase"
    elif tot_cjk >= CJK_TH:
        reason = f"text CJK ({tot_cjk} caractere)"
    elif tot_cyr >= CYR_TH:
        reason = f"text Cyrillic ({tot_cyr} caractere)"
    elif english_foreign:
        reason = "text ENGLEZ ars (" + ", ".join(en_report[:6]) + ")"
    return {"ok": ok, "reason": reason, "cjk": tot_cjk, "cyr": tot_cyr,
            "en": en_report, "ro_diac": ro_diac, "frames": frames_scanned}


# ════════════ orchestrare: găsește footage curat (decupat) per produs ════════════

def find_clean_footage(products, queries, W, H, tmp, log):
    """Pentru fiecare produs: caută, descarcă candidați scurți, CROP anti-watermark, apoi filtru OCR.
    Întoarce listă paralelă cu products: path la footage CURAT și DECUPAT (gata de montaj), sau None.
    Plus un dict de statistici de respingere {en, cjkcyr}."""
    results = []
    stats = {"en": 0, "cjkcyr": 0}
    for pi, (p, q) in enumerate(zip(products, queries)):
        log.append(f"\n── Produs {pi}: {p['title'][:50]}")
        log.append(f"   query: \"{q}\"")
        clean = None
        try:
            cands = yt_search(q, n=6)
        except Exception as e:
            log.append(f"   ! yt-dlp search a eșuat: {e}")
            cands = []
        short = [c for c in cands if c.get("duration") and 4 < c["duration"] < 60]
        short.sort(key=lambda c: (abs(c["duration"] - 25), -c["views"]))
        log.append(f"   {len(cands)} rezultate, {len(short)} scurte (<60s)")
        tried = 0
        for c in short[:4]:                       # până la 4 candidați scurți
            tried += 1
            dl = tmp / f"dl_{pi}_{c['id']}.mp4"
            log.append(f"   ↓ candidat {c['id']} ({c['duration']}s, {c['views']} views): {c['title'][:48]}")
            got = None
            try:
                got = yt_download(c["id"], str(dl))
            except Exception as e:
                log.append(f"     ! download eșuat: {e}")
            if not got:
                log.append("     ! descărcare goală/eșuată — skip")
                continue
            # 2b. CROP anti-watermark + re-încadrare ÎNAINTE de OCR
            cropped = tmp / f"crop_{pi}_{c['id']}.mp4"
            cp = crop_reframe(got, W, H, str(cropped))
            if not cp:
                log.append("     ! crop/re-încadrare eșuată — skip")
                continue
            verdict = ocr_foreign_scan(cp, tmp)
            if verdict["ok"]:
                log.append(f"     ✓ CURAT (crop {int(CROP_TOP*100)}%/{int(CROP_BOTTOM*100)}%, "
                           f"CJK={verdict['cjk']}, Cyr={verdict['cyr']}, EN={verdict['en']}, "
                           f"{verdict['frames']} cadre)")
                clean = cp
                break
            else:
                if verdict.get("en"):
                    stats["en"] += 1
                elif verdict["cjk"] >= 3 or verdict["cyr"] >= 3:
                    stats["cjkcyr"] += 1
                log.append(f"     ✗ RESPINS: {verdict['reason']}")
        log.append(f"   → produs {pi}: încercați {tried}, "
                   + ("footage curat (decupat)" if clean else "FĂRĂ footage curat → fallback poză"))
        results.append(clean)
    return results, stats


# ════════════════════════ 4. Overlay-uri PIL (caption + preț) ════════════════════════
# overlay-urile stau pe PASTILE solide (badge preț pe negru, prețul nou pe accent) ca să NU
# re-expună un watermark/text rezidual rămas în footage după crop.

def make_overlay_png(caption, prod, role, W, H, brand, path):
    """Overlay transparent la format: caption RO sus (UGC style), badge preț jos (vechi tăiat / nou),
    burst -X%. Toate textele NOASTRE stau pe pastile/benzi solide (anti re-expunere watermark)."""
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np, math
    accent = "#E63329"
    ac = pv._hex(accent)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # benzi gradient sus+jos pt lizibilitate peste footage
    top = np.zeros((int(H * 0.26), W, 4), np.uint8)
    top[:, :, 3] = np.linspace(190, 0, top.shape[0]).astype(np.uint8)[:, None]
    ov.alpha_composite(Image.fromarray(top, "RGBA"), (0, 0))
    bot = np.zeros((int(H * 0.34), W, 4), np.uint8)
    bot[:, :, 3] = np.linspace(0, 205, bot.shape[0]).astype(np.uint8)[:, None]
    ov.alpha_composite(Image.fromarray(bot, "RGBA"), (0, H - bot.shape[0]))
    # bandă aproape opacă pe ULTIMUL ~6% — maschează orice handle/text rezidual la marginea de jos
    foot = np.zeros((int(H * 0.06), W, 4), np.uint8)
    foot[:, :, 3] = 225
    ov.alpha_composite(Image.fromarray(foot, "RGBA"), (0, H - foot.shape[0]))
    d = ImageDraw.Draw(ov)

    # ── caption sus (mare, UGC) ──
    base_fs = int(H * (0.060 if role in ("hook", "cta") else 0.050))
    f_top, lines = pv._fit_font(caption, d, W * 0.90, base_fs, int(H * 0.036), 2)
    lh = int(f_top.size * 1.16)
    ty = int(H * 0.085)
    for ln in lines:
        lw = d.textlength(ln, font=f_top)
        d.text(((W - lw) / 2, ty), ln, font=f_top, fill="white",
               stroke_width=max(3, f_top.size // 14), stroke_fill=(0, 0, 0, 245))
        ty += lh
    # etichetă brand sub caption (doar pe hook/cta), pe pastilă accent
    if role in ("hook", "cta"):
        fb = ImageFont.truetype(pv.FONT, int(H * 0.026))
        bt = brand.upper()
        bw = d.textlength(bt, font=fb)
        pad = int(H * 0.012)
        bx0, bx1 = (W - bw) / 2 - pad, (W + bw) / 2 + pad
        d.rounded_rectangle([bx0, ty + 8, bx1, ty + 8 + int(H * 0.026) + pad], radius=int(H * 0.014),
                            fill=(ac[0], ac[1], ac[2], 235))
        d.text(((W - bw) / 2, ty + 8 + pad // 2), bt, font=fb, fill="white")

    # ── badge preț jos (vechi tăiat + nou pe accent) + burst -X% ──
    if prod is not None:
        p, old, pct = prod.get("price"), prod.get("old"), prod.get("pct")
        by = int(H * 0.85)
        newtxt = f"{_lei(p)} lei"
        fs_new = int(H * 0.066)
        fnew = ImageFont.truetype(pv.FONT, fs_new)
        fs_old = int(H * 0.032)
        fold = ImageFont.truetype(pv.FONT, fs_old)
        oldtxt = f"{_lei(old)} lei" if old else ""
        new_w = d.textlength(newtxt, font=fnew)
        old_w = d.textlength(oldtxt, font=fold) if oldtxt else 0
        gap = int(W * 0.030)
        total_w = new_w + (old_w + gap if oldtxt else 0)
        bx = (W - total_w) / 2
        pad = int(fs_new * 0.42)
        # pastilă neagră solidă sub TOT prețul → niciun watermark rezidual nu transpare
        d.rounded_rectangle([bx - pad, by - pad, bx + total_w + pad, by + fs_new + pad],
                            radius=int(fs_new * 0.42), fill=(0, 0, 0, 200))
        xcur = bx
        if oldtxt:
            oy = by + (fs_new - fs_old)
            d.text((xcur, oy), oldtxt, font=fold, fill=(210, 210, 210, 255))
            d.line([xcur, oy + fs_old * 0.55, xcur + old_w, oy + fs_old * 0.55],
                   fill=(245, 70, 70, 255), width=max(3, fs_old // 9))
            xcur += old_w + gap
        d.rounded_rectangle([xcur - pad // 2, by - pad // 2, xcur + new_w + pad // 2, by + fs_new + pad // 2],
                            radius=int(fs_new * 0.3), fill=(ac[0], ac[1], ac[2], 255))
        d.text((xcur, by), newtxt, font=fnew, fill="white",
               stroke_width=max(2, fs_new // 22), stroke_fill=(0, 0, 0, 235))
        # burst -X% colț dreapta-sus
        if pct:
            bs = int(H * 0.072)
            bxc, byc = int(W * 0.80), int(H * 0.305)
            burst = Image.new("RGBA", (bs * 2, bs * 2), (0, 0, 0, 0))
            bd = ImageDraw.Draw(burst)
            pts = []
            for i in range(20):
                ang = math.pi * i / 10 - 0.15
                r = bs if i % 2 == 0 else bs * 0.76
                pts.append((bs + r * math.cos(ang), bs + r * math.sin(ang)))
            bd.polygon(pts, fill=(235, 30, 35, 255), outline=(255, 255, 255, 255))
            fbu = ImageFont.truetype(pv.FONT, int(bs * 0.5))
            btx = f"-{pct}%"
            bw2 = bd.textlength(btx, font=fbu)
            bd.text((bs - bw2 / 2, bs - bs * 0.28), btx, font=fbu, fill="white",
                    stroke_width=2, stroke_fill=(0, 0, 0, 220))
            ov.alpha_composite(burst, (bxc - bs, byc - bs))
    ov.save(path)
    return path


# ════════════════════════ 5a. clip din FOOTAGE REAL (deja decupat) ════════════════════════

def clip_from_footage(footage, overlay, dur, W, H, path):
    """footage = clip DEJA decupat (crop_reframe) la W×H. Ia 'dur' sec din mijloc + overlay PIL static.
    Întoarce path sau None."""
    src_dur = _ffdur(footage)
    start = max(0.0, (src_dur - dur) / 2) if src_dur > dur else 0.0
    fc = (f"[0:v]trim=0:{dur},setpts=PTS-STARTPTS,fps={FPS},setsar=1[base];"
          f"[base][1:v]overlay=0:0:format=auto[v]")
    r = _run(["ffmpeg", "-y", "-ss", f"{start:.2f}", "-i", footage, "-loop", "1", "-i", overlay,
              "-filter_complex", fc, "-map", "[v]", "-t", f"{dur:.2f}",
              "-r", str(FPS), "-an", "-pix_fmt", "yuv420p", path], timeout=120)
    if os.path.exists(path) and os.path.getsize(path) > 10000:
        return path
    sys.stderr.write(f"[footage clip] ffmpeg fail: {r.stderr[-300:]}\n")
    return None


# ════════════════════════ 5b. clip FAKE-UGC din POZĂ (fallback) ════════════════════════

def clip_fake_ugc(img, overlay, dur, W, H, path, seed=0):
    """Fake-UGC pe poză: handheld shake (jitter x/y) + punch zoom + overlay static.
    Supra-probă mai mare ca jitter-ul să nu scoată bare negre."""
    frames = int(dur * FPS)
    rnd = random.Random(seed)
    amp_x, amp_y = 26, 34
    fx = rnd.uniform(0.7, 1.5)
    fy = rnd.uniform(0.6, 1.3)
    px = rnd.uniform(0, 6.28)
    py = rnd.uniform(0, 6.28)
    SW, SH = int(W * 1.18), int(H * 1.18)
    z0 = 1.04 + rnd.uniform(0, 0.03)
    fc = (
        f"[0:v]scale={SW}:{SH}:force_original_aspect_ratio=increase,crop={SW}:{SH},setsar=1,"
        f"zoompan=z='min({z0}+0.0016*on,1.20)':d={frames}:s={SW}x{SH}:fps={FPS},"
        f"crop={W}:{H}:"
        f"x='(in_w-{W})/2+{amp_x}*sin({fx:.2f}*t+{px:.2f})':"
        f"y='(in_h-{H})/2+{amp_y}*sin({fy:.2f}*t+{py:.2f})'[base];"
        f"[base][1:v]overlay=0:0:format=auto[v]"
    )
    r = _run(["ffmpeg", "-y", "-loop", "1", "-i", img, "-loop", "1", "-i", overlay,
              "-filter_complex", fc, "-map", "[v]", "-t", f"{dur:.2f}",
              "-r", str(FPS), "-an", "-pix_fmt", "yuv420p", path], timeout=120)
    if os.path.exists(path) and os.path.getsize(path) > 10000:
        return path
    sys.stderr.write(f"[fake-ugc clip] ffmpeg fail: {r.stderr[-300:]}\n")
    return None


# ════════════════════════ montaj final ════════════════════════

def build_ad(products, copy, footage, W, H, brand, out_path, tmp, log):
    """hook → demo produse → CTA. Footage real (decupat) unde există, fake-UGC din poză unde nu.
    Întoarce (out_path, used_real, n_fallback, DUR)."""
    hook, cta, caps = copy["hook"], copy["cta"], copy["captions"]
    DUR_HOOK, DUR_PROD, DUR_CTA = 2.4, 2.8, 2.6
    beats = []
    used_real = 0
    n_fallback = 0

    # HOOK
    hook_ov = make_overlay_png(hook, None, "hook", W, H, brand, str(tmp / "ov_hook.png"))
    hook_src_idx = next((i for i, f in enumerate(footage) if f), None)
    hc = None
    if hook_src_idx is not None:
        hc = clip_from_footage(footage[hook_src_idx], hook_ov, DUR_HOOK, W, H, str(tmp / "b_hook.mp4"))
    if hc is None:
        hc = clip_fake_ugc(products[0]["img"], hook_ov, DUR_HOOK, W, H, str(tmp / "b_hook.mp4"), seed=99)
    if hc:
        beats.append((hc, DUR_HOOK))

    # PRODUSE
    for i, p in enumerate(products):
        ov = make_overlay_png(caps[i], p, "product", W, H, brand, str(tmp / f"ov_{i}.png"))
        c = None
        if footage[i]:
            c = clip_from_footage(footage[i], ov, DUR_PROD, W, H, str(tmp / f"b_{i}.mp4"))
            if c:
                used_real += 1
                log.append(f"   beat {i}: FOOTAGE REAL")
        if c is None:
            c = clip_fake_ugc(p["img"], ov, DUR_PROD, W, H, str(tmp / f"b_{i}.mp4"), seed=i * 7 + 3)
            n_fallback += 1
            log.append(f"   beat {i}: fake-UGC (poză){' — fallback, fără footage curat' if not footage[i] else ''}")
        if c:
            beats.append((c, DUR_PROD))

    # CTA
    cta_ov = make_overlay_png(cta, products[-1], "cta", W, H, brand, str(tmp / "ov_cta.png"))
    cta_src = next((footage[i] for i in range(len(footage) - 1, -1, -1) if footage[i]), None)
    cc = None
    if cta_src:
        cc = clip_from_footage(cta_src, cta_ov, DUR_CTA, W, H, str(tmp / "b_cta.mp4"))
    if cc is None:
        cc = clip_fake_ugc(products[-1]["img"], cta_ov, DUR_CTA, W, H, str(tmp / "b_cta.mp4"), seed=55)
    if cc:
        beats.append((cc, DUR_CTA))

    DUR = sum(b[1] for b in beats)
    music = pv.make_music_deals(DUR, str(tmp / "music.wav"))

    lst = tmp / "list.txt"
    lst.write_text("\n".join(f"file '{b[0]}'" for b in beats))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
           "-i", music, "-map", "0:v", "-map", "1:a",
           "-af", f"afade=t=out:st={max(DUR-0.6,0):.2f}:d=0.6", "-shortest",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-movflags", "+faststart", str(out_path)]
    _run(cmd, timeout=240)
    return out_path, used_real, n_fallback, DUR


# ════════════════════════ produse: storefront / manifest ════════════════════════

def _normalize_image(src, dst):
    """Re-salvează imaginea ca JPEG static curat (RGB, prim cadru dacă e GIF/animat).
    Storefront-ul poate servi GIF-uri (placeholder lazy-load) sub extensie .jpg → ffmpeg crapă
    cu „Option loop not found" pe ele. PIL le citește și scoate un cadru static valid."""
    from PIL import Image
    try:
        im = Image.open(src)
        im.seek(0)              # primul cadru, dacă e animat
        im = im.convert("RGB")
        im.save(dst, "JPEG", quality=92)
        return dst
    except Exception as e:
        sys.stderr.write(f"[normalize] {src}: {e}\n")
        return None


def resolve_products(a, tmp):
    """Întoarce listă de produse [{title,price,old,pct,img(abs)}] din --storefront sau --manifest.
    Normalizează fiecare imagine la JPEG static (anti-GIF) → ffmpeg/PIL nu mai crapă."""
    if a.storefront:
        print(f"● trag produse din storefront public: {a.storefront} ...")
        outdir = tmp / "src"
        products = pv.fetch_storefront(a.storefront, a.n, str(outdir))
    elif a.manifest:
        man = json.load(open(a.manifest))
        base = Path(a.manifest).resolve().parent
        products = man[:a.n]
        for p in products:
            ip = Path(p["img"])
            p["img"] = str(ip if ip.is_absolute() else base / ip)
    else:
        sys.exit("dă --storefront <domeniu> sau --manifest <json>")
    # căi absolute + normalizare la JPEG static
    norm_dir = tmp / "norm"
    norm_dir.mkdir(parents=True, exist_ok=True)
    good = []
    for i, p in enumerate(products):
        src = str(Path(p["img"]).resolve())
        nj = str(norm_dir / f"n{i:02d}.jpg")
        if _normalize_image(src, nj):
            p["img"] = nj
            good.append(p)
        else:
            print(f"   ! imagine ilizibilă, sar peste produs: {p.get('title','')[:40]}")
    return good


# ════════════════════════════ main ════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Concept UGC demo-footage video ad (Ofertele Zilei)")
    ap.add_argument("--storefront", default="", help="domeniu storefront public (ex ofertelezilei.ro)")
    ap.add_argument("--manifest", default="", help="JSON cu produse [{title,price,old,pct,img}]")
    ap.add_argument("--brand", default=DEFAULT_BRAND)
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop/pmax-video"))
    ap.add_argument("--fmt", default="9:16", choices=list(pv.FMT))
    ap.add_argument("--n", type=int, default=4, help="câte produse")
    ap.add_argument("--no-network", action="store_true", help="sare peste yt-dlp (testează fallback-ul poză)")
    a = ap.parse_args()

    W, H = pv.FMT[a.fmt]
    brand = a.brand
    brandslug = re.sub(r"[^A-Za-z0-9]", "", brand)

    tmp = Path(tempfile.mkdtemp(prefix="ugc_"))
    log = []

    # 0. produse
    products = resolve_products(a, tmp)
    if len(products) < 1:
        sys.exit(f"prea puține produse ({len(products)})")
    print(f"● {brand}: {len(products)} produse ({a.fmt}, {W}×{H}) →")
    for p in products:
        print(f"   - {p['title'][:55]}  ({_lei(p['price'])} lei, -{p.get('pct')}%)")

    # 1. Gemini queries + copy
    print("● Gemini: extrag query-uri de căutare + copy RO...")
    queries = gemini_queries(products, brand)
    copy = gemini_copy(products, brand)
    for p, q in zip(products, queries):
        print(f"   query[{p['title'][:28]}] = \"{q}\"")
    print(f"   hook: „{copy['hook']}”  · CTA: „{copy['cta']}”")
    for i, c in enumerate(copy["captions"]):
        print(f"   caption {i}: „{c}”")

    # 2-4. footage real → crop anti-watermark → filtru text străin (EN/CJK/Cyrillic)
    if a.no_network:
        print("● --no-network: sar peste yt-dlp → forțez fallback fake-UGC")
        footage = [None] * len(products)
        stats = {"en": 0, "cjkcyr": 0}
        log.append("MOD --no-network: niciun footage descărcat (test fallback).")
    else:
        print("● caut footage real (yt-dlp) → CROP anti-watermark → filtru text străin (OCR)...")
        footage, stats = find_clean_footage(products, queries, W, H, tmp, log)

    n_real = sum(1 for f in footage if f)
    print(f"● footage curat (decupat) găsit: {n_real}/{len(products)} produse  "
          f"(respinse: {stats['en']} EN, {stats['cjkcyr']} CJK/Cyr)")

    # 5. montaj
    out_path = str(Path(a.out) / f"{brandslug}_UGC_{a.fmt.replace(':', 'x')}.mp4")
    print(f"● montez reclama ({a.fmt}, captions RO + preț + muzică)...")
    out_path, used_real, n_fallback, dur = build_ad(products, copy, footage, W, H, brand,
                                                    out_path, tmp, log)
    print(f"✓ render: {out_path}  (~{dur:.1f}s, {used_real} beat-uri footage real, {n_fallback} fallback poză)")

    # log detaliat lângă output
    logp = Path(a.out) / f"{brandslug}_UGC_log.txt"
    logp.write_text("\n".join(log))
    print(f"  log filtru: {logp}")
    print(f"\nQUERIES={json.dumps(queries, ensure_ascii=False)}")
    print(f"REAL_FOOTAGE_BEATS={used_real}")
    print(f"REJECTED_EN={stats['en']} REJECTED_CJK_CYR={stats['cjkcyr']}")
    print(f"OUTPUT={out_path}")


if __name__ == "__main__":
    main()
