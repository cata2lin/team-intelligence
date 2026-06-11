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
