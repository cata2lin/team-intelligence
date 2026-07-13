---
name: instapress
description: "Distribuție de advertoriale / comunicate de presă prin InstaPress (app.instapress.ro) pe contul ARONA — 2461 site-uri media RO, 2456 DOFOLLOW (autoritate reală de ranking). Catalog scrapuit pe NAS+KB (domeniu, DR/DA/trafic/TrustFlow, tipuri articole + prețuri). Găsește pe ce site-uri publici (nișă/preț/DR/dofollow) + fluxul de publicare. Use when: instapress, advertorial, comunicat de presa, press release, distributie presa, backlink dofollow platit, PR link building RO, pe ce site publicam, adevarul/antena3/libertatea advertorial."
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

> 🔑 **Insight cheie: 2.456 din 2.461 sunt `dofollow`** → **InstaPress transmite autoritate REALĂ de ranking**, nu doar reach. E canalul off-site cu cel mai bun raport preț/valoare pe RO. (⚠️ Corecție 7-iul: advertorialele agenției Limitless pe Grandia s-au dovedit **tot dofollow**, nu nofollow cum notasem — vezi [[offsite-seo-strategy]]. Avantajul InstaPress față de ele = **cost + volum + control + diversitate de site-uri/anchor**, nu 6 plasări clusterizate pe o categorie.) Vezi strategia [[offsite-seo-strategy]] și [[grandia-seo-limitless]].

## Contul ARONA (shared, pt toate brandurile)
- Cont **Advertiser** pe firma **ARONA SRL**. Login **fără CAPTCHA**; înregistrarea are Cloudflare Turnstile (blochează automatul → se face manual în browser).
- Credențiale: KB `INSTAPRESS_ARONA_EMAIL` / `INSTAPRESS_ARONA_PASSWORD`.
  ⚠️ **Parola din KB e GREȘITĂ** (confirmat 13-iul: login POST prin curl → 302 înapoi la `?show=login`). Fișierul local `~/Downloads/credentials/instapress.txt` **nu mai există**. → **Cere userului să se logheze el** în browserul de automatizare (`app.instapress.ro/?show=login`), apoi continui: sesiunea rămâne validă pentru tot batch-ul. (Nu încerca să tastezi tu parola — n-o ai, și nu se pun secrete în tool-calls.)
- **Plata = manuală** (încărcare credit prin MobilPay, card — nu se poate automatiza). Fără credit nu se poate trimite.

## 🔗 Politica de LINKURI (verificat la sursă în FAQ-ul lor, 13-iul-2026) — CITEȘTE ÎNAINTE SĂ SCRII ARTICOLUL
> „**Bunele practici seo spun că este bine ca într-un articol să existe un singur link.** Cu toate acestea sunt site-uri care permit şi **2 linkuri**. Recomandarea noastră rămâne pentru 1 link."
> „Îţi garantăm că lucrăm doar cu site-uri care oferă linkuri **dofollow** și care **nu vor fi marcate cu `rel="ugc"` sau `rel="sponsored"`**."

- **Plafon real = 2 linkuri** (NU 3-4). Câte permite fiecare site → câmpul **`maxLinks`** din `instapress_catalog_full.json` (majoritatea = 2).
- **Cum folosești cele 2 linkuri (structura care merge):**
  1. **Link „money" CONTEXTUAL, ÎN CORPUL articolului** — ancoră parțial-match naturală, în paragraful care chiar discută produsul, către **pagina de colecție care trebuie să urce** (nu homepage). Ex: „un [raft metalic](…/collections/rafturi-metalice) bun rezolvă…".
  2. **Link de BRAND** în semnătura de final — ancoră = numele magazinului → homepage (diluează profilul de ancore, arată natural).
- ❌ **NU** irosi singurul link într-un bloc „Despre X" la subsol cu ancoră = numele brandului. E cel mai slab tip de link care există. Scoate blocul „Despre X"; lasă doar `*Articol oferit de [Brand](https://brand.ro).*`
- Alte reguli din `/conditii-de-publicare` + `/faq`: articol **UNIC** (fără duplicat / paragrafe copiate), **min 500 cuv / rec. 700**, imagine landscape ≥800×600 + **sursă obligatorie**, **relevanță la topicul site-ului** (pe site de ȘTIRI → încadrează-l ca știre/lifestyle, altfel publisherul respinge). Interzis: criptomonede, PR negativ, promovarea concurenței.

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

### Gotchas la publicare (empiric — proof Esteban→agerpres iul 2026; reconfirmat + extins pe Runda 2, 13-iul)
Urcarea se face în browser (Alpine.js + Summernote + jQuery 3.6). Câmpurile se setează via `evaluate_script` cu **dispatch `input`+`change`** (altfel Alpine nu vede valoarea):
- **⚠️ URL-ul paginii de publicare (13-iul): `https://app.instapress.ro/metagenerate/publisher/?setPublisherID=<id>&setTypeID=1`.** Varianta cu `/account/` în față (`/account/metagenerate/…`) dă **404**. Baza corectă e `/metagenerate/`, dashboard-ul e la `/account/dashboard.php`, lista articolelor la `/account/articles.php`.
- **Câmpuri:** `pageURL` = ținta backlink-ului · `pageTitle` · `pageSlug` · `categoryID` = **după textul opțiunii** · conținut = `jQuery('#pageContentID').summernote('code', html)` + setează și textarea `#pageContentID`.
- **✅ `linkNofollow` = checkbox, DEFAULT NEBIFAT = dofollow. NU-L ATINGE** (dacă e bifat, `.click()` să-l scoți). `linksMode` = default `LINK` („Link on Link") → **respectă ancorele `<a>` din conținutul tău**, nu le rescrie. Deci pui linkurile direct în HTML și lași `insertLinks` gol. (`insertLinks` = builder alternativ, format `https://url|Anchor Text`.)
- **Categorii complete `categoryID`:** No Category, 00_Promo, Astro, Beauty, Books, Business, Cariera, Celebrity, Culture, Diverse, Entertaiment, Facts, Fashion, Food, Health, **Home**, Internet, **Lifestyle**, Love, **Mens**, Movies, News, Parenting, Politic, Sex, Sport, Tech, Texts, Travel. (parfumuri → `Beauty`; Grandia/casă → `Home`.)
- **⚠️ Sunt DOUĂ butoane „Previzualizare".** Unul e din toolbar-ul Summernote (deschide dialogul „Inserează link"!). Cel REAL e lipit de „Trimite la Publicare" → **găsește-le după text**, nu după uid: `[...document.querySelectorAll('button')].find(b=>/trimite la publicare/i.test(b.innerText))`.
- **⚠️ uid-urile din snapshot SE SCHIMBĂ după `upload_file`** (DOM-ul se re-randează) → refă snapshot-ul dacă mai ai nevoie de uid-uri după upload.
- **Conținut = HTML pe `<p>`** (nu markdown). Convertește articolul în paragrafe `<p>…</p>` (tratează și `## titlu`→`<strong>`, `*italic*`→`<em>`, `[text](url)`→`<a>`); apoi injectează cu `jQuery('#pageContentID').summernote('code', html)` + setează și textarea.
- **⚠️ Cum bagi HTML-ul în editor (metoda care MERGE la batch):** browser-ul chrome-devtools rulează **IZOLAT de rețeaua ta locală** — `fetch('http://localhost…')` dă timeout (nu-ți vede serverul), iar base64 inline în `evaluate_script` e **fragil** pe articole lungi (trunchezi pasteul → „Invalid token"). **Soluția fiabilă:** pune cele N `.html` pe un **repo GitHub PUBLIC temporar** (`gh repo create <nume> --public --source=. --push`; token-ul KB `GITHUB_TOKEN` are scope `repo`, **NU** `gist`), apoi în browser `fetch("https://raw.githubusercontent.com/<owner>/<repo>/main/<fișier>.html")` (HTTPS + CORS `*` = merge) → `summernote('code', html)`. Un singur call/articol, fără base64, diacritice intacte. La final fă repo-ul privat/șterge-l (token n-are `delete_repo` → `gh api -X PATCH repos/<o>/<r> -f private=true`).
- **Imagine: JPG obligatoriu, ≥1000px lat, ≤8MB, ~16:9.** Convertește PNG→JPG (`sips -s format jpeg`). Urcă prin `upload_file` pe zona de drop → populează hidden `pageImage` (`…/providers/tempo/images/<slug>.jpg`). ⚠️ `upload_file` **cere fișierul în workspace root** (copiază-l acolo întâi).
- **⚠️ `imageSource` e OBLIGATORIU chiar dacă ai urcat fișierul.** Drop-ul setează `pageImage` dar NU `imageSource` → eroare „Sursa imaginii este obligatorie!". Fix: pune în `imageSource` **exact URL-ul din `pageImage`**.
- **⚠️ Filtru de cuvinte „adult".** Conținutul e scanat; cuvinte ca **senzual, unisex (conține „sex"), sex, gol/goală, erotic** marchează articolul „adult" → publisherii non-adult (agerpres) blochează cu „Editorul nu acceptă următoarele tipuri de articole pentru adulți:" (lista apare goală, dar TOT blochează). **Pre-scanează și rescrie** înainte de upload (senzual→învăluitor, unisex→„pentru oricine", gol/goală→„liber/neamenajat").
- **⚠️ Schimbarea de rețea în sesiune → blocaj „New IP".** `ajax/publisher.php` întoarce **HTTP 200 dar cu HTML-ul paginii de Login** („Dashboard is Locked or New IP") → spinnerul „Se încarcă…" rămâne agățat, JS crapă (Sentry), **nu se debitează nimic**. Articolul rămâne **draft** (`?id=<n>`, datele persistă). Fix: reload → parola în caseta de deblocare → revii pe draft → retrimite. Parola = fișierul local `~/Downloads/credentials/instapress.txt` (NU cea din KB, care e greșită).
- **Snapshot uriaș:** `take_snapshot` pe pagina asta depășește limita de tokeni → salvează cu `filePath` în workspace root și `grep` uid-urile (Trimite/Previzualizare/zonă upload).
- **Ordine sigură:** **Previzualizare** (nu costă, verifici vizual imagine+titlu+text) → **Trimite la Publicare** (costă). Validarea eșuată NU debitează, deci poți itera în siguranță.

### Pipeline batch dovedit (8 articole / 8 site-uri unice, iul 2026)
Regula: **1 articol UNIC / site** (fără conținut duplicat). Buget real: SEO ~150-200 RON/site pe DR 32-45 cu trafic. 8 plasări ≈ 1.286 RON.
1. **Alege site-urile** cu `catalog.py` (sortează pe **trafic**, nu doar DR — multe domenii DR mari au 0 trafic = link-farm). ID-ul de publisher e câmpul **`id`** din `instapress_catalog_full.json` (ex elegantes.ro=528, casesigradini.ro=1024). Navighează direct: `…/metagenerate/publisher/?setPublisherID=<id>&setTypeID=1` (nu mai cauți în select).
2. **Categorie pe nișă:** parfumuri → `Beauty`; Grandia/casă → `Home`. (Opțiuni: Beauty/Fashion/Home/Lifestyle/News/Love/Mens/Health/Travel…)
3. **Un articol, pas cu pas** (≈6 tool-calls): (a) `navigate ?setPublisherID=<id>`; (b) `evaluate_script`: fetch raw HTML de pe repo + set pageURL/pageTitle/pageSlug + `summernote('code')` + categorie; (c) `take_snapshot --filePath` + `grep "imaginea principal"` → uid zonă upload; (d) `upload_file <uid> <JPG din workspace>`; (e) `evaluate_script`: set `imageSource=pageImage` + **precheck** (DoFollow=Da, imagine, titlu, content>2000) + click „Trimite la Publicare" doar dacă precheck trece; (f) `evaluate_script`: confirmă „a fost trimis publisherului" + click „Continua" + citește Credit.
4. **Verificarea debitului = creditul** (sursă de adevăr): scade exact cu prețul/articol; ex 1.300 → 14,37 după 8. Fiecare submit reușit arată modalul verde „Articolul dvs. a fost trimis publisherului!" → status „PUBLICARE ÎN ASTEPTARE" (max ~5 zile) → apoi linkul dofollow e live.
> Imaginile: produsul NOSTRU real ca `--ref` în `gigi:image-gen` (sticla brandului / produs Grandia), NU inventat AI (vezi memoria [[instapress-channel]]).

### Precheck OBLIGATORIU înainte de submit (nu arunca banii)
Rulează-l în același `evaluate_script` ca submit-ul și **dă click doar dacă trece**:
```js
const q=n=>(document.querySelector(`[name="${n}"]`)||{}).value||'';
const c=jQuery('#pageContentID').summernote('code');
const nf=document.querySelector('[name="linkNofollow"]');
const ok = q('pageTitle') && q('pageURL') && q('pageSlug') && q('pageImage') && q('imageSource')
        && !nf.checked                                   // DOFOLLOW
        && c.replace(/<[^>]+>/g,' ').split(/\s+/).filter(Boolean).length > 500
        && (c.match(/<a /g)||[]).length === 2            // exact maxLinks
        && !/senzual|unisex|erotic/i.test(c);            // filtrul „adult"
```
**Previzualizare (gratis) verifică ce contează:** parsează HTML-ul din modal și confirmă `links[].rel === null` (= dofollow, fără `sponsored`/`ugc`), imaginea și titlul. Abia apoi „Trimite la Publicare".

### Runda 2 — „few & good" (4 articole, 13-iul-2026, 1.467,13 RON) ✅
Strategie: **puține și bune** > multe și slabe. Domenii **FRESH** (nu cele din Runda 1 — diversitate de referring domains), DR 37-74, fiecare cu **2/2 linkuri DoFollow**.
| Articol | Publisher (id) | DR | Preț | Țintă money |
|---|---|---|--:|---|
| Grandia parchet | **libertatea.ro** (943) | 74 | 423,50 | `/collections/parchet-autoadeziv-…` |
| Grandia rafturi | **confluente.ro** (485) | 37 | 196,63 | `/collections/rafturi-metalice` |
| GT colecție | **unica.ro** (1665) | 59 | 423,50 | `/collections/barbati` |
| GT concentrații | **elle.ro** (1565) | 56 | 423,50 | `/collections/barbati` |
Credit 1.474,37 → **7,24** (debit exact). ID-uri articol: 101468-101471.
> ⚠️ Din Runda 1, **radardemedia.ro a ieșit „EȘUAT"** („Problemă temporară", 157,30 RON) — **verifică lista `/account/articles.php` după fiecare rundă** și cere refund/resubmit pe cele eșuate.

### Imagini — lecții (13-iul)
- **Verifică imaginea față de produsul REAL de pe site, nu față de titlu.** Capcană trăită: colecția Grandia „parchet" are DOUĂ produse — „Set 36 Plăci **Aspect Lemn Stejar**" e de fapt **roșcat-vișiniu**, iar stejarul deschis e alt SKU („parchet 91.5x15.2"). Dacă generezi „stejar" fără să te uiți, imaginea **nu reprezintă produsul**. Trage `products.json` din colecție și **uită-te la poză** înainte.
- **Fizică:** nu cere „sticlă care pulverizează" cu **capacul pus** (imposibil → arată fals). Ori capac scos, ori fără spray.
- **Contrast:** sticlele NEGRE (GT) pe fundal închis = se pierd. Cere **fundal deschis** (travertin/marmură crem) → contrast puternic, se potrivește și cu estetica revistelor beauty (unica/elle).
- Cere explicit **păstrarea etichetei** („reproduce the printed label INCLUDING the text — must NOT be blank"), altfel iese sticla goală.

> ⚠️ **Brand-safety (parfumuri):** conținutul care merge la presă **NU** numește branduri de lux (dupe/clonă) — riscul de trademark/counterfeit care a suspendat Esteban pe Google. Framing pe profiluri/comportament (studii cu date reale), nu pe „clona de X". Rulează `gigi:ai-scrub` pe articol înainte de urcare.

## Refresh catalog (când vrei prețuri/site-uri la zi)
Platforma paginează server-side (`marketplace.php?page=1..62`, ~40 site/pagină) cu datele injectate inline în `Alpine.data('appMarketplace',()=>({… publishers:[…] …}))`. Refresh:
1. Login în browser (chrome-devtools MCP): `app.instapress.ro/?show=login`, completează email+parolă (KB/fișier), fără CAPTCHA.
2. Pe `marketplace.php`, rulează un `evaluate_script` care fetch-uiește toate paginile și extrage `publishers:[…]` (bracket-matching string-aware + `eval`), compactează câmpurile + `offers`, salvează cu `filePath` (în workspace, apoi mută pe NAS).
3. `kb.py file-add` + `resource-add` pt înregistrare. (Codul exact al scraper-ului = în istoricul sesiunii care a creat skill-ul; poate fi portat într-un script requests-based — login POST fără CAPTCHA + parse `publishers:`.)

Companion: `gigi:seo-backlinks` (analiză profil linkuri), `gigi:public-relations`, `core:*-articles` (scriere în vocea brandului), `gigi:ai-scrub` (de-AI pre-publicare).
