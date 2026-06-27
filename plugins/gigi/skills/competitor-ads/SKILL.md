---
name: competitor-ads
description: Competitive Creative Intelligence — vezi ce reclame rulează competiția, găsește-i CELE MAI BUNE creative, analizează-le cu AI și primește recomandări de creative pentru brandurile noastre. Rank pe LONGEVITATE (zile active = cel mai bun proxy public de performanță: brandurile omoară repede ce nu merge și scalează câștigătorii). Analiză VISION (Gemini) pe creativele câștigătoare → unghi, hook, ofertă, format, CTA + pattern-uri comune. COMPARATIV cu ad-urile noastre și RECOMANDĂRI concrete de creative de testat (pe gap-uri). Sursă v1: Google Ads Transparency Center (fără login, reutilizează gigi:ads-transparency); Meta Ad Library + TikTok se adaugă. Folosește pentru „ce reclame rulează competitorul X", „cele mai bune creative ale unui competitor", „analiză creative competiție", „ce ads merg la concurență", „recomandă-mi creative", „compară ad-urile noastre cu ale competitorului", „spy pe reclamele concurenței", „competitor ad intelligence", „creative analysis".
argument-hint: best <domeniu...> [--top N] | analyze <competitor> [--vs <domeniul-nostru>] [--top N]
---

# competitor-ads — Competitive Creative Intelligence

Răspunde la „ce reclame rulează competiția, care-s cele mai bune, ce să copiem/testăm".
Construit PE `gigi:ads-transparency` (RPC-ul Google Transparency Center, fără auth).

## Comenzi
```bash
# GOOGLE — cele mai bune creative ale unui competitor (rank pe longevitate)
uv run scripts/competitor_ads.py best rasheed.ro --top 10
uv run scripts/competitor_ads.py best notino.ro evero.ro parfumat.ro   # batch
# Google: best + analiză vision (Gemini) + comparativ cu noi + recomandări
uv run scripts/competitor_ads.py analyze rasheed.ro --top 6 --vs esteban.ro

# TIKTOK — reclamele unui advertiser din TikTok Ad Library UE (Playwright + Chrome)
uv run --with playwright scripts/tiktok_ads.py best "answear" --top 10
uv run --with playwright scripts/tiktok_ads.py best "Answear" --json

# META (FB/IG) — Ad Library API oficial (cere META_ADLIB_TOKEN = USER token cont confirmat)
uv run scripts/meta_ads.py best "answear" --country RO --top 10
uv run scripts/meta_ads.py analyze "answear" --vs "nubra"
```

## Ideea de bază: longevitatea = proxy de câștigător
Bibliotecile publice de reclame NU dau performanță (spend/ROAS). Dar dau **de când**
rulează fiecare creativ. Brandurile **opresc repede** ce nu convertește și **scalează**
ce merge → un creativ activ de sute de zile = aproape sigur un câștigător. De-aia `best`
ordonează pe **zile active** (🟢 = activ acum, ultimele 7 zile).

## Ce face `analyze`
1. **Best** — top creative pe longevitate.
2. **Vision (Gemini)** pe imaginile câștigătoare → per creativ: unghi/mesaj, hook vizual,
   ofertă/promo, format, CTA; apoi **pattern-urile comune** (ce repetă = ce funcționează).
3. **Comparativ** cu domeniul nostru (`--vs`) — câte creative avem, ce longevitate.
4. **Recomandări** — 5 creative concrete de testat, pe **gap-urile** față de competitor.

LLM-ul vine din KB: `GEMINI_API_KEY` / `GOOGLE_AI_API_KEY` (vezi [[image-gen-skill]] pt aceleași chei).

## Surse & roadmap
| Sursă | Stare |
|---|---|
| **Google** Ads Transparency Center | ✅ (fără auth) — `competitor_ads.py` |
| **TikTok** Ad Library UE | ✅ (`tiktok_ads.py`, Playwright + Chrome) |
| **Meta** (FB/IG) Ad Library API | ✅ (`meta_ads.py`, oficial) — cere `META_ADLIB_TOKEN` = **USER token de cont confirmat** (facebook.com/ID). App/system tokens dau 2332002. Tokenul e short-lived (~2h); când lipsește/expiră, **tool-ul îl cere INTERACTIV** (explică de unde-l iei, validează că-i USER, îl salvează singur în KB). Opțional: long-lived 60z prin `fb_exchange_token` cu app_id+secret. |
| **Comparativ cu PERFORMANȚA noastră reală** | ⏳ enhancement: ROAS/CTR/spend per ad din conturile noastre via `gigi:meta-ads`/`tiktok-ads`/`google-ads-mcc` (la noi avem date reale, nu doar longevitate) |

## TikTok — detalii (tiktok_ads.py)
TikTok caută pe **advertiser**, nu pe domeniu. API-ul intern (`library.tiktok.com/api/v1/{suggestion,search}`)
e semnat (`x-ccl-str` din JS) → îl conducem prin **Playwright + Chrome de sistem** (`channel="chrome"`, fără
download de chromium). Flux: nume → `biz_id` (suggestion) → reclame (search). **Capcană critică: `query_type=2`**
— fără el, numele e tratat ca keyword și întoarce feed-ul generic RO (44M reclame), nu advertiserul. Răspunsul dă
`first/last_shown_date` (longevitate), `title` (copy-ul), `estimated_audience`, `cover_img`/`video_url`.

## Capcane
- Region code Google = `2642` (RO). Pt altă țară, deschide adstransparency.google.com pe regiunea aia și citește numărul din payload.
- TikTok: cere Google Chrome instalat. Numele de advertiser ≠ domeniu (ex. „answear", nu „answear.ro"); dacă-s mai mulți omonimi, scriptul caută la fiecare până găsește reclame.
- `best` e cap-at la `--limit` (100). „100 creative" poate însemna „100+".
- Imaginile (`tpc.googlesyndication.com/archive/simgad/…`) se deschid direct ca imagini și se pot da la vision.
- Longevitatea e proxy, nu performanță certă — un creativ vechi încă activ e foarte probabil câștigător, dar confirmă cu bunul-simț (poate fi un evergreen de brand).

## Unghiuri noi (adoptate MIT)
- **gigi:competitors** + **gigi:competitor-profiling** — research competiție dincolo de reclame. **gigi:ads-competitor** — framework de analiză ads competiție.
