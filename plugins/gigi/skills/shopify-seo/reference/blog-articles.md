# Blog articles + images — the repeatable playbook (Esteban / GT / Nubra / Grandia)

End-to-end process for adding SEO blog articles to a store, **with the images that
make them look native**. Distilled from the multi-store rollout (perfume dupes +
Grandia utility). Read before writing or "fixing" articles.

## 0. When to write a NEW article — the gap test (don't write filler)
Write an article ONLY when all three hold (verify, don't assume — this is the same
"verify before redoing" discipline as the rest of the skill):
1. **Demand** — the query/cluster has real GSC impressions (`gigi:analytics` →
   `gsc.py queries --brand <b> --days 90`). A cluster of 1–13 impressions is NOT
   demand (that was Nubra non-brand: skip net-new, the bottleneck is authority).
   Esteban "paco rabanne 33 ml" = 107 imp = real → write.
2. **In-stock product** — there's a real, ACTIVE, in-stock product to link/funnel to.
   No product → no article. OOS product → **defer + flag restock**, don't funnel to a
   0-stock page (we skipped "etajeră baie fără găurire", "amortizor ușă", "noptieră"
   — all high-demand but OOS; those are buying decisions, not article tasks).
3. **No existing article** — check the store's current articles first
   (`articles(first:60){nodes{handle title}}`). Grandia already had 24 (pernă
   cervicală, tapet 3D, rafturi…) — don't duplicate. A **brand-structure gap** counts
   even at low demand: Nubra had Tom Ford/Armani/Dior articles but no Paco Rabanne
   while holding 10 in-stock Paco dupes → legitimate to write (parity + internal
   linking), framed as pre-seed, not demand-chasing.

Product-specific head terms that already rank via the product page (e.g. Grandia
"biblioteca tree of knowledge", pos 2–11) = **product-page optimization, not an
article**. Don't write a guide for those.

## 1. Write + verify (workflow)
Use a 2-stage workflow (write → adversarial verify), grounded in the brand voice doc
(`shared/apps/<store>.md`) and the in-stock catalog (`blog-rollout/catalog/<store>.md`).
The verifier MUST: confirm every linked handle is in-catalog + in-stock; confirm each
named original matches that product's `inspiration`/`inspired_by` (the Esteban Armani
draft invented an "Acqua di Gio" pairing with no catalog match → verifier removed it);
enforce voice (perfume = "inspirat de", never copie/clonă/fake/replică; Grandia utility
= practical, NO discount hype, no invented prices); 3.5–5k chars; title ≤70; summary
≤240; no `<hr>`/markdown/emoji; RO diacritics.

## 2. SEO meta is METAFIELDS, not the `seo` field
`Article.seo` **does not exist** on these stores' API versions (it throws
`Field 'seo' doesn't exist on type 'Article'`). Article SEO = the metafields
**`global.title_tag`** + **`global.description_tag`** (single_line_text_field).
- Read: `metafield(namespace:"global",key:"title_tag"){value}` / `…description_tag…`.
- Write: `metafieldsSet(metafields:[{ownerId:<articleGID>, namespace:"global",
  key:"title_tag", type:"single_line_text_field", value:…}, …])`.
- Targets: **title_tag ≤ 60 chars** (keyword-first, no brand append), **description_tag
  140–160 chars** (keyword + a sober value line; for utility stores end with
  "Livrare în toată țara." — never discount hype).

## 3. Which TOKEN can write (critical for non-ARONA stores)
- **ARONA app** (`Store("esteban"|"gt"|"nubra"|"labnoir")`) — read+write on its 4 stores.
- **Grandia & other non-ARONA stores**: the `SHOPIFY` app `client_credentials` token is
  **READ-ONLY on content** — `articleUpdate`/`metafieldsSet`/`articleCreate` return
  **`null`** (silent, no userError). Use the per-store `shpat_` token instead:
  **`Store.from_csv("GRAN")`** (reads the `prefix,shop,token` row from
  `SHOPIFY_STORES_CSV`). That token writes fine. Symptom to recognize: mutation result
  field is `null` with no errors → wrong/read-only token.

## 4. Images — the part that makes articles look native
Audit finding: existing articles have BOTH a **featured image** (blog-listing
thumbnail) AND a **banner in the body** (top of the article). New articles created
with only a product-photo hero look bare. **Standard for every new article: a 16:9
banner embedded at the top of the body AND set as the featured image.** (Per-store the
old norm differed — Esteban 1 in-body banner, Grandia rich lifestyle images, Nubra was
hero-only — but the agreed standard now is banner-in-body + featured for ALL.)

### 4a. Generate the banner — `gigi:image-gen` `hero.py`, with the REAL product
`uv run hero.py --store <S> --title "<t>" --scene "<topic scene>" --ref <real product
image url/path> --engine gemini --pro --aspect 16:9 --out <dir> --name <slug>`
- **Always `--ref` the store's OWN product photo** so the banner shows OUR product, not
  an invented one. Perfume: the real bottle ("sticla noastră" — Esteban black
  rectangular `img_product_1.jpg`; Nubra cylindrical turquoise/burgundy). Utility
  (Grandia): the real shed/canopy/hedge so the scene shows the actual item. Gemini
  preserves the referenced subject faithfully (incl. our label).
- **Garbled text gotcha:** if the ref product carries branding, Gemini may hallucinate
  gibberish text on it (the Grandia balance bike came out "BABY BO PABS"). Fix: add
  "no text, no logos" to the scene, or regenerate **without `--ref`** for a clean
  representative shot. **Always eyeball every generated banner (Read the PNG) before
  publishing to a live site.**
- The Esteban dupe products all share one generic bottle render; that's fine to use as
  the ref (it IS our bottle/label).

### 4b. Upload to Shopify Files (per-store write token)
`stagedUploadsCreate(input:[{filename,mimeType:"image/png",resource:FILE,
httpMethod:POST,fileSize}])` → POST multipart to the staged target
(`data=parameters` + `files={"file":(name,fh,"image/png")}`, expect 200/201/204) →
`fileCreate(files:[{originalSource:<resourceUrl>, contentType:IMAGE, alt}])` →
**poll** `node(id){... on MediaImage{image{url}}}` until `image.url` is non-null
(processing is async, ~2–10s). That CDN url is permanent.

### 4c. Embed + set featured
- Body, at the very top: `<!-- hero-banner --><div style="margin:0 0 2rem 0;"><img
  src="URL" alt="ALT" style="width:100%;height:auto;border-radius:8px;"></div>` then the
  article body. The `<!-- hero-banner -->` marker = idempotency (skip/replace on re-run;
  to swap, regex-remove `<!-- hero-banner --><div.*?</div>\s*` then prepend the new one).
- `articleUpdate(article:{ body:<new>, image:{url:URL, altText:ALT} })` sets BOTH the
  in-body banner and the **featured image** in one call.
- **Why featured matters:** some themes (Esteban) do NOT render the featured image on
  the single-article page — only the body banner shows there — but the featured image
  IS the blog-listing thumbnail. Setting both covers article page + listing.
- **CDN lag:** right after upload the `<img>` can render broken for a few seconds while
  the file propagates (the file URL already returns 200). Re-check after a moment;
  it's not a real failure.

## 5. Publish
`articleCreate(article:{blogId, title, handle, body (HTML!), summary, tags,
isPublished, author:{name}, image:{url,altText}})`. **Not idempotent** — query
`articles(query:"handle:<slug>")` first and skip if it exists (re-run = duplicate).
Grandia blog = `news`. After create, set the `global.title_tag`/`description_tag`
metafields. Publish flow we use: create with everything (incl. banner already in body)
→ `isPublished:true`. For a brand-new public article get a quick go-live OK; the lever
fixes on EXISTING articles (meta, internal links) we apply directly.

## 6. The "lever" on EXISTING articles (cheap, high-ROI)
Audit every article for under-optimization and fix in place:
- Missing `description_tag` / default-or-too-long `title_tag` → write proper meta.
- **0 internal product links** in big guides (they linked only collections) → append a
  `<h2>Produse recomandate</h2>` block with 2–4 real in-stock product links (match
  products to the topic; drop off-topic candidates like "autocolant marmură" on a
  window-film article). Marker-guarded for idempotency.
- Draft articles that are complete → publish.
Audit query per article: `handle title isPublished tags image{url} body
tt:metafield(global/title_tag) dd:metafield(global/description_tag)`; flag
desc-missing / title-default / thin(<1800 ch) / 0-product-links / no-image / draft.

## 7. Data-quality side-catches
While grounding articles you'll surface product data bugs — fix them. Example: "Phantom
Elixir" was mislabeled **"by Armani"** across Esteban/GT/Nubra (it's **Paco Rabanne**;
the `inspired_by_photo` even pointed to a `rabanne-` file). Fix title + `custom.inspired_by`
+ description + `description_tag`; the brand smart-collections re-evaluate (Nubra/Esteban
by TITLE-contains, GT by a brand TAG — so on GT swap the `Armani` tag for `Paco Rabanne`).
Smart-collection membership updates **asynchronously** (give it ~20s before re-checking).
