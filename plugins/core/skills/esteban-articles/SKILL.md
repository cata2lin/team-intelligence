---
name: esteban-articles
description: Generate, verify and publish editorial/SEO blog articles for the Maison d'Esteban Shopify store (esteban.ro) in the Esteban brand voice (lux accesibil, "experienta de designer la o fractiune din cost", 12h+, names the inspiration openly, maison-curated tone). Use when creating or refreshing Esteban blog content.
---

# esteban-articles

> Author: **Arona core**. Sibling of `labnoir-articles`, generalized for the
> ARONA perfume stores. Brand voice doc: `shared/apps/esteban.md`. **Full
> operational pipeline + lessons: `shared/apps/blog-playbook.md`.**

Publish editorial/SEO blog articles on the **Maison d'Esteban** store
(`esteban.ro`). Read `shared/apps/esteban.md` first — tone: lux accesibil,
"spune la revedere parfumurilor de lux prea scumpe", experienta de designer la o
fractiune din cost, 12h+, de la 45 lei, 2+1 gratis, maison-curated/elegant.
Esteban **names the inspiration openly** in product titles
(`L'Essence No. 88, inspirat de Q by D&G`). Never use "copie/clona/fake/replica".
⚠️ Not the French brand "Estéban Paris Parfums".

## Auth & secrets (nothing to configure)
ARONA Assistant custom app. `kb_env` loads `SHOPIFY_ARONA_CLIENT_ID/SECRET`,
`SHOPIFY_ARONA_ESTEBAN_DOMAIN`, `SHOPIFY_ARONA_API_VERSION`. Blog =
`gid://shopify/Blog/110902477145` (`/blog`).

## Grounding (no hallucinated products)
Only REAL, in-stock products. Pipeline in `blog-rollout/`:
- `_blog_recon.py` → `recon/esteban.json`.
- `build_index.py` → `index/index_esteban.json`.
- `build_catalog.py` → `catalog/esteban.md` (handle | name | inspiration | gender | family | stock).
Handles are inconsistent (`esteban-essential-femei-88`, `lessence-no-116`, …) —
always use the EXACT handle from the catalog, never reconstruct it.

## Write
Workflow `blog-rollout/articles_workflow.js` (writer + adversarial verifier,
grounded in `esteban.md` + `catalog/esteban.md`), then **always**
`blog-rollout/process_results.py` (normalizes entity-escaped HTML, resolves hero
image) → `blog-rollout/articles/esteban.json`. Never publish raw workflow output.

## Publish
> Publish/SEO **run out of the box on any machine**: the article + index + SEO JSON
> are bundled in the plugin at `scripts/blog_data/`. The `blog-rollout/` pipeline above
> is only for authoring NEW content (maintainer's machine) — point the scripts at a fresh
> working dir with `BLOG_DATA_DIR=/path/to/blog-rollout`.
```bash
# dry-run = validate (handles real+in-stock, banned words); --draft = staged; no flag = LIVE
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store esteban --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store esteban --draft
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store esteban
```

## SEO (title_tag / description_tag) + handle
SEO `<title>`/`<meta description>` are metafields, **not** the article `summary`.
Generate with `blog-rollout/seo_workflow.js` → `process_seo.py` (title ≤60, desc ≤160):
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_seo_and_handle.py" --store esteban --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_seo_and_handle.py" --store esteban   # +--new-handle blog to rename
```

## Footer link (menu `footer`, the "Suport" column)
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_add_to_footer.py" --store esteban --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_add_to_footer.py" --store esteban
```

## Rules
- Stay in Esteban voice (esteban.md). Naming the original openly is fine; never
  "copie/clona/fake". Don't promise "identic 100%".
- Every `/products/...` CTA = real in-stock handle (validator enforces).
- Cite only real claims (12h+, de la 45 lei, 2+1, transport gratuit >150 lei).
- **Confirm with the user before publishing LIVE.**
- Store-facts table + all 7 lessons: `shared/apps/blog-playbook.md`.
