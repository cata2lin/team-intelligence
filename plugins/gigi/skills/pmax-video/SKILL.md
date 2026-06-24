---
name: pmax-video
description: Fabrică AUTOMATĂ de reclame VIDEO pentru Google Ads Performance Max / YouTube Shorts (formate 9:16 + 1:1 + 16:9) din datele REALE ale brandului. Două moduri. (1) `make` — reclamă CINEMATIC single-produs (gândit pt parfumuri Esteban/GT/Nubra): trage poze curate din Shopify, Gemini regizează un script Hook-Body-CTA, rembg decupează produsul REAL (eticheta păstrată fidel), Veo generează un fundal cinematic, compus cu glow/umbră/captions/muzică + oferta reală a brandului (2+1 GRATIS) în CTA. (2) `montage` — reclamă „deals" rapidă multi-produs (pt magazine de oferte gen Ofertele Zilei): trage produsele + prețurile/reducerile REALE din storefront-ul public, scrie hook-uri scroll-stopping în RO, și randează în mai multe STILURI de editare — classic (carduri produs + tranziții slide/wipe), kinetic (typografie animată + price-drop), bento (grid „sute de oferte"), mograph (motion-graphics: cutout-uri rembg care zboară/se suprapun peste fundal în mișcare — cel mai „non-slideshow"), ugc (footage demo real cu filtru de text străin). Captions muted-first, pe beneficiu, română corectă; prețurile/reducerile sunt REALE (preț vechi tăiat / nou / -50%), niciodată inventate. Montajul = ~$0 (FFmpeg-first); Veo = accent opțional. Folosește pentru „fă un video pentru Google Ads / PMax", „reclamă video pe produs", „montaj deals/oferte", „video ad short-form", „creative video din pozele magazinului", „video pe produsele Ofertele Zilei".
argument-hint: make --brand <B> --store <key> --ai --all-formats | montage --storefront <domeniu> --brand <B> --style <classic|kinetic|bento|mograph|ugc> [--all-formats]
---

# pmax-video — fabrică automată de video-ad-uri (PMax / Shorts)

Două moduri. `make` = cinematic single-produs (Veo + produs fidel). `montage` = deals multi-produs (FFmpeg, ieftin, mai multe stiluri de editare). Creierul de copy/montaj = [`METHODOLOGY.md`](METHODOLOGY.md) (Hook 0-3s decid ~71%, beat la 2-3s, captions pe mut, CTA cu ofertă).

## Setup
- `GEMINI_API_KEY` (KB) pt copy/regie. FFmpeg, font Poppins/Arial.
- rembg (decupaj): venv local `scripts/.rembg` (creat o dată cu `bash ../ad-banners/scripts/setup_env.sh`). NU se commit-ează.
- Rulează cu `uv run scripts/pmax_video.py ...` (deps inline PEP-723).

## Mod 1 — `make` (cinematic single-produs, parfumuri)
```bash
# produs REAL decupat + fundal Veo cinematic + glow/umbră/muzică + ofertă reală în CTA
uv run scripts/pmax_video.py make --brand "Maison d'Esteban" --store esteban --ai --all-formats --open
```
- `--store` trage poze curate din Shopify; `--images <dir>` = poze locale.
- `--ai` = path FIDEL (rembg cutout + Veo bg). Fără `--ai` = montaj simplu poză+Ken-Burns.
- Oferta reală per brand din `BRAND_OFFERS` (Esteban/GT/Nubra = „2+1 GRATIS"); `--offer` o suprascrie.
- Captions = beneficii (NU text de pe etichetă), fără URL în CTA, gramatică RO, wrap conștient de propoziție.

## Mod 2 — `montage` (deals multi-produs, Ofertele Zilei & co)
```bash
# trage produse + prețuri/reduceri din storefront-ul public, scrie hook + montează
uv run scripts/pmax_video.py montage --storefront ofertelezilei.ro --brand "Ofertele Zilei" \
    --style mograph --all-formats --open
```
- Sursă produse: `--storefront <domeniu>` (Shopify `/products.json` public, fără auth) sau `--manifest <json>`.
- Preț REAL: preț vechi tăiat + preț nou + badge `-X%` (din `compare_at_price`). NU se inventează prețuri.
- Hook scroll-stopping (formule: curiozitate/problemă/șoc-preț/FOMO/claim) + 3 variante pt A/B.
- `--n` produse, `--out`, `--fmt`, `--all-formats`.

### Stiluri de editare (`--style`)
| Stil | Ce e | Cost |
|---|---|---|
| `classic` | Carduri produs (produs mare + preț) + tranziții slide/wipe/circle + punch-in/out, text static | $0 |
| `kinetic` | Typografie animată + price-drop cu impact, carduri cu border accent | $0 |
| `bento` | Grid „sute de oferte" (deals dump) + tile-uri hero | $0 |
| **`mograph`** | **Motion-graphics**: cutout-uri rembg care zboară/scalează/se suprapun peste fundal bokeh în mișcare, motion blur, ritm pe beat. **Cel mai „non-slideshow".** Produsele care nu se decupează curat → tile rotunjit (fallback). | $0 |
| `ugc` | Footage demo REAL (yt-dlp) cu **filtru de text străin** (crop watermark + OCR: respinge CJK/chirilică/engleză). Footage rar pt dropshipping → fallback pe poze. | $0 |
| `reel` | Editare ritmică **pe beat** (124 BPM): tăieturi cu lungimi variate (burst + hold), punch-in, white-flash, captions kinetice. Footage real dacă există, altfel Ken-Burns pe poze. | $0 |

## Cost
- **Montajul (toate stilurile) = ~$0** — FFmpeg pur + un singur apel Gemini-text ieftin per render.
- Veo (mod `make --ai`, sau accent) = `veo-3.0-fast` ~$0.15/s × 8s ≈ $1.20/clip. ⚠️ **`veo-3.0-fast` e deprecated (shutdown 30 iun 2026)** → de migrat la Veo 3.1 dacă se păstrează calea Veo.

## Capcane
- FFmpeg fără freetype → fără filtrul `drawtext`; tot textul = PNG-uri PIL overlay.
- `zoompan` cu dimensiuni FIXE (`s=WxH`); `crop` cu dimensiuni variabile în timp = output 0-byte.
- Footage real de dropshipping e saturat de text străin ars + OCR-ul (tesseract) e nesigur → `ugc`/`reel` cad des pe poze. Fix viitor: filtru de limbă pe Gemini-vision.
- Pozele de produs au margini albe → `_trim_product` taie la conținut înainte de scale-to-fill.

## Livrare în PMax (după render)
Urcă pe canalul YouTube al brandului + atașează la asset group (vezi `gigi:google-ads-mcc` → `yt_upload.py` + `attach_videos.py`).
