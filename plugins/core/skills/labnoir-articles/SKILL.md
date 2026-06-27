---
name: labnoir-articles
description: Generate, publish, and rewrite editorial blog articles for the Lab Noir Shopify store (labnoir.ro) in the Lab Noir brand voice (artisanal perfumery, "parfumuri cu gust", reinterpretations — never naming the original/inspiration brands). Use when creating or refreshing Lab Noir blog/editorial content.
---

# labnoir-articles

> Author: **Arona core**. Ported from assistant v2.

Publish and rewrite editorial blog articles on the **Lab Noir** Shopify store
(`labnoir.ro`). Read the brand master document first — `shared/apps/labnoir.md`
in the repo — for tone: *parfumuri cu gust*, reinterpretare, laborator,
descoperire; **never mention the original/inspiration brand names**, only
profile / era / origin descriptors an enthusiast recognizes.

## Auth & secrets (nothing to configure)
Uses the **ARONA Assistant** Shopify app on the Lab Noir store. Credentials are
pulled from the DB secret store automatically (`kb_env` loads
`SHOPIFY_ARONA_CLIENT_ID`, `SHOPIFY_ARONA_CLIENT_SECRET`,
`SHOPIFY_ARONA_LABNOIR_DOMAIN`, `SHOPIFY_ARONA_API_VERSION`).

## Run
```bash
# Publish a batch of editorial articles (title, ~3500-4500 char HTML body,
# summary, tags, featured image, product CTA + 1 cross-sell):
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/labnoir_publish_articles.py" --help
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/labnoir_publish_articles.py"

# Rewrite the published articles (strip RO diacritics, drop <hr>, shorter
# sentences, SEO-tighter, keep handles/URLs stable):
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/labnoir_rewrite_articles.py"
```

## Rules
- Stay in the Lab Noir voice; never name the inspiration brands.
- Keep article handles/URLs stable on rewrite (SEO).
- Confirm with the user before publishing a new batch.

## Unghiuri noi (adoptate MIT)
- **gigi:content-strategy** — planificare editorială/topic clusters. **gigi:copywriting** + **gigi:copy-editing** (Seven Sweeps). **gigi:seo-content-brief** — brief competitiv din SERP. **gigi:seo-cluster** — arhitectură hub-and-spoke.
