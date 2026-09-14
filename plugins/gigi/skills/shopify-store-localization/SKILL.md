---
name: shopify-store-localization
description: >
  Fully localize / translate a Shopify cross-border store into a target language — theme
  sections, collections, navigation menus, metafield FILTER FACETS (labels + values), the
  perfume note / ingredient vocabulary, product descriptions + SEO, policies and pages — then
  publish the locale and hand off the two admin-only steps. Use for "translate the store to
  Bulgarian / Czech / Polish / Hungarian", "traduceti magazinul", "localize Duppo / a perfume
  store", "switch the storefront language", "the filters are in English", "translate collections
  / menu / product notes". Encodes the platform gotchas that waste hours (primary language is
  NOT settable via API, the privacy policy is auto-managed and self-localizes, Horizon ships the
  target locale, regenerate templated descriptions from metafields, facet VALUES need translating
  not just labels, Search & Discovery filter labels are app-only).
argument-hint: "<store> <target-lang> (e.g. Duppo bg)"
---

# shopify-store-localization

> Author: **Gigi**. Turn an English (or any-language) Arona cross-border Shopify store into a
> fully native storefront in the market's language. Worked example: **Duppo BG** (bg.duppo.eu),
> EN → Bulgarian, 113 perfumes, July 2026 — every surface below was translated end to end.

The value of this skill is the **playbook + the platform gotchas**. The translation *content* is
produced per store/language by you (the operator/LLM); the scripts are the API glue that applies
it and the reference implementations from the Duppo run.

## Decide the delivery model FIRST (it forks everything)
For a **single-market** store (a `bg.`/`cz.`/`pl.` domain) the right model is **target language as
the store's PRIMARY/base content**, not a bilingual translation layer:
- You rewrite the base content directly in the target language (this skill's scripts do that).
- English is then dropped. No language switcher, no `/xx/` subfolders, no Translations-API digests.
Only choose a bilingual layer if the store genuinely serves two markets — then the theme-section
Translations API becomes painful and is out of scope here.

## The ordered checklist (do it in this order)
1. **Theme sections** — the biggest, most visible chunk. These are your custom section builders
   (`build_home.py → index.json`, `build_product.py → product.json`, `collection.json`) plus the
   section-group JSONs (`header-group.json` announcement bar, `footer-group.json`, `page.*.json`).
   Rewrite the copy strings to the target language and re-push with your `theme.py put`.
   - Hard-coded strings inside `.liquid` markup (not settings) must be edited in the liquid too.
   - Where a section derives a word from a structural TAG (e.g. dp-related builds "More **woody**
     perfumes" from the English family tag), add a parallel target-language array indexed by
     `forloop.index0` and render that — **do not** translate the tag itself.
2. **Collections** — `collectionUpdate` title + descriptionHtml (`scripts/localize.py collections`).
3. **Navigation menus** — `menuUpdate` for main + footer + help. Menu item titles are explicit
   snapshots, not dynamic — set them. Submenu family items can inherit the (now-translated)
   collection titles. (`scripts/localize.py menus`)
4. **Metafield filter FACETS** — two parts, both matter:
   - **Definition names** → `metafieldDefinitionUpdate` (this is the facet's default label).
   - **VALUES on every product** → `metafieldsSet` (the facet OPTIONS the shopper clicks, and the
     PDP chips). Translating only the label leaves English options. (`scripts/localize.py facets`)
   - Build a controlled vocabulary map per facet (family, gender, occasion, season, intensity,
     longevity) and verify coverage before applying — assert every distinct value is mapped.
5. **Note / ingredient vocabulary** (perfume stores) — the `notes_top/heart/base` list metafields
   hold ~200 distinct ingredient names shown prominently on the PDP. Build a full glossary
   (`Bergamot→Бергамот`, `Vanilla→Ванилия`, …) with a coverage assert, then `metafieldsSet`.
   (`scripts/localize.py notes`)
6. **Product descriptions + SEO** — these are TEMPLATED (Top/Heart/Base + family line +
   boilerplate). **Regenerate** them from the now-translated metafields instead of translating 100+
   bespoke bodies. Keep product TITLES as the proper names (translate only a `No.`→`№`-style prefix
   if asked). (`scripts/localize.py descriptions`)
7. **Policies + pages** — the refund/shipping/terms policies are plain HTML → translate via
   `shopPolicyUpdate`. Pages (about/contact) via `pageUpdate`. **The PRIVACY policy is special**
   (see gotchas). Contact page content often lives in a `page.contact.json` section — translate
   that too.
8. **Publish the locale** — `shopLocaleUpdate(locale, {published:true})`.
9. **Hand off the two admin-only steps** (see below).

## ⚠️ Platform gotchas (each cost real time on Duppo BG)
- **Primary/default language CANNOT be set via Admin API.** Only `shopLocaleEnable/Disable/Update`
  exist (Update just toggles `published`). The merchant flips **Settings → Languages → Default
  language** by hand; that swap makes your target-language base content primary and drops English.
  Tell them explicitly — it's the one thing you cannot automate.
- **Horizon ships the target `locales/<xx>.json`.** So the moment the language is active, "Add to
  cart", cart, search AND **checkout** localize automatically — you do NOT translate theme system
  strings. Verify with `theme.py get locales/<xx>.json` (grep `actions/add_to_cart`).
- **The Privacy policy is `autoManaged:true`** → `shopPolicyUpdate` is rejected ("Automatic
  management must be turned off"). Do **not** turn it off — it already lists the target locale in
  `supportedLocales` and **self-localizes + stays legally maintained** once the language is active.
  Leave it alone; it becomes native for free.
- **Facet VALUES vs family TAGS.** The `filter_*` metafield values only drive the S&D filter
  options + PDP chips → safe to translate. The family **tags** (Floral/Woody…) drive collection
  membership + related-products logic → keep them English; map to the target language only for
  *display*.
- **Search & Discovery filter LABELS are app-only.** S&D snapshots the label when a filter is
  added; renaming the metafield definition later does NOT update an already-added filter, and there
  is **no Admin API** for the S&D config. The merchant edits labels (or removes+re-adds filters) in
  Online Store → Search & Discovery → Filters. Also: a "Brand" filter on **Vendor** shows only your
  own brand — for "inspired-by" brands, create a `custom.filter_brand` metafield from
  `inspired_house` and have them add THAT.
- **Money format** stays a store setting; `€` before/after the number is not copy you translate.
- **"No." → "№"** (numero sign) is the natural Bulgarian numbering prefix; the bottle photo's baked
  "50 ml / No. X" label can't change — that's fine.

## Auth
Perfume stores (Duppo/Nubra/Esteban/GT) authenticate through the **ARONA Assistant** app with
`grant_type=client_credentials` (secrets `SHOPIFY_ARONA_*` in the KB) — see `[[shopify-arona-assistant-mint]]`.
`scripts/localize.py` uses that flow; pass `--domain <shop>.myshopify.com` or set the store's
`SHOPIFY_ARONA_<STORE>_DOMAIN`. Never print a token. For stores on static/OAuth tokens instead,
swap in `core.stores.get_store(prefix)`.

## Run
```bash
# 1) produce the translation JSONs (you/the LLM write these against a locked glossary)
#    collections.json, menus.json, facets.json (label+value maps), notes.json, desc_boilerplate.json
# 2) apply, per surface (dry-run first, then --apply):
uv run scripts/localize.py collections  --domain <shop> --in collections.json --apply
uv run scripts/localize.py menus        --domain <shop> --in menus.json --apply
uv run scripts/localize.py facets       --domain <shop> --in facets.json --apply   # def names + values + coverage assert
uv run scripts/localize.py notes        --domain <shop> --in notes.json --keys notes_top,notes_heart,notes_base --apply
uv run scripts/localize.py publish      --domain <shop> --locale bg --apply
```
**Theme-section copy** (step 1), **product TITLES** and **product DESCRIPTIONS/SEO** are edited in
the store's own build repo (its `build_*.py` / section JSONs, pushed with `theme.py`) — the copy is
store-shaped. Descriptions are TEMPLATED, so regenerate them from the now-translated metafields
rather than translating bespoke bodies; the Duppo `tr_descs.py` (Връх/Сърце/База + family line +
boilerplate) is the reference.

## Lock the glossary before mass-translating
The "100% natural, nothing weird" bar is met by **agreeing terminology up front**. Bulgarian
reference set that worked: eau de parfum→**парфюмна вода** (EDP stays), Top/Heart/Base→**Връх/Сърце/База**,
scent family→**ароматно семейство** (NOT „фамилия" = surname!), Cash on delivery→**наложен платеж**,
Scent profile→**Ароматен профил**, For her/him/Unisex→**За нея/За него/Унисекс**, working days→**работни
дни**, pick-up point→**офис на куриер**. Present the glossary, get sign-off, then translate.

See the memory `[[duppo-bulgarian-translation]]` for the full worked run.
