---
name: instapress
description: "Distribuție de advertoriale / comunicate de presă prin InstaPress (app.instapress.ro) pe contul ARONA — 2461 site-uri media RO, 2456 DOFOLLOW (autoritate reală de ranking, nu nofollow ca advertorialele clasice). Catalog scrapuit pe NAS+KB (domeniu, DR/DA/trafic/TrustFlow, tipuri articole + prețuri). Găsește pe ce site-uri publici (nișă/preț/DR/dofollow) + fluxul de publicare. Use when: instapress, advertorial, comunicat de presa, press release, distributie presa, backlink dofollow platit, PR link building RO, pe ce site publicam, adevarul/antena3/libertatea advertorial."
user-invocable: true
argument-hint: "[--niche Frumus] [--dofollow] [--max-price 300] [--min-dr 40]"
license: MIT
metadata:
  author: gigi
  version: "1.0.0"
  category: seo
---

# InstaPress — advertoriale / comunicate de presă (contul ARONA)

**InstaPress** (`app.instapress.ro`) = platformă RO de distribuție de advertoriale/comunicate pe **2.461 site-uri** media (Adevărul, Antena3, Libertatea, HotNews, ZF, Agerpres, Digi24…). Publici un articol pe site-ul ales → primești linkul instant.

> 🔑 **Insight cheie: 2.456 din 2.461 sunt `dofollow`.** Deci — spre deosebire de advertorialele „native advertising" clasice (Limitless = toate nofollow, [[grandia-seo-limitless]]) — **InstaPress transmite autoritate REALĂ de ranking**, nu doar reach. E canalul off-site cel mai bun raport preț/valoare pe RO. Vezi strategia [[offsite-seo-strategy]].

## Contul ARONA (shared, pt toate brandurile)
- Cont **Advertiser** pe firma **ARONA SRL**. Login **fără CAPTCHA**; înregistrarea are Cloudflare Turnstile (blochează automatul → se face manual în browser).
- Credențiale: KB `INSTAPRESS_ARONA_EMAIL` / `INSTAPRESS_ARONA_PASSWORD` (⚠️ parola reală e cea din fișierul local `~/Downloads/credentials/instapress.txt` — email `gheorghe.beschea@overheat.agency`, cu „a" la final).
- **Plata = manuală** (încărcare credit prin MobilPay, card — nu se poate automatiza). Fără credit nu se poate trimite.

## Catalogul de site-uri (unde publicăm + cât costă)
Scrapuit integral și salvat pe **NAS + KB** (resource `seo/InstaPress catalog`):
- `$NAS_ROOT/data/instapress/instapress_catalog_full.json` — TOATE câmpurile + toate ofertele/tipurile per site.
- `…/instapress_catalog.csv` — flat (un rând/site).
- `…/instapress_shortlist_branduri.csv` — 1.945 site relevante (beauty/lifestyle/news, dofollow, cu articol SEO), sortate pe DR.

**Per site:** `domain`, `dr` (Ahrefs DR), `da` (Moz), `tf`/`cf` (Majestic), `traffic`, `refdomains`, `linkType`, `minWords` (de regulă 500), `maxLinks`, `categories` (nișă), `accepts` (ce conținut acceptă), și **`offers`** = tipurile de articole cu preț fiecare.

**Tipuri de articole (`offers[].type`):** `SEO` (articol standard, 2.261 site), `HOMEPAGE` (advertorial cu afișare pe homepage, 380, mai scump), + adult/casino/crypto/alcool etc. Fiecare cu preț `{value, vat, total}` și `isDoFollow`.

**Prețuri articol SEO:** median ~121 RON, range 6–13.830. Exemple: agerpres.ro DR75/151 RON · antena3.ro DR72/417 · libertatea.ro DR74/424 · adevarul.ro DR77/932 · hotnews.ro DR76/3.146.

### Interoghează catalogul (helper, fără re-scrape)
```bash
uv run scripts/catalog.py --niche Frumus --dofollow --max-price 300      # site beauty dofollow sub 300 RON
uv run scripts/catalog.py --niche "Mod de viata" --sort dr --limit 30    # lifestyle pe DR
uv run scripts/catalog.py --domain adevarul                              # detalii un site
uv run scripts/catalog.py --min-dr 60 --max-price 500 --type SEO         # DR mare, ieftin
```
Flags: `--niche`, `--type` (SEO/HOMEPAGE, default SEO), `--dofollow`, `--min-dr`, `--max-price`, `--sort dr|price|traffic`, `--limit`, `--domain`.

## Fluxul de publicare
1. **Alege site-urile** cu `catalog.py` (relevante pe nișă + dofollow + DR bun + în buget). Pt branduri de parfum → nișe Frumusețe/Modă/Mod de viață/Știri; pt Grandia → Casă/Familie + presă generală/regională.
2. **Încarcă credit** (MobilPay, manual — pasul uman).
3. **Urcă articolul** logat pe app.instapress.ro → Marketplace → site → tip articol (`SEO`) → upload (min 500 cuvinte, unic, cu imagine principală 16:9 ≥900px, fără preț/telefon în imagine).
4. Primești linkul → se publică (instant pe site-urile `instant`, altele cu aprobare în N zile).

> ⚠️ **Brand-safety (parfumuri):** conținutul care merge la presă **NU** numește branduri de lux (dupe/clonă) — riscul de trademark/counterfeit care a suspendat Esteban pe Google. Framing pe profiluri/comportament (studii cu date reale), nu pe „clona de X". Rulează `gigi:ai-scrub` pe articol înainte de urcare.

## Refresh catalog (când vrei prețuri/site-uri la zi)
Platforma paginează server-side (`marketplace.php?page=1..62`, ~40 site/pagină) cu datele injectate inline în `Alpine.data('appMarketplace',()=>({… publishers:[…] …}))`. Refresh:
1. Login în browser (chrome-devtools MCP): `app.instapress.ro/?show=login`, completează email+parolă (KB/fișier), fără CAPTCHA.
2. Pe `marketplace.php`, rulează un `evaluate_script` care fetch-uiește toate paginile și extrage `publishers:[…]` (bracket-matching string-aware + `eval`), compactează câmpurile + `offers`, salvează cu `filePath` (în workspace, apoi mută pe NAS).
3. `kb.py file-add` + `resource-add` pt înregistrare. (Codul exact al scraper-ului = în istoricul sesiunii care a creat skill-ul; poate fi portat într-un script requests-based — login POST fără CAPTCHA + parse `publishers:`.)

Companion: `gigi:seo-backlinks` (analiză profil linkuri), `gigi:public-relations`, `core:*-articles` (scriere în vocea brandului), `gigi:ai-scrub` (de-AI pre-publicare).
