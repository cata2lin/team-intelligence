# Perfume "inspired-by" catalog playbook (Esteban / Nubra / GT …)

The end-to-end, **repeatable** process for a dupe/"inspirat din" perfume store. Captures
everything learned doing Esteban + Nubra so the next store (or a re-run) is mechanical.
Read this BEFORE re-deriving any of it.

## Order of operations (proven on Esteban + Nubra)
1. **Brand collections** (`scripts/brand_collections.py`) — smart collections per inspiration brand, **auto-published to all channels**.
2. **Menu** (`scripts/menu_addbrands.py`) — one top-level "Inspirate din" dropdown, **top-N brands only**, `--after "<item>"` for placement.
3. **Collection sidebar** — "După brand" + "Categorii" link blocks **below the filters** (theme edit, see §Sidebar).
4. **Internal linking** (`scripts/internal_links.py`) — `cluster` (interlink top brand collections) + `pdp-brand` (link each product to its brand collection) + `deorphan` (blog↔collection).
5. **Copy rewrite** — mass-market voice (6-agent fan-out), then align to metafields.
6. **Note/metafield verification** — verify notes vs the REAL original, fix, then regenerate descriptions FROM metafields (see §Notes).
7. **FAQ block** — dynamic from metafields + FAQPage schema (see §FAQ).
8. **inspired_by block** — link the brand name to its collection (see §inspired_by).
9. Fill metafield/SEO gaps; verify live.

## Per-store split: where brand/category links live
- **Collection DESCRIPTION = blog article links ONLY** ("Ghiduri utile: …", `/blogs/…`). NO collection cross-links in the description.
- **Collection cross-links (brand + category) = SIDEBAR** ("După brand" + "Categorii", below the filters).
- Every hub collection should have ≥1 relevant article in its description (keyword-match + fallback to 2 evergreen guides). `internal_links.py deorphan` only maps a subset → run the keyword auto-map (in this file's git history) so collections like `barbati` aren't left without articles.
- On a store where the sidebar truly can't be done, the description is the fallback for brand/category links — but prefer the sidebar.

## Sidebar: "După brand" + "Categorii" (theme edit, per-theme)
Goal: two link blocks **below** the native filters, in the collection's left sidebar. Use
**static links** (NOT `linklists[...]` — that often resolves empty in the storefront). Top
~12 brand collections by product count + the main category collections.

- **Esteban (Ella/Halo "Ella cu kit 3"):** edit `snippets/collection-sidebar.liquid`. The
  category-block path is gated off (`has_sidebar`/`category` block won't render), but the
  snippet IS rendered — append your static `<div class="sidebarBlock …">` block at the END
  of the snippet (after the filter section) so it sits below the filters. Reuse the theme's
  `.sidebarBlock`/`.sidebarBlock-heading` classes.
- **Nubra (Online Store 2.0, "nubra v1.1"):** the facets are in `blocks/filters.liquid`
  (~1485 lines). The **desktop pane** (`{% if should_show_pane %}` → `{{ rendered_filters }}`)
  does **NOT** render on the live page — the visible desktop sidebar comes from the
  **DRAWER** path (the `{%- for filter in filters -%}` loop rendered with `in_drawer: true`,
  ~line 442). Inject your block right **after that drawer loop's `{%- endfor -%}`**, before
  its `</div>`. (Injecting into the desktop pane = invisible; spent 3 attempts learning this.)
- **Limit the NOTE facet**: section setting `max_item_per_filter` (default 10) → set to ~6 so
  the sidebar isn't a mile long; the rest collapse behind "Vezi tot".
- After ANY collection-template/snippet edit, the storefront is **CDN-cached** — see §Cache.

## FAQ block (dynamic, per-product, with FAQPage schema)
A Custom Liquid block that builds 3-4 Q&A from `inspired_by` + note metafields + emits
`FAQPage` JSON-LD (AEO + rich results). Placement: a **separate block lower on the page**
(after the description/specs), NOT glued to the spec table.
- **Esteban:** a `custom_liquid` block in `templates/product.json` `main` section, added to
  `block_order` near the end (id e.g. `brand_faq_block`).
- **Nubra:** a `custom-liquid` block (theme has `blocks/custom-liquid.liquid`, setting id
  `custom_liquid`) inside the `product-details` block's nested `block_order`, after `product-specs`.
- **Question style (owner-approved):** short, distinct, NO product name repeated:
  - `Seamănă cu {original}?` — use just the perfume name (split `inspired_by` on " by ", take `[0]`); keeps the searchable term, drops "by Brand".
  - `Cum miroase?` — from `varf` (+ `note_baza`).
  - `Cât rezistă pe piele?` — 12h+ (Esteban) / 10-14 ore (Nubra).
  - `Pentru cine este potrivit?` — from `sex` + `mom_zi`.
- Guard the whole block on `{%- if insp != blank -%}`. Conditionally include the notes/gender
  questions only when those metafields exist.

## inspired_by block → brand-collection link
The product "Inspirat din {original} by {Brand}" card can link to the brand collection.
Derive the handle: `'parfumuri-inspirate-' | append: brand | handleize`, then:
```liquid
{%- assign bcol = collections[bhandle] -%}
{%- if bcol != blank and bcol.id -%} <a href="{{ bcol.url }}">Vezi toate … {{ brand }}</a> {%- endif -%}
```
**GOTCHA (real bug):** do NOT write `bcol.products_count > 0` — for a non-existent collection
(`collections['x']` = EmptyDrop), `products_count` is an empty string → `"" > 0` throws
`Liquid error: comparison of String with 0 failed` on the product page. Use `bcol != blank
and bcol.id`. Brands with <3 products have no collection → the link is correctly omitted.
Nubra renders the description raw, so its brand link lives in the **description** (clickable);
Esteban strips the description (see pitfalls §12) so its brand link lives in the **inspired_by block**.

## Notes: metafields are the source of truth; verify them
The rewrite agents pulled "Profil olfactiv" from the OLD (often inaccurate) descriptions, so
description notes drifted from the spec-table metafields. Fix order:
1. **Verify metafields vs the REAL original** (multi-agent workflow — see
   `scripts/verify-perfume-notes.workflow.js`): each agent recalls the documented top/heart/base
   notes of the original perfume and proposes corrections (conservative — only when confident).
   On the first run: 303 checked → 71 fixed (e.g. Soleil Blanc had Fame's notes; Stronger With
   You Sandalwood was mislabeled Bărbați; many `note_parfum` values were outside the controlled
   vocabulary).
2. **Apply** via `metafieldsSet`: `varf`/`note_inima`/`note_baza` = `multi_line_text_field`
   (plain string); `sex`/`note_parfum`/`mom_zi` = `list.single_line_text_field` (JSON array
   string); `volum` = `number_integer`.
3. **Regenerate description "Profil olfactiv" FROM metafields** so description = spec table =
   FAQ = reality: `Profil olfactiv: vârf de {varf}; inimă de {note_inima}; bază de {note_baza}.`
- **`note_parfum` controlled vocabulary:** Oriental, Floral, Lemnos, Dulce, Fresh, Fructat,
  Aromatic, Gourmand, Condimentat, Acvatic, Vanilat, Cypre, Aldehidic. (Reject "Clasic",
  "Citrus", "Pudrat", "Alb", "Verde", "piele", "Gurmand"→"Gourmand", etc.)
- `sex` values: `Femei` / `Barbati` / `Unisex` (no diacritics). Niche fragrances mislabeled
  with all three → collapse to the real gender (often `Unisex`).

## Copy voice (mass-market RO perfume)
No jargon ("siaj"/"sillage"), few adjectives, NO "flacon", NO volume, **don't repeat the
on-page offer** ("2+1 gratis" is already shown). 2-3 short `<p>`, bold `Profil olfactiv:`.
Pair with `gigi:ai-scrub`. The Profil olfactiv line should come from metafields (see §Notes).

## Verify live: CDN cache + client-side rendering
- **Collection/product pages are heavily CDN-cached.** `?param` does NOT bust it. `curl` hits
  the cached edge (you'll see the OLD HTML even after a correct edit). To verify:
  1. Read the change back from the **theme asset / metafield via API** (authoritative).
  2. **Bust the page**: `collectionUpdate`/`productUpdate` (a no-op re-save) busts THAT page's cache.
  3. In the browser, **fullPage screenshot or `wait_for` + `evaluate` AFTER load** (the page
     renders client-side; an early `evaluate` returns an empty DOM → false negatives).
- A run where "0 in served HTML" but the asset clearly contains the block = it's cache. Bust + browser-verify.

## Per-store theme matrix
| | Esteban — Ella/Halo "Ella cu kit 3" | Nubra — OS2.0 "nubra v1.1" |
|---|---|---|
| Product description render | `strip_html` summary → edit `product-short-description.liquid` to `{{ desc }}` for links/bold | raw `{{ closest.product.description }}` → links/bold work natively |
| Spec table | Custom Liquid block in `templates/product.json` (list metafields need `\| join`) | native `product-specs` block (no fix) |
| Brand encoding | "… by Brand" in title | "… by Brand" in title |
| Sidebar inject point | `snippets/collection-sidebar.liquid` (append at end) | `blocks/filters.liquid` DRAWER loop (`in_drawer: true`) |
| FAQ block type | `custom_liquid` (templates JSON) | `custom-liquid` block (`blocks/custom-liquid.liquid`) |
| inspired_by link | in the inspired_by Custom Liquid block | in the product description (renders raw) |
| Native perfume blocks | minimal | rich: `card-inspired-by`, `product-specs`, `accordion`, `perfume-notes-badges` |

**GT (not yet done):** brand is in `custom.inspired_by` (NOT the title); existing brand
collections use a **TAG EQUALS** smart-collection rule; volume key is `custom.volum_ml_`.
`brand_collections.py` (title-based) must be adapted to read `inspired_by`.
