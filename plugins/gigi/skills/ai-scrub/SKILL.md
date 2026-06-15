---
name: ai-scrub
description: De-AI-ing pre-publish gate for content (Romanian-first) — strips the invisible Unicode watermarks LLMs leave (zero-width/Cf chars), flags AI-tell phrasing via a diacritic-insensitive Romanian blocklist ("în concluzie", "merită menționat", "deblochează", "peisajul", "fără efort"…), and reports over-used em-dashes, with a cleanliness score. Use before publishing blog articles (core:esteban/gt/nubra/labnoir-articles) or any copy, or when asked to "check if this reads AI-written", "curăță textul de urme AI", "de-AI a content", "remove AI watermarks".
argument-hint: "--file articol.md [--fix]"
---

# ai-scrub — de-AI content gate (RO)
> Author: Gigi.

Catches the tells that make copy read as machine-written and the invisible characters LLMs/paste leave behind. Pure stdlib, no keys, offline.

```bash
uv run scrub.py --file articol.md          # report invisibles + em-dashes + RO AI-tell phrases + score
uv run scrub.py --file articol.md --fix    # write a .clean copy (invisibles removed, em-dash normalized)
echo "text" | uv run scrub.py              # from stdin
```

## What it flags
- **Invisible/watermark chars** — zero-width space/joiner, BOM, soft hyphen, narrow-NBSP, and any other Unicode `Cf`-category char. These are removed on `--fix`.
- **Em-dashes (—)** — over-use is a classic LLM tell; `--fix` normalizes spaced em-dashes to commas.
- **RO AI-tell phrases** — diacritic-insensitive (catches both "menționat" and "mentionat"): *în concluzie, merită menționat, deblochează, valorifică, peisajul, fără efort, joacă un rol crucial, transformă modul în care, o adevărată artă, într-o lume în care*, etc.
- **Cleanliness score /100** — ≥80 = OK to publish; below = rewrite the flagged spots.

## How to use it
Run as the **last step before publishing** an article from `core:*-articles`. `--fix` handles the invisibles/em-dashes automatically; the AI-tell phrases are **listed, not auto-rewritten** — a human rewrites those (auto-rewriting would just produce more AI text). Pairs with `gigi:shopify-geo` (AEO readiness) as the two pre-publish gates: scrub = "doesn't read AI", geo = "will AI cite it".

## Caveats
- The blocklist is RO-tuned; extend `RO_TELLS` in `scrub.py` as you spot new tells. English copy needs an EN list.
- It flags *style* tells, not facts — a separate fact-check pass (LLM-based) is a future add.
