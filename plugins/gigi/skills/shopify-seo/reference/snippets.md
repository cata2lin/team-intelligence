# Drop-in Liquid / JSON-LD snippets

Inject head schema **before `</head>`** in `layout/theme.liquid` (string-replace).
Guard each by template so it only renders where it should. Always check your
marker isn't already present (idempotent), then PUT the modified asset value.

---

## BreadcrumbList (product) — when the theme shows a visible breadcrumb but no schema
```liquid
{%- if template.name == 'product' -%}
{%- assign bc = product.collections.first -%}
{%- for col in product.collections -%}
  {%- unless col.handle == 'frontpage' or col.handle contains 'do-not-delete' -%}
    {%- assign bc = col -%}{%- break -%}
  {%- endunless -%}
{%- endfor -%}
<script type="application/ld+json">{"@context":"https://schema.org","@type":"BreadcrumbList","itemListElement":[{"@type":"ListItem","position":1,"name":"Acasă","item":{{ shop.url | json }}}{%- if bc -%},{"@type":"ListItem","position":2,"name":{{ bc.title | json }},"item":{{ shop.url | append: bc.url | json }}}{%- endif -%},{"@type":"ListItem","position":{% if bc %}3{% else %}2{% endif %},"name":{{ product.title | json }},"item":{{ shop.url | append: product.url | json }}}]}</script>
{%- endif -%}
```

## Organization + sameAs — when the theme hardcodes a minimal Organization
Replace the existing `"url": …` / closing brace of the Organization block in
`sections/header.liquid` (or wherever it lives) so it adds `sameAs` and fixes `url`:
```liquid
"url": {{ request.origin | json }},
"sameAs": ["https://www.facebook.com/…","https://www.instagram.com/…","https://www.tiktok.com/@…"]
```
Also change `@context` from `http://schema.org` to `https://schema.org`.

## WebSite + SearchAction — homepage only (brand search box in Google)
```liquid
{%- if request.page_type == 'index' -%}
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite","name":{{ shop.name | json }},"url":{{ request.origin | json }},"potentialAction":{"@type":"SearchAction","target":{"@type":"EntryPoint","urlTemplate":"{{ request.origin }}/search?q={search_term_string}"},"query-input":"required name=search_term_string"}}</script>
{%- endif -%}
```

## AggregateRating (product) — only if Judge.me/app isn't already injecting it
Render right after the theme's Product structured data, pulling review metafields:
```liquid
{%- assign r = product.metafields.reviews.rating.value -%}
{%- assign rc = product.metafields.reviews.rating_count -%}
{%- if r and rc and rc > 0 -%}
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":{{ product.title | json }},"aggregateRating":{"@type":"AggregateRating","ratingValue":"{{ r.rating }}","reviewCount":"{{ rc }}","bestRating":"{{ r.scale_max | default: 5 }}"}}</script>
{%- endif -%}
```

## Offer enrichment — add priceValidUntil + itemCondition to a Product Offer block
Inside the `offers` object you control:
```liquid
"itemCondition": "https://schema.org/NewCondition",
"priceValidUntil": "{{ 'now' | date: '%s' | plus: 31536000 | date: '%Y-%m-%d' }}",
```

## FAQPage — inject into the FAQ page body (Q&A often live in a theme accordion)
Extract the `<summary>` (question) + answer pairs from the rendered accordion, then
append a `<script type="application/ld+json">` with `{"@type":"FAQPage","mainEntity":
[{"@type":"Question","name":Q,"acceptedAnswer":{"@type":"Answer","text":A}}…]}` to the
page `body_html` (REST page update). NB: no SERP accordion for e-commerce since 2023.

## og:image → https + twitter:image — in `snippets/meta-tags.liquid`
```diff
- <meta property="og:image" content="http:{{ page_image | image_url }}">
+ <meta property="og:image" content="https:{{ page_image | image_url }}">
+ <meta name="twitter:image" content="https:{{ page_image | image_url }}">
```
(Ella uses `img_url: 'master'` instead of `image_url`. Only the `http:` og:image
needs changing — `og:image:secure_url` is usually already https.)

## noindex a utility page (NOT real collections)
```liquid
{%- if template == 'page' and page.handle == 'search-results' -%}<meta name="robots" content="noindex, follow">{%- endif -%}
```

---

### Verify any of these
1. API/source: confirm the asset value contains your marker after PUT.
2. Live: `fetch_live(...)` with a cache-buster, parse the JSON-LD with `json.loads`.
3. Cache: if a FAIL looks stale, open the page in chrome-devtools and read
   `document.querySelectorAll('script[type="application/ld+json"]')`.
4. Validate at search.google.com/test/rich-results for the real verdict.
