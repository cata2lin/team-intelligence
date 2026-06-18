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
# cele mai bune creative ale unui competitor (rank pe longevitate)
uv run scripts/competitor_ads.py best rasheed.ro --top 10
uv run scripts/competitor_ads.py best notino.ro evero.ro parfumat.ro   # batch

# best + analiză vision (Gemini) + comparativ cu noi + recomandări
uv run scripts/competitor_ads.py analyze rasheed.ro --top 6 --vs esteban.ro
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
| **Google** Ads Transparency Center | ✅ v1 (fără auth) |
| **Meta** Ad Library API | ⏳ după confirmarea ID pe contul Meta (facebook.com/ads/library/api) — apoi `ads_archive` cu tokenul nostru |
| **TikTok** Commercial Content Library (UE) | ⏳ recon endpoint (library.tiktok.com; API intern de descoperit cu chrome-devtools) |
| **Comparativ cu PERFORMANȚA noastră reală** | ⏳ enhancement: ROAS/CTR/spend per ad din conturile noastre via `gigi:meta-ads`/`tiktok-ads`/`google-ads-mcc` (la noi avem date reale, nu doar longevitate) |

## Capcane
- Region code Google = `2642` (RO). Pt altă țară, deschide adstransparency.google.com pe regiunea aia și citește numărul din payload.
- `best` e cap-at la `--limit` (100). „100 creative" poate însemna „100+".
- Imaginile (`tpc.googlesyndication.com/archive/simgad/…`) se deschid direct ca imagini și se pot da la vision.
- Longevitatea e proxy, nu performanță certă — un creativ vechi încă activ e foarte probabil câștigător, dar confirmă cu bunul-simț (poate fi un evergreen de brand).
