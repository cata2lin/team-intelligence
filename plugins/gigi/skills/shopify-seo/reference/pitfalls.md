# Pitfalls — the traps that cost real time on the ARONA rollout

Each one bit us once. Read before touching a new store.

## 1. `seo:{}` replaces, never merges  ⚠️ the expensive one
`productUpdate(input:{seo:{title:"x"}})` **clears** `seo.description`. We stripped
the brand from SEO *titles* and silently wiped meta descriptions on 300+ products
and 44 collections. Symptom was sneaky: pages still showed a description because
the theme **falls back to the product body** for `<meta name=description>`, so the
field looked "fine" in the admin preview while the dedicated field was empty.
**Rule:** always send `seo:{title, description}` together. To change one, read the
current other first and pass it back.

## 2. Theme appends the brand to `<title>`
Set SEO title `"… | GT Parfumuri"`, theme appends `" – GT Parfumuri by George
Talent"` → brand twice. Happens per-template and differs: GT doubled on products,
collections, **pages, and articles**; Esteban doubled on products but **not** pages.
Nubra didn't append at all. **Always check the rendered `<title>` per page type**
and strip the brand from the stored SEO title where the theme appends. Don't strip
where it doesn't (a single `| Nubra` is fine and is NOT doubling — beware
false-positive "doubling" detectors that flag any single brand token).

## 3. "Issue" that isn't there — verify before fixing
- **WebP "missing":** CDN serves webp/avif via content negotiation. A curl without
  `Accept: image/webp` sees jpeg. Test with the header → it IS webp. Non-issue.
- **canonical "missing":** present, but `<link href=… rel=canonical>` (attr order)
  dodged a `rel="canonical" href=` regex. Use a tolerant `<meta/link>` parser.
- **Judge.me stars "missing":** injected client-side via JS — present in a browser,
  absent in curl. Only GT genuinely lacked them.
- **meta description "empty":** see #1 — body fallback renders even when the field
  is empty; and an attribute-order regex misses the tag entirely.
Lesson: parse properly, send the right headers, read the API back, and look in a
**browser** before declaring a problem — and certainly before "fixing" it.

## 4. Edge cache makes verification lie
Storefront HTML (esp. product & homepage) is cached hard; `?nc=<ts>` busts the
HTML URL but a given page can still serve stale for a while, and different products
refresh at different times. So a fresh-looking curl can show the OLD `<title>`/
schema right after you changed it. **API read-back is the source of truth for
fields;** for rendered output, confirm any FAIL in chrome-devtools. Several "still
broken!" moments were just cache.

## 5. Admin API host ≠ custom domain
OAuth token + Admin API only respond on `*.myshopify.com` (the secret domain).
Hitting `https://esteban.ro/admin/oauth/access_token` returns HTML →
`JSONDecodeError`. Use the myshopify domain for API; the custom domain only for
storefront/live fetches. `Store` resolves both (`.admin` / `.public`).

## 6. New collections publish to Online Store only
`publishablePublish` with just the Online Store publication leaves a collection
**off Google & YouTube / Shop / FB&IG / TikTok / POS**. Existing collections were
on all 6; the new ones weren't, so they were invisible to Shopping. Always publish
new collections to **every** publication.

## 7. Theme asset PUT persistence is inconsistent
- `sections/footer-group.json` (section-group): a full-object PUT **did not
  persist** (the live footer is `footer-group.json`, not `settings_data.json`).
  String-replace on the fetched value **did** persist.
- `settings_data.json` social toggles: full-dump PUT didn't stick → owner toggled
  in the Theme Customizer instead.
- Template JSONs and Liquid files: behave normally (full PUT / string-replace).
When a "successful" PUT doesn't show up live, suspect this and switch to
string-replace, or hand the toggle to the owner via the Customizer.

## 8. GraphQL shape differs by API version
`pageByHandle`, `menu(handle:…)` threw `KeyError 'data'` on the store's version.
Use the connection form with a query filter instead: `pages(first:1, query:"handle:…")`,
`menus(first:20){nodes{…}}` filtered client-side. Always guard `if "errors" in r`.

## 9. Sitemap sub-URLs carry required params
`/sitemap.xml` lists `…/sitemap_collections_1.xml?from=<id>&to=<id>`. Fetching the
sub-sitemap **without** the `?from&to` query returns empty → false "0 collections
in sitemap". Parse the sub-sitemap `<loc>` (with its params) from the index.

## 10. Don't touch what the owner uses
- **GTIN/barcodes** may feed the WMS — never "clean" them.
- **Visible product titles** — don't rename for SEO; use the SEO title field.
- **Ad pixels / third-party scripts** — don't defer/strip; breaks attribution.
- **"Junk"-looking collections** (`frontpage`/"Home page") may be real, populated
  categories — confirm before noindex/redirect/delete. We wrongly noindexed
  `frontpage` once; it was a real perfume collection.
- **/collections/all redirects** — Shopify ignores redirects on real routes (returns
  200); the owner may want both collections left as-is. Ask.

## 11. Get approval for anything visible or structural
Menus, homepage content blocks, redirects, noindex, breadcrumb styling — propose
first (mockup the structure), apply after sign-off. The owner corrected the menu
labels ("Fresh" not "Proaspete") and a wrongly-noindexed collection post-hoc; both
were avoidable with an upfront yes/no.

## 12. Product description renders via `strip_html` — a link there is NOT clickable until you fix the snippet
On Esteban's Ella/Halo theme the ONLY place the product description shows is
`snippets/product-short-description.liquid`:
```liquid
{{ desc | strip_html | truncatewords: word_number }}
```
`strip_html` removes ALL tags → an `<a>` becomes plain text (un-clickable), `<strong>`
won't bold, and adjacent `</p><p>` paragraphs glue ("word.Next", no space). There is
**no separate full-HTML description tab** on this layout — verify in the **browser
DOM** (`.productView-desc` → `querySelector('a')`), not just `curl` (a stray raw
`<strong>` in served HTML can be a hidden/schema block, not the visible description).
A `c_f.short_description` metafield can override `desc` (also stripped).

To make an in-description link clickable + bold render: edit the snippet to
`{{ desc }}` (raw). Trade-off: the FULL description then shows by the buy button with
no truncation — fine if descriptions are concise (ours are). Back up the snippet
first; it's a sitewide visible change. Also insert a space at paragraph boundaries
(`</p> <p>`) so even a stripped render doesn't glue sentences.

## 13. Mutation field types that mismatch (silent until you hit them)
- `articleUpdate(article:{body})` → **HTML!**
- `productUpdate(input:{descriptionHtml})` → **String!**  (opposite of articleUpdate)
- `collectionUpdate`/`productUpdate` `seo{}` **REPLACES** — always re-send title+desc.
- New collections publish to **0 channels** → 404 on storefront; `publishablePublish`
  to every `publications` node (see golden rule #6).

## 14. Pages can render client-side — curl won't see them
On JS-heavy themes/apps the product content (tabs, spec tables) is built in the
browser. `curl` returns a shell with the text **absent**. Use chrome-devtools
(`evaluate_script`) to inspect the real DOM, computed styles, and class names.
**Never inspect a `status:draft` product** for rendering — it won't render at all
(wasted a debugging loop on a draft once). Filter `status:active` first.

## 15. Spec tables are often a Custom Liquid block in templates/*.json, not a snippet
A grepping pass over `.liquid` finds nothing because the block lives in
`templates/product.json` (and variants) as a `custom_liquid` section setting
(also check `config/settings_data.json`). Grep the **`.json`** assets for the CSS
class (e.g. `specs-container`). Edit it by parsing the JSON, replacing the block's
`settings.custom_liquid`, and `asset_put` the whole template back (back it up first).
Two real bugs we fixed on Esteban's spec grid:
- **list metafields without `| join`** (`custom.sex` etc.) render glued:
  "UnisexFemeiBarbati". Add `| join: ", "` (safe no-op on string metafields too).
- grid misaligns when a value wraps to 2 lines → add `align-items: start;`.
Spec metafields on Esteban: `custom.note_parfum`/`sex`/`mom_zi` =
`list.single_line_text_field` (JSON array value); `varf`/`note_inima`/`note_baza` =
`multi_line_text_field` (plain comma string); `volum` = `number_integer`.
Gift-set templates (`set-3`/`set-6`/`kit`/`parfum-cadou`) disable the spec block —
don't expect a fragrance pyramid there.

## 16. Copy voice for mass-market RO (perfume)
The owner's bar: **no niche jargon** ("siaj"/"sillage" — even the DOOM-correct
"siaj" reads as connoisseur-speak), **few adjectives** (cut "cremoasă, învăluitoare,
catifelată" pile-ups), **no "flacon"**, and **don't repeat the on-page offer**
("2+1 gratis" already shows above the description). Keep notes faithful to the
original — never invent a fragrance pyramid. Format: 2-3 short `<p>` with a bold
`Profil olfactiv:`. Pair with `gigi:ai-scrub` for the de-AI pass.

## 17. CDN cache makes a live fix look broken ("nu merge" was just stale cache)
Shopify's full-page cache serves **stale, even inconsistent-per-URL** HTML: one
`?a=1` render showed the new raw description, a sibling `?b=2` still showed the old
stripped one — same product, same moment. We burned several rounds because a theme
fix "didn't work" when it was only cache. **Before concluding a change failed:**
1. Read the change back from the **theme asset** (`asset_get`) — is the source right?
2. In the browser, **reload with `ignoreCache: true`** (or Cmd+Shift+R / incognito)
   and re-check the DOM, not a normal navigation.
3. Touching the product (`productUpdate`) busts that product's page cache.
Adding `?nc=<rand>` busts collection pages but **not always product pages**. If the
asset is correct and an ignore-cache reload shows the fix, it IS live — tell the
owner to hard-refresh; it propagates on its own in minutes.

## 18. Filling missing perfume metafields
Audit gaps per key across `status:active` products; most "gaps" are gift
sets/bundles (different template, spec block disabled) — exclude them. Real single
perfumes usually miss only `sex` or `note_parfum`, fillable accurately from the
known original (e.g. Tom Ford Private Blend, MFK, Sospiro, Kilian, Nasomatto, Xerjoff
niche = **Unisex**, not the gender collection they sit in). `sex`/`note_parfum` are
`list.single_line_text_field` → set value as a JSON array string via `metafieldsSet`.
A frequent import artifact: `sex = ["Unisex","Femei","Barbati"]` (all three) on niche
unisex perfumes — collapse to `["Unisex"]`.

## 20. `collections[handle]` for a missing collection throws on `.products_count > 0`
`collections['does-not-exist']` returns an EmptyDrop whose `.products_count` is an **empty
string**, so `{% if bcol.products_count > 0 %}` throws `Liquid error: comparison of String
with 0 failed` on the live page (hit it on a product whose brand had no collection, e.g.
Louis Vuitton). Guard with `{% if bcol != blank and bcol.id %}` instead — never compare a
possibly-empty drop field with a number.

## 21. The full perfume-catalog process is documented — don't re-derive it
For inspired-by perfume stores, the entire ordered, per-theme playbook (brand collections →
menu → sidebar → internal links → copy → note verification → FAQ → inspired_by link) is in
`reference/perfume-catalog-playbook.md`, with the Esteban-vs-Nubra theme matrix and the
note-verification workflow (`scripts/verify-perfume-notes.workflow.js`). Read it first.

## 19. Each store is a different theme — re-check rendering before replicating
The same owner's perfume stores run **different themes** with different behaviour;
don't assume Esteban's fixes transfer 1:1. Check per store before mass-editing:
- **Description rendering:** Esteban (Ella/Halo) renders the description via a
  `strip_html` short snippet → needs the `{{ desc }}` edit for clickable links
  (§12). **Nubra** (an Online Store 2.0 theme) renders `{{ closest.product.description }}`
  **raw** in a `blocks/product-description.liquid` → links/bold work natively, no edit.
- **Spec table:** Esteban builds it from a `custom_liquid` block in `templates/product.json`
  (the `| join` + `align-items` bug, §15). **Nubra** has no such block (renders metafields
  via a native OS2.0 block) → nothing to fix.
- **Brand encoding (for `brand_collections`):** Esteban/Nubra carry "... by <Brand>" in
  the product title; **GT** has no brand in the title (it's in `custom.inspired_by`) and
  its brand collections use a **TAG EQUALS** rule, not title-CONTAINS.
- **Volume metafield key** even varies: `custom.volum` (Esteban/Nubra) vs
  `custom.volum_ml_` (GT).
Playbook order that worked for a store (Esteban, Nubra): brand collections (+publish)
→ menu → internal-link cluster → copy rewrite (6-agent fan-out, see §16) → brand link
on PDPs → de-orphan blog articles (per-store `deorphan_<store>.json` map) → fill
metafield/SEO gaps.

## Meta-quality sweep & gotchas (Jun 2026 — Grandia / Belasil)
**"100% SEO coverage" can be a lie on supplier-import stores.** Stores that import from
CN suppliers (Grandia, Belasil) ship products with **empty `seo.title`** and **raw
supplier-junk `seo.description`** (e.g. `Brand Name:MagiDeal / Origin:Mainland China /
Material:...`). A prior "100%" claim missed **80/476** products on Grandia; **all 21**
Belasil products had **zero** meta description. Run a recurring read-only sweep —
paginate `status:active` products and flag:
- empty/blank `seo.title`;
- `seo.description` matching: `Brand Name:|Model Number:|Mainland China|High-concerned|Origin:|Item Type:|Package Included|Feature:Stocked|Material:[A-Z]|Color:[A-Z]|Size:[0-9].*inch`.
Regenerate keyword-led RO title (≤60) + desc (120–155) **grounded ONLY in the product's
real title** — validate: every number in the new meta must already appear in
`title+handle`, else it's hallucinated. The product **`title` is ground truth, NOT the
handle** (handles go stale: a balloon handle said `80-cm` but the real title was `90 cm`).

**`seo.title == product.title` → Shopify stores `null`** (no userError); `<title>` falls
back to the product title (fine if concise). Don't chase it as "empty/broken".

**High rank + ~0 CTR → check for a CONTAMINATED `seo.title`.** Belasil ranked #1.2 on
"lavete magice" (789 impr) with **0 clicks** because the same wrong title ("Belasil Ultra
– Kit 10 lavete…") was copy-pasted across every lavete product → SERP title didn't match
the query. Fix = give each product its own keyword-matching title.

## Homepage title/meta — the `{% if page_description %}` trap
Themes output `<title>{{ page_title }}…</title>` where on the homepage `page_title` falls
back to `shop.name` (e.g. just "Belasil"), and the meta-description line is often wrapped in
`{% if page_description %}…{% endif %}` — **false on the homepage** (blank) → homepage gets
**no meta description at all**. Fix in `layout/theme.liquid`:
- Title: `{%- if request.page_type == 'index' and page_title == shop.name -%}<keyword-rich homepage title>{%- else -%}…default…{%- endif -%}` (the `== shop.name` guard respects a Preferences title set later).
- Desc: replace the block with `{%- if page_description != blank -%}<meta …page_description>{%- elsif request.page_type == 'index' -%}<meta …homepage fallback>{%- endif -%}`.
Also set og:title/og:description homepage fallbacks in `snippets/meta-tags.liquid` (og_description defaults to `shop.name` otherwise).

## og:image homepage gap
Many themes emit og:image only inside `{%- if page_image -%}` → the homepage (no
`page_image`) renders **no og:image / twitter:image**. Add a fallback in `meta-tags.liquid`:
`{%- if page_image == blank and request.page_type == 'index' and settings.logo != blank -%}`
→ og:image + og:image:secure_url + twitter:image = `https:{{ settings.logo | image_url: width: 1200 }}`.
Also: og:image served on `http:` → switch primary to `https:` (keep `og:image:secure_url`),
and add `twitter:image` (themes often omit it).

## Cross-store access — the ARONA app is NOT on every store
`shopify_lib.Store` / `seo_audit.py` use the ARONA custom-app `client_credentials` grant —
installed on Esteban/GT/Nubra/Grandia but **NOT Belasil** (seo_audit.py errors there). For
those stores use `../shopify-stores/scripts/shopify_gql.py --prefix <PREFIX>` (token from
`SHOPIFY_STORES_CSV`) for Admin GraphQL, and storefront fetch for on-page checks.

## Asset API read-after-write lag
`PUT themes/{id}/assets.json` returns OK but an **immediate** GET can still show the OLD
content (eventual consistency) — re-read after a few seconds before concluding the edit
failed. Separately, the **storefront** is edge-cached (very persistent): verify the THEME
SOURCE via `asset_get`, not just the live page (`?nc=` doesn't reliably bust the full-page cache).

## Footer ANPC/SOL badges & compliance links (hard-won, 2026-06)
Surfacing the ANPC SAL + SOL icon badges in store footers (see `scripts/footer_badges.py`)
hit a wall of theme-specific traps. The legal *content* (trader-id + ANPC links) lives in
the Terms policy via `compliance.py`; the *visible footer icons* are this separate job.

- **Verify footer colour with PIXELS, not your eyes.** chrome-devtools `take_screenshot`
  is unreliable for footers: lazy-loaded content (review carousels) reflows the page so the
  capture is a STALE frame, and a thin white band is easy to misread visually. Ground truth =
  PIL-sample the saved screenshot (`im.getpixel`) **and** DOM `getComputedStyle` /
  `document.elementsFromPoint(x,y)`. They agreed; my eyeballing the rendered PNG did not.
- **custom-liquid footer sections are page-width-constrained** → a coloured `.footer-badges`
  div sits ~80px narrower than the viewport, leaving gutters of the section's default bg (looks
  like a floating box). Full-bleed it: `margin-left:calc(50% - 50vw);margin-right:calc(50% - 50vw)`
  (NOT `width:100vw` — that adds a scrollbar). This is what `footer_badges.py` injects.
- **GemPages footers** (Belasil: `layout/theme.gempages.footer.liquid`) paint backgrounds on
  ROW elements with auto-generated ids (`#gXXXX`), not on semantic classes; the `<body>` shows
  WHITE through transparent rows. To recolour a band: `<style>#id1,#id2,#outerRow{background:<hex>
  !important}` for every white row INCLUDING the outermost. The `.anpc` badge div there is injected
  before `</body>` in BOTH theme.liquid and theme.gempages.footer.liquid — edit both.
- **`.anpc` div injected in theme.liquid** (Esteban/Belasil/Apreciat, the vbrmarketing image
  pattern): it renders on the page default (white) BELOW the footer. Give it `background:<footer
  hex>` + `padding-top` so it joins the footer band. Esteban footer black = `#232323`, Apreciat
  dark bar = `#1a1a1a`.
- **menuUpdate to drop ANPC/SOL text links**: fetch items WITH `resourceId`, rebuild keeping the
  rest; pass `resourceId` for SHOP_POLICY/PAGE/BLOG items (else "Subject can't be blank"), `url`
  for HTTP. A store often has the links in TWO menus (`footer` + `legalitate`) — find the one
  that actually renders (match the visible SOL title wording).
- **Ella-theme footers** wire each column to a **theme-config linklist** in the footer SECTION
  settings, so editing the Shopify menu may NOT surface a new item — check the section settings.
- **Rebranded store contactEmail is stale**: Casa Ofertelor's `shop.contactEmail` is
  `contact@bonhaus.ro` (old brand) while `shop.email` is `contact@casaofertelor.ro`.
  `compliance.py` now prefers the email whose domain matches the store domain; also sweep
  old pages (FAQ/returns/GDPR) for the wrong-brand email.
- **NEVER add badges without checking for existing ones first** (the duplicate-icons trap that
  burned us across Gento/Grandia/Belasil). `footer_badges.py add` is idempotent and refuses a
  second set unless `--force`.

## Interpreting seo_audit.py — false signals that waste effort (2026-06)
The audit reads Shopify API fields + a urllib live fetch. Three results LIE; verify before acting:

- **"0/N product meta / SEO title FAIL" ≠ thin content.** Deals/value stores (MagDeal,
  Reduceri Bune, Ofertele, Casa Ofertelor, Apreciat, Covoria, Nocturna…) build unique copy into
  the product PAGE — page-builder landing pages (GemPages/PageFly) or theme metafield blocks —
  while the Shopify `seo.description`/`descriptionHtml` API field stays EMPTY. Live bodies are
  200–860 words with real headings. **Do NOT generate/overwrite product descriptions off the
  API signal — you'd destroy the landing pages.** `seo_audit.py` now prints rendered body word
  count; >350 words = leave it alone. Confirm with a browser before any bulk description job.
- **`<title>=''` + every LIVE check FAIL = FETCH BLOCKED, not a broken page.** Cloudflare on the
  deals storefronts 403s the urllib fetcher (even with a Chrome UA). Googlebot is NOT blocked.
  Re-check those stores in a browser (chrome-devtools) — the pages render fine. `seo_audit.py`
  now flags this explicitly.
- **API `seo.title = 0/N` is usually a NON-issue.** Shopify doesn't store `seo.title` when it
  equals the product title (it uses the default, which renders correctly). The live `<title>` is
  the truth — only act if the live `<title>` is actually wrong/empty/brand-doubled.

## Dawn footer: a menu renders ONLY via a `link_list` BLOCK
Adding items to the `footer` menu (e.g. "Ștergere date (GDPR)") does NOTHING visible unless the
footer SECTION has a `link_list` block whose `settings.menu` points at that handle. The menu
existing is not enough (this hid the GDPR link on Reduceri Bune + Covoria). `footer_badges.py
gdpr-link` builds the menu AND adds the block. `show_policy:true` only lists the shop POLICIES
(Terms/Privacy/Refund) — never custom pages like /pages/stergere-date.

## Verify footer/visual changes with PIXELS + a hard refresh — and tell the user to refresh
Confirming a footer change via DOM `querySelector` returning found:1 is NOT proof the user sees
it: (a) chrome-devtools screenshots can be stale frames after lazy reflow — PIL-sample the PNG;
(b) the user's browser serves a CACHED footer (Shopify section cache + edge), so a change you can
see may look "missing" to them for minutes — say "hard-refresh / Ctrl-Shift-R". Don't claim a
footer change is live off DOM alone; screenshot it, and account for their cache.
