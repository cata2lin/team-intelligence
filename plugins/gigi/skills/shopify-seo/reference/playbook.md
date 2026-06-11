# Shopify SEO playbook — detailed how-to by area

Every section: what, why, how (API), and the verify step. All writes go through
`Store` from `../scripts/shopify_lib.py`. Dry-run → `--apply` → verify.

---

## 1. Duplicate content → unique descriptions (highest ROI)

**Symptom:** dozens/hundreds of products share near-identical body copy. Google
treats it as thin/duplicate and suppresses rankings.

**Fix:** rewrite a unique `descriptionHtml` per product. For volume, fan out with
the `Workflow` tool (one agent per batch) or a templated generator keyed by
structured data (gender, olfactory family, inspired-from, notes). Keep brand
voice; lead with the differentiator. Same idea for collection intros.

```python
s.gql('mutation($id:ID!,$h:String!){productUpdate(input:{id:$id,descriptionHtml:$h}){userErrors{message}}}',
      {"id": gid, "h": html})
```

Verify: spot-check several rendered PDPs; confirm the body differs per product.

---

## 2. Meta — SEO title + meta description

**Products / collections** use the native `seo{title description}` field.
**Pages / articles** use metafields `global.title_tag` + `global.description_tag`
(the `seo{}` field is unreliable for them).

- Title ≤ 60 chars, description ≤ 155. Front-load the keyword.
- **ALWAYS send title AND description together** (golden rule #1).
- **Strip the brand** if the theme appends it (golden rule #2) — check live.

Products/collections:
```python
s.gql('mutation($id:ID!,$t:String!,$d:String!){productUpdate(input:{id:$id,seo:{title:$t,description:$d}}){userErrors{message}}}',
      {"id": gid, "t": title[:60], "d": desc[:160]})
```
Pages/articles (metafields, via metafieldsSet — owner is the page/article GID):
```python
s.gql('mutation($mf:[MetafieldsSetInput!]!){metafieldsSet(metafields:$mf){userErrors{message}}}',
  {"mf":[{"ownerId":gid,"namespace":"global","key":"title_tag","type":"single_line_text_field","value":t},
         {"ownerId":gid,"namespace":"global","key":"description_tag","type":"single_line_text_field","value":d}]})
```
Skip utility/funnel pages (search-results, data-deletion, tiktok, 1-lux-… landing
pages). Verify: API read-back of `seo.title/description`, then the rendered
`<title>` and `<meta name=description>` (tolerant parser — see pitfalls).

Templated meta that worked well (adapt voice per store):
`"Alternativă inspirată din {original}: parfum {gen} cu note {family}, persistență …, preț accesibil."`

---

## 3. Image alt text (shared-image aware)

**Trap:** many products reference the **same** generic image file. Bulk-setting a
"specific" alt cross-assigns wrong descriptions. Detect shared media first.

- Count distinct vs total media references. For files referenced by many products
  → **generic honest alt**. For unique files → specific alt (product + inspired-from).
- Set alt via `productUpdate` media or the `image alt` field; collection image via
  `collectionUpdate`.

WebP: **do not "fix".** Shopify CDN serves WebP/AVIF automatically when the theme
uses `image_url`/`img_url` — it content-negotiates on the `Accept` header. Verify
with `fetch_live(url, accept="image/avif,image/webp,*/*")` → Content-Type is webp.
A naive curl without the header sees jpeg and falsely reports "no webp".

---

## 4. Structured data (JSON-LD)

Validate **every** block by `json.loads` — one syntax error invalidates it.

- **Product + Offer:** ensure `offers` has `price`, `priceCurrency`, `availability`,
  and ideally `priceValidUntil` (+1 year) + `itemCondition` (recommended for
  Merchant Center; missing = warning, not error). Some themes emit a minimal
  Offer via the `structured_data` filter — enrich your own block if you control one.
- **AggregateRating:** **verify it isn't already there** before adding. Judge.me
  injects `aggregateRating` client-side (visible in a browser, not in curl). Only
  add from `reviews.rating` / `reviews.rating_count` metafields if genuinely absent.
- **Organization + sameAs:** on the homepage/header. Many Horizon themes hardcode a
  minimal Organization (name/logo/url) with **no sameAs** and don't read social
  settings → inject `sameAs` (real FB/IG/TikTok) directly in the theme. Fix `url`
  to `request.origin` (some themes wrongly use the current page URL). `@context`
  https.
- **WebSite + SearchAction:** homepage only (`request.page_type == 'index'`) — gives
  the brand search box in Google.
- **BreadcrumbList:** product + collection. Some themes render a *visible*
  breadcrumb with **no JSON-LD** (e.g. Ella native) → inject the schema separately,
  guarded to the right template, path = Home › collection(non-junk) › item.
- **Article / BlogPosting:** on blog posts (author, datePublished, image). Usually
  theme-generated; just validate.
- **FAQPage:** valid + future-proof, **but** Google restricted FAQ rich results to
  gov/health sites since Aug 2023 — no SERP accordion for e-commerce. Low value; say so.

Snippets for all of these: `snippets.md`.

---

## 5. Navigation & structure

**High-intent collections in the menu.** Orphan collections (only reachable via
cross-links) get little authority. Add gender + olfactory-family + inspired-by-brand
collections to the main menu (or a "Categorii" dropdown). **Get user approval on
the menu design** — it's visible.

`menuUpdate` **replaces the entire menu** — you must echo every existing item
faithfully (id, title, type, and resourceId for COLLECTION/PAGE/PRODUCT, url for
HTTP, nested items). Build a `conv()` that preserves items, then append/modify.
`menu(handle:…)` may not exist in your API version → use the `menus` connection +
filter by handle. Parent of a dropdown still needs a destination (point it at a
real collection like toate-parfumurile).

**Publish collections to ALL sales channels** (golden rule #6):
```python
pubs = [p["id"] for p in s.gql('{publications(first:20){nodes{id name}}}')["publications"]["nodes"]]
s.gql('mutation($id:ID!,$in:[PublicationInput!]!){publishablePublish(id:$id,input:$in){userErrors{message}}}',
      {"id": collection_gid, "in": [{"publicationId": p} for p in pubs]})
```
Verify with `resourcePublicationsV2`.

---

## 6. Technical hygiene

- **canonical:** Shopify always emits one; verify with a tolerant regex (attribute
  order varies) before declaring it missing.
- **og:image https:** themes often emit `content="http:{{ … | image_url }}"` →
  mixed content, breaks share previews. Change `http:` → `https:` in
  `snippets/meta-tags.liquid`. Add **twitter:image** (= og:image) if absent.
- **noindex** utility pages (search-results, internal search) via a guarded
  `<meta name=robots>` in `theme.liquid`. **Do NOT noindex real collections** —
  confirm with the owner what a collection actually is (the "frontpage"/"Home page"
  collection may be a real, populated category).
- **hreflang:** only if the store is genuinely multi-locale. Single-locale RO → skip.
- **404:** confirm a bogus URL returns 404 (it should).
- Speed/defer third-party scripts: **don't** touch ad pixels (FB/TikTok/GA) without
  the owner's OK — breaks attribution.

---

## 7. Content — blog cluster + homepage text

- **Blog cluster:** topic articles (families, gift guides, EDP vs EDT, occasion
  guides) each linking to relevant collections + 2–3 products. Generate a **unique**
  hero image per article (Gemini `gemini-2.5-flash-image`, save JPEG q85, base64),
  publish via REST `blogs/{id}/articles.json` with `metafields` title_tag/desc_tag.
  Don't reuse one image across articles. Fan out generation with `Workflow`.
- **Homepage SEO text:** a keyword-rich block at the bottom (brand + categories).
  Add a section to `templates/index.json`: Ella → `rich-text` section (heading+text
  blocks; **minimal settings** — some setting values 422 the PUT); Horizon → a
  generic `section` with text blocks. Verify visually in a browser.

---

## Theme-edit mechanics (asset PUT quirks)
- **Template JSON** (`templates/index.json`, `collection.json`, `product.json`,
  `article.json`): full PUT works.
- **Section-group JSON** (`sections/footer-group.json`): a full-dump PUT often does
  **not** persist — use a **string-replace** on the fetched value and PUT that.
- **Liquid** (`layout/theme.liquid`, `snippets/*.liquid`, `sections/*.liquid`):
  fetch → string-replace (idempotent: check your marker isn't already present) → PUT.
- Inject head schema before `</head>`, guarded by `template.name == '…'` or
  `request.page_type`.
