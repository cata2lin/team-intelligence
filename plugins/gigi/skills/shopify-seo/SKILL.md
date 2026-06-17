---
name: shopify-seo
description: End-to-end SEO + good-practice optimisation for a Shopify store via the Admin API — audit, then fix on-page meta, duplicate content, image alt, structured data (Product/Offer/AggregateRating/Organization/WebSite/BreadcrumbList/Article/FAQ), navigation, sales-channel publication, technical hygiene (canonical, og/twitter, https, noindex), and a blog content cluster. Use when asked to "improve SEO", "audit SEO", fix meta titles/descriptions, add schema/rich results, fix social-share previews, surface collections in the menu, or otherwise raise organic visibility for a Shopify shop. Works on any store the ARONA Assistant app is installed on. Battle-tested on Esteban / GT / Nubra.
---

# shopify-seo

> Author: **Gigi**. Shared with the whole team via the `gigi` plugin.

A repeatable playbook + tooling to take a Shopify store from "audited" to
"fixed and verified" across the whole SEO surface. Encodes every fix **and every
hard-won gotcha** from the ARONA stores rollout, so the next site goes fast and
avoids the same traps.

## Golden rules (read first — these cost real time to learn)

1. **`seo:{}` REPLACES, it does not merge.** On `productUpdate`/`collectionUpdate`,
   if you send `seo:{title:…}` *without* `description`, Shopify **wipes** the
   description (and vice-versa). **Always send BOTH** title and description, even
   when changing only one. This silently nuked 300+ meta descriptions once.
2. **Themes often append the brand to `<title>`.** If your SEO title already ends
   with `| Brand`, the rendered title doubles: `… | Brand – Brand by X`. **Strip
   the brand** from SEO titles on stores whose theme appends it (check live first;
   it differs per template — product vs page vs article).
3. **Verify before you "fix".** Half the "issues" in the first audit were false:
   WebP *was* served (CDN content-negotiates on the `Accept` header), canonical
   *was* present (attribute order fooled a naive grep), Judge.me stars *were*
   there (injected client-side). Check with a real parser / proper headers / the
   API read-back **before** changing anything.
4. **Edge cache lies.** Storefront pages (esp. product & homepage) serve stale
   HTML even with `?nc=…`. The **API read-back is authoritative**; for rendered
   output, confirm a FAIL in a **real browser** (chrome-devtools) before believing it.
5. **Admin API talks to `*.myshopify.com`, not the custom domain.** Token + Admin
   API only work on the myshopify domain (from the secret). The custom domain is
   for storefront/live checks only. `Store` handles both.
6. **Publish to ALL sales channels, not just Online Store.** New smart collections
   default to Online Store only → invisible on Google & YouTube / Shop / etc.
7. **Never print a secret or token.** Fetch via `kb.py secret-get`, pipe into the process.
8. **Description renders twice:** a SHORT summary (`strip_html`+truncate → no links/bold) and a FULL tab (raw HTML → links work). Spec tables are usually a **Custom Liquid block in `templates/*.json`**, not a snippet — list metafields there need `| join: ", "`. Mutation types bite: `articleUpdate.body`=HTML!, `productUpdate.descriptionHtml`=String!. Pages may render **client-side** (curl shows nothing → use chrome-devtools) and **never inspect a draft product**. See `reference/pitfalls.md` §12-16.

## Auth / setup

The **ARONA Assistant** custom app (full scopes) is installed on the team stores.
Tokens are minted per-run via OAuth `client_credentials`. App id/secret/version
live in the SharedClaude `secrets` table; per-store admin domain in
`SHOPIFY_ARONA_<STORE>_DOMAIN`. All of this is wrapped in `scripts/shopify_lib.py`:

```python
import sys, os; sys.path.insert(0, os.path.dirname(__file__))
from shopify_lib import Store, fetch_live
s = Store("esteban")            # store key -> SHOPIFY_ARONA_ESTEBAN_DOMAIN, or pass a *.myshopify.com
s.gql("{ shop { name } }")      # Admin GraphQL (throttle-retry built in)
s.gql_all("products", "handle seo{title description}", "status:active")  # auto-paginate
s.rest("GET", "pages.json?handle=contact")
s.asset_get("layout/theme.liquid"); s.asset_put("layout/theme.liquid", new)  # main theme
fetch_live(f"https://{s.public}/products/x")   # storefront HTML, cache-busted
```

## Process — always in this order

### 1. Audit (read-only, no writes)
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/seo_audit.py" --store <key-or-myshopify-domain>
```
Prints a pass/fail matrix: meta coverage (products/collections), structured-data
presence & validity, og:image https + twitter:image, canonical, single H1,
breadcrumbs, brand-doubling in `<title>`, homepage SEO text, channel breadth,
sitemap. **Re-check any FAIL in a browser** before acting (cache).

### 2. Prioritise by impact
Rough order of ROI (see `reference/playbook.md` for the full list & how-to):
1. **Duplicate content** → unique product/collection descriptions (the #1 killer).
2. **Missing meta** → SEO title + meta description on products, collections, pages.
3. **Structured data** → Product+Offer, AggregateRating (verify Judge.me first!),
   Organization+sameAs, WebSite+SearchAction, BreadcrumbList, Article, FAQPage.
4. **Image alt** → bulk, but detect shared images (generic alt on shared, specific on unique).
5. **Navigation/structure** → high-intent collections into the menu; publish to all channels.
6. **Technical hygiene** → canonical, og:image https, twitter:image, noindex utility pages.
7. **Content** → blog cluster (topic articles + internal links), homepage SEO text.

### 3. Fix — dry-run, apply, verify
Every change: build → print a dry-run diff → `--apply` → verify (API read-back +
cache-busted live + browser for anything visible). Get user approval for anything
**visible or structural** (menus, homepage content, redirects, noindex).

### 4. Re-audit
Re-run `seo_audit.py`; confirm the matrix is green. Note any deliberate skips.

## What "cover everything" means — the full checklist

On-page: unique descriptions · SEO title ≤60 · meta desc ≤155 · no brand-doubling ·
no double-spaces · H1 single & meaningful. Collections: intro copy + SEO meta +
internal cross-links. Pages: title_tag/description_tag metafields on real pages
(skip utility/funnel). Images: alt everywhere (shared-image aware); WebP is
automatic via CDN (verify, don't "fix"). Structured data: all of the types above,
**valid JSON** (parse every block). Technical: canonical present · og:image
**https** · twitter:image · hreflang only if multi-locale · 404 real · noindex
search/utility pages. Structure: gender + family + brand collections in the menu
(`menuUpdate` preserves existing items faithfully) · everything published to all
sales channels · sitemap includes new collections/articles. Content: blog cluster
with AI hero images · homepage SEO text block.

Deep detail, exact patterns, and copy templates: **`reference/playbook.md`**.
The traps that waste hours: **`reference/pitfalls.md`**.
Drop-in Liquid / JSON-LD: **`reference/snippets.md`**.

## Crawl-based internal-link audit — `linkgraph.py`
The Admin-API audit above sees pages in isolation; this sees the **link graph**. BFS-crawls from the homepage (seeded with the full sitemap incl. Shopify's `?from=&to=` sub-sitemaps), builds the internal link graph, computes internal **PageRank**, click-depth and inbound counts, and flags **orphan** (0 inbound), **under-linked** (<3), and **too-deep** (>3 clicks) pages. Pure stdlib + requests/bs4, no keys.
```bash
uv run linkgraph.py audit --site esteban.ro --max 150 --threads 10
```
Typical win it surfaces: **blog articles orphaned** (0 internal inbound) — on esteban.ro every blog post was an orphan, so the AEO/organic content wasn't linked from anywhere. Fix = add contextual internal links from high-PageRank pages (top collections, a blog hub) down to the buried product/collection/blog pages it lists. Pairs with `gigi:shopify-geo` (that orphaned blog content is exactly the AEO play).

## SEO drift baseline — `drift.py`
Catches **silent regressions**: a theme update or app that quietly drops a title/canonical/schema or flips a page to `noindex`. Snapshots the SEO-critical fields into local SQLite (`~/.cache/arona-seo/drift.db`) and diffs later — the complement to GSC week-over-week (which sees traffic, not the cause). Pure stdlib + requests/bs4.
```bash
uv run drift.py baseline --site esteban.ro --max 40        # snapshot top pages (weekly, e.g. cron)
uv run drift.py compare  --url https://esteban.ro/collections/dama   # diff vs last snapshot
uv run drift.py history  --url https://esteban.ro/collections/dama
```
Snapshots title/meta/canonical/robots/H1/H2-count/JSON-LD types/OG/word-count + a hash. `compare` flags 🔴 CRITIC (title/canonical/robots-noindex/status/schema removed), 🟡 WARN (meta desc/H1/OG/word-count drop >30%), ℹ️ info. Run `baseline` weekly; `compare` (or re-baseline) to see what changed since. Pairs with GSC `wow` (`gigi:analytics`): wow tells you traffic dropped, drift tells you *which on-page element broke*.

## Apply listing/SEO fixes to a product — `scripts/product_fix.py` (DRY-RUN default)
The write-layer that turns diagnoses (from `gigi:cro`, `gigi:pricewatch compare`, `gigi:merchant-center-feed`, `gigi:cross-sell`) into actual product changes — **selectively + safely**.
```bash
# DRY-RUN (writes nothing) — shows before → after for each fix you pass:
uv run scripts/product_fix.py --store esteban --product <handle> \
   --seo-title "..." --seo-description "..." --body-file new.html
# Grandia & the single 'shopify' app: --app SHOPIFY --store n12w89-yy.myshopify.com
# Cross-sell metafield:  --metafield "custom.bought_together=gid://shopify/Product/..."
# Execute only after approval:  add --apply
```
- **DRY-RUN by default** — nothing is written without `--apply` (team write-guardrail, like `cs-actions`).
- **Selective approval** = you pass only the fix flags you approve (`--seo-title`/`--seo-description`/`--body`/`--metafield`); each shows `era → nou`.
- **Two apps:** `--app SHOPIFY_ARONA` (default: esteban/gt/nubra/labnoir) or `--app SHOPIFY` (the `n12w89-yy.myshopify.com` store = Grandia etc.).
- **Scope (all / low-sellers / specific):** the *selection* of which products comes from the diagnosis skills (cross-sell low-sellers, merchant-feed disapprovals, pricewatch compare) — state in chat which set + how many you're acting on; `product_fix` applies per product. Verified DRY-RUN: Grandia "raft-depozitare…" had an **empty SEO description** → the writer would fill it.

## Perfume "inspired-by" catalog — full playbook in `reference/perfume-catalog-playbook.md`
For a dupe/"inspirat din" perfume store (Esteban, Nubra, GT, LabNoir…) the **complete, ordered,
per-theme process** lives in `reference/perfume-catalog-playbook.md` — brand collections, menu,
**collection sidebar ("După brand"+"Categorii") per theme**, internal linking, copy rewrite,
**note/metafield verification vs the real original** (`scripts/verify-perfume-notes.workflow.js`),
**dynamic FAQ + FAQPage schema**, the inspired_by brand link (+ the EmptyDrop `products_count`
bug), and the description=blog / sidebar=collections split. **Read it before re-deriving any of
this.** The scripts below are the building blocks.

## Catalog/nav structure — `scripts/brand_collections.py` + `scripts/menu_addbrands.py`
For dupe/inspired-by catalogs: build **smart collections by inspiration brand** (SEO hubs + internal-link targets) and a **"După Brand" menu dropdown**.
```bash
uv run scripts/brand_collections.py --store esteban --min 3 [--brand-name "Nubra"]  # DRY-RUN: brands (from "...by <Brand>" titles) → proposed smart collections
uv run scripts/brand_collections.py --store esteban --min 3 --apply                 # create + auto-publish to all channels
uv run scripts/menu_addbrands.py    --store nubra --top 8 --title "Inspirate din" --after "Unisex"        # DRY-RUN
uv run scripts/menu_addbrands.py    --store nubra --top 8 --title "Inspirate din" --after "Unisex" --apply # menuUpdate (preserves the whole tree)
```
DRY-RUN by default. `menu_addbrands` keeps only the **top-N brands by product count** in the menu (rest stay as collections for SEO/links — don't dump 21 items in nav); `--title` names the item, `--after "<menu item title>"` controls placement (default "Toate parfumurile"; falls back to front if not found). `brand_collections --brand-name` overrides the shop name in SEO (default = `shop.name`); `--apply` **auto-publishes each new collection to ALL sales channels** (golden rule #6 — new collections default to ZERO channels → 404 + invisible on Google/Shop; this bit us once). Proven on **Esteban** (21 collections + "Inspirate din" menu) and **Nubra** (17 collections + menu). **Per-store caveat:** how a store encodes the inspiration varies — Esteban/Nubra titles carry "... by <Brand>" (title-CONTAINS rule), but **GT** has no brand in the title (it's in `custom.inspired_by` = "Miss by Dior") and its existing brand collections use a **TAG EQUALS <Brand>** rule. Check the title format + an existing brand collection's `ruleSet` before running.

## Internal linking — `scripts/internal_links.py` (DRY-RUN default)
Distribuie PageRank intern + de-orfanizează conținut. **Fără emoji** în textul inserat (convenție echipă). Toate inserțiile sunt **idempotente** (un marker regex șterge blocul anterior înainte de re-adăugare) și fiecare `collectionUpdate` retrimite SEO title+description existente (golden rule #1). Trei moduri:
```bash
uv run scripts/internal_links.py cluster   --store esteban --top 8           # interlink top-N colecții de brand (fiecare -> 3 frați circular)
uv run scripts/internal_links.py pdp-brand --store esteban --top 0 --apply    # link spre colecția de brand pe TOATE produsele (top 0 = toate brandurile)
uv run scripts/internal_links.py deorphan  --store esteban --map deorphan.json --apply  # colecție<->articol bidirecțional dintr-un JSON
```
- **cluster**: huburile de brand se leagă între ele -> crawl + flux PageRank. Ancoră = numele brandului.
- **pdp-brand**: pe fiecare produs „... by <Brand>" adaugă la finalul descrierii `Vezi toate <a>parfumurile inspirate din <Brand></a>` (ancoră = brandul, țintă = colecția lui). `--top 0` = toate brandurile (Esteban: **133 produse**); `--top 8` = doar topurile.
- **deorphan**: dă fiecărui articol orfan un inbound dintr-o colecție-hub tematică (colecțiile sunt în meniu => PageRank mare) + un CTA înapoi din articol. Maparea (colecție↔articole) e specifică magazinului -> fișier JSON: `[{"collection":"dama","label":"Damă","articles":[{"handle":"...","title":"..."}]}]`.

Făcut pe Esteban (Jun 2026): cluster top-8 + de-orfanizate toate 15 articolele blog (bidirecțional colecție↔articol) + link de brand pe toate 133 produse. **`mutation` type-uri care înșeală:** `articleUpdate.body` = **HTML!**, `productUpdate.descriptionHtml` = **String!** (opuse — nu le confunda).

## Baseline comun pe magazinele ARONA (ce găsești pe un store nou)

Din rollout-ul **Esteban / GT / Nubra** (iun 2026, baseline din Admin API) — verifică ÎNTÂI astea cu `scripts/seo_audit.py`, sunt aproape garantate pe orice store ARONA nou:
- **Duplicate/thin content la produse** — cel mai mare blocaj: Esteban 138/153, GT 161/163, Nubra 150/151 produse cu descriere IDENTICĂ (template ~27-43 cuvinte). Fix: descrieri unice (vezi `core:<store>-articles` / `product_fix.py`).
- **Alt text 100% lipsă** — ~1.200 imagini fără alt (Esteban 483, GT 412, Nubra 305), inclusiv featured.
- **Meta SEO lipsă** — GT & Nubra: 100% produse fără `seo_title`+`meta_description`; TOATE colecțiile (Esteban 24, GT 6, Nubra 5) fără meta. Setate ca metafields `global.title_tag`/`global.description_tag`. **Regula de aur:** `seo:{}` ÎNLOCUIEȘTE (nu merge) → trimite mereu title ȘI description.
- **Mizerie colecții** (Esteban): nume duplicate („Cele mai vândute" ×4), colecții junk indexabile („Home page", „Ultimate Search - Do not delete").
Aceleași clase de probleme + recomandările tehnice (brand pages, internal linking, schema) le-am rezolvat și pe **Grandia** (mai jos). Patternurile generalizează între magazine.

## Pagini de brand + internal linking + schema (metaobject-driven — Grandia, iun 2026)

Pattern-uri pt un magazin unde brandurile sunt **metaobjects** (`type: brand`, câmpuri name/logo/description) și produsele referă unul printr-un metafield `metaobject_reference` (`custom.brand`). Implementate live pe Grandia (recomandările agenției Limitless). Editarea temei: vezi `gigi:shopify-stores` (`scripts/shopify_theme.py`).
- **Colecții de listare per brand:** o **smart collection** per brand cu regula `column: PRODUCT_METAFIELD_DEFINITION, relation: EQUALS, condition: <metaobject gid>, conditionObjectId: <metafield-definition gid>`, apoi `publishablePublish` pe Online Store. **CAPCANĂ:** smart collections NU se reindexează după un `metafieldsSet` în masă → reaplică ruleSet-ul (`collectionUpdate` cu aceeași regulă) ca să forțezi re-evaluarea.
- **Pagina de brand** (template metaobject): H1=brand.name, H2/H3 în jurul listării, sidebar „BRANDURI" care enumeră `shop.metaobjects.brand.values` (filtrat la cele cu colecție `brand-<handle>` care are produse), brand activ evidențiat, breadcrumb + `BreadcrumbList` JSON-LD. Randează produsele cu grila TA (nu grila full-width a temei — colapsează în coloană îngustă).
- **Agregator + strip de branduri pe homepage:** auto-enumeră `shop.metaobjects.brand.values` în loc de blocuri adăugate manual (ca brandurile noi să apară automat).
- **Meta title per brand:** metaobjectele n-au câmp SEO implicit → adaugă `seo_title`/`seo_description` la definiție (`metaobjectDefinitionUpdate`), populează, și în `meta-tags.liquid` suprascrie `page_title`/description când `metaobject.seo_title != blank`.
- **Mesh internal linking pe categorii:** colecțiile au `custom.parent_collection` (referință Collection) → pe pagina de categorie arată copiii (existenți) + un bloc „ALTE CATEGORII" cu **frați** (același parent) + **părinte**; pt o categorie top-level, frații = categoriile principale din main-menu.
- **Pagina de produs:** secțiune de mesh internal-linking (link spre colecțiile principale) + `BreadcrumbList` JSON-LD care oglindește breadcrumb-ul vizibil. Judge.me injectează deja un al 2-lea nod Product cu `aggregateRating` pe același `@id` → stele în SERP (nu dubla).
- **Colecții de filtrare/duplicat** (ex. `brand-*` folosite doar ca sursă de date): `noindex,follow` + canonical spre pagina reală în `meta-tags.liquid`, ca să eviți conținut duplicat. **Shopify auto-injectează** canonical pe URL-uri cu `?sort_by`/`?q=`/paginate via `content_for_header` — tema nu-l poate scoate (și nici nu trebuie; e consolidare corectă).

## Acoperire SEO în masă + schema + layout colecții (Grandia, iun 2026 — runda 2)

Proiect mare „toate categoriile" pe Grandia. Toate aplicate live + verificate desktop **și** mobil. Editarea temei: `gigi:shopify-stores` (`shopify_theme.py`). API version 2026-01.

- **Meta în masă pe produse:** `productUpdate(product: ProductUpdateInput!)` (NU mai e `input:` în 2026-01) cu `seo:{title,description}`. Generează din câmpuri (titlu + `grupa_principala` + frază-beneficiu per categorie); titlu ≤60 (taie la cuvânt + scoate cuvintele de legătură finale „de/sau/cu/pentru…"), descriere ≤158 cu trust („Livrare rapidă, plata la livrare"). **Trimite mereu title ȘI description** (seo înlocuiește) — păstrează valorile existente unde sunt. ~476 produse, 0 erori. Reparat și vendor junk (`n12w89-yy`→brand real, ca feed-ul să aibă brand).
- **Meta + intro pe colecții:** `collectionUpdate(input: CollectionInput!)` cu `seo:{}` + `descriptionHtml` (intro scurt keyword-rich deasupra produselor). Themed pe categorie (detectează tema din titlu/handle). Exclude `brand-*` (sunt noindex) + `all`.
- **Descrieri LSI pe colecții = metafield `custom.seo_lsi` de tip `rich_text_field` (NU HTML!).** Format = JSON Shopify: `{"type":"root","children":[{"type":"heading","level":2,"children":[{"type":"text","value":"…"}]},{"type":"paragraph",…},{"type":"list","listType":"unordered","children":[{"type":"list-item",…}]}]}` (text node acceptă `"bold":true`). Se randează prin **binding dinamic** în template: setting-ul secțiunii = `{{ collection.metafields.custom.seo_lsi | metafield_tag }}` (Liquid convertește rich-text→HTML). Stil Limitless: H2 + intro + ghid „Cum alegi" + listă beneficii + FAQ. **Pune LSI-ul SUB produse** (mută instanța secțiunii după `main` în order[] — Google citește below-the-fold; userii văd produsele primii).
- **FAQPage JSON-LD** (din FAQ-ul deja vizibil în LSI): metafield `custom.faq_jsonld` (type `json`, listă `[{q,a}]`) + snippet care emite `FAQPage` (`item.q | json`, `item.a | json`), randat din `main-collection`. Conform Google doar dacă FAQ-ul e vizibil pe pagină (este, în LSI).
- **Schema globală:** `WebSite`+`SearchAction` în `layout/theme.liquid` doar pe home (`request.page_type=='index'`, `urlTemplate: {{shop.secure_url}}/search?q={search_term_string}`); `BreadcrumbList` pe colecții dintr-un snippet care urcă pe lanțul `custom.parent_collection` (Home>parent>current, sare peste `brand-*`/`all`).
- **H1 pe PDP (temă Horizon pe blocuri):** blocul `blocks/product-title.liquid` emitea `<p>` (H1=0 pe toate produsele). Fă-l `<h1>` DOAR pt produsul principal: gardă `request.page_type=='product' and (product==blank or closest.product.id==product.id)` → cardurile/recomandările (încărcate via Section API, alt page_type) rămân `<p>`. Dacă blocul are deja `type_preset: h1`, schimbarea tagului e vizual identică. Verifică: **exact 1 H1/pagină** (PDP și colecție).
- **Sidebar categorii pe colecții (2 coloane, internal linking):** mută nav-ul de categorii dintr-o secțiune separată deasupra produselor într-o **coloană stânga reală** în `main-collection` (snippet `category-rail.liquid` = children cu thumbnail + „Alte categorii" frați/părinte). Împachetează `<results-list>` într-un `.coll2` (grid `248px minmax(0,1fr)` la ≥990px, rail sticky). Grila de produse e `repeat(auto-fill, minmax())` → se reflowează singură în coloana îngustă (fără surgery). Neutralizează centrarea page-width: `.coll2 .collection-wrapper{display:block}`. Mobil: children = slider orizontal, frații ascunși. Gardă `.coll2:has(.cat-rail)` ca grid-ul să nu se aplice pe search/empty. **Efect: primul produs urcă mult (973→351px), above the fold.**
- **Striking-distance din GSC real** (`gigi:analytics` → `gsc.py opportunities --brand <b>`): colecțiile pe pagina 2 cu impresii mari → rescrie `seo_title`/`seo_desc` cu termenul exact în față; produsele top-rank (poz≤5) cu CTR ~0% → meta description mai atractiv. Cel mai ieftin câștig: urci în pagina 1 fără pagină nouă.
- **Internal linking blog:** articolele orfane → `articleUpdate(id, article:{body})` cu un bloc „Categorii recomandate" (3-4 linkuri spre colecții relevante, mapate pe keyword din titlu); marker `<!-- … -->` ca să fie idempotent. Funnel de autoritate spre paginile comerciale.
- **PRINCIPIU — colecții head-term doar cu STOC:** keyword-research dă CEREREA, dar colecție creezi DOAR unde ai produse reale (~10+). Termen cu volum mare fără marfă = pagină goală = thin content (penalizare), nu task SEO ci decizie de aprovizionare. Validează inventarul ÎNTÂI. Câștig curat = re-terminologie pe stoc existent (ex. „chiuvetă" 18k/lună pe produsele numite „lavoar": retitlezi colecția + meta + LSI cu ambii termeni).
- **Capcane temă:** (1) **Edge-cache Shopify pe colecții e foarte persistent** — `ignoreCache` la reload NU ajunge; verifică prin URL cu query unic `?fresh=NNN` în context Chrome izolat. (2) Ca să ștergi o secțiune din template trebuie scoasă din **AMBELE** `sections` ȘI `order` (altfel 422). (3) Dacă muți/ștergi secțiunea care randa un JSON-LD (ex. BreadcrumbList era în `category_bar`), re-randează-l din altă parte (`main-collection`) — altfel pierzi schema. (4) `seo` pe product/collection ÎNLOCUIEȘTE — trimite ambele câmpuri.

## Logging (team convention)
After a run: `kb.py log --type skill --action used --name gigi:shopify-seo --summary "…"`.
