---
name: gt-articles
description: Generate, verify and publish editorial/SEO blog articles for the GT Parfumuri (by George Talent) Shopify store (george-talent.ro) in the GT brand voice (influencer energy, "miroase scump dar nu e scump", 25% esenta, 10-14h, creative N° names, inspiration referenced via product tags). Use when creating or refreshing GT blog content.
---

# gt-articles

> Author: **Arona core**. Sibling of `labnoir-articles`, generalized for the
> ARONA perfume stores. Brand voice doc: `shared/apps/gt.md`. **Full operational
> pipeline (recon→write→publish→SEO→footer) + lessons: `shared/apps/blog-playbook.md`.**

Publish editorial/SEO blog articles on the **GT Parfumuri by George Talent**
store (`george-talent.ro`). Read the brand master doc first — `shared/apps/gt.md`
— for tone: influencer/viral energy, "miroase de 10 ori mai scump decat costa",
25% esenta, 10–14h, fabricat in Romania fara intermediari. Product names are
creative (`N°2 | Tobacco & Vanile`); the inspiration house lives in the product
**tags**, not the title. Never use "copie/clona/fake/replica".

## Auth & secrets (nothing to configure)
ARONA Assistant custom app. `kb_env` loads `SHOPIFY_ARONA_CLIENT_ID/SECRET`,
`SHOPIFY_ARONA_GT_DOMAIN`, `SHOPIFY_ARONA_API_VERSION`. Blog =
`gid://shopify/Blog/116880474435` (`/blog`).

## Grounding (no hallucinated products)
Articles must link only REAL, in-stock products. The pipeline lives in
`blog-rollout/`:
- `_blog_recon.py` → pulls blogs/products/images per store into `recon/`.
- `build_index.py` → `index/index_gt.json` (handle, inspiration, gender, family, stock, image).
- `build_catalog.py` → `catalog/gt.md` (compact in-stock list writers/verifiers read).

Re-run recon when the catalog changes (stock/new products).

## Write
Workflow `blog-rollout/articles_workflow.js` (writer + adversarial verifier per
article, grounded in `gt.md` + `catalog/gt.md`). Then **always** run
`blog-rollout/process_results.py` — it normalizes HTML (agents sometimes return
entity-escaped `&lt;p&gt;`), resolves the hero image from the main product, and
writes `blog-rollout/articles/gt.json`. Never publish raw workflow output.

## Publish
> Publish/SEO **run out of the box on any machine**: the article + index + SEO JSON
> are bundled in the plugin at `scripts/blog_data/`. The `blog-rollout/` pipeline above
> is only for authoring NEW content (maintainer's machine) — point the scripts at a fresh
> working dir with `BLOG_DATA_DIR=/path/to/blog-rollout`.
```bash
# dry-run validates handles (real + in-stock) and banned words; --draft stages
# UNPUBLISHED for review; no flag = LIVE (confirm with the user first).
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store gt --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store gt --draft
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store gt
```

## SEO (title_tag / description_tag) + handle
SEO `<title>`/`<meta description>` are metafields, **not** the article `summary`.
Generate with `blog-rollout/seo_workflow.js` → `process_seo.py` (title ≤60,
desc ≤160), then:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_seo_and_handle.py" --store gt --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_seo_and_handle.py" --store gt   # add --new-handle blog to rename
```

## Footer link
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_add_to_footer.py" --store gt --dry-run  # GT's footer already has Blog
```

## Rules
- Stay in GT voice (gt.md). Lead with the creative name; you may name the
  inspiration for SEO but never call it a copy/clone.
- Every `/products/...` CTA must be a real in-stock handle (validator enforces).
- Cite only real claims (25% esenta, 10–14h, oferta curenta).
- **Confirm with the user before publishing LIVE.**
- See `shared/apps/blog-playbook.md` for the store-facts table and all 7 lessons
  (HTML escaping, banned-words-even-when-negating, session-limit fallback, etc.).

## Unghiuri noi (adoptate MIT)
- **gigi:content-strategy** — planificare editorială/topic clusters. **gigi:copywriting** + **gigi:copy-editing** (Seven Sweeps). **gigi:seo-content-brief** — brief competitiv din SERP. **gigi:seo-cluster** — arhitectură hub-and-spoke.
