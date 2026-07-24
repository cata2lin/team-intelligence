---
name: articles
description: Generate, verify and publish editorial/SEO blog articles for ANY ARONA perfume store — Esteban (esteban.ro), GT / George Talent (george-talent.ro), Nubra (nubra.ro) and Lab Noir (labnoir.ro) — each in its own brand voice, via one pipeline (`--store <brand>`). Grounded in real in-stock products, adversarially verified, de-AI'd, with SEO metafields + footer link. Use for "scrie/publică articol de blog", "blog article", "editorial", "refresh blog content" on any of these stores. Replaces the 4 per-brand skills (esteban/gt/nubra/labnoir-articles).
argument-hint: "--store esteban|gt|nubra|labnoir  (+ --dry-run | --draft | LIVE)"
---

# articles — blog editorial/SEO pt magazinele de parfum ARONA (un pipeline, 4 branduri)

> Author: **Arona core**. Unifică `articles` + `articles` + `articles` (partajau deja
> `blog_publish_articles.py --store <x>`) + `articles` (variantă cu scripturi proprii). Pipeline complet
> + lecții: `shared/apps/blog-playbook.md`. Voce per brand: `shared/apps/<store>.md`.

## Alege magazinul (`--store`)
| store | domeniu | Blog GID | footer menu | inspirația | scripturi |
|---|---|---|---|---|---|
| **esteban** | esteban.ro | `Blog/110902477145` | `footer` (col. Suport) | **o numește deschis** în titlu (`No. 88, inspirat de Q by D&G`) | `blog_*.py --store esteban` |
| **gt** | george-talent.ro | `Blog/116880474435` | `footer` (are deja Blog) | în **tags**, nu în titlu (nume creativ `N°2 \| Tobacco & Vanile`) | `blog_*.py --store gt` |
| **nubra** | nubra.ro | `Blog/102386696425` | **`footer-menu`** (NU `footer`) | **o numește deschis** (`No. 100, inspirat din Black Afgano`); tonul cel mai value | `blog_*.py --store nubra` |
| **labnoir** | labnoir.ro | (propriu) | — | ⛔ **NU numi NICIODATĂ** originalul; doar profil/eră/origine | `labnoir_publish_articles.py` + `labnoir_rewrite_articles.py` |

Citește ÎNTÂI vocea brandului: `shared/apps/{esteban,gt,nubra,labnoir}.md`. Niciun magazin nu folosește „copie/clonă/fake/replica"; nu promite „identic 100%". ⚠️ Esteban ≠ brandul francez „Estéban Paris Parfums".

## Auth (nimic de configurat)
App custom **ARONA Assistant**. `kb_env` încarcă `SHOPIFY_ARONA_CLIENT_ID/SECRET`, `SHOPIFY_ARONA_<STORE>_DOMAIN`, `SHOPIFY_ARONA_API_VERSION`.

## Pipeline (esteban / gt / nubra — script partajat)
1. **Grounding** (doar produse REALE, în stoc) — `blog-rollout/`: `_blog_recon.py` → `recon/<store>.json`; `build_index.py` → `index/index_<store>.json`; `build_catalog.py` → `catalog/<store>.md`. Folosește **handle-ul EXACT din catalog**, nu-l reconstrui.
2. **Write** — `blog-rollout/articles_workflow.js` (writer + verificator adversarial, grounded în `<store>.md` + `catalog/<store>.md`), apoi **întotdeauna** `process_results.py` (normalizează HTML entity-escaped, rezolvă hero image) → `articles/<store>.json`. Niciodată nu publica output brut de workflow.
3. **De-AI** — treci textul prin **`gigi:ai-scrub`** (scoate watermark-uri + fraze AI, blocklist RO) înainte de publicare. NU reimplementa blocklistul.
4. **Publish** (bundle-uit în plugin la `scripts/blog_data/`; pt conținut NOU pune `BLOG_DATA_DIR=/path/to/blog-rollout`):
```bash
# dry-run = validează (handle real+în stoc, cuvinte interzise) · --draft = staged UNPUBLISHED · fără flag = LIVE
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store <store> --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store <store> --draft
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_publish_articles.py" --store <store>
```
5. **SEO** (`title_tag`/`description_tag` = metafields, NU `summary`) — `seo_workflow.js` → `process_seo.py` (title ≤60, desc ≤160):
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_seo_and_handle.py" --store <store> --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_seo_and_handle.py" --store <store>   # +--new-handle blog ca să redenumești
```
6. **Footer link** (⚠️ nubra = `footer-menu`, nu `footer`; gt are deja Blog):
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_add_to_footer.py" --store <store> --dry-run
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/blog_add_to_footer.py" --store <store>
```

## Variantă `--store labnoir` (scripturi proprii)
Lab Noir folosește scripturi separate (ton *parfumuri cu gust*, reinterpretare; **niciodată** numele originalului):
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/labnoir_publish_articles.py"          # publică batch editorial
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/labnoir_rewrite_articles.py"          # rescrie (păstrează handle-urile/URL-urile stabile)
```
De-AI-ul = **`gigi:ai-scrub`** (nu reimplementa blocklistul RO în rewrite).

## Reguli
- Rămâi în vocea magazinului (`<store>.md`). Fiecare `/products/...` CTA = handle real, în stoc (validatorul enforce-uiește).
- Citează doar afirmații reale (12h+, prețuri, 2+1, transport). **Confirmă cu userul înainte de LIVE.**
- Store-facts + toate cele 7 lecții: `shared/apps/blog-playbook.md`.

## Unghiuri noi (adoptate)
`gigi:content-strategy` (topic clusters) · `gigi:copywriting`+`gigi:copy-editing` (Seven Sweeps) · `gigi:seo-content-brief` (brief SERP) · `gigi:seo-cluster` (hub-and-spoke) · `gigi:ai-scrub` (de-AI pre-publicare).
