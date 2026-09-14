---
name: label-swap
description: Pune eticheta/designul TĂU peste eticheta unui produs din poze de mockup sau studio — potrivire EXACTĂ, nu lipit din ochi. Detectează automat patrulaterul real al etichetei existente (inclusiv înclinarea/perspectiva sticlei), reașază designul pe el cu perspective warp, îl întinde ca fundalul să umple toată eticheta fără să deformeze literele, și compune supersamplat 4× ca marginile să iasă antialiasate (chiar mai curate decât originalul). Folosește pentru „pune eticheta X pe pozele produsului Y", rebranding de mockup-uri, o gamă nouă pe aceeași sticlă, variante de aromă/parfum, poze de produs pentru un brand nou pornind de la pozele altui brand.
argument-hint: "--design eticheta.png --photos 'poze/*.jpg' [--out final] [--fit seam|contain] [--probe] [--apply]"
category: creative-content
version: 1.0.0
---

# label-swap — eticheta ta pe pozele produsului

Ai pozele de studio ale unui produs (sticlă de parfum, borcan, cutie) cu eticheta unui brand
și vrei aceleași poze cu **eticheta ta**. Skill-ul o schimbă *geometric corect*: găsește singur
unde e eticheta, sub ce unghi stă, și pune designul exact acolo.

## When to use
- „Pune eticheta Duppo pe cele 3 poze cu sticlele de Esteban" (cazul care l-a născut).
- Un brand nou pe același ambalaj / aceeași sticlă (Duppo pornit din pozele Esteban).
- Variante de produs (aromă, concentrație, No. X) fără să refaci shooting-ul.
- NU e pentru: generat poze de la zero (→ `gigi:image-gen`), reîncadrat pozele din Shopify
  (→ `gigi:shopify-product-images`), bannere de reclamă (→ `gigi:ad-banners`), coli de etichete
  la print (→ `gigi:label-sheet`).

## Steps
```bash
cd plugins/gigi/skills/label-swap/scripts

# 0) verifică ce a detectat (scrie probe_N.jpg cu patrulaterul trasat roșu) — fă asta întâi
uv run label_swap.py --design "eticheta.png" --photos "poze/*.jpg" --probe

# 1) dry-run: raportează dimensiuni + înclinare, nu scrie nimic
uv run label_swap.py --design "eticheta.png" --photos "poze/"

# 2) scrie rezultatele
uv run label_swap.py --design "eticheta.png" --photos "poze/" --out "final" --prefix duppo --apply
```

Flag-uri: `--fit seam|contain` · `--grow` (px de extindere, 1.5) · `--ss` (supersampling, 4) ·
`--thresh` (prag luminanță etichetă, 70) · `--light` (etichetă deschisă la culoare) ·
`--erode` (21) · `--border` (grosime chenar design, implicit auto) ·
`--quad x1,y1,...,x4,y4` (colțuri manuale TL,TR,BR,BL) · `--quality` (97) · `--probe` · `--apply`.

## Cum funcționează (și de ce așa)
1. **Detecție** — masca etichetei = cea mai mare zonă compactă întunecată (`--thresh`), cu
   `binary_fill_holes` (**altfel textul alb din etichetă rupe regiunea** și iese trunchiată) și
   `binary_erosion` (taie muchiile subțiri de sticlă lipite de etichetă), apoi crescută la loc.
2. **Patrulaterul REAL** — fit least-squares pe cele **4 laturi** (nu bounding-box!) și
   intersecția dreptelor → 4 colțuri. Bounding-box-ul pe o sticlă întoarsă dă etichetă dreaptă
   pe un produs înclinat = arată „decupat". Raportează `înclinare` și `abatere_max`.
3. **Umplere** (`--fit seam`) — interiorul designului e segmentat în **benzi cu text (fixe)** și
   **goluri (întinsibile)**. Literele se scalează doar cu raportul de lățime; tot spațiul vertical
   rămas se distribuie în goluri. Așa fundalul umple toată eticheta **fără litere alungite**.
   `--fit contain` = scalare proporțională, centrat (când designul trebuie păstrat 1:1).
4. **Perspective warp** — designul e randat la `4×` dimensiunea țintă și deformat pe patrulater.
5. **Compunere antialiasată** — masca e poligonul patrulaterului la 4×, culoarea e
   **premultiplicată cu alfa** înainte de reducere (altfel intră în medie pixeli din afara
   etichetei = halou), reducerea e **mediere pe blocuri 4×4** (acoperire reală), iar compunerea
   se face în float: `foto·(1−α) + etichetă·α`.

## Notes
- **⚠️ Extinderea cu `--grow` NU e cosmetică — e fixul principal de antialiasing.** Pozele de
  mockup au ele însele marginea etichetei **aliasată** (trepte de ~2px). Dacă lipești exact pe
  muchia detectată, rampa ta antialiasată se termină fix acolo unde încep pixelii aliasați ai
  originalului → **treptele rămân vizibile și pare că n-ai făcut nimic**. Cu `--grow 1.5`
  (offset pe normala fiecărei laturi, nu scalare din centru) muchia ta o acoperă pe cea veche.
  Verificare: valorile de tranziție trebuie să varieze continuu de la o coloană la alta
  (ex. 86 → 56 → 44), nu să sară sec (0 → 136).
- **Chenarul designului e detectat automat** (grosime + culoare reală, ex. `rgb(3,3,3)`, nu negru
  pur) și scalat proporțional. Suprascrie cu `--border 0` dacă vrei fără chenar.
- **Ocluzii** (mâna, un colț de sticlă peste etichetă): masca detectată le păstrează doar dacă
  folosești varianta cu mască (implicit e poligonul curat). Dacă produsul are ceva peste etichetă,
  rulează `--probe` și verifică — la nevoie dă colțurile cu `--quad`.
- **Salvare**: JPEG `--quality 97` **fără subsampling cromatic** (4:4:4), ca să nu reintroducă
  artefacte pe muchia negru/fundal deschis.
- **Verificare vizuală**: zoom cu NEAREST (nu cu interpolare) — orice treaptă rămasă se vede
  imediat; cu LANCZOS o ascunzi din greșeală.
- Windows: scriptul forțează UTF-8 la output (diacritice).
- Related: [[gigi:image-gen]], [[gigi:ad-banners]], [[gigi:shopify-product-images]],
  [[gigi:label-sheet]], [[library:banner-design]].
