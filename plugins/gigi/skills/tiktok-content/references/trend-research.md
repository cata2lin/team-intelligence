# TikTok Trend + Competitor Research — ARONA (Romania)

Cum găsești ce e în trend pe TikTok RO + ce postează competiția, și cum transformi un
trend într-un brief de conținut pentru brandurile noastre — în <48h.

## 1. Surse (gratuite, fără login)
- **TikTok Creative Center** (`ads.tiktok.com/business/creativecenter`, free, region=RO):
  - **Trending Hashtags** / **Trending Songs** / **Breakout** — filtrează pe RO + industrie.
  - **Top Ads** — ce reclame performează pe RO (idei de hook/format).
  - **Keyword Insights** — ce caută lumea pe TikTok (intenție).
  - Mapare industrie Creative Center → brand ARONA: *Beauty/Personal Care* → Esteban/GT/Nubra/Rossi;
    *Home Improvement/Household* → Grandia/Bonhaus/Belasil/Carpetto; *Apparel/Accessories* → Gento.
- **TikTok Research API** — semnal organic agregat, dar **gated** (cerere de acces academic/aprobat);
  pt noi = secundar, folosește Creative Center întâi.
- **TikTok indexat de Google** → `gigi:social-listening` (mențiuni brand RO pe TikTok via SERP).
- **TikTok Ad Library (UE)** → `gigi:competitor-ads` (ce reclame PLĂTITE rulează competiția).

## 2. Token-uri (vezi `posting-analytics.md`)
Research-ul de mai sus e web/Creative-Center (fără token). Research API real = app gated separat.
Nu hardcoda nimic; secrete în KB.

## 3. Competiție RO (watchlist per verticală — ce urmărim organic)
| Verticală | Competitori de urmărit | Ce căutăm |
|---|---|---|
| Parfumuri | conturi de „dupes"/parfumuri RO, recenzii TikTok | formate GRWM/„miroase a…", longevitate-test |
| Unghii | Lila Rossa, Cupio, nail-artiste RO | tutoriale DIY, before/after, sunete |
| Home/curățenie | conturi #CleanTok RO, retaileri | oddly-satisfying, „lifehack", demo produs |
| Covoare/genți | retaileri home/fashion RO | transformare cameră, haul, styling |

Pt **reclamele** lor → `gigi:competitor-ads` (rank pe longevitate + analiză vision). Pt
**organic** → caută handle-urile + uită-te la postările cu cele mai multe views.

## 4. Trend-jacking <48h (workflow)
1. **Spot** (zilnic, 10 min): Creative Center RO → 3-5 trenduri/sunete în creștere.
2. **Fit** (filtru GO/NO-GO, 5 puncte): (a) se potrivește cu o verticală a noastră? (b) putem
   filma în <24h cu ce avem (produs/UGC)? (c) sunetul/formatul e încă în urcare (nu saturat)?
   (d) nu ne pune în lumină proastă / fără claim-uri riscante? (e) are CTA natural spre produs?
   2+ NO → skip.
3. **Brief** (§5) → 4. **Producție** (UGC Cristina / `gigi:pmax-video`) → 5. **Publish**
   (`tiktok_post.py`) → 6. **Read** (`tiktok_organic.py` la 24-48h).

## 5. Trend → brief (template + completează cu trendul zilei)
```
Brand: <…>  | Trend/sunet: <…>  | Format: <demo/POV/GRWM/before-after>
Hook (2s): <…>   Mesaj: <beneficiu + dovadă>   CTA: <comentează PREȚ / link în bio / ramburs>
Produs in-frame: <SKU pe stoc!>   Durată: 15-25s   Sunet: <trending>
```
Exemple gata: Esteban „longevity test 12h", Belasil „#CleanTok rufe ca la hotel", Rossi „polygel
DIY before/after", Grandia/Magdeal „things you need din casă".

## 6-7. Native + compliance
Hook în primele 2s; vertical 9:16; sunet trending; UGC autentic > reclamă lustruită; subtitrare.
**Fără superlative neverificabile** („cel mai bun/garantat") — sensibil ANPC/Google (vezi
[[mc-deals-store-misrepresentation]]). COD: „plată ramburs, livrare rapidă". Treci textul prin
`gigi:ai-scrub` înainte de postare.

## 8. Capcane
Trend saturat (ai ratat fereastra) · sunet fără licență comercială · produs din video = epuizat
(verifică stocul Shopify ÎNAINTE!) · claim de sănătate pe cosmetice/curățenie.
