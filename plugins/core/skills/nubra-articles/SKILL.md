---
name: nubra-articles
description: Generate, verify and publish editorial/SEO blog articles for the Nubra Shopify store (nubra.ro) in the Nubra brand voice (value-first, "miros de lux la pret accesibil", "cel mai mic pret garantat", "platesti pentru esenta nu pentru ambalaje", 12h+, Made in France, names the inspiration openly). Use when creating or refreshing Nubra blog content.
---

# nubra-articles

> Author: **Arona core**. Sibling of `labnoir-articles`, generalized for the
> ARONA perfume stores. Brand voice doc: `shared/apps/nubra.md`. **Full
> operational pipeline + lessons: `shared/apps/blog-playbook.md`.**

Publish editorial/SEO blog articles on the **Nubra** store (`nubra.ro`). Read
`shared/apps/nubra.md` first — tone: value-first, direct, practic. "Miros de lux
la pret accesibil", "cel mai mic pret garantat", "platesti doar pentru calitatea
esentei, nu pentru ambalaje scumpe", 12h+, ingrediente Made in France, 2+1 gratis.
Nubra **names the inspiration openly** (`No. 100, inspirat din Black Afgano by
Nasomatto`). It is the bluntest value brand of the three (vs Esteban's maison
tone). Never use "copie/clona/fake/replica".

## Auth & secrets (nothing to configure)
ARONA Assistant custom app. `kb_env` loads `SHOPIFY_ARONA_CLIENT_ID/SECRET`,
`SHOPIFY_ARONA_NUBRA_DOMAIN`, `SHOPIFY_ARONA_API_VERSION`. Blog =
`gid://shopify/Blog/102386696425` (`/blog`).

## Grounding (no hallucinated products)
Only REAL, in-stock products. Pipeline in `blog-rollout/`:
- `_blog_recon.py` → `recon/nubra.json`.
- `build_index.py` → `index/index_nubra.json`.
- `build_catalog.py` → `catalog/nubra.md`. Handle = `nubra-<N>`.

## Write
Workflow `blog-rollout/articles_workflow.js` (writer + adversarial verifier,
grounded in `nubra.md` + `catalog/nubra.md`), then **always**
`blog-rollout/process_results.py` (normalizes entity-escaped HTML, resolves hero
image) → `blog-rollout/articles/nubra.json`. Never publish raw workflow output.

## Publish
> Publish/SEO **run out of the box on any machine**: the article + index + SEO JSON
> are bundled in the plugin at `scripts/blog_data/`. The `blog-rollout/` pipeline above
> is only for authoring NEW content (maintainer's machine) — point the scripts at a fresh
> working dir with `BLOG_DATA_DIR=/path/to/blog-rollout`.
```bash
# dry-run = validate (handles real+in-stock, banned words); --draft = staged; no flag = LIVE
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store nubra --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store nubra --draft
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store nubra
```

## SEO (title_tag / description_tag) + handle
SEO `<title>`/`<meta description>` are metafields, **not** the article `summary`.
Generate with `blog-rollout/seo_workflow.js` → `process_seo.py` (title ≤60, desc ≤160):
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_seo_and_handle.py" --store nubra --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_seo_and_handle.py" --store nubra   # +--new-handle blog to rename
```

## Footer link (menu `footer-menu` — NOT `footer`, which the theme doesn't render)
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_add_to_footer.py" --store nubra --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_add_to_footer.py" --store nubra
```

## Rules
- Stay in Nubra voice (nubra.md), bluntest value tone. Naming the original openly
  is fine; never "copie/clona/fake". Don't promise "identic 100%".
- Every `/products/...` CTA = real in-stock handle (validator enforces).
- Cite only real claims (12h+, Made in France, cel mai mic pret, 2+1).
- **Confirm with the user before publishing LIVE.**
- Store-facts table + all 7 lessons: `shared/apps/blog-playbook.md`.
