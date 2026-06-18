# Limitless — Extragere completă a livrabilelor + Plan SEO pentru un brand nou

> Sursă: `~/Downloads/Livrabile grandia.ro` (WeTransfer, 17 iun 2026). Colaborare Limitless Agency pe **grandia.ro**, **5 luni** (15 ian – 14 iun 2026), încheiată. Documentul de față = (A) tot ce au livrat, extras și organizat ca să rămână la noi knowledge-ul, și (B) un plan reutilizabil ca să facem același lucru singuri pentru următorul brand, cu uneltele noastre.

---

# PARTEA A — Ce a livrat Limitless (inventar complet)

Colaborarea a fost structurată ca un **tracker lună-cu-lună** (12 taskuri tehnice) + 4 fluxuri de conținut paralele.

## A1. Master tracker — „Centralizator task-uri tehnice" (12 taskuri / 5 luni)

| NID | Lună | Task | Impact | Status livrare |
|----|------|------|:------:|----------------|
| 1.0 | L1 (15 ian–14 feb) | **Keyword research inițial** | High | Informativ |
| 2.0 | L1 | Recomandări optimizare pagina principală de blog | High | Finalizat |
| 3.0 | L2 (15 feb–14 mar) | **Optimizare homepage** | High | Finalizat |
| 4.0 | L2 | Optimizare structură meniu | High | Trimis |
| 5.0 | L2 | **Optimizare Meta titlu / Meta descriere / H1** | High | Finalizat |
| 6.0 | L2 | Optimizare H2 & H3 + heading-uri irelevante | High | Finalizat |
| 7.0 | L3 (15 mar–14 apr) | **Schema internal linking** categorii/subcategorii | High | Trimis |
| 8.0 | L3 | **Condiții crawling & indexabilitate** per tip pagină | High | Trimis |
| 9.0 | L4 (15 apr–14 mai) | Optimizare footer | High | Finalizat |
| 10.0 | L4 | Recomandări tehnice **pagina de brand** | High | Trimis |
| 11.0 | L5 (15 mai–14 iun) | Recomandări tehnice **pagina agregatoare Brands** | High | Trimis |
| 12.0 | L5 | **Optimizare pagini de produse** | High | Trimis |

Plus tracker secundar `Centralizator task-uri tehnice - grandia.ro.xlsx` (934 rânduri, log fin), `Prioritati SEO.xlsx`, `Ghid publicare articole de blog.docx`, `Indicatii utilizare SEO Monitor.docx`.

## A2. Keyword Research (flux 1)
**Fișiere:** `Etapa pentru keyword research initial.docx` (metodologie) + `Keyword Research inițial.xlsx` (lista).
**Metodologie (de reținut):**
- 4 intenții de căutare: **Informațională** (blog), **Tranzacțională** (categorie/produs), **Navigațională** (brand), **Investigațională** (comparativ/blog). Pt categorii/produse ținta = **tranzacțional**.
- Coloane în xlsx: `Topic principal` (cel mai căutat cuvânt) · `Cuvinte cheie` · `Volum mediu lunar (media 12 luni)` · `Pagina de destinație relevantă`.
- Reguli: volumul mediu = anual/12 (sezonalitate ascunsă); poziția #1 ≈ 30% din volum; verifică **ce afișează Google** pt expresie (relevanță reală, nu doar volum); **nu elimina expresii cu volum mic** — la început țintești volume mici (mai puțin competitive), apoi generaliste.

## A3. SEO On-Page – Tehnic (flux 2, miezul)
- **Condiții crawling & indexabilitate per tip de pagină** (spec-ul tehnic, vezi tabelul în B1).
- **Meta titlu / descriere / H1** (`.xlsx`): formulă `{{Categorie}} - Grandia.ro` la title; meta descriere benefit-driven ~150 caractere; **H1 lipsea** ("Nu există") → de adăugat = numele categoriei.
- **H2 & H3 + heading-uri irelevante** (`.xlsx` + 11 screenshot-uri „Poziționare H1/H2/H3").
- **Schema internal linking** categorii ↔ subcategorii (`.docx`): copii + frați + părinte.
- **Optimizare homepage** (`.docx`, 1MB) + **meniu** (`.docx`) + **footer**.
- **Pagina de produs** (`.htm`, 570KB), **pagina de brand** + **agregator Brands** (`.html`).
- **Recomandări pagină de blog** (`.docx`).

## A4. Content Marketing (flux 3)
~10 articole blog (1.500–2.000 cuvinte, structură H2/H3 + FAQ + imagini) — amenajare living/baie/grădină, iluminat interior/exterior/bucătărie, montaj lustre LED, cameră bebeluș, idei lustre living. + `Strategie continut - Descrieri SEO LSI.xlsx`.

## A5. Link building (flux 4)
~15 articole scrise pentru **site-uri EXTERNE** (backlinks către grandia.ro), pe teme iluminat/mobilier/amenajare. NU sunt pentru blogul nostru — sunt momeală de linkuri pe alte domenii.

## A6. Descrieri SEO + FAQ / LSI (flux 5)
8 descrieri de categorie (`.docx`): Accesorii grădină, Articole copii, Corpuri iluminat, Decorațiuni, Lustre LED, Mobilier, Plafoniere LED, Bricolaj. **Structura LSI = H2 keyword-rich + paragrafe + beneficii + H3 „cum alegi" + FAQ.**

---

# PARTEA B — Plan SEO pentru un brand nou (playbook reutilizabil)

Reproducem TOT ce a făcut Limitless, **singuri**, cu skill-urile echipei. Ordinea = de la fundație la autoritate. Magazin tip: Shopify RO/COD.

## B0. Principii (învățate)
1. **Inventory-gating:** colecții/pagini head-term DOAR unde ai stoc real (~10+). Volum mare fără marfă = pagină goală = decizie de aprovizionare, nu SEO.
2. **Brand vs non-brand:** brandul nou n-are căutări → toată creșterea vine din **non-brand**. Măsoară split-ul în GSC.
3. **Intenție:** categorie/produs = tranzacțional; blog = informațional/investigațional.
4. **Volume mici întâi:** rankează pe long-tail, apoi pe generaliste.
5. **Verifică randat în browser** (desktop **și** mobil), nu pe curl (edge-cache Shopify înșală). URL cu `?fresh=NNN` în context izolat ca să sari de cache.

## B1. Faza 0 — Fundație tehnică (zilele 1–3) · skill `gigi:shopify-seo`
**Spec crawl/index per tip de pagină** (exact ce a cerut Limitless):

| Tip pagină | Meta robots | Canonical |
|---|---|---|
| Homepage / Categorie / Subcategorie / Produs / Brand / Pagini statice / Articol blog | `index, follow` | self (nativ Shopify) |
| Categorie/Subcategorie **+ ?sort_by=** | `noindex, follow` | **elimină canonicalul custom** |
| **/search?q=** (search intern) | `noindex, follow` | **elimină canonicalul custom** |
| Linkuri externe | — | `rel="nofollow"` pe orice link extern |
| `?variant=` | fără modificări | — |

+ **Schema**: Organization (cu `sameAs`), **WebSite + SearchAction** (homepage), Product/Offer + AggregateRating (din app reviews), **BreadcrumbList** (colecții + produse), **FAQPage** (din FAQ vizibil). + **H1 = exact unul/pagină** (fix bloc product-title pe temele Horizon). + og:image `https` + twitter:image.

## B2. Faza 1 — Keyword research (zilele 2–5) · `gigi:google-ads-mcc` (`kw_ideas.py`) + `gigi:analytics` (`gsc.py`)
- Volume reale RO: `kw_ideas.py --seeds "..."` (geo 2642, **limbă 1032**). Per categorie principală + subcategorii reprezentative.
- Construiește tabelul: `Topic | Cuvinte cheie | Volum | Pagina destinație (existentă / nouă / blog)`.
- Marchează golurile (volum mare, fără pagină) — **dar validează stocul** înainte să propui colecție nouă.
- După ce site-ul are istoric: `gsc.py opportunities` (striking-distance) + split brand/non-brand.

## B3. Faza 2 — Acoperire on-page în masă (zilele 4–10) · `gigi:shopify-seo`
- **Meta titlu/descriere pe TOATE produsele + colecțiile** (formula Limitless: title `{{Nume}} | Brand`, descriere benefit + livrare/COD, ≤60/≤158). `productUpdate(product:…)` / `collectionUpdate(input:…)` — `seo` înlocuiește, trimite ambele câmpuri.
- **H1** pe toate paginile; **H2/H3** curate (scoate heading-urile irelevante).
- **Descrieri LSI pe toate categoriile** (stil Limitless H2+beneficii+H3+FAQ) = metafield `rich_text_field` legat dinamic, randat **vizibil** (atenție la infinite scroll — pune-l sus, colapsat, nu jos unde nu se ajunge).

## B4. Faza 3 — Structură & internal linking (zilele 7–12)
- Ierarhie categorii (metafield `parent_collection`) → **mesh**: copii + frați + părinte, în **sidebar stânga lângă produse** (nu bloc deasupra), breadcrumb vizibil + JSON-LD.
- Optimizare **meniu** (structură pe topicuri) + **footer** (linkuri către categorii/pagini cheie) + **pagini de brand** (dacă brandul are sub-branduri).

## B5. Faza 4 — Conținut (lunar) · `core:<brand>-articles` + `gigi:ai-scrub`
- Cluster informațional: 2–4 articole/lună pe topicurile cu volum (ghiduri „cum alegi/amenajezi"), 1.500–2.000 cuvinte, H2/H3 + FAQ.
- **Internal linking blog → colecții** (bloc „Categorii recomandate") + colecții → blog.

## B6. Faza 5 — Off-page & AEO (lunar)
- **Link building**: articole pe site-uri externe cu link către brand (ce făcea Limitless în flux 4) + PR/parteneriate.
- **AEO/GEO** (`gigi:shopify-geo`): FAQ schema, pasaje front-loaded, robots pt boți AI, citabilitate ChatGPT/Perplexity.

## B7. Monitorizare (continuu) · `gigi:analytics`
- Săptămânal: `gsc.py wow` (brand vs non-brand), `gsc.py opportunities` (striking-distance). Lunar: GA4 canale + CVR organic. Feed: `gigi:merchant-center-feed`.

## B8. Mapare: livrabil Limitless → cum îl facem noi

| Ce livra Limitless | Cum facem noi (tool/skill) |
|---|---|
| Keyword research (volume RO) | `gigi:google-ads-mcc` → `kw_ideas.py` (Keyword Planner) + `gigi:analytics` GSC |
| Crawl/index + meta robots/canonical | `gigi:shopify-seo` (`meta-tags.liquid` per tip pagină) |
| Meta titlu/descriere/H1 în masă | `gigi:shopify-seo` (productUpdate/collectionUpdate bulk) |
| Descrieri SEO + FAQ (LSI) categorii | `gigi:shopify-seo` (metafield `seo_lsi` rich_text + FAQPage) |
| Schema internal linking | `gigi:shopify-seo` (mesh `parent_collection` + rail + breadcrumb) |
| Homepage / meniu / footer / brand / produs | `gigi:shopify-stores` (`shopify_theme.py`) după patternuri |
| Content marketing (blog) | `core:<brand>-articles` + `gigi:ai-scrub` + `publish_blog.py` |
| Link building (articole externe) | scriere internă + outreach (manual) |
| SEO Monitor (raportare) | `gigi:analytics` (`gsc.py`/`ga4.py`) |
| Prioritizare taskuri | acest plan + audit multi-agent (workflow) |

## B9. Calendar comprimat (recomandat pt brand nou)
- **Săpt. 1:** Faza 0 (tehnic) + start Faza 1 (keyword research).
- **Săpt. 2–3:** Faza 2 (acoperire meta + LSI) + Faza 3 (structură/linking).
- **Săpt. 4+:** Faza 4 (conținut, lunar) + Faza 5 (off-page) + B7 (monitorizare).
> Limitless a întins asta pe 5 luni (ritm de agenție). Cu uneltele noastre, fundația + acoperirea on-page se fac în ~2 săptămâni; conținutul și autoritatea rămân efort lunar continuu.
